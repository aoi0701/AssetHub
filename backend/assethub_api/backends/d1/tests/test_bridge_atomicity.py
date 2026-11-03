"""
Test Suite: Cloudflare D1 Bridge Atomicity, Idempotency & Classification
Verifies:
1. Conservative read/write classification (whitelists only known reads; treats unknown as mutations).
2. Lost response after commit (idempotent replay returns cached response, database written once).
3. Same Idempotency-Key with different payload rejects with HTTP 422.
4. Fault injection between commit and response completion (mutation committed, replay returns full results/meta, not PENDING).
5. Quoted identifiers in RETURNING clause (RETURNING "id", RETURNING "departments_department"."id").
6. Batch fault injection before update: replay receives D1IncompleteBatchError (HTTP 500 INCOMPLETE_BATCH_STATE, committed: true), zero fake results.
7. Unit tests for D1Client urlopen mock: non-retry on committed batch error, full retries on generic 500.
8. Concurrent requests with same Idempotency-Key both receive valid protocol success and database written once.
"""
import io
import os
import sys
import uuid
import json
import concurrent.futures
from pathlib import Path
from unittest.mock import patch
from dotenv import load_dotenv

# Load environment without printing secrets
env_paths = [
    Path("/app/.env.test"),
    Path(__file__).resolve().parents[3] / ".env.test",
    Path(__file__).resolve().parents[4] / "spikes" / "cf-d1-spike" / ".env.test",
]
for p in env_paths:
    if p.exists():
        load_dotenv(p)
        break

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "backend"))

import pytest
import urllib.request
import urllib.error
from assethub_api.backends.d1.client import D1Client, is_read_only_query, D1IncompleteBatchError
from django.db import IntegrityError, ProgrammingError, OperationalError


@pytest.fixture(scope="module")
def bridge_credentials():
    url = os.environ.get("D1_BRIDGE_URL", "https://assethub-test-worker.hnhoa494-assethub.workers.dev")
    token = os.environ.get("D1_BRIDGE_TOKEN")
    if not token or len(token.strip()) < 32:
        pytest.skip("D1_BRIDGE_TOKEN is not configured with >= 32 chars in environment")
    return url, token


def get_client(url, token):
    return D1Client(base_url=url, token=token, timeout=45.0)


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


def test_d1_client_incomplete_batch_state_no_retry():
    """
    Unit test: Mock urlopen responding with HTTP 500 {"code": "INCOMPLETE_BATCH_STATE", "committed": true}.
    Asserts:
    1. urlopen called EXACTLY 1 time (no auto-retries!).
    2. D1IncompleteBatchError is raised.
    3. exc_info.value.committed is True.
    4. exc_info.value.idempotency_key == idem_key.
    """
    client = D1Client(base_url="http://127.0.0.1:8787", token="a" * 32, timeout=5.0)
    idem_key = f"test-mock-inc-{uuid.uuid4()}"
    statements = [{"sql": "INSERT INTO departments_department (code) VALUES (?)", "params": ["D-MOCK"]}]

    error_payload = {
        "error": "Incomplete batch state after commit",
        "code": "INCOMPLETE_BATCH_STATE",
        "committed": True,
    }

    def mock_urlopen(req, timeout=None):
        fp = io.BytesIO(json.dumps(error_payload).encode("utf-8"))
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=500,
            msg="Internal Server Error",
            hdrs={"Content-Type": "application/json"},
            fp=fp,
        )

    with patch("urllib.request.urlopen", side_effect=mock_urlopen) as mock_open, \
         patch("time.sleep") as mock_sleep:
        with pytest.raises(D1IncompleteBatchError) as exc_info:
            client.batch(statements, idempotency_key=idem_key, retries=3)

        assert mock_open.call_count == 1, f"Expected exactly 1 call (no retries), got {mock_open.call_count}"
        assert mock_sleep.call_count == 0, f"Expected 0 sleep calls, got {mock_sleep.call_count}"
        assert exc_info.value.committed is True, "exc_info.value.committed must be True"
        assert exc_info.value.idempotency_key == idem_key, f"Expected idempotency_key {idem_key}, got {exc_info.value.idempotency_key}"


