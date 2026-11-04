"""
Harness for Local Cloudflare D1 and Worker Bridge Rehearsal.

Manages the lifecycle of a dedicated, isolated local Cloudflare D1 SQLite database
and Worker Bridge running on 127.0.0.1:8787 for zero-cloud migration testing.

Isolation Rules:
- NEVER deploy to Cloudflare.
- NEVER use --remote flag.
- NEVER reset cloud D1 resources.
- Strict token authentication and local file-backed persistence.
"""

import atexit
import glob
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

# Rehearsal Configuration Constants
DEFAULT_PORT = 8787
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PERSIST_DIR = Path("/tmp/wrangler-d1-rehearsal-20261104")
DEFAULT_ENV_FILE = Path("/tmp/wrangler-rehearsal-20261104.env")
DEFAULT_PID_FILE = Path("/tmp/wrangler-rehearsal-20261104.pid")
DEFAULT_TOKEN = (
    os.environ.get("D1_BRIDGE_TOKEN")
    or "assethub_rehearsal_bridge_token_local_test_32chars_min"
)
DEFAULT_DB_NAME = "assethub-test-d1"
DEFAULT_BRIDGE_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
DEFAULT_QUERY_URL = f"{DEFAULT_BRIDGE_URL}/api/v1/query"

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


def _kill_processes_on_port(port: int = DEFAULT_PORT) -> None:
    """
    Safely terminate any lingering process listening on the specified port.
    Crucial: Kills entire process tree (node/wrangler/workerd) to prevent auto-respawn.
    """
    # 1. Kill any wrangler dev process targeting this port
    try:
        subprocess.run(["pkill", "-9", "-f", f"wrangler.*dev.*{port}"], capture_output=True)
    except Exception:
        pass

    # 2. Check stored pid file if present
    if DEFAULT_PID_FILE.exists():
        try:
            pid = int(DEFAULT_PID_FILE.read_text().strip())
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except Exception:
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass
            DEFAULT_PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    # 3. Kill listeners and their parent dev processes
    try:
        res = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True)
        pids = [int(p) for p in res.stdout.strip().split() if p.isdigit()]
        for pid in pids:
            # Walk up to kill parents (wrangler/node) first so workerd does not respawn
            curr = pid
            to_kill = [curr]
            while curr > 1:
                try:
                    with open(f"/proc/{curr}/stat", "r") as f:
                        ppid = int(f.read().split()[3])
                        if ppid > 1 and ppid not in to_kill:
                            to_kill.append(ppid)
                            curr = ppid
                        else:
                            break
                except Exception:
                    break

            for p in reversed(to_kill):
                try:
                    os.kill(p, signal.SIGKILL)
                except ProcessLookupError:
                    pass
    except Exception:
        pass

    # 4. Secondary fuser safeguard
    try:
        subprocess.run(["fuser", "-k", "-9", f"{port}/tcp"], capture_output=True)
    except Exception:
        pass


def create_env_file(env_file: Path = DEFAULT_ENV_FILE, token: str = DEFAULT_TOKEN) -> Path:
    """Generate temporary environment file containing BRIDGE_SECRET_TOKEN."""
    token_clean = token.strip()
    if len(token_clean) < 32:
        raise ValueError("BRIDGE_SECRET_TOKEN must be at least 32 characters long.")
    target_path = Path(env_file).resolve()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(f"BRIDGE_SECRET_TOKEN={token_clean}\n", encoding="utf-8")
    return target_path


