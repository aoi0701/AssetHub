"""
Cloudflare D1 HTTP Client for AssetHub Django Backend
Implements HTTP communication with D1 Worker Bridge using standard library only.
"""
import os
import time
import json
import uuid
import hashlib
import urllib.request
import urllib.error
from urllib.parse import urlsplit
from django.db import IntegrityError, OperationalError, ProgrammingError, DatabaseError


class D1IncompleteBatchError(DatabaseError):
    def __init__(self, message, code="INCOMPLETE_BATCH_STATE", committed=True, idempotency_key=None, payload=None, raw_response=None):
        super().__init__(message)
        self.code = code
        self.committed = committed
        self.idempotency_key = idempotency_key
        self.payload = payload
        self.raw_response = raw_response


ALLOWED_TEST_BRIDGE_URLS = (
    "https://assethub-test-worker.hnhoa494-assethub.workers.dev",
    "http://127.0.0.1:8787",
    "http://localhost:8787",
)


def parse_and_normalize_endpoint(url_str: str) -> tuple[str, str, int, str]:
    """
    Parse and normalize URL into (scheme, hostname, port, path).
    Strictly forbids userinfo (credentials in URL) and unapproved schemes.
    """
    if not url_str or not isinstance(url_str, str):
        raise OperationalError("Bridge URL must be a non-empty string.")

    parsed = urlsplit(url_str.strip())
    if parsed.username or parsed.password:
        raise OperationalError("Test Isolation Guard: Bridge URL with userinfo is not permitted.")

    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise OperationalError(f"Test Isolation Guard: Unsupported bridge URL scheme '{scheme}'.")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise OperationalError("Test Isolation Guard: Missing hostname in bridge URL.")

    port = parsed.port if parsed.port is not None else (443 if scheme == "https" else 80)
    path = parsed.path.rstrip("/")
    return scheme, hostname, port, path


ALLOWED_TEST_BRIDGE_ENDPOINTS = {
    parse_and_normalize_endpoint(u) for u in ALLOWED_TEST_BRIDGE_URLS
}


def is_test_environment() -> bool:
    """Detect if running in automated test suite."""
    import sys
    return bool(
        os.environ.get("PYTEST_CURRENT_TEST")
        or os.environ.get("DJANGO_TEST")
        or "pytest" in sys.modules
        or any("test" in arg for arg in sys.argv)
    )


def is_read_only_query(sql: str) -> bool:
    """
    Conservative classification: strictly whitelist verified read-only statements.
    Unknown statements, CTEs, or ambiguous prefixes are treated as mutations.
    """
    if not sql or not isinstance(sql, str):
        return False

    cleaned = sql.strip()
    # Strip multi-line comments
    while cleaned.startswith("/*"):
        end_idx = cleaned.find("*/")
        if end_idx == -1:
            break
        cleaned = cleaned[end_idx + 2:].strip()

    # Strip single-line comments
    while cleaned.startswith("--"):
        end_idx = cleaned.find("\n")
        if end_idx == -1:
            cleaned = ""
            break
        cleaned = cleaned[end_idx + 1:].strip()

    tokens = cleaned.split()
    if not tokens:
        return False

    first_word = tokens[0].upper()
    if first_word in ("SELECT", "EXPLAIN"):
        return True

    if first_word == "PRAGMA":
        upper_sql = cleaned.upper()
        read_only_pragmas = (
            "PRAGMA TABLE_INFO",
            "PRAGMA TABLE_XINFO",
            "PRAGMA INDEX_LIST",
            "PRAGMA INDEX_INFO",
            "PRAGMA FOREIGN_KEY_LIST",
            "PRAGMA DATABASE_LIST",
        )
        return any(upper_sql.startswith(p) for p in read_only_pragmas)

    # Any unknown or unclassified statement is treated as a mutation
    return False