def test_d1_client_generic_500_retries_exhausted():
    """
    Unit test: Mock urlopen responding with generic HTTP 500 without committed: true.
    Asserts:
    1. Retry loop runs up to retries count (e.g. 3 attempts).
    2. OperationalError is raised after exhausting retries.
    """
    client = D1Client(base_url="http://127.0.0.1:8787", token="a" * 32, timeout=5.0)
    idem_key = f"test-mock-gen-{uuid.uuid4()}"
    statements = [{"sql": "INSERT INTO departments_department (code) VALUES (?)", "params": ["D-MOCK-GEN"]}]

    error_payload = {
        "error": "Temporary server error",
    }

    def mock_urlopen(req, timeout=None):
        fp = io.BytesIO(json.dumps(error_payload).encode("utf-8"))
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=500,
            msg="Internal Server Error",
            hdrs={"Content-Type": "application/json"},
            fp=fp,
        )

    retries_count = 3
    with patch("urllib.request.urlopen", side_effect=mock_urlopen) as mock_open, \
         patch("time.sleep") as mock_sleep:
        with pytest.raises(OperationalError) as exc_info:
            client.batch(statements, idempotency_key=idem_key, retries=retries_count)

        assert mock_open.call_count == retries_count, f"Expected {retries_count} calls, got {mock_open.call_count}"
        assert "500" in str(exc_info.value) or "Temporary server error" in str(exc_info.value)


def test_atomic_idempotency_lost_response_replay(bridge_credentials):
    """
    Test: Lost response after commit.
    Scenario: Client sends mutation with Idempotency-Key.
    Client retries with SAME key and SAME payload.
    Expected: Bridge returns exact replay (HTTP 200, X-Idempotent-Replay), DB has only 1 row.
    """
    url, token = bridge_credentials
    client = get_client(url, token)

    idem_key = f"test-replay-{uuid.uuid4()}"
    test_code = f"T-REP-{uuid.uuid4().hex[:6].upper()}"

    # Clean up any potential stale record
    client.query("DELETE FROM departments_department WHERE code = ?", [test_code], idempotency_key=f"clean-pre-{uuid.uuid4()}")

    insert_sql = "INSERT INTO departments_department (code, name, description) VALUES (?, ?, ?)"
    params = [test_code, "Phòng Kiểm Thử Replay", "Kiểm thử mất phản hồi sau commit"]

    # First attempt: execute mutation with idempotency key
    res1 = client.query(insert_sql, params, idempotency_key=idem_key)
    assert res1.get("success") is True
    assert res1.get("meta", {}).get("changes") == 1

    # Simulate retry: execute EXACT same query and params with EXACT same idempotency key
    res2 = client.query(insert_sql, params, idempotency_key=idem_key)
    assert res2.get("success") is True
    assert res2.get("meta", {}).get("changes") == 1

    # Check database: record count MUST BE EXACTLY 1 (no double insert!)
    check_res = client.query("SELECT COUNT(*) as cnt FROM departments_department WHERE code = ?", [test_code])
    assert check_res["results"][0]["cnt"] == 1

    # Clean up
    client.query("DELETE FROM departments_department WHERE code = ?", [test_code], idempotency_key=f"clean-post-{uuid.uuid4()}")


def test_query_replay_preserves_returning_data(bridge_credentials):
    """
    Test: Query with RETURNING clause preserves exact row contents on replay and after simulated crash.
    Verifies: Replay does NOT replace returning data with an empty results array.
    """
    url, token = bridge_credentials
    client = get_client(url, token)

    idem_key = f"test-ret-{uuid.uuid4()}"
    test_code = f"T-RET-{uuid.uuid4().hex[:6].upper()}"
    insert_sql = "INSERT INTO departments_department (code, name, description) VALUES (?, ?, ?) RETURNING id, code, name"
    params = [test_code, "Phòng RETURNING Test", "Kiểm thử bảo toàn dữ liệu RETURNING"]

    # 1. First execution
    res1 = client.query(insert_sql, params, idempotency_key=idem_key)
    assert res1.get("success") is True
    assert len(res1.get("results", [])) == 1
    row1 = res1["results"][0]
    inserted_id = row1.get("id")
    assert inserted_id is not None
    assert row1.get("code") == test_code
    assert row1.get("name") == "Phòng RETURNING Test"
    assert res1.get("meta", {}).get("changes") == 1

    # 2. Replay execution with same key -> Must return EXACT same row contents, NOT empty array!
    res2 = client.query(insert_sql, params, idempotency_key=idem_key)
    assert res2.get("success") is True
    assert len(res2.get("results", [])) == 1
    row2 = res2["results"][0]
    assert row2.get("id") == inserted_id
    assert row2.get("code") == test_code
    assert row2.get("name") == "Phòng RETURNING Test"
    assert res2.get("meta", {}).get("changes") == 1

    # Clean up
    client.query("DELETE FROM departments_department WHERE id = ?", [inserted_id], idempotency_key=f"clean-post-{uuid.uuid4()}")