def ingest_d1_ddl(
    persist_dir: Path = DEFAULT_PERSIST_DIR,
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

    persist_dir_path = Path(persist_dir).resolve()
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


def get_d1_sqlite_file(persist_dir: Path = DEFAULT_PERSIST_DIR) -> Path:
    """Locate the miniflare D1 SQLite database file."""
    persist_dir_path = Path(persist_dir).resolve()
    pattern = persist_dir_path / "v3" / "d1" / "miniflare-D1DatabaseObject" / "*.sqlite"
    matches = [Path(p) for p in glob.glob(str(pattern)) if not p.endswith("metadata.sqlite")]
    if not matches:
        raise FileNotFoundError(f"No D1 SQLite database found in persist directory pattern: {pattern}")
    return matches[0]


def query_bridge_http(
    sql: str,
    params: Optional[List[Any]] = None,
    url: str = DEFAULT_QUERY_URL,
    token: str = DEFAULT_TOKEN,
    timeout: float = 5.0,
) -> Dict[str, Any]:
    """Execute SQL query over Worker Bridge HTTP API."""
    if params is None:
        params = []
    payload = json.dumps({"sql": sql, "params": params}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Bridge-Token": token.strip(),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def check_bridge_health(
    url: str = DEFAULT_QUERY_URL,
    token: str = DEFAULT_TOKEN,
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
    token: str = DEFAULT_TOKEN,
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


def verify_d1_target_clean_sqlite(persist_dir: Path = DEFAULT_PERSIST_DIR) -> Dict[str, int]:
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
    token: str = DEFAULT_TOKEN,
) -> Dict[str, int]:
    """Inspect local D1 via Worker Bridge HTTP query to verify table existence and zero rows."""
    res = query_bridge_http(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '_cf_%' AND name NOT LIKE 'sqlite_%';",
        url=url,
        token=token,
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
        c_res = query_bridge_http(f'SELECT count(*) as cnt FROM "{t}";', url=url, token=token)
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
    token: str = DEFAULT_TOKEN,
    persist_dir: Path = DEFAULT_PERSIST_DIR,
) -> Dict[str, int]:
    """Verify target tables are clean (0 rows) and all exist, preferring bridge when active."""
    if check_bridge_health(url=url, token=token, timeout=1.0):
        return verify_d1_target_clean_bridge(url=url, token=token)
    return verify_d1_target_clean_sqlite(persist_dir=persist_dir)


def start_d1_bridge(
    port: int = DEFAULT_PORT,
    persist_dir: Path = DEFAULT_PERSIST_DIR,
    env_file: Path = DEFAULT_ENV_FILE,
    token: str = DEFAULT_TOKEN,
    bridge_worker_dir: Optional[Path] = None,
    timeout: float = 15.0,
) -> subprocess.Popen:
    """
    Launch npx wrangler dev on port 8787 with:
    --ip 127.0.0.1 --port 8787 --persist-to <persist_dir> --env-file <env_file>
    Polls health check with SELECT 1 until ready (15s timeout).
    """
    _kill_processes_on_port(port)
    create_env_file(env_file=env_file, token=token)

    repo_root = get_repo_root()
    if bridge_worker_dir is None:
        bridge_worker_dir = repo_root / "spikes" / "cf-d1-spike" / "bridge-worker"

    persist_dir_path = Path(persist_dir).resolve()
    env_file_path = Path(env_file).resolve()

    cmd = [
        "npx",
        "wrangler",
        "dev",
        "--ip",
        DEFAULT_HOST,
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
        preexec_fn=os.setsid,
        text=True,
    )

    # Record PID
    DEFAULT_PID_FILE.write_text(str(proc.pid), encoding="utf-8")

    query_url = f"http://{DEFAULT_HOST}:{port}/api/v1/query"
    try:
        is_ready = wait_for_bridge_ready(proc=proc, url=query_url, token=token, timeout=timeout)
        if not is_ready:
            teardown_d1_target(proc=proc, persist_dir=None, env_file=None, port=port)
            raise TimeoutError(f"Worker bridge failed to become ready on {query_url} within {timeout}s.")
    except Exception:
        teardown_d1_target(proc=proc, persist_dir=None, env_file=None, port=port)
        raise

    return proc


def teardown_d1_target(
    proc: Optional[subprocess.Popen] = None,
    persist_dir: Optional[Path] = DEFAULT_PERSIST_DIR,
    env_file: Optional[Path] = DEFAULT_ENV_FILE,
    port: int = DEFAULT_PORT,
) -> None:
    """
    Terminates the wrangler dev process and cleans up:
    - /tmp/wrangler-d1-rehearsal-20261104
    - /tmp/wrangler-rehearsal-20261104.env
    """
    # 1. Terminate provided process instance
    if proc is not None:
        try:
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
        except Exception:
            pass

    # 2. Terminate any stray listeners on rehearsal port & ancestors
    _kill_processes_on_port(port)

    # 3. Clean up rehearsal persist directory
    if persist_dir:
        p = Path(persist_dir)
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)

    # 4. Clean up rehearsal environment and pid files
    if env_file:
        ef = Path(env_file)
        if ef.exists():
            try:
                ef.unlink(missing_ok=True)
            except Exception:
                pass

    if DEFAULT_PID_FILE.exists():
        try:
            DEFAULT_PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass


