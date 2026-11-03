import time
import json
import os
from workers import WorkerEntrypoint, Response

# Setup minimal Django settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "spike_settings")

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        SECRET_KEY="spike-insecure-secret-key-cf-workers-free-tier-2026",
        DEBUG=False,
        PASSWORD_HASHERS=[
            "django.contrib.auth.hashers.PBKDF2PasswordHasher",
        ],
        INSTALLED_APPS=[
            "django.contrib.auth",
            "django.contrib.contenttypes",
            "rest_framework",
        ],
    )
    django.setup()

from django.contrib.auth.hashers import make_password, check_password, identify_hasher
from rest_framework_simplejwt.tokens import RefreshToken


# Pre-computed PBKDF2 hash with 1,000,000 iterations for password 'SpikeTestPass#2026'
# Generated with Django 5.2.18 PBKDF2PasswordHasher
SPIKE_PASSWORD_HASH = "pbkdf2_sha256$1000000$2bjb5xLEmTnPub2DmzZq09$xuwNOU2VCmQyYjoZnDis17pEHi8NY/7Sl58EhIHdG6k="


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = request.url
        path = "/" + "/".join(url.split("/")[3:]).split("?")[0]
        method = request.method

        start_wall = time.perf_counter()
        start_cpu = time.perf_counter()

        try:
            if path == "/health" or path == "":
                return Response(json.dumps({
                    "status": "ok",
                    "django_version": django.__version__,
                    "hasher": identify_hasher(SPIKE_PASSWORD_HASH).algorithm,
                    "iterations": identify_hasher(SPIKE_PASSWORD_HASH).iterations,
                }), headers={"Content-Type": "application/json"})

            elif path == "/api/auth/login":
                # Test login with PBKDF2 password check
                body = {}
                if method == "POST":
                    try:
                        raw = await request.text()
                        body = json.loads(raw)
                    except:
                        pass
                password = body.get("password", "SpikeTestPass#2026")
                is_valid = check_password(password, SPIKE_PASSWORD_HASH)

                cpu_ms = (time.perf_counter() - start_cpu) * 1000
                wall_ms = (time.perf_counter() - start_wall) * 1000

                return Response(json.dumps({
                    "status": "success" if is_valid else "unauthorized",
                    "valid": is_valid,
                    "cpu_time_ms": round(cpu_ms, 2),
                    "wall_time_ms": round(wall_ms, 2),
                    "hasher_iterations": identify_hasher(SPIKE_PASSWORD_HASH).iterations,
                }), headers={"Content-Type": "application/json"})

            elif path == "/api/auth/register":
                # Test registration with PBKDF2 password generation
                body = {}
                if method == "POST":
                    try:
                        raw = await request.text()
                        body = json.loads(raw)
                    except:
                        pass
                new_pass = body.get("password", "NewUserSpikePass#2026")
                new_hash = make_password(new_pass)

                cpu_ms = (time.perf_counter() - start_cpu) * 1000
                wall_ms = (time.perf_counter() - start_wall) * 1000

                return Response(json.dumps({
                    "status": "created",
                    "hash_prefix": new_hash[:40] + "...",
                    "cpu_time_ms": round(cpu_ms, 2),
                    "wall_time_ms": round(wall_ms, 2),
                    "hasher_iterations": identify_hasher(new_hash).iterations,
                }), headers={"Content-Type": "application/json"})

            elif path == "/api/test/jwt":
                # Test SimpleJWT generation and token payload
                class FakeUser:
                    pk = 42
                    id = 42
                    username = "spike_user"
                    is_active = True
                
                refresh = RefreshToken.for_user(FakeUser())
                access_str = str(refresh.access_token)
                
                cpu_ms = (time.perf_counter() - start_cpu) * 1000
                wall_ms = (time.perf_counter() - start_wall) * 1000

                return Response(json.dumps({
                    "status": "success",
                    "access_token_len": len(access_str),
                    "cpu_time_ms": round(cpu_ms, 2),
                    "wall_time_ms": round(wall_ms, 2),
                }), headers={"Content-Type": "application/json"})

            elif path == "/api/test/d1-batch-rollback":
                # Test batch rollback: statement 1 succeeds, statement 2 fails
                db = self.env.DB
                stmt1 = db.prepare("INSERT INTO test_audit_logs (asset_id, action, performed_by_id) VALUES (?, ?, ?)").bind(1, "TEST_BATCH_1", 1)
                stmt2 = db.prepare("INSERT INTO non_existent_table_xyz VALUES (1)")
                
                error_caught = None
                try:
                    await db.batch([stmt1, stmt2])
                except Exception as e:
                    error_caught = str(e)

                # Check if statement 1 was rolled back
                check = await db.prepare("SELECT COUNT(*) as cnt FROM test_audit_logs WHERE action = 'TEST_BATCH_1'").first()

                return Response(json.dumps({
                    "batch_error": error_caught,
                    "rolled_back": check["cnt"] == 0,
                    "audit_count": check["cnt"],
                }), headers={"Content-Type": "application/json"})

            elif path == "/api/test/d1-trigger-raise":
                # Test RAISE(ABORT, ...) in SQLite trigger: deactivating last admin
                db = self.env.DB
                # Ensure 1 admin exists
                await db.prepare("DELETE FROM test_users").run()
                await db.prepare("INSERT INTO test_users (id, username, email, password, role, is_active) VALUES (1, 'admin1', 'a@a.com', 'h', 'ADMIN', 1)").run()

                error_caught = None
                try:
                    await db.prepare("UPDATE test_users SET is_active = 0 WHERE id = 1").run()
                except Exception as e:
                    error_caught = str(e)

                user = await db.prepare("SELECT is_active FROM test_users WHERE id = 1").first()

                return Response(json.dumps({
                    "trigger_error": error_caught,
                    "admin_deactivated": user["is_active"] == 0,
                    "trigger_protected": "Cannot deactivate the last active Admin" in (error_caught or ""),
                }), headers={"Content-Type": "application/json"})

            elif path == "/api/test/d1-zero-rows-guard":
                # Compare UPDATE 0 rows behavior:
                db = self.env.DB
                # 1. Reset asset 1 to ALLOCATED
                await db.prepare("DELETE FROM test_assets").run()
                await db.prepare("INSERT INTO test_assets (id, code, name, status) VALUES (1, 'AST-01', 'Laptop', 'ALLOCATED')").run()
                await db.prepare("DELETE FROM test_audit_logs").run()

                # Scenario A: Raw batch without guard
                stmt_upd = db.prepare("UPDATE test_assets SET status = 'ALLOCATED' WHERE id = 1 AND status = 'AVAILABLE'")
                stmt_log = db.prepare("INSERT INTO test_audit_logs (asset_id, action, performed_by_id) VALUES (1, 'UNGUARDED_LOG', 1)")
                await db.batch([stmt_upd, stmt_log])
                count_unguarded = await db.prepare("SELECT COUNT(*) as cnt FROM test_audit_logs WHERE action = 'UNGUARDED_LOG'").first()

                # Scenario B: Guarded batch using changes() + CHECK constraint
                stmt_upd_2 = db.prepare("UPDATE test_assets SET status = 'ALLOCATED' WHERE id = 1 AND status = 'AVAILABLE'")
                stmt_guard = db.prepare("INSERT INTO _assert_guard (rows_affected) VALUES (changes())")
                stmt_log_2 = db.prepare("INSERT INTO test_audit_logs (asset_id, action, performed_by_id) VALUES (1, 'GUARDED_LOG', 1)")

                guard_error = None
                try:
                    await db.batch([stmt_upd_2, stmt_guard, stmt_log_2])
                except Exception as e:
                    guard_error = str(e)

                count_guarded = await db.prepare("SELECT COUNT(*) as cnt FROM test_audit_logs WHERE action = 'GUARDED_LOG'").first()

                return Response(json.dumps({
                    "unguarded_audit_written_on_0_rows": count_unguarded["cnt"] > 0,
                    "guarded_error_caught": guard_error,
                    "guarded_audit_written": count_guarded["cnt"] > 0,
                    "guard_prevented_orphan_log": count_guarded["cnt"] == 0,
                }), headers={"Content-Type": "application/json"})

            elif path == "/api/test/d1-reset-asset":
                db = self.env.DB
                await db.prepare("DELETE FROM test_audit_logs").run()
                await db.prepare("DELETE FROM test_assets").run()
                await db.prepare("INSERT INTO test_assets (id, code, name, status, assigned_to_id, version) VALUES (1, 'AST-01', 'Dell XPS', 'AVAILABLE', NULL, 1)").run()
                return Response(json.dumps({"status": "asset reset to AVAILABLE"}), headers={"Content-Type": "application/json"})

            elif path == "/api/test/d1-allocate":
                db = self.env.DB
                body = {}
                if method == "POST":
                    try:
                        raw = await request.text()
                        body = json.loads(raw)
                    except:
                        pass
                user_id = body.get("user_id", 1)

                stmt_upd = db.prepare("UPDATE test_assets SET status = 'ALLOCATED', assigned_to_id = ?, version = version + 1 WHERE id = 1 AND status = 'AVAILABLE'").bind(user_id)
                stmt_guard = db.prepare("INSERT INTO _assert_guard (rows_affected) VALUES (changes())")
                stmt_log = db.prepare("INSERT INTO test_audit_logs (asset_id, action, performed_by_id) VALUES (1, 'ALLOCATE', ?)").bind(user_id)

                try:
                    await db.batch([stmt_upd, stmt_guard, stmt_log])
                    return Response(json.dumps({"status": "success", "user_id": user_id}), headers={"Content-Type": "application/json"})
                except Exception as e:
                    return Response(json.dumps({"status": "failed", "error": str(e), "user_id": user_id}), status=409, headers={"Content-Type": "application/json"})

            return Response(json.dumps({"error": "Not Found"}), status=404, headers={"Content-Type": "application/json"})

        except Exception as e:
            import traceback
            return Response(json.dumps({
                "error": str(e),
                "traceback": traceback.format_exc(),
            }), status=500, headers={"Content-Type": "application/json"})