def test_fault_injection_crash_after_commit_and_replay(bridge_credentials):
    """
    Test: Fault injection right between commit and response completion with RETURNING clause.
    Scenario:
    1. Request 1 commits mutation + idempotency log in D1, then crashes before HTTP 200 returned.
    2. Client receives HTTP 500 and retries with the SAME key and payload.
    3. Expected: Replay returns valid HTTP 200 with complete results containing actual RETURNING data (NEVER empty or PENDING).
    4. Database has exactly 1 row written.
    """
    url, token = bridge_credentials
    client = get_client(url, token)

    idem_key = f"test-fault-{uuid.uuid4()}"
    test_code = f"T-FLT-{uuid.uuid4().hex[:6].upper()}"
    insert_sql = "INSERT INTO departments_department (code, name, description) VALUES (?, ?, ?) RETURNING id"
    params = [test_code, "Phòng Fault Injection", "Kiểm thử lỗi giữa commit và phản hồi"]

    # Step 1: Send request with fault injection header and retries=1 -> must fail with OperationalError (500)
    with pytest.raises(OperationalError) as exc_info:
        client.query(
            insert_sql,
            params,
            idempotency_key=idem_key,
            custom_headers={"X-Test-Fault-Injection": "crash-after-commit"},
            retries=1,
        )
    assert "Fault Injected" in str(exc_info.value) or "500" in str(exc_info.value)

    # Step 2: Client retries normal request with same key
    replay_res = client.query(insert_sql, params, idempotency_key=idem_key)
    assert replay_res.get("success") is True
    assert isinstance(replay_res.get("results"), list)
    assert len(replay_res["results"]) == 1
    # Crucial: Must preserve actual RETURNING id at commit time, NOT empty array!
    returning_row = replay_res["results"][0]
    created_id = returning_row.get("id")
    assert created_id is not None
    meta = replay_res.get("meta")
    assert isinstance(meta, dict)
    assert meta.get("changes") == 1
    assert replay_res.get("status") != "PENDING"

    # Step 3: Check database: exactly 1 row committed
    check = client.query("SELECT COUNT(*) as cnt FROM departments_department WHERE id = ?", [created_id])
    assert check["results"][0]["cnt"] == 1

    # Clean up
    client.query("DELETE FROM departments_department WHERE id = ?", [created_id], idempotency_key=f"clean-post-{uuid.uuid4()}")


def test_fault_injection_crash_after_commit_and_replay_quoted_id(bridge_credentials):
    """
    Test: Quoted identifier RETURNING "id" preserves returning id after simulated crash.
    """
    url, token = bridge_credentials
    client = get_client(url, token)

    idem_key = f"test-fault-quoted-{uuid.uuid4()}"
    test_code = f"T-FLTQ-{uuid.uuid4().hex[:6].upper()}"
    insert_sql = 'INSERT INTO "departments_department" ("code", "name", "description") VALUES (?, ?, ?) RETURNING "id"'
    params = [test_code, "Phòng Quoted ID", "Kiểm thử quoted returning id"]

    # Step 1: Initial call with fault injection
    with pytest.raises(OperationalError) as exc_info:
        client.query(
            insert_sql,
            params,
            idempotency_key=idem_key,
            custom_headers={"X-Test-Fault-Injection": "crash-after-commit"},
            retries=1,
        )
    assert "Fault Injected" in str(exc_info.value) or "500" in str(exc_info.value)

    # Step 2: Replay must recover extracted ID from last_insert_rowid()
    replay_res = client.query(insert_sql, params, idempotency_key=idem_key)
    assert replay_res.get("success") is True
    assert isinstance(replay_res.get("results"), list)
    assert len(replay_res["results"]) == 1
    created_id = replay_res["results"][0].get("id")
    assert created_id is not None
    assert replay_res.get("meta", {}).get("changes") == 1

    # Step 3: Check DB count
    check = client.query('SELECT COUNT(*) as cnt FROM "departments_department" WHERE "id" = ?', [created_id])
    assert check["results"][0]["cnt"] == 1

    # Clean up
    client.query('DELETE FROM "departments_department" WHERE "id" = ?', [created_id], idempotency_key=f"clean-post-{uuid.uuid4()}")