class D1TargetHarness:
    """Context manager and orchestrator for local D1 Target rehearsal environment."""

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        persist_dir: Path = DEFAULT_PERSIST_DIR,
        env_file: Path = DEFAULT_ENV_FILE,
        token: str = DEFAULT_TOKEN,
        proc: Optional[subprocess.Popen] = None,
    ):
        self.port = port
        self.persist_dir = Path(persist_dir)
        self.env_file = Path(env_file)
        self.token = token
        self.proc = proc
        self.base_url = f"http://{DEFAULT_HOST}:{port}"
        self.query_url = f"{self.base_url}/api/v1/query"

    def setup(self, clean_first: bool = True) -> "D1TargetHarness":
        """Execute full initialization pipeline."""
        if clean_first:
            teardown_d1_target(persist_dir=self.persist_dir, env_file=self.env_file, port=self.port)

        ingest_d1_ddl(persist_dir=self.persist_dir, port=self.port)
        self.proc = start_d1_bridge(
            port=self.port,
            persist_dir=self.persist_dir,
            env_file=self.env_file,
            token=self.token,
        )
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
        """Teardown running bridge and purge rehearsal files."""
        if self in _active_harnesses:
            _active_harnesses.remove(self)
        teardown_d1_target(
            proc=self.proc,
            persist_dir=self.persist_dir,
            env_file=self.env_file,
            port=self.port,
        )
        self.proc = None

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
    persist_dir: Path = DEFAULT_PERSIST_DIR,
    env_file: Path = DEFAULT_ENV_FILE,
    token: str = DEFAULT_TOKEN,
    clean_first: bool = True,
) -> D1TargetHarness:
    """Factory function: Setup clean D1 target, start bridge, and verify destination."""
    harness = D1TargetHarness(
        port=port,
        persist_dir=persist_dir,
        env_file=env_file,
        token=token,
    )
    return harness.setup(clean_first=clean_first)


if __name__ == "__main__":
    import sys

    arg = sys.argv[1] if len(sys.argv) > 1 else "--test"

    if arg == "--setup":
        print(f"Setting up D1 target harness on port {DEFAULT_PORT}...")
        harness = setup_d1_target()
        print(f"Bridge running at {harness.base_url}. PID: {harness.proc.pid if harness.proc else 'unknown'}")
        print("Target tables verified clean (0 rows):", len(TARGET_TABLES))

    elif arg == "--teardown":
        print("Tearing down D1 target harness...")
        teardown_d1_target()
        print("Teardown complete.")

    elif arg == "--verify":
        counts = verify_d1_target_clean()
        print("Verification PASS. All tables empty:")
        for t, c in counts.items():
            print(f"  {t}: {c}")

    elif arg == "--test":
        print("Running D1 target harness self-test...")
        with D1TargetHarness() as h:
            print(f"[1/4] Harness initialized at {h.base_url}")
            counts = h.verify_clean()
            print(f"[2/4] Verified {len(counts)} tables have 0 rows.")
            assert "ai_suggestion_logs" in counts, "ai_suggestion_logs must be present"

            # Execute sample query
            q_res = h.query("SELECT 42 as answer;")
            assert q_res.get("results") == [{"answer": 42}], f"Unexpected result: {q_res}"
            print("[3/4] SELECT query validated successfully.")

        # Verify teardown
        assert not DEFAULT_ENV_FILE.exists(), f"Env file was not cleaned: {DEFAULT_ENV_FILE}"
        assert not DEFAULT_PERSIST_DIR.exists(), f"Persist dir was not cleaned: {DEFAULT_PERSIST_DIR}"
        print("[4/4] Teardown cleaned all persist files and killed worker process.")
        print("ALL HARNESS TESTS PASSED.")
