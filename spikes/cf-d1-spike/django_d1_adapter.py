"""
AssetHub D1 Adapter & Verification Suite for Django Container
Uses Python standard library (urllib.request, json) - Zero extra dependencies (Ponytail style).
Tests Django in container communicating with Cloudflare D1 via authenticated Worker Bridge.
"""
import os
import sys
import time
import json
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor

import django
from django.apps import apps
if not apps.ready:
    django.setup()

# Cloudflare D1 Bridge Configuration
BRIDGE_URL = os.environ.get("D1_BRIDGE_URL", "")
BRIDGE_TOKEN = os.environ.get("D1_BRIDGE_TOKEN", "")
if not BRIDGE_TOKEN or not BRIDGE_URL:
    env_test = os.path.join(os.path.dirname(__file__), ".env.test")
    if os.path.exists(env_test):
        with open(env_test) as f:
            for line in f:
                line = line.strip()
                if line.startswith("D1_BRIDGE_URL=") and not BRIDGE_URL:
                    BRIDGE_URL = line.split("=", 1)[1].strip()
                elif line.startswith("D1_BRIDGE_TOKEN=") and not BRIDGE_TOKEN:
                    BRIDGE_TOKEN = line.split("=", 1)[1].strip()


class D1Client:
    def __init__(self, base_url=BRIDGE_URL, token=BRIDGE_TOKEN, timeout=10.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _headers(self):
        return {
            "Content-Type": "application/json",
            "X-Bridge-Token": self.token,
            "User-Agent": "AssetHub-Backend-Container/1.0",
        }

    def _post(self, path, payload, retries=3):
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self._headers(), method="POST")
        
        last_err = None
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw)
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8")
                # 401 Unauthorized or 409 Conflict should not be retried
                if e.code in (401, 400, 409):
                    raise RuntimeError(f"D1 HTTP {e.code}: {err_body}")
                last_err = RuntimeError(f"D1 HTTP {e.code}: {err_body}")
                time.sleep(0.2 * (attempt + 1))
            except Exception as e:
                last_err = e
                time.sleep(0.2 * (attempt + 1))
        raise last_err

    def execute(self, sql, params=None):
        if params is None:
            params = []
        payload = {"sql": sql, "params": params}
        data = self._post("/api/v1/query", payload)
        if not data.get("success"):
            raise RuntimeError(f"D1 Execution Failed: {data.get('error')}")
        return data

    def batch(self, statements):
        formatted = []
        for st in statements:
            if isinstance(st, (tuple, list)):
                formatted.append({"sql": st[0], "params": list(st[1]) if len(st) > 1 else []})
            elif isinstance(st, dict):
                formatted.append({"sql": st["sql"], "params": st.get("params", [])})
            elif isinstance(st, str):
                formatted.append({"sql": st, "params": []})

        data = self._post("/api/v1/batch", {"statements": formatted})
        if not data.get("success"):
            raise RuntimeError(f"D1 Batch Failed: {data.get('error')}")
        return data["results"]