def test_fault_injection_crash_after_commit_and_replay_table_prefixed_quoted_id(bridge_credentials):
    """
    Test: Table-prefixed quoted identifier RETURNING "departments_department"."id" preserves returning id.
    """
    url, token = bridge_credentials
    client = get_client(url, token)

    idem_key = f"test-fault-prefixed-{uuid.uuid4()}"
    test_code = f"T-FLTP-{uuid.uuid4().hex[:6].upper()}"
    insert_sql = 'INSERT INTO "departments_department" ("code", "name", "description") VALUES (?, ?, ?) RETURNING "departments_department"."id"'
    params = [test_code, "Phòng Table Prefixed ID", "Kiểm thử table-prefixed quoted returning id"]

    # Step 1: Initial call with fault injection
    with pytest.raises(OperationalError) as exc_info:
        client.query(
            insert_sql,
            params,
            idempotency_key=idem_key,
            custom_headers={"X-Test-Fault-Injection": "crash-after-commit"},
            retries=1,
        )
    assert "Fault Injected" in str(exc_info.value) or "500" in str(exc_info.value)

    # Step 2: Replay must recover extracted ID
    replay_res = client.query(insert_sql, params, idempotency_key=idem_key)
    assert replay_res.get("success") is True
    assert isinstance(replay_res.get("results"), list)
    assert len(replay_res["results"]) == 1
    created_id = replay_res["results"][0].get("id")
    assert created_id is not None
    assert replay_res.get("meta", {}).get("changes") == 1

    # Step 3: Check DB count
    check = client.query('SELECT COUNT(*) as cnt FROM "departments_department" WHERE "id" = ?', [created_id])
    assert check["results"][0]["cnt"] == 1

    # Clean up
    client.query('DELETE FROM "departments_department" WHERE "id" = ?', [created_id], idempotency_key=f"clean-post-{uuid.uuid4()}")


def test_batch_replay_preserves_multi_statement_results(bridge_credentials):
    """
    Test: Batch with multiple statements having different result structures.
    Verifies:
    1. Statement 1 (INSERT RETURNING id, code) returns row data.
    2. Statement 2 (Normal INSERT) returns changes == 1.
    3. Statement 3 (SELECT count) returns aggregate count.
    4. Replay preserves all 3 distinct per-statement result structures and contents.
    """
    url, token = bridge_credentials
    client = get_client(url, token)

    idem_key = f"test-batch-rep-{uuid.uuid4()}"
    code_a = f"T-BA-{uuid.uuid4().hex[:6].upper()}"
    code_b = f"T-BB-{uuid.uuid4().hex[:6].upper()}"

    statements = [
        {"sql": "INSERT INTO departments_department (code, name) VALUES (?, ?) RETURNING id, code", "params": [code_a, "Dept Batch A"]},
        {"sql": "INSERT INTO departments_department (code, name) VALUES (?, ?)", "params": [code_b, "Dept Batch B"]},
        {"sql": "SELECT count(*) as total FROM departments_department WHERE code IN (?, ?)", "params": [code_a, code_b]},
    ]

    # First attempt: execute batch with idempotency key
    res1 = client.batch(statements, idempotency_key=idem_key)
    assert res1.get("success") is True
    assert len(res1.get("results", [])) == 3

    st1_res = res1["results"][0]
    assert len(st1_res["results"]) == 1
    id_a = st1_res["results"][0].get("id")
    assert id_a is not None
    assert st1_res["results"][0].get("code") == code_a

    st2_res = res1["results"][1]
    assert st2_res["meta"]["changes"] == 1

    st3_res = res1["results"][2]
    assert st3_res["results"][0]["total"] == 2

    # Second attempt: exact replay with same key -> verify exact contents
    res2 = client.batch(statements, idempotency_key=idem_key)
    assert res2.get("success") is True
    assert len(res2.get("results", [])) == 3

    rep_st1 = res2["results"][0]
    assert len(rep_st1["results"]) == 1
    assert rep_st1["results"][0].get("id") == id_a
    assert rep_st1["results"][0].get("code") == code_a

    rep_st2 = res2["results"][1]
    assert rep_st2["meta"]["changes"] == 1

    rep_st3 = res2["results"][2]
    assert rep_st3["results"][0]["total"] == 2

    # Clean up
    client.batch([
        {"sql": "DELETE FROM departments_department WHERE code IN (?, ?)", "params": [code_a, code_b]}
    ], idempotency_key=f"clean-batch-{uuid.uuid4()}")


