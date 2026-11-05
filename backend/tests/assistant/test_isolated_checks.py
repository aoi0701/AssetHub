"""Safety tests for the Week 5 isolated runner; Docker, harness and child are mocked."""
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from dotenv import load_dotenv

from tests.assistant import run_isolated_checks as runner

ANON_A = "a" * 64
ANON_B = "b" * 64
ANON_MOUNT = {"Type": "volume", "Name": ANON_A, "Destination": "/var/lib/postgresql/data"}
CLOUD_KEYS = (
    "D1_BRIDGE_URL", "D1_BRIDGE_SECRET_TOKEN", "D1_BRIDGE_TOKEN",
    "POSTGRES_RO_USER", "POSTGRES_RO_PASSWORD",
    "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET_NAME", "R2_ENDPOINT_URL",
    "AI_WORKER_URL", "AI_SERVICE_TOKEN",
)


def _result(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class FakeDocker:
    """Answers only the commands the runner may issue; anything else fails the test."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.calls = []
        self.container = None
        self.volumes = []
        self.referenced = set()
        self.removed = []
        self.rm_ok = True
        self.image_ok = True

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("image", "inspect"):
            return _result(0 if self.image_ok else 1, stderr="" if self.image_ok else "No such image")
        if args == ("volume", "ls", "-q"):
            return _result(stdout="\n".join(self.volumes))
        if args[:3] == ("inspect", "--type", "container"):
            return _result(stdout=json.dumps([self.container])) if self.container else _result(1)
        if args[:4] == ("ps", "-a", "-q", "--filter"):
            key, value = args[4].split("=", 1)
            if key == "name":
                return _result(stdout="cid" if self.container else "")
            return _result(stdout="other" if value in self.referenced else "")
        if args[:2] == ("volume", "rm"):
            if self.rm_ok:
                self.removed.append(args[2])
            return _result(0 if self.rm_ok else 1)
        raise AssertionError(f"Unexpected docker command: {args}")


class FakeHarness:
    def __init__(self, ctx, run_id, host, port, dbname):
        self.ctx = ctx
        self.run_id = run_id
        self.host = host
        self.port = port
        self.dbname = dbname
        self.container_name = f"assethub-pg-reh-{run_id}"
        self.admin_user = f"pgadm_{run_id}"
        self.admin_password = "harness-secret"

    def start(self, recreate):
        assert recreate is False
        self.ctx.events.append("start")
        if self.ctx.start_error:
            raise self.ctx.start_error
        self.ctx.docker.container = self.ctx.container_for(self)
        return True

    def teardown(self):
        self.ctx.events.append(f"teardown:{self.run_id}")
        if self.ctx.teardown_removes:
            self.ctx.docker.container = None
        return self.ctx.teardown_result


class FakePopen:
    def __init__(self, ctx, args, **kwargs):
        ctx.children.append(self)
        self.ctx = ctx
        self.args = args
        self.kwargs = kwargs
        self.env = kwargs["env"]
        self.waits = list(ctx.child_waits)

    def wait(self, timeout=None):
        step = self.waits.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            step()
            raise AssertionError("Signal did not interrupt the wait")
        self.ctx.events.append("child-done")
        return step

    def terminate(self):
        self.ctx.events.append("terminate")

    def kill(self):
        self.ctx.events.append("kill")


@pytest.fixture
def ctx(monkeypatch):
    state = SimpleNamespace(
        events=[], children=[], harnesses=[], child_waits=[0], start_error=None,
        teardown_result=True, teardown_removes=True, mounts=[ANON_MOUNT], mutate=lambda info: None,
    )
    state.docker = FakeDocker(state)

    def container_for(harness):
        info = {
            "Name": f"/{harness.container_name}",
            "Config": {"Labels": {"assethub.run_id": harness.run_id, "assethub.owner": "rehearsal"}},
            "State": {"Running": True},
            "NetworkSettings": {
                "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(harness.port)}]},
            },
            "Mounts": state.mounts,
        }
        state.mutate(info)
        return info

    def build_harness(**kwargs):
        harness = FakeHarness(state, **kwargs)
        state.harnesses.append(harness)
        return harness

    state.container_for = container_for
    monkeypatch.setattr(runner, "_docker", state.docker)
    monkeypatch.setattr(runner, "PGSourceHarness", build_harness)
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(runner.subprocess, "Popen", lambda args, **kw: FakePopen(state, args, **kw))
    return state


def test_runner_injects_only_owned_pg_settings(ctx, monkeypatch):
    for key, value in {"DB_HOST": "db", "DB_PORT": "5433", "DB_NAME": "assethub", "DB_USER": "assethub_user",
                       "DB_PASSWORD": "app-secret", "TEST_DB_NAME": "test_assethub", "USE_D1_DATABASE": "True",
                       "PGHOSTADDR": "10.0.0.1", "DJANGO_SETTINGS_MODULE": "other"}.items():
        monkeypatch.setenv(key, value)

    assert runner.main(["--", "-m", "pytest", "x.py"]) == 0

    harness = ctx.harnesses[0]
    child = ctx.children[0]
    assert re.fullmatch(r"w5_[0-9a-f]+", harness.run_id)
    assert harness.dbname == f"assethub_w5_{harness.run_id}"
    assert harness.host == "127.0.0.1"
    assert child.args == [sys.executable, "-m", "pytest", "x.py"]
    assert "shell" not in child.kwargs
    assert Path(child.kwargs["cwd"]) == runner.REPO_ROOT
    assert (Path(child.kwargs["cwd"]) / "backend" / "manage.py").exists()
    assert {key: child.env[key] for key in (
        "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "TEST_DB_NAME",
        "USE_D1_DATABASE", "DJANGO_SETTINGS_MODULE", "ASSETHUB_WEEK5_PG_RUN_ID",
    )} == {
        "DB_HOST": "127.0.0.1",
        "DB_PORT": str(harness.port),
        "DB_NAME": harness.dbname,
        "DB_USER": harness.admin_user,
        "DB_PASSWORD": harness.admin_password,
        "TEST_DB_NAME": f"test_assethub_w5_{harness.run_id}",
        "USE_D1_DATABASE": "False",
        "DJANGO_SETTINGS_MODULE": "assethub_api.settings",
        "ASSETHUB_WEEK5_PG_RUN_ID": harness.run_id,
    }
    assert "PGHOSTADDR" not in child.env


def _set_label(key, value):
    def mutate(info):
        info["Config"]["Labels"][key] = value
    return mutate


@pytest.mark.parametrize("mutate", [
    _set_label("assethub.run_id", "foreign"),
    _set_label("assethub.owner", "someone-else"),
    lambda info: info.update(Name="/assethub-db-1"),
    lambda info: info["State"].update(Running=False),
    lambda info: info["NetworkSettings"].update(Ports={"5432/tcp": [{"HostIp": "0.0.0.0", "HostPort": "1"}]}),
    lambda info: info["NetworkSettings"].update(Ports={}),
], ids=["foreign-run", "foreign-owner", "foreign-name", "not-running", "wrong-mapping", "no-mapping"])
def test_busy_port_or_foreign_labels_prevent_child(ctx, mutate):
    ctx.mutate = mutate
    ctx.teardown_removes = False
    ctx.teardown_result = False

    assert runner.main(["--", "-c", "pass"]) == runner.EXIT_BLOCKED

    assert ctx.children == []
    assert ctx.docker.removed == []


def test_busy_port_stops_without_retry_or_touching_listener(ctx):
    ctx.start_error = RuntimeError("Port 40000 on 127.0.0.1 is already in use by an external process.")

    assert runner.main(["--", "-c", "pass"]) == runner.EXIT_BLOCKED

    assert ctx.children == []
    assert len(ctx.harnesses) == 1
    assert ctx.events == ["start", f"teardown:{ctx.harnesses[0].run_id}"]


def test_start_error_never_prints_credentials(ctx, capsys):
    ctx.start_error = RuntimeError("docker said harness-secret")

    assert runner.main(["--", "-c", "pass"]) == runner.EXIT_BLOCKED

    assert "harness-secret" not in "".join(capsys.readouterr())


def test_child_failure_and_interrupt_teardown_owned_harness(ctx):
    ctx.child_waits = [1]
    assert runner.main(["--", "-c", "pass"]) == 1
    assert ctx.events[-1] == f"teardown:{ctx.harnesses[0].run_id}"
    assert ctx.docker.removed == [ANON_A]

    ctx.events.clear()
    ctx.docker.volumes = []
    ctx.child_waits = [KeyboardInterrupt(), -2]
    assert runner.main(["--", "-c", "pass"]) == runner.EXIT_INTERRUPTED
    assert ctx.events == ["start", "terminate", "child-done", f"teardown:{ctx.harnesses[1].run_id}"]


def test_sigterm_stops_child_and_tears_down(ctx):
    previous = signal.getsignal(signal.SIGTERM)
    ctx.child_waits = [lambda: os.kill(os.getpid(), signal.SIGTERM), -15]

    assert runner.main(["--", "-c", "pass"]) == runner.EXIT_INTERRUPTED

    assert ctx.events[-3:] == ["terminate", "child-done", f"teardown:{ctx.harnesses[0].run_id}"]
    assert signal.getsignal(signal.SIGTERM) is previous


def test_child_is_killed_when_it_ignores_terminate(ctx):
    ctx.child_waits = [KeyboardInterrupt(), subprocess.TimeoutExpired("child", 1), -9]

    assert runner.main(["--", "-c", "pass"]) == runner.EXIT_INTERRUPTED

    assert ctx.events[1:] == ["terminate", "kill", "child-done", f"teardown:{ctx.harnesses[0].run_id}"]


@pytest.mark.parametrize("mounts, preexisting, referenced, rm_ok, removed, exit_code", [
    ([ANON_MOUNT], [], set(), True, [ANON_A], 0),
    ([], [], set(), True, [], 0),
    ([ANON_MOUNT], [ANON_A], set(), True, [], runner.EXIT_CLEANUP_FAILED),
    ([{"Type": "volume", "Name": "assethub_pgdata", "Destination": "/d"}], [], set(), True, [],
     runner.EXIT_CLEANUP_FAILED),
    ([{"Type": "bind", "Source": "/srv/pg", "Destination": "/d"}], [], set(), True, [], runner.EXIT_CLEANUP_FAILED),
    ([ANON_MOUNT], [], {ANON_A}, True, [], runner.EXIT_CLEANUP_FAILED),
    ([ANON_MOUNT], [], set(), False, [], runner.EXIT_CLEANUP_FAILED),
    ([ANON_MOUNT, {"Type": "volume", "Name": ANON_B, "Destination": "/e"}], [ANON_B], set(), True, [ANON_A],
     runner.EXIT_CLEANUP_FAILED),
], ids=["owned", "no-volume", "preexisting", "named", "bind", "still-referenced", "rm-fails", "mixed"])
def test_volume_cleanup_requires_run_provenance(ctx, mounts, preexisting, referenced, rm_ok, removed, exit_code):
    ctx.mounts = mounts
    ctx.docker.volumes = preexisting
    ctx.docker.referenced = referenced
    ctx.docker.rm_ok = rm_ok

    assert runner.main(["--", "-c", "pass"]) == exit_code

    assert ctx.docker.removed == removed


@pytest.mark.parametrize("teardown_result, teardown_removes", [(False, False), (True, False)],
                         ids=["teardown-refused", "container-still-present"])
def test_unconfirmed_teardown_fails_and_keeps_volumes(ctx, teardown_result, teardown_removes):
    ctx.teardown_result = teardown_result
    ctx.teardown_removes = teardown_removes

    assert runner.main(["--", "-c", "pass"]) == runner.EXIT_CLEANUP_FAILED

    assert ctx.docker.removed == []


def test_cleanup_failure_keeps_child_failure_exit_code(ctx):
    ctx.child_waits = [7]
    ctx.teardown_result = False
    ctx.teardown_removes = False

    assert runner.main(["--", "-c", "pass"]) == 7


def test_teardown_exception_is_reported_as_cleanup_failure(ctx, monkeypatch):
    def explode(self):
        raise OSError("docker gone")

    monkeypatch.setattr(FakeHarness, "teardown", explode)

    assert runner.main(["--", "-c", "pass"]) == runner.EXIT_CLEANUP_FAILED


def test_missing_image_does_not_pull_or_fallback(ctx):
    ctx.docker.image_ok = False

    assert runner.main(["--", "-c", "pass"]) == runner.EXIT_BLOCKED

    assert ctx.harnesses == []
    assert ctx.children == []
    assert ctx.docker.calls == [("image", "inspect", "postgres:16-alpine")]


def test_missing_docker_binary_blocks(ctx, monkeypatch):
    monkeypatch.setattr(runner.shutil, "which", lambda name: None)

    assert runner.main(["--", "-c", "pass"]) == runner.EXIT_BLOCKED

    assert ctx.docker.calls == []
    assert ctx.harnesses == []


def test_image_lost_after_preflight_never_pulls(monkeypatch):
    """Real harness, mocked Docker: preflight sees the image, then docker run no longer does."""
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[1:3] == ["image", "inspect"]:
            return _result()
        if command[1:3] == ["volume", "ls"] or command[1] == "ps":
            return _result(stdout="")
        if command[1] == "run":
            return _result(125, stderr="Unable to find image 'postgres:16-alpine' locally")
        return _result(1)  # docker inspect: no such container

    popen_calls = []
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *a, **kw: popen_calls.append(a))
    monkeypatch.setattr(runner.shutil, "which", lambda name: f"/usr/bin/{name}")

    assert runner.main(["--", "-c", "pass"]) == runner.EXIT_BLOCKED

    assert popen_calls == []
    assert not [command for command in commands if "pull" in command]
    run_command = next(command for command in commands if command[1] == "run")
    assert "--pull=never" in run_command
    assert run_command.index("--pull=never") < run_command.index("postgres:16-alpine")


def test_chosen_port_is_never_a_week4_port(ctx):
    assert runner.main(["--", "-c", "pass"]) == 0

    assert ctx.harnesses[0].port not in (5432, 5433, 5434)


def _set_hostile_cloud_env(monkeypatch):
    for key in CLOUD_KEYS:
        monkeypatch.setenv(key, f"real-{key}")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "real-cf-token")
    monkeypatch.setenv("ASSISTANT_EVAL_WORKER_URL", "https://eval.example")


def test_default_mode_clears_cloud_and_none_mode_has_no_pg(ctx, monkeypatch, tmp_path):
    _set_hostile_cloud_env(monkeypatch)
    monkeypatch.setenv("DB_HOST", "db")
    monkeypatch.setenv("DB_PASSWORD", "app-secret")
    monkeypatch.setenv("RUN_ASSISTANT_LLM_EVAL", "1")  # opt-in alone must not enable live AI

    assert runner.main(["--", "-c", "pass"]) == 0
    assert runner.main(["--database", "none", "--", "-c", "pass"]) == 0

    assert len(ctx.harnesses) == 1, "none mode must not build a harness"
    pg_env, none_env = ctx.children[0].env, ctx.children[1].env
    for env in (pg_env, none_env):
        assert {key: env[key] for key in CLOUD_KEYS} == dict.fromkeys(CLOUD_KEYS, "")
        assert env["USE_D1_DATABASE"] == "False"
        assert env["RUN_ASSISTANT_LLM_EVAL"] == "0"
        assert env["ASSISTANT_EVAL_WORKER_URL"] == ""
        assert env["ASSETHUB_WEEK5_LIVE_AI_ALLOWED"] == "0"
        assert "CLOUDFLARE_API_TOKEN" not in env
    assert Path(none_env["DB_HOST"]).is_absolute()
    assert not Path(none_env["DB_HOST"]).exists()
    assert none_env["DB_PORT"] == ""
    assert none_env["DB_PASSWORD"] == ""
    assert none_env["DB_USER"] != "assethub_user"
    assert none_env["ASSETHUB_WEEK5_PG_RUN_ID"] == ""

    # Empty values must survive settings.py's load_dotenv, which would otherwise restore secrets.
    dotenv_file = tmp_path / ".env"
    dotenv_file.write_text("R2_ACCESS_KEY_ID=leak\nAI_SERVICE_TOKEN=leak\nDB_HOST=db\nDB_PASSWORD=leak\n")
    with pytest.MonkeyPatch.context() as env_patch:
        for key in list(os.environ):
            env_patch.delenv(key)
        for key, value in none_env.items():
            env_patch.setenv(key, value)
        load_dotenv(dotenv_file)
        assert os.environ["R2_ACCESS_KEY_ID"] == ""
        assert os.environ["AI_SERVICE_TOKEN"] == ""
        assert os.environ["DB_HOST"] == none_env["DB_HOST"]
        assert os.environ["DB_PASSWORD"] == ""


@pytest.mark.parametrize("env", [
    {},
    {"RUN_ASSISTANT_LLM_EVAL": "1"},
    {"RUN_ASSISTANT_LLM_EVAL": "1", "ASSISTANT_EVAL_WORKER_URL": "https://eval.example"},
    {"RUN_ASSISTANT_LLM_EVAL": "1", "AI_SERVICE_TOKEN": "token"},
    {"ASSISTANT_EVAL_WORKER_URL": "https://eval.example", "AI_SERVICE_TOKEN": "token"},
], ids=["nothing", "opt-in-only", "no-token", "no-endpoint", "no-opt-in"])
def test_allow_live_ai_without_all_inputs_blocks_before_start(ctx, monkeypatch, env):
    for key in ("RUN_ASSISTANT_LLM_EVAL", "ASSISTANT_EVAL_WORKER_URL", "AI_SERVICE_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    assert runner.main(["--allow-live-ai", "--", "-c", "pass"]) == runner.EXIT_BLOCKED

    assert ctx.harnesses == []
    assert ctx.children == []


def test_allow_live_ai_with_all_inputs_keeps_only_ai_settings(ctx, monkeypatch):
    _set_hostile_cloud_env(monkeypatch)
    monkeypatch.setenv("RUN_ASSISTANT_LLM_EVAL", "1")
    monkeypatch.setenv("AI_SERVICE_TOKEN", "service-token")

    assert runner.main(["--allow-live-ai", "--", "-c", "pass"]) == 0

    env = ctx.children[0].env
    assert env["ASSETHUB_WEEK5_LIVE_AI_ALLOWED"] == "1"
    assert env["RUN_ASSISTANT_LLM_EVAL"] == "1"
    assert env["AI_WORKER_URL"] == "https://eval.example"
    assert env["ASSISTANT_EVAL_WORKER_URL"] == "https://eval.example"
    assert env["AI_SERVICE_TOKEN"] == "service-token"
    assert env["USE_D1_DATABASE"] == "False"
    for key in set(CLOUD_KEYS) - {"AI_WORKER_URL", "AI_SERVICE_TOKEN"}:
        assert env[key] == ""


@pytest.mark.parametrize("argv", [[], ["--"], ["-c", "pass"], ["--database", "sqlite", "--", "-c", "pass"]],
                         ids=["empty", "no-child", "no-separator", "bad-database"])
def test_invalid_arguments_exit_without_side_effects(ctx, argv):
    with pytest.raises(SystemExit) as exit_info:
        runner.main(argv)

    assert exit_info.value.code == 2
    assert ctx.docker.calls == []
    assert ctx.harnesses == []


def test_runner_import_does_not_load_django():
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('r', {str(Path(runner.__file__))!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "sys.exit(1 if 'django' in sys.modules else 0)\n"
    )

    assert subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0
