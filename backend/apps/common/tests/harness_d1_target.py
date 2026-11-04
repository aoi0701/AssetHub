"""
Harness for Local Cloudflare D1 and Worker Bridge Rehearsal.

Manages the lifecycle of a dedicated, isolated local Cloudflare D1 SQLite database
and Worker Bridge running on 127.0.0.1:8787 for zero-cloud migration testing.

Isolation Rules:
- NEVER deploy to Cloudflare.
- NEVER use --remote flag.
- NEVER reset cloud D1 resources.
- Strict token authentication and local file-backed persistence.
- Strict process ownership: Only manage the process spawned by this instance.
- Port collision guard: Refuse to start and never kill external processes.
- Run-scoped directory and marker ownership: Only clean directories with matching marker.
"""

import atexit
import datetime
import glob
import json
import logging
import os
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Rehearsal Configuration Constants
DEFAULT_PORT = 8787
DEFAULT_HOST = "127.0.0.1"


def generate_local_token() -> str:
    """Generate a cryptographically secure random token for local Worker Bridge auth."""
    return secrets.token_urlsafe(32)


# Local random token generated per session - never reuse cloud token from D1_BRIDGE_TOKEN
DEFAULT_TOKEN = generate_local_token()

DEFAULT_DB_NAME = "assethub-test-d1"
DEFAULT_BRIDGE_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
DEFAULT_QUERY_URL = f"{DEFAULT_BRIDGE_URL}/api/v1/query"

# Backward compatibility defaults
DEFAULT_PERSIST_DIR = Path("/tmp/assethub_rehearsal_d1_default")
DEFAULT_ENV_FILE = Path("/tmp/assethub_rehearsal_d1_default.env")
DEFAULT_PID_FILE = Path("/tmp/assethub_rehearsal_d1_default.pid")

# Target tables to verify: 16 source mandatory tables + ai_suggestion_logs (17 total)
TARGET_TABLES: List[str] = [
    "django_content_type",
    "auth_permission",
    "auth_group",
    "departments_department",
    "departments_category",
    "auth_group_permissions",
    "authentication_user",
    "authentication_user_groups",
    "authentication_user_user_permissions",
    "django_admin_log",
    "django_migrations",
    "django_session",
    "assets_identifiersequence",
    "assets_asset",
    "requests_assetrequest",
    "assets_assethistorylog",
    "ai_suggestion_logs",
]

# Track active harness instances for clean atexit teardown
_active_harnesses: List["D1TargetHarness"] = []


def get_repo_root() -> Path:
    """Resolve project repository root from module location."""
    return Path(__file__).resolve().parents[4]


def assert_safe_environment(cmd_args: List[str]) -> None:
    """Strict guard: Block any command with --remote or deploy."""
    for arg in cmd_args:
        lower = arg.lower()
        if "--remote" in lower or "-r" == lower:
            raise RuntimeError("CRITICAL ISOLATION GUARD: --remote is strictly forbidden in local rehearsal!")
        if "deploy" in lower:
            raise RuntimeError("CRITICAL ISOLATION GUARD: wrangler deploy is strictly forbidden in local rehearsal!")


def check_port_in_use(host_or_port: Any = DEFAULT_HOST, port: Optional[int] = None, host: Optional[str] = None) -> bool:
    """Check if a TCP port is currently open and accepting connections."""
    if host is not None:
        target_host = host
        target_port = port if port is not None else DEFAULT_PORT
    elif isinstance(host_or_port, int):
        target_host = DEFAULT_HOST
        target_port = host_or_port
    else:
        target_host = str(host_or_port)
        target_port = port if port is not None else DEFAULT_PORT
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((target_host, target_port)) == 0