def test_batch_fault_injection_crash_after_commit_and_replay(bridge_credentials):
    """
    Test: Batch fault injection right between commit and response completion.
    Scenario:
    1. Batch commits in D1, then crashes via X-Test-Fault-Injection: crash-after-commit.
    2. Client receives HTTP 500 on initial call.
    3. Client replays with same key and receives D1IncompleteBatchError (HTTP 500 INCOMPLETE_BATCH_STATE with committed: true).
       Zero fake numbers or mock results shells returned.
    4. Database rows committed exactly once.
    """
    url, token = bridge_credentials
    client = get_client(url, token)

    idem_key = f"test-batch-flt-{uuid.uuid4()}"
    code_x = f"T-BX-{uuid.uuid4().hex[:6].upper()}"

    statements = [
        {"sql": "INSERT INTO departments_department (code, name) VALUES (?, ?) RETURNING id, code", "params": [code_x, "Dept Batch Fault"]},
        {"sql": "SELECT count(*) as cnt FROM departments_department WHERE code = ?", "params": [code_x]},
    ]

    # Step 1: Send batch with fault injection header and retries=1 -> must fail with OperationalError (500)
    with pytest.raises(OperationalError) as exc_info1:
        client.batch(
            statements,
            idempotency_key=idem_key,
            custom_headers={"X-Test-Fault-Injection": "crash-after-commit"},
            retries=1,
        )
    assert "Fault Injected" in str(exc_info1.value) or "500" in str(exc_info1.value)

    # Step 2: Client retries normal batch request with same key
    # Replay must receive D1IncompleteBatchError with code INCOMPLETE_BATCH_STATE and committed: True,
    # confirming transaction committed and absolutely ZERO fake numbers or results returned!
    with pytest.raises(D1IncompleteBatchError) as exc_info2:
        client.batch(statements, idempotency_key=idem_key, retries=1)

    assert exc_info2.value.code == "INCOMPLETE_BATCH_STATE"
    assert exc_info2.value.committed is True
    assert exc_info2.value.idempotency_key == idem_key

    # Step 3: Check database: record count MUST BE EXACTLY 1!
    check = client.query("SELECT COUNT(*) as cnt FROM departments_department WHERE code = ?", [code_x])
    assert check["results"][0]["cnt"] == 1

    # Clean up
    client.query("DELETE FROM departments_department WHERE code = ?", [code_x], idempotency_key=f"clean-post-{uuid.uuid4()}")


def test_idempotency_same_key_different_payload_conflict(bridge_credentials):
    """
    Test: Same Idempotency-Key with DIFFERENT payload must be rejected with HTTP 422 Conflict.
    """
    url, token = bridge_credentials
    client = get_client(url, token)

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
    client.query("DELETE FROM departments_department WHERE code IN (?, ?)", [test_code_1, test_code_2], idempotency_key=f"clean-post-{uuid.uuid4()}")


def test_concurrent_requests_with_same_idempotency_key(bridge_credentials):
    """
    Test: Two concurrent requests with the SAME Idempotency-Key.
    Expected: Both requests succeed according to protocol (one original, one replay),
    both return valid meta.changes == 1, and database is written EXACTLY ONCE.
    """
    url, token = bridge_credentials
    client1 = get_client(url, token)
    client2 = get_client(url, token)

    test_code = f"T-CONC-{uuid.uuid4().hex[:6].upper()}"
    client1.query(
        "INSERT INTO departments_department (code, name, description) VALUES (?, ?, ?)",
        [test_code, "Phòng Đồng Thời", "Khởi tạo"],
        idempotency_key=f"setup-conc-{uuid.uuid4()}",
    )

    idem_key = f"test-concurrent-{uuid.uuid4()}"
    update_sql = "UPDATE departments_department SET description = ? WHERE code = ?"
    params = ["Cập nhật đồng thời", test_code]

    def make_call(client_instance):
        try:
            return client_instance.query(update_sql, params, idempotency_key=idem_key)
        except Exception as e:
            return e

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(make_call, client1)
        f2 = executor.submit(make_call, client2)
        res_list = [f1.result(), f2.result()]

    # Protocol Requirement: BOTH requests must succeed with valid response (one original, one replay)
    for r in res_list:
        assert isinstance(r, dict), f"Request failed with error: {r}"
        assert r.get("success") is True
        assert r.get("meta", {}).get("changes") == 1
        assert isinstance(r.get("results"), list)

    # Database state: verify row exists and description was updated
    check = client1.query("SELECT COUNT(*) as cnt, description FROM departments_department WHERE code = ?", [test_code])
    assert check["results"][0]["cnt"] == 1
    assert check["results"][0]["description"] == "Cập nhật đồng thời"

    # Clean up
    client1.query("DELETE FROM departments_department WHERE code = ?", [test_code], idempotency_key=f"clean-conc-{uuid.uuid4()}")
