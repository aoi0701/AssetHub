"""
Test Suite: Cloudflare D1 Bridge Atomicity, Idempotency & Classification
Verifies:
1. Conservative read/write classification (whitelists only known reads; treats unknown as mutations).
2. Lost response after commit (idempotent replay returns cached response, database written once).
3. Same Idempotency-Key with different payload rejects with HTTP 422.
4. Concurrent requests with same Idempotency-Key result in exactly one write.
"""
import os
import sys
import uuid
import json
import concurrent.futures
from pathlib import Path
from dotenv import load_dotenv

# Load environment without printing secrets
env_path = Path(__file__).resolve().parent.parent / ".env.test"
load_dotenv(env_path)

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "backend"))

import pytest
import urllib.request
import urllib.error
from assethub_api.backends.d1.client import D1Client, is_read_only_query
from django.db import IntegrityError, ProgrammingError, OperationalError


@pytest.fixture(scope="module")
def bridge_credentials():
    url = os.environ.get("D1_BRIDGE_URL", "https://assethub-test-worker.hnhoa494-assethub.workers.dev")
    token = os.environ.get("D1_BRIDGE_TOKEN")
    if not token or len(token.strip()) < 32:
        pytest.skip("D1_BRIDGE_TOKEN is not configured with >= 32 chars in environment")
    return url, token


def test_conservative_read_write_classification():
    """Conservative classification: only explicitly verified read operations are read-only."""
    # Read-only cases
    assert is_read_only_query("SELECT * FROM authentication_user") is True
    assert is_read_only_query("select count(*) from departments_department") is True
    assert is_read_only_query("EXPLAIN SELECT 1") is True
    assert is_read_only_query("/* leading comment */ SELECT * FROM assets_asset") is True
    assert is_read_only_query("-- single line comment\nSELECT 1") is True
    assert is_read_only_query("PRAGMA table_info('authentication_user')") is True
    assert is_read_only_query("PRAGMA foreign_key_list('assets_asset')") is True

    # Mutations & Unrecognized statements must be classified as mutations (False)
    assert is_read_only_query("INSERT INTO departments_department (code) VALUES ('D1')") is False
    assert is_read_only_query("UPDATE authentication_user SET is_active=0") is False
    assert is_read_only_query("DELETE FROM assets_asset WHERE id=1") is False
    assert is_read_only_query("CREATE TABLE foo (id INT)") is False
    assert is_read_only_query("DROP TABLE foo") is False
    assert is_read_only_query("ALTER TABLE foo ADD COLUMN bar TEXT") is False
    assert is_read_only_query("REPLACE INTO departments_department (id) VALUES (1)") is False
    assert is_read_only_query("WITH cte AS (SELECT 1) SELECT * FROM cte") is False  # Conservative!
    assert is_read_only_query("BEGIN TRANSACTION") is False
    assert is_read_only_query("VACUUM") is False
    assert is_read_only_query("UNKNOWN_ARBITRARY_COMMAND") is False
    assert is_read_only_query("") is False
    assert is_read_only_query(None) is False


def test_atomic_idempotency_lost_response_replay(bridge_credentials):
    """
    Test: Lost response after commit.
    Scenario: Client sends mutation with Idempotency-Key.
    Client retries with SAME key and SAME payload.
    Expected: Bridge returns exact replay (HTTP 200, X-Idempotent-Replay), DB has only 1 row.
    """
    url, token = bridge_credentials
    client = D1Client(base_url=url, token=token)

    idem_key = f"test-replay-{uuid.uuid4()}"
    test_code = f"T-REP-{uuid.uuid4().hex[:6].upper()}"

    # Clean up any potential stale record
    client.query("DELETE FROM departments_department WHERE code = ?", [test_code])

    insert_sql = "INSERT INTO departments_department (code, name, description) VALUES (?, ?, ?)"
    params = [test_code, "Phòng Kiểm Thử Replay", "Kiểm thử mất phản hồi sau commit"]

    # First attempt: execute mutation with idempotency key
    res1 = client.query(insert_sql, params, idempotency_key=idem_key)
    assert res1.get("success") is True
    assert res1.get("meta", {}).get("changes") == 1

    # Simulate retry: execute EXACT same query and params with EXACT same idempotency key
    res2 = client.query(insert_sql, params, idempotency_key=idem_key)
    assert res2.get("success") is True

    # Check database: record count MUST BE EXACTLY 1 (no double insert!)
    check_res = client.query("SELECT COUNT(*) as cnt FROM departments_department WHERE code = ?", [test_code])
    assert check_res["results"][0]["cnt"] == 1

    # Clean up
    client.query("DELETE FROM departments_department WHERE code = ?", [test_code])