def assert_port_available(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """
    Verify the target port is available.
    If the port is already in use: FAIL IMMEDIATELY with RuntimeError.
    Strictly forbidden to kill external processes.
    """
    if check_port_in_use(host=host, port=port):
        raise RuntimeError(
            f"Port {port} on {host} is already in use by another process. Refusing to terminate external process."
        )


def write_run_marker(persist_dir: Path, run_id: str, pid: Optional[int] = None) -> Path:
    """Write run_marker.json inside persist_dir recording run_id, pid, and creation timestamp."""
    target_dir = Path(persist_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    marker_path = target_dir / "run_marker.json"
    marker_data = {
        "run_id": run_id,
        "pid": pid,
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    marker_path.write_text(json.dumps(marker_data, indent=2), encoding="utf-8")
    return marker_path


def cleanup_persist_directory(
    persist_dir: Optional[Path],
    run_id: Optional[str] = None,
    raise_on_error: bool = False,
) -> bool:
    """
    Safely delete rehearsal directory only if:
    1. Directory exists and is within /tmp
    2. Name matches /tmp/assethub_rehearsal_d1_* or /tmp/wrangler_d1_*
    3. Contains run_marker.json with matching run_id
    Never deletes arbitrary paths.
    """
    if persist_dir is None:
        return False
    path = Path(persist_dir).resolve()
    if not path.exists():
        return False

    def _refuse(reason: str) -> bool:
        logger.warning("Refusing to delete directory '%s': %s", path, reason)
        if raise_on_error:
            raise RuntimeError(f"Refusing to delete directory '{path}': {reason}")
        return False

    # Guard 1: Must be inside /tmp
    tmp_path = Path("/tmp").resolve()
    try:
        path.relative_to(tmp_path)
    except ValueError:
        return _refuse("Path is not inside /tmp")

    if path == tmp_path:
        return _refuse("Path is the root /tmp directory itself")

    # Guard 2: Name pattern match
    name = path.name
    if not (name.startswith("assethub_rehearsal_d1_") or name.startswith("wrangler_d1_")):
        return _refuse(f"Directory name '{name}' does not match run directory pattern")

    # Guard 3: run_id must be provided
    if not run_id:
        return _refuse("No run_id provided for marker verification")

    # Guard 4: run_marker.json must exist
    marker_path = path / "run_marker.json"
    if not marker_path.is_file():
        return _refuse(f"Missing run_marker.json in {path}")

    # Guard 5: run_id matches marker contents
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _refuse("run_marker.json does not contain a JSON object")
        if data.get("run_id") != run_id:
            return _refuse(
                f"Marker run_id '{data.get('run_id')}' does not match expected run_id '{run_id}'"
            )
    except Exception as exc:
        return _refuse(f"Failed to read or parse run_marker.json: {exc}")

    # All checks passed: safely remove directory
    shutil.rmtree(path, ignore_errors=True)
    return True


def cleanup_env_file(env_file: Optional[Path]) -> bool:
    """Safely unlink temporary rehearsal env file under /tmp."""
    if env_file is None:
        return False
    path = Path(env_file).resolve()
    if not path.is_file():
        return False

    tmp_path = Path("/tmp").resolve()
    try:
        path.relative_to(tmp_path)
    except ValueError:
        return False

    if path == tmp_path:
        return False

    name = path.name
    if not (name.startswith("assethub_rehearsal_d1_") or name.startswith("wrangler")):
        return False

    try:
        path.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def terminate_process(proc: Optional[subprocess.Popen], timeout: float = 5.0) -> None:
    """
    Safely terminate only the given subprocess and its process group.
    Sends SIGTERM to process group, waits up to timeout (5s), sends SIGKILL if still running.
    Does nothing if proc is None or already terminated.
    """
    if proc is None:
        return
    if proc.poll() is not None:
        return

    # 1. Send SIGTERM to process group
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass

    # 2. Wait up to timeout
    try:
        proc.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass

    # 3. If still running, send SIGKILL to process group
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass

    try:
        proc.wait(timeout=2.0)
    except Exception:
        pass


def create_env_file(env_file: Path = DEFAULT_ENV_FILE, token: Optional[str] = None) -> Path:
    """Generate temporary environment file containing BRIDGE_SECRET_TOKEN with 0600 file permissions."""
    token_val = (token or DEFAULT_TOKEN).strip()
    if len(token_val) < 32:
        raise ValueError("BRIDGE_SECRET_TOKEN must be at least 32 characters long.")
    target_path = Path(env_file).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    flags = os.O_CREAT | os.O_WRONLY | os.O_TRUNC
    fd = os.open(str(target_path), flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with open(fd, "w", encoding="utf-8") as f:
            f.write(f"BRIDGE_SECRET_TOKEN={token_val}\n")
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return target_path


def ingest_d1_ddl(
    persist_dir: Optional[Path] = None,
    bridge_worker_dir: Optional[Path] = None,
    schema_file: Optional[Path] = None,
    db_name: str = DEFAULT_DB_NAME,
    port: int = DEFAULT_PORT,
) -> subprocess.CompletedProcess:
    """
    Ingests spikes/cf-d1-spike/schema.sql into local D1 via:
    cd spikes/cf-d1-spike/bridge-worker && npx wrangler d1 execute assethub-test-d1 --local --file=../schema.sql --persist-to=<persist_dir> -y
    """
    repo_root = get_repo_root()
    if bridge_worker_dir is None:
        bridge_worker_dir = repo_root / "spikes" / "cf-d1-spike" / "bridge-worker"
    if schema_file is None:
        schema_file = repo_root / "spikes" / "cf-d1-spike" / "schema.sql"

    persist_dir_path = Path(persist_dir or DEFAULT_PERSIST_DIR).resolve()
    persist_dir_path.mkdir(parents=True, exist_ok=True)

    if not bridge_worker_dir.exists():
        raise FileNotFoundError(f"Bridge worker directory not found: {bridge_worker_dir}")
    if not schema_file.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_file}")

    cmd = [
        "npx",
        "wrangler",
        "d1",
        "execute",
        db_name,
        "--local",
        f"--file={schema_file.resolve()}",
        f"--persist-to={persist_dir_path}",
        "-y",
    ]
    assert_safe_environment(cmd)

    result = subprocess.run(
        cmd,
        cwd=str(bridge_worker_dir),
        capture_output=True,
        text=True,
        check=True,
    )
    return result


# Alias for compatibility
init_d1_ddl = ingest_d1_ddl


def get_d1_sqlite_file(persist_dir: Optional[Path] = None) -> Path:
    """Locate the miniflare D1 SQLite database file."""
    persist_dir_path = Path(persist_dir or DEFAULT_PERSIST_DIR).resolve()
    pattern = persist_dir_path / "v3" / "d1" / "miniflare-D1DatabaseObject" / "*.sqlite"
    matches = [Path(p) for p in glob.glob(str(pattern)) if not p.endswith("metadata.sqlite")]
    if not matches:
        raise FileNotFoundError(f"No D1 SQLite database found in persist directory pattern: {pattern}")
    return matches[0]


def query_bridge_http(
    sql: str,
    params: Optional[List[Any]] = None,
    url: str = DEFAULT_QUERY_URL,
    token: Optional[str] = None,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Execute SQL query over Worker Bridge HTTP API."""
    if params is None:
        params = []
    token_val = (token or DEFAULT_TOKEN).strip()
    payload = json.dumps({"sql": sql, "params": params}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Bridge-Token": token_val,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_bridge_health(
    url: str = DEFAULT_QUERY_URL,
    token: Optional[str] = None,
    timeout: float = 2.0,
) -> bool:
    """Poll Worker Bridge with 'SELECT 1' health check query."""
    try:
        res = query_bridge_http("SELECT 1;", params=[], url=url, token=token, timeout=timeout)
        return bool(res.get("success") is True)
    except Exception:
        return False


def wait_for_bridge_ready(
    proc: Optional[subprocess.Popen] = None,
    url: str = DEFAULT_QUERY_URL,
    token: Optional[str] = None,
    timeout: float = 15.0,
) -> bool:
    """Wait up to timeout seconds for bridge to respond to SELECT 1."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        if proc is not None and proc.poll() is not None:
            _, err = proc.communicate()
            raise RuntimeError(f"wrangler dev process exited prematurely with code {proc.returncode}: {err}")
        if check_bridge_health(url=url, token=token, timeout=1.5):
            return True
        time.sleep(0.3)
    return False


def verify_d1_target_clean_sqlite(persist_dir: Optional[Path] = None) -> Dict[str, int]:
    """Inspect local SQLite database directly to verify table existence and zero rows."""
    db_file = get_d1_sqlite_file(persist_dir)
    conn = sqlite3.connect(str(db_file))
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '_cf_%' AND name NOT LIKE 'sqlite_%';")
        tables = {row[0] for row in cursor.fetchall()}

        missing = [t for t in TARGET_TABLES if t not in tables]
        if missing:
            raise AssertionError(f"Missing required target tables in local D1 SQLite: {missing}")

        if "ai_suggestion_logs" not in tables:
            raise AssertionError("ai_suggestion_logs table missing from local D1 SQLite")

        row_counts: Dict[str, int] = {}
        non_empty: Dict[str, int] = {}
        for t in TARGET_TABLES:
            cursor.execute(f'SELECT count(*) FROM "{t}";')
            count = cursor.fetchone()[0]
            row_counts[t] = count
            if count != 0:
                non_empty[t] = count

        if non_empty:
            raise AssertionError(f"Destination tables are non-empty: {non_empty}")

        return row_counts
    finally:
        conn.close()


def verify_d1_target_clean_bridge(
    url: str = DEFAULT_QUERY_URL,
    token: Optional[str] = None,
) -> Dict[str, int]:
    """Inspect local D1 via Worker Bridge HTTP query to verify table existence and zero rows."""
    token_val = token or DEFAULT_TOKEN
    res = query_bridge_http(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '_cf_%' AND name NOT LIKE 'sqlite_%';",
        url=url,
        token=token_val,
    )
    if not res.get("success"):
        raise RuntimeError(f"Failed to query sqlite_master over bridge: {res}")

    raw_results = res.get("results", [])
    tables = {r.get("name") for r in raw_results if isinstance(r, dict)}

    missing = [t for t in TARGET_TABLES if t not in tables]
    if missing:
        raise AssertionError(f"Missing required target tables via bridge: {missing}")

    if "ai_suggestion_logs" not in tables:
        raise AssertionError("ai_suggestion_logs table missing from local D1 via bridge")

    row_counts: Dict[str, int] = {}
    non_empty: Dict[str, int] = {}
    for t in TARGET_TABLES:
        c_res = query_bridge_http(f'SELECT count(*) as cnt FROM "{t}";', url=url, token=token_val)
        if not c_res.get("success"):
            raise RuntimeError(f"Failed to query row count for table '{t}': {c_res}")
        items = c_res.get("results", [])
        if not items:
            count = 0
        else:
            first_val = list(items[0].values())[0]
            count = int(first_val)
        row_counts[t] = count
        if count != 0:
            non_empty[t] = count

    if non_empty:
        raise AssertionError(f"Destination tables are non-empty via bridge: {non_empty}")

    return row_counts


def verify_d1_target_clean(
    url: str = DEFAULT_QUERY_URL,
    token: Optional[str] = None,
    persist_dir: Optional[Path] = None,
) -> Dict[str, int]:
    """Verify target tables are clean (0 rows) and all exist, preferring bridge when active."""
    token_val = token or DEFAULT_TOKEN
    if check_bridge_health(url=url, token=token_val, timeout=1.0):
        return verify_d1_target_clean_bridge(url=url, token=token_val)
    return verify_d1_target_clean_sqlite(persist_dir=persist_dir)


def start_d1_bridge(
    port: int = DEFAULT_PORT,
    host: str = DEFAULT_HOST,
    persist_dir: Optional[Path] = None,
    env_file: Optional[Path] = None,
    token: Optional[str] = None,
    bridge_worker_dir: Optional[Path] = None,
    timeout: float = 15.0,
    run_id: Optional[str] = None,
) -> subprocess.Popen:
    """
    Launch npx wrangler dev on host:port with:
    --ip <host> --port <port> --persist-to <persist_dir> --env-file <env_file>
    Tests connection via socket.connect_ex first: Fails immediately if port is in use.
    Spawns process via start_new_session=True.
    Polls health check with SELECT 1 until ready.
    """
    assert_port_available(host=host, port=port)

    persist_dir_path = Path(persist_dir or DEFAULT_PERSIST_DIR).resolve()
    env_file_path = Path(env_file or DEFAULT_ENV_FILE).resolve()
    token_val = token or DEFAULT_TOKEN

    create_env_file(env_file=env_file_path, token=token_val)

    repo_root = get_repo_root()
    if bridge_worker_dir is None:
        bridge_worker_dir = repo_root / "spikes" / "cf-d1-spike" / "bridge-worker"

    cmd = [
        "npx",
        "wrangler",
        "dev",
        "--ip",
        host,
        "--port",
        str(port),
        "--persist-to",
        str(persist_dir_path),
        "--env-file",
        str(env_file_path),
    ]
    assert_safe_environment(cmd)

    proc = subprocess.Popen(
        cmd,
        cwd=str(bridge_worker_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
        text=True,
    )

    if run_id:
        write_run_marker(persist_dir_path, run_id=run_id, pid=proc.pid)

    query_url = f"http://{host}:{port}/api/v1/query"
    try:
        is_ready = wait_for_bridge_ready(proc=proc, url=query_url, token=token_val, timeout=timeout)
        if not is_ready:
            terminate_process(proc)
            raise TimeoutError(f"Worker bridge failed to become ready on {query_url} within {timeout}s.")
    except Exception:
        terminate_process(proc)
        raise

    return proc


def teardown_d1_target(
    proc: Optional[subprocess.Popen] = None,
    persist_dir: Optional[Path] = None,
    env_file: Optional[Path] = None,
    port: int = DEFAULT_PORT,
    run_id: Optional[str] = None,
) -> None:
    """
    Safely tears down only the provided process and cleans up only its own run-scoped files:
    - ONLY terminates proc if given
    - Cleans persist_dir only if marker validates against run_id
    - Cleans env_file only if under /tmp
    """
    if proc is not None:
        terminate_process(proc)

    if persist_dir is not None:
        cleanup_persist_directory(persist_dir, run_id=run_id)

    if env_file is not None:
        cleanup_env_file(env_file)


class D1TargetHarness:
    """Context manager and orchestrator for local D1 Target rehearsal environment."""

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        host: str = DEFAULT_HOST,
        persist_dir: Optional[Path] = None,
        env_file: Optional[Path] = None,
        token: Optional[str] = None,
        proc: Optional[subprocess.Popen] = None,
        run_id: Optional[str] = None,
    ):
        if isinstance(port, str) and run_id is None:
            run_id = port
            port = DEFAULT_PORT
        self.run_id = run_id or secrets.token_hex(6)
        self.port = port
        self.host = host
        self.persist_dir = (
            Path(persist_dir).resolve()
            if persist_dir is not None
            else Path(f"/tmp/assethub_rehearsal_d1_{self.run_id}")
        )
        self.env_file = (
            Path(env_file).resolve()
            if env_file is not None
            else Path(f"/tmp/assethub_rehearsal_d1_{self.run_id}.env")
        )
        self.token = token or DEFAULT_TOKEN
        self.process: Optional[subprocess.Popen] = proc
        self.base_url = f"http://{self.host}:{self.port}"
        self.query_url = f"{self.base_url}/api/v1/query"

    @property
    def proc(self) -> Optional[subprocess.Popen]:
        """Backward compatibility alias for self.process."""
        return self.process

    @proc.setter
    def proc(self, value: Optional[subprocess.Popen]) -> None:
        self.process = value

    def setup(self, clean_first: bool = True) -> "D1TargetHarness":
        """Execute initialization pipeline with strict process and directory isolation."""
        # 1. Immediate port check: Fail if port is in use; NEVER kill external processes
        assert_port_available(host=self.host, port=self.port)

        # 2. Clean prior instance files if requested
        if clean_first:
            if self.process is not None:
                terminate_process(self.process)
                self.process = None
            cleanup_persist_directory(self.persist_dir, run_id=self.run_id)
            cleanup_env_file(self.env_file)

        # 3. Create run directory and write initial marker
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        write_run_marker(self.persist_dir, run_id=self.run_id, pid=None)

        # 4. Ingest DDL
        ingest_d1_ddl(persist_dir=self.persist_dir, port=self.port)

        # 5. Launch Worker Bridge
        self.process = start_d1_bridge(
            port=self.port,
            host=self.host,
            persist_dir=self.persist_dir,
            env_file=self.env_file,
            token=self.token,
            run_id=self.run_id,
        )

        # 6. Update marker with active PID
        write_run_marker(self.persist_dir, run_id=self.run_id, pid=self.process.pid)

        # 7. Verify destination clean state (17 tables, 0 rows)
        self.verify_clean()

        if self not in _active_harnesses:
            _active_harnesses.append(self)
        return self

    def query(self, sql: str, params: Optional[List[Any]] = None) -> Dict[str, Any]:
        """Execute query against running bridge."""
        return query_bridge_http(sql, params=params, url=self.query_url, token=self.token)

    def verify_clean(self) -> Dict[str, int]:
        """Verify all target tables exist and have 0 rows."""
        return verify_d1_target_clean_bridge(url=self.query_url, token=self.token)

    def teardown(self) -> None:
        """Teardown only this harness's process and purge only its own files."""
        if self in _active_harnesses:
            _active_harnesses.remove(self)

        # Terminate only our own process
        if self.process is not None:
            terminate_process(self.process)
            self.process = None

        # Clean only our own directory after verifying marker
        cleanup_persist_directory(self.persist_dir, run_id=self.run_id)

        # Clean only our own env file
        cleanup_env_file(self.env_file)

    def close(self) -> None:
        """Alias for teardown."""
        self.teardown()

    def __enter__(self) -> "D1TargetHarness":
        return self.setup()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.teardown()


@atexit.register
def _cleanup_atexit() -> None:
    """Ensure all background processes and temporary directories are destroyed on exit."""
    for harness in list(_active_harnesses):
        try:
            harness.teardown()
        except Exception:
            pass


def setup_d1_target(
    port: int = DEFAULT_PORT,
    host: str = DEFAULT_HOST,
    persist_dir: Optional[Path] = None,
    env_file: Optional[Path] = None,
    token: Optional[str] = None,
    clean_first: bool = True,
    run_id: Optional[str] = None,
) -> D1TargetHarness:
    """Factory function: Setup clean D1 target, start bridge, and verify destination."""
    harness = D1TargetHarness(
        port=port,
        host=host,
        persist_dir=persist_dir,
        env_file=env_file,
        token=token,
        run_id=run_id,
    )
    return harness.setup(clean_first=clean_first)


if __name__ == "__main__":
    import sys

    arg = sys.argv[1] if len(sys.argv) > 1 else "--test"

    if arg == "--setup":
        print(f"Setting up D1 target harness on port {DEFAULT_PORT}...")
        harness = setup_d1_target()
        print(f"Bridge running at {harness.base_url}. PID: {harness.process.pid if harness.process else 'unknown'}")
        print("Target tables verified clean (0 rows):", len(TARGET_TABLES))

    elif arg == "--teardown":
        print("Tearing down active D1 target harnesses...")
        for h in list(_active_harnesses):
            h.teardown()
        print("Teardown complete.")

    elif arg == "--verify":
        counts = verify_d1_target_clean()
        print("Verification PASS. All tables empty:")
        for t, c in counts.items():
            print(f"  {t}: {c}")

    elif arg == "--test":
        print("Running D1 target harness self-test...")
        with D1TargetHarness() as h:
            print(f"[1/4] Harness initialized at {h.base_url} (run_id: {h.run_id})")
            counts = h.verify_clean()
            print(f"[2/4] Verified {len(counts)} tables have 0 rows.")
            assert "ai_suggestion_logs" in counts, "ai_suggestion_logs must be present"

            # Execute sample query
            q_res = h.query("SELECT 42 as answer;")
            assert q_res.get("results") == [{"answer": 42}], f"Unexpected result: {q_res}"
            print("[3/4] SELECT query validated successfully.")

            # Record paths to verify post-cleanup
            p_dir = h.persist_dir
            e_file = h.env_file

        # Verify teardown
        assert not e_file.exists(), f"Env file was not cleaned: {e_file}"
        assert not p_dir.exists(), f"Persist dir was not cleaned: {p_dir}"
        print("[4/4] Teardown cleaned all persist files and killed worker process.")
        print("ALL HARNESS TESTS PASSED.")