def run_full_integration_tests():
    print("================================================================")
    print("AssetHub Django Container -> Cloudflare D1 Integration Suite")
    print("================================================================")
    client = D1Client()

    # -------------------------------------------------------------------------
    # TEST 1: Connectivity & Inter-Service Security
    # -------------------------------------------------------------------------
    print("\n[TEST 1] Testing Inter-service Authentication...")
    bad_client = D1Client(token="invalid-token-xyz")
    try:
        bad_client.execute("SELECT 1")
        print("  FAIL: Bad token was accepted!")
        return False
    except RuntimeError as e:
        assert "401" in str(e), f"Expected 401 error, got: {e}"
        print("  PASS: Invalid token correctly rejected with 401 Unauthorized.")

    res = client.execute("SELECT 1 as num")
    assert res["results"][0]["num"] == 1
    print("  PASS: Authenticated D1 query succeeded.")

    # -------------------------------------------------------------------------
    # TEST 2: Schema Reset & Seed for Integration Test
    # -------------------------------------------------------------------------
    print("\n[TEST 2] Seeding Test Data (User with 1,000,000-iteration PBKDF2 hash)...")
    client.batch([
        "DELETE FROM test_audit_logs",
        "DELETE FROM test_requests",
        "DELETE FROM test_assets",
        "DELETE FROM test_users",
    ])

    from django.contrib.auth.hashers import make_password, check_password, identify_hasher
    test_password = "AssetHub#Spike2026"
    t_hash_start = time.perf_counter()
    hashed_pw = make_password(test_password)
    t_hash_duration_ms = (time.perf_counter() - t_hash_start) * 1000

    hasher_info = identify_hasher(hashed_pw)
    print(f"  Generated hash in container: {hasher_info.algorithm} with {hasher_info.iterations} iterations")
    print(f"  Container hashing time (wall-clock): {t_hash_duration_ms:.2f} ms")
    assert hasher_info.iterations == 1000000, "Iterations must be 1,000,000!"

    client.batch([
        ("INSERT INTO test_users (id, username, email, password, role, is_active) VALUES (?, ?, ?, ?, ?, ?)",
         [1, "admin_user", "admin@test.local", hashed_pw, "ADMIN", 1]),
        ("INSERT INTO test_users (id, username, email, password, role, is_active) VALUES (?, ?, ?, ?, ?, ?)",
         [2, "staff_user", "staff@test.local", hashed_pw, "USER", 1]),
    ])
    print("  PASS: Test users seeded successfully into Cloudflare D1.")

    # -------------------------------------------------------------------------
    # TEST 3: Login Verification with D1 User & JWT Full Authentication
    # -------------------------------------------------------------------------
    print("\n[TEST 3] Testing Authentication Flow (Django Container + D1 DB)...")
    res = client.execute("SELECT * FROM test_users WHERE username = ?", ["staff_user"])
    assert len(res["results"]) == 1, "User not found in D1!"
    db_user = res["results"][0]

    t_verify_start = time.perf_counter()
    is_valid = check_password(test_password, db_user["password"])
    t_verify_duration_ms = (time.perf_counter() - t_verify_start) * 1000
    assert is_valid is True, "Password check failed!"
    print(f"  PASS: Password verification against D1 stored hash succeeded ({t_verify_duration_ms:.2f} ms).")

    from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
    class ContainerUser:
        pk = db_user["id"]
        id = db_user["id"]
        username = db_user["username"]
        role = db_user["role"]
        is_active = bool(db_user["is_active"])
        auth_version = db_user["auth_version"]

    refresh = RefreshToken.for_user(ContainerUser())
    refresh["role"] = ContainerUser.role
    refresh["auth_version"] = ContainerUser.auth_version
    access_token_str = str(refresh.access_token)

    decoded = AccessToken(access_token_str)
    assert str(decoded["user_id"]) == "2"
    assert decoded["role"] == "USER"
    assert decoded["auth_version"] == 1
    print("  PASS: JWT issued and validated with custom claims (role=USER, auth_version=1).")

    # -------------------------------------------------------------------------
    # TEST 4: CRUD Operations through Django Client on Real D1 Data
    # -------------------------------------------------------------------------
    print("\n[TEST 4] Testing CRUD Operations on D1...")
    client.execute(
        "INSERT INTO test_assets (id, code, name, status, assigned_to_id, version) VALUES (?, ?, ?, ?, NULL, 1)",
        [10, "AST-TEST-01", "MacBook Pro 16", "AVAILABLE"]
    )
    asset = client.execute("SELECT * FROM test_assets WHERE id = ?", [10])["results"][0]
    assert asset["name"] == "MacBook Pro 16"
    assert asset["status"] == "AVAILABLE"
    print("  PASS: Create and Read asset verified.")

    client.execute("UPDATE test_assets SET name = ? WHERE id = ?", ["MacBook Pro M3 Max", 10])
    updated_asset = client.execute("SELECT name FROM test_assets WHERE id = ?", [10])["results"][0]
    assert updated_asset["name"] == "MacBook Pro M3 Max"
    print("  PASS: Update asset verified.")

    client.execute("DELETE FROM test_assets WHERE id = ?", [10])
    count = client.execute("SELECT COUNT(*) as c FROM test_assets WHERE id = ?", [10])["results"][0]["c"]
    assert count == 0
    print("  PASS: Delete asset verified.")

    # -------------------------------------------------------------------------
    # TEST 5: Atomic Batch Rollback on Error
    # -------------------------------------------------------------------------
    print("\n[TEST 5] Testing Atomic Batch Rollback...")
    client.execute("INSERT INTO test_assets (id, code, name, status, assigned_to_id, version) VALUES (1, 'AST-01', 'Monitor Dell', 'AVAILABLE', NULL, 1)")

    try:
        client.batch([
            ("UPDATE test_assets SET status = 'ALLOCATED', assigned_to_id = 2 WHERE id = 1", []),
            ("INSERT INTO test_audit_logs (asset_id, action, performed_by_id) VALUES (1, 'ALLOCATE', 999999)", [])
        ])
        print("  FAIL: Batch should have thrown FK error!")
        return False
    except RuntimeError as e:
        print(f"  PASS: Batch error caught properly: {e}")

    asset_after = client.execute("SELECT status, assigned_to_id FROM test_assets WHERE id = 1")["results"][0]
    assert asset_after["status"] == "AVAILABLE"
    assert asset_after["assigned_to_id"] is None
    print("  PASS: Step 1 was completely rolled back! Status remains AVAILABLE.")

    # -------------------------------------------------------------------------
    # TEST 6: Guarded Mutation on UPDATE 0 Rows (Prevent Orphan Audit Log)
    # -------------------------------------------------------------------------
    print("\n[TEST 6] Testing UPDATE 0 Rows Guard (Prevent Orphan Audit Logs)...")
    try:
        client.batch([
            ("UPDATE test_assets SET status = 'ALLOCATED', assigned_to_id = 2 WHERE id = 1 AND status = 'MAINTENANCE'", []),
            ("INSERT INTO _assert_guard (rows_affected) VALUES (changes())", []),
            ("INSERT INTO test_audit_logs (asset_id, action, performed_by_id) VALUES (1, 'ALLOCATE', 2)", [])
        ])
        print("  FAIL: Guard should have aborted batch because UPDATE affected 0 rows!")
        return False
    except RuntimeError as e:
        assert "CHECK constraint failed: rows_affected > 0" in str(e)
        print("  PASS: Guard caught UPDATE 0 rows and raised CHECK constraint failure.")

    audit_count = client.execute("SELECT COUNT(*) as c FROM test_audit_logs WHERE asset_id = 1")["results"][0]["c"]
    assert audit_count == 0, f"Expected 0 audit logs, found {audit_count}!"
    print("  PASS: Zero orphan audit logs created on 0-row update.")

    # -------------------------------------------------------------------------
    # TEST 7: High Concurrency Race Condition (2 Requests Racing for Same Asset)
    # -------------------------------------------------------------------------
    print("\n[TEST 7] Testing Concurrency (2 Concurrent Requests Allocating Asset 1)...")
    def try_allocate(user_id):
        c = D1Client()
        try:
            c.batch([
                ("UPDATE test_assets SET status = 'ALLOCATED', assigned_to_id = ?, version = version + 1 WHERE id = 1 AND status = 'AVAILABLE'", [user_id]),
                ("INSERT INTO _assert_guard (rows_affected) VALUES (changes())", []),
                ("INSERT INTO test_audit_logs (asset_id, action, performed_by_id) VALUES (1, 'ALLOCATE', ?)", [user_id]),
            ])
            return ("SUCCESS", user_id)
        except RuntimeError as e:
            return ("CONFLICT", user_id, str(e))

    with ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(try_allocate, 1)
        f2 = executor.submit(try_allocate, 2)
        res1 = f1.result()
        res2 = f2.result()

    print(f"  Worker Thread 1 Result: {res1[0]}")
    print(f"  Worker Thread 2 Result: {res2[0]}")

    results = [res1[0], res2[0]]
    assert results.count("SUCCESS") == 1, "Exactly ONE request must succeed!"
    assert results.count("CONFLICT") == 1, "Exactly ONE request must fail with CONFLICT!"

    winner = res1[1] if res1[0] == "SUCCESS" else res2[1]
    asset_final = client.execute("SELECT status, assigned_to_id, version FROM test_assets WHERE id = 1")["results"][0]
    assert asset_final["status"] == "ALLOCATED"
    assert asset_final["assigned_to_id"] == winner
    assert asset_final["version"] == 2

    audit_logs = client.execute("SELECT * FROM test_audit_logs WHERE asset_id = 1")["results"]
    assert len(audit_logs) == 1, "Exactly 1 audit log must exist!"
    assert audit_logs[0]["performed_by_id"] == winner
    print(f"  PASS: Race condition resolved safely. Winner: User {winner}. Zero duplicate allocation, exactly 1 audit log.")

    # -------------------------------------------------------------------------
    # TEST 8: Trigger Protection (Protect Last Admin)
    # -------------------------------------------------------------------------
    print("\n[TEST 8] Testing Database-Level Protection (Trigger Protects Last Admin)...")
    try:
        client.execute("UPDATE test_users SET is_active = 0 WHERE id = 1")
        print("  FAIL: Should not be able to deactivate the last admin!")
        return False
    except RuntimeError as e:
        assert "Cannot deactivate the last active Admin" in str(e)
        print("  PASS: SQLite trigger in D1 blocked deactivation of the last active Admin.")

    print("\n================================================================")
    print("ALL 8 INTEGRATION TESTS PASSED 100% ON REAL CLOUDFLARE D1!")
    print("================================================================")
    return True


if __name__ == "__main__":
    success = run_full_integration_tests()
    sys.exit(0 if success else 1)