def test_idempotency_same_key_different_payload_conflict(bridge_credentials):
    """
    Test: Same Idempotency-Key with DIFFERENT payload must be rejected with HTTP 422 Conflict.
    """
    url, token = bridge_credentials
    client = D1Client(base_url=url, token=token)

    idem_key = f"test-conflict-{uuid.uuid4()}"
    test_code_1 = f"T-CONF1-{uuid.uuid4().hex[:6].upper()}"
    test_code_2 = f"T-CONF2-{uuid.uuid4().hex[:6].upper()}"

    insert_sql = "INSERT INTO departments_department (code, name, description) VALUES (?, ?, ?)"

    # First request
    res1 = client.query(insert_sql, [test_code_1, "Dept 1", "Initial"], idempotency_key=idem_key)
    assert res1.get("success") is True

    # Second request with SAME key but DIFFERENT payload -> must raise ProgrammingError (422)
    with pytest.raises(ProgrammingError) as exc_info:
        client.query(insert_sql, [test_code_2, "Dept 2", "Modified Payload"], idempotency_key=idem_key)

    assert "422" in str(exc_info.value) or "Conflict" in str(exc_info.value)

    # Check database: only Dept 1 was written, Dept 2 was NOT written
    check1 = client.query("SELECT COUNT(*) as cnt FROM departments_department WHERE code = ?", [test_code_1])
    assert check1["results"][0]["cnt"] == 1
    check2 = client.query("SELECT COUNT(*) as cnt FROM departments_department WHERE code = ?", [test_code_2])
    assert check2["results"][0]["cnt"] == 0

    # Clean up
    client.query("DELETE FROM departments_department WHERE code IN (?, ?)", [test_code_1, test_code_2])


def test_concurrent_requests_with_same_idempotency_key(bridge_credentials):
    """
    Test: Two concurrent requests with the SAME Idempotency-Key.
    Expected: Exactly one row is created in D1. Neither produces duplicates.
    """
    url, token = bridge_credentials
    client1 = D1Client(base_url=url, token=token)
    client2 = D1Client(base_url=url, token=token)

    idem_key = f"test-concurrent-{uuid.uuid4()}"
    test_code = f"T-CONC-{uuid.uuid4().hex[:6].upper()}"
    insert_sql = "INSERT INTO departments_department (code, name, description) VALUES (?, ?, ?)"
    params = [test_code, "Phòng Đồng Thời", "Kiểm thử race condition"]

    results = []
    errors = []

    def make_call(client_instance):
        try:
            return client_instance.query(insert_sql, params, idempotency_key=idem_key)
        except Exception as e:
            return e

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(make_call, client1)
        f2 = executor.submit(make_call, client2)
        res_list = [f1.result(), f2.result()]

    # At least one call must succeed
    successes = [r for r in res_list if isinstance(r, dict) and r.get("success") is True]
    assert len(successes) >= 1

    # Database count must be EXACTLY 1!
    check = client1.query("SELECT COUNT(*) as cnt FROM departments_department WHERE code = ?", [test_code])
    assert check["results"][0]["cnt"] == 1

    # Clean up
    client1.query("DELETE FROM departments_department WHERE code = ?", [test_code])
