"""Run a Python child against a PostgreSQL instance owned by this invocation.

Usage: run_isolated_checks.py [--database postgres|none] [--allow-live-ai] -- <python args>
Exit code: the child's on failure; 2 blocked before the child; 3 child passed but cleanup
is incomplete; 130 interrupted. Django must not be imported here: the child env comes first.
"""
import argparse
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from apps.common.tests.harness_pg_source import DEFAULT_IMAGE, PGSourceHarness  # noqa: E402

EXIT_BLOCKED = 2
EXIT_CLEANUP_FAILED = 3
EXIT_INTERRUPTED = 130
CHILD_STOP_TIMEOUT_SECONDS = 15
HOST = "127.0.0.1"
ANONYMOUS_VOLUME = re.compile(r"[0-9a-f]{64}")
CLOUD_KEYS = (
    "D1_BRIDGE_URL", "D1_BRIDGE_SECRET_TOKEN", "D1_BRIDGE_TOKEN",
    "POSTGRES_RO_USER", "POSTGRES_RO_PASSWORD",
    "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME", "R2_ENDPOINT_URL",
    "AI_WORKER_URL", "AI_SERVICE_TOKEN",
)


class Blocked(Exception):
    """A prerequisite is missing; nothing was started."""


def _report(message):
    print(f"[run_isolated_checks] {message}", file=sys.stderr)


def _docker(*args):
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt


def _live_ai_settings(allowed):
    """Return (endpoint, token) only with the flag, the opt-in and both secrets present."""
    if not allowed:
        return None
    endpoint = os.environ.get("ASSISTANT_EVAL_WORKER_URL")
    token = os.environ.get("AI_SERVICE_TOKEN")
    if os.environ.get("RUN_ASSISTANT_LLM_EVAL") != "1" or not endpoint or not token:
        raise Blocked(
            "--allow-live-ai needs RUN_ASSISTANT_LLM_EVAL=1, ASSISTANT_EVAL_WORKER_URL and AI_SERVICE_TOKEN"
        )
    return endpoint, token


def _base_env(live_ai):
    # Empty, not popped: settings.py's load_dotenv would otherwise restore values from .env.
    env = {key: value for key, value in os.environ.items() if not key.startswith(("PG", "CLOUDFLARE_"))}
    env.update(dict.fromkeys(CLOUD_KEYS, ""))
    env.update(
        DJANGO_SETTINGS_MODULE="assethub_api.settings",
        USE_D1_DATABASE="False",
        RUN_ASSISTANT_LLM_EVAL="0",
        ASSISTANT_EVAL_WORKER_URL="",
        ASSETHUB_WEEK5_LIVE_AI_ALLOWED="0",
        ASSETHUB_WEEK5_PG_RUN_ID="",
    )
    if live_ai:
        endpoint, token = live_ai
        env.update(
            RUN_ASSISTANT_LLM_EVAL="1",
            ASSISTANT_EVAL_WORKER_URL=endpoint,
            ASSETHUB_WEEK5_LIVE_AI_ALLOWED="1",
            AI_WORKER_URL=endpoint,
            AI_SERVICE_TOKEN=token,
        )
    return env