class D1Client:
    def __init__(self, base_url=None, token=None, timeout=30.0):
        if base_url is None:
            base_url = os.environ.get("D1_BRIDGE_URL", "")
        self.base_url = base_url.rstrip("/")

        if token is None:
            token = os.environ.get("D1_BRIDGE_TOKEN", "")
        self.token = token
        self.timeout = timeout

        if not self.base_url:
            raise OperationalError("D1_BRIDGE_URL environment variable is required.")
        if not self.token or len(self.token.strip()) < 32:
            raise OperationalError("D1_BRIDGE_TOKEN must be configured with a secure key (>=32 chars).")

    def _headers(self, idempotency_key=None, custom_headers=None):
        headers = {
            "Content-Type": "application/json",
            "X-Bridge-Token": self.token,
            "User-Agent": "AssetHub-Django-D1-Engine/1.0",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = str(idempotency_key)
        if custom_headers and isinstance(custom_headers, dict):
            headers.update(custom_headers)
        return headers

    def _post(self, path, payload, idempotency_key=None, is_mutation=False, retries=3, custom_headers=None):
        # Test Isolation Guard: In test environments, block ANY mutation to unauthorized endpoints
        # Checked BEFORE sending any HTTP packet over the network!
        if is_mutation and is_test_environment():
            try:
                target_endpoint = parse_and_normalize_endpoint(self.base_url)
            except OperationalError:
                raise
            except Exception as e:
                raise OperationalError(f"Test Isolation Guard: Invalid bridge URL '{self.base_url}': {str(e)}")

            if target_endpoint not in ALLOWED_TEST_BRIDGE_ENDPOINTS:
                raise OperationalError(
                    f"Test Isolation Guard: Mutation blocked to unauthorized bridge endpoint '{self.base_url}'. "
                    f"Parsed: {target_endpoint}. Allowed test endpoints: {ALLOWED_TEST_BRIDGE_URLS}."
                )

        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        headers = self._headers(idempotency_key=idempotency_key, custom_headers=custom_headers)
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        # Safe retry policy: only retry if read-only, or if mutation has an Idempotency-Key
        can_retry = (not is_mutation) or (idempotency_key is not None)
        max_attempts = retries if can_retry else 1

        last_err = None
        for attempt in range(max_attempts):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw)
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8")
                # Parse JSON error if possible
                msg = err_body
                parsed = {}
                try:
                    parsed = json.loads(err_body)
                    msg = parsed.get("error", err_body)
                except Exception:
                    parsed = {}

                if isinstance(parsed, dict) and (parsed.get("code") == "INCOMPLETE_BATCH_STATE" or parsed.get("committed") is True):
                    raise D1IncompleteBatchError(
                        f"D1 Incomplete Batch State ({e.code}): {msg}",
                        code=parsed.get("code", "INCOMPLETE_BATCH_STATE"),
                        committed=True,
                        idempotency_key=idempotency_key,
                        payload=payload,
                        raw_response=parsed,
                    )

                if e.code == 409 or "SQLITE_CONSTRAINT" in msg or "CHECK constraint failed" in msg:
                    raise IntegrityError(f"D1 Constraint Conflict: {msg}")
                elif e.code in (400, 422):
                    raise ProgrammingError(f"D1 Bad Request ({e.code}): {msg}")
                elif e.code == 401:
                    raise OperationalError("D1 Authentication Failure: Invalid bridge secret token.")
                else:
                    last_err = OperationalError(f"D1 HTTP {e.code}: {msg}")

                if not can_retry:
                    raise last_err
                time.sleep(0.2 * (attempt + 1))

            except urllib.error.URLError as e:
                last_err = OperationalError(f"D1 Network Transport Error: {e.reason}")
                if not can_retry:
                    raise OperationalError(
                        f"D1 Network Timeout during un-idempotent mutation! State is UNKNOWN: {e.reason}"
                    )
                time.sleep(0.2 * (attempt + 1))
            except Exception as e:
                last_err = OperationalError(f"D1 Unexpected Error: {str(e)}")
                if not can_retry:
                    raise last_err
                time.sleep(0.2 * (attempt + 1))

        raise last_err

    def query(self, sql, params=None, idempotency_key=None, custom_headers=None, retries=3):
        if params is None:
            params = []
        clean_params = [
            1 if p is True else (0 if p is False else p)
            for p in params
        ]
        is_read_only = is_read_only_query(sql)
        is_mutation = not is_read_only
        payload = {"sql": sql, "params": clean_params}
        return self._post(
            "/api/v1/query",
            payload,
            idempotency_key=idempotency_key,
            is_mutation=is_mutation,
            retries=retries,
            custom_headers=custom_headers,
        )

    def batch(self, statements, idempotency_key=None, custom_headers=None, retries=3):
        formatted = []
        for st in statements:
            if isinstance(st, (tuple, list)):
                raw_sql = st[0]
                raw_params = list(st[1]) if len(st) > 1 else []
            elif isinstance(st, dict):
                raw_sql = st["sql"]
                raw_params = st.get("params", [])
            elif isinstance(st, str):
                raw_sql = st
                raw_params = []
            else:
                continue

            clean_params = [
                1 if p is True else (0 if p is False else p)
                for p in raw_params
            ]
            formatted.append({"sql": raw_sql, "params": clean_params})

        if not idempotency_key:
            idempotency_key = f"batch-{uuid.uuid4()}"

        return self._post(
            "/api/v1/batch",
            {"statements": formatted},
            idempotency_key=idempotency_key,
            is_mutation=True,
            retries=retries,
            custom_headers=custom_headers,
        )