def _run_child(child_args, env):
    process = subprocess.Popen([sys.executable, *child_args], cwd=REPO_ROOT, env=env)
    try:
        return process.wait()
    except BaseException:
        process.terminate()
        try:
            process.wait(timeout=CHILD_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise


def _run_without_database(child_args, live_ai):
    with tempfile.TemporaryDirectory(prefix="assethub_w5_") as directory:
        env = _base_env(live_ai)
        env.update(
            DB_HOST=str(Path(directory) / "no-such-socket-dir"),
            DB_PORT="",
            DB_NAME="assethub_w5_no_database",
            DB_USER="assethub_w5_disabled",
            DB_PASSWORD="",
            TEST_DB_NAME="test_assethub_w5_no_database",
        )
        return _run_child(child_args, env)


def _require_prerequisites():
    if not shutil.which("docker"):
        raise Blocked("Docker binary not found")
    image = _docker("image", "inspect", DEFAULT_IMAGE)
    if image.returncode != 0:
        raise Blocked(f"Image {DEFAULT_IMAGE} or Docker daemon unavailable, not pulling: {image.stderr.strip()}")


def _existing_volumes():
    volumes = _docker("volume", "ls", "-q")
    if volumes.returncode != 0:
        raise Blocked(f"Cannot list Docker volumes: {volumes.stderr.strip()}")
    return set(volumes.stdout.split())


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        return probe.getsockname()[1]


def _inspect_container(name):
    inspected = _docker("inspect", "--type", "container", name)
    if inspected.returncode != 0:
        return None
    return json.loads(inspected.stdout)[0]


def _labels_match(info, harness):
    labels = info["Config"]["Labels"] or {}
    return labels.get("assethub.run_id") == harness.run_id and labels.get("assethub.owner") == "rehearsal"


def _verify_ownership(harness):
    info = _inspect_container(harness.container_name)
    if info is None:
        raise RuntimeError(f"Container {harness.container_name} not found after start")
    failed = [
        check for check, passed in (
            ("name", info["Name"] == f"/{harness.container_name}"),
            ("labels", _labels_match(info, harness)),
            ("running", info["State"]["Running"] is True),
            ("port mapping", info["NetworkSettings"]["Ports"].get("5432/tcp")
             == [{"HostIp": HOST, "HostPort": str(harness.port)}]),
        ) if not passed
    ]
    if failed:
        raise RuntimeError(f"Container {harness.container_name} ownership check failed: {', '.join(failed)}")


def _postgres_env(harness, live_ai):
    env = _base_env(live_ai)
    env.update(
        DB_HOST=harness.host,
        DB_PORT=str(harness.port),
        DB_NAME=harness.dbname,
        DB_USER=harness.admin_user,
        DB_PASSWORD=harness.admin_password,
        TEST_DB_NAME=f"test_assethub_w5_{harness.run_id}",
        ASSETHUB_WEEK5_PG_RUN_ID=harness.run_id,
    )
    return env


def _container_absent(name):
    listed = _docker("ps", "-a", "-q", "--filter", f"name=^/{name}$")
    return listed.returncode == 0 and not listed.stdout.strip()


def _owned_volumes(harness, preexisting):
    """Return (volumes proven created for this container, whether every mount was proven)."""
    info = _inspect_container(harness.container_name)
    if info is None:
        return [], _container_absent(harness.container_name)
    if not _labels_match(info, harness):
        return [], False
    owned = []
    proven = True
    for mount in info.get("Mounts") or []:
        name = mount.get("Name", "")
        if mount.get("Type") == "volume" and ANONYMOUS_VOLUME.fullmatch(name) and name not in preexisting:
            owned.append(name)
        else:
            _report(f"Keeping unproven {mount.get('Type')} mount at {mount.get('Destination')}")
            proven = False
    return owned, proven


def _remove_volume(name):
    users = _docker("ps", "-a", "-q", "--filter", f"volume={name}")
    if users.returncode != 0 or users.stdout.strip():
        _report(f"Keeping volume {name}: possibly still referenced")
        return False
    if _docker("volume", "rm", name).returncode != 0:
        _report(f"Could not remove volume {name}")
        return False
    return True


def _cleanup(harness, preexisting):
    try:
        volumes, proven = _owned_volumes(harness, preexisting)
        # teardown() also returns True when docker inspect merely failed, so absence is checked separately.
        if not harness.teardown() or not _container_absent(harness.container_name):
            _report(f"Container {harness.container_name} was not confirmed removed")
            return False
        results = [_remove_volume(name) for name in volumes]
        return proven and all(results)
    except (Exception, KeyboardInterrupt) as error:
        _report(f"Cleanup stopped early: {type(error).__name__}")
        return False


def _run_with_postgres(child_args, live_ai):
    _require_prerequisites()
    preexisting = _existing_volumes()
    run_id = f"w5_{secrets.token_hex(6)}"
    harness = PGSourceHarness(run_id=run_id, host=HOST, port=_free_port(), dbname=f"assethub_w5_{run_id}")
    exit_code = EXIT_BLOCKED
    cleaned = False
    try:
        harness.start(recreate=False)
        _verify_ownership(harness)
        exit_code = _run_child(child_args, _postgres_env(harness, live_ai))
    except KeyboardInterrupt:
        exit_code = EXIT_INTERRUPTED
    except Exception as error:
        _report(f"BLOCKED: {str(error).replace(harness.admin_password, '******')}")
    finally:
        cleaned = _cleanup(harness, preexisting)
    if not cleaned:
        _report(f"CLEANUP INCOMPLETE for run {run_id}; inspect container {harness.container_name} and its volumes")
        return exit_code or EXIT_CLEANUP_FAILED
    return exit_code


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database", choices=("postgres", "none"), default="postgres")
    parser.add_argument("--allow-live-ai", action="store_true")
    # Split by hand so argparse never interprets the child's own flags.
    own, separator, child_args = argv, False, []
    if "--" in argv:
        index = argv.index("--")
        own, separator, child_args = argv[:index], True, argv[index + 1:]
    args = parser.parse_args(own)
    if not separator or not child_args:
        parser.error("child Python arguments are required after --")
    return args, child_args


def main(argv: list[str] | None = None) -> int:
    args, child_args = _parse_args(sys.argv[1:] if argv is None else list(argv))
    # SIGTERM would otherwise kill the runner without its finally, leaking the container.
    previous_handler = signal.signal(signal.SIGTERM, _raise_interrupt)
    try:
        live_ai = _live_ai_settings(args.allow_live_ai)
        if args.database == "none":
            return _run_without_database(child_args, live_ai)
        return _run_with_postgres(child_args, live_ai)
    except Blocked as error:
        _report(f"BLOCKED: {error}")
        return EXIT_BLOCKED
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


if __name__ == "__main__":
    sys.exit(main())
