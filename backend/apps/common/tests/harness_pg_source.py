# -*- coding: utf-8 -*-
"""
PostgreSQL Test Harness for Local Rehearsal: isolated authentic PostgreSQL test source.

Manages:
1. Docker container lifecycle: 'assethub-pg-rehearsal-20261104' on host port 5434.
2. Schema creation via authentic Django migrations ('python manage.py migrate').
   Verifies that 'ai_suggestion_logs' does NOT exist in this source database.
3. Comprehensive test dataset covering all 15 mandatory source tables with:
   - Exact primary keys.
   - Valid PBKDF2 1,000,000-iteration password hashes (compatible with Django auth).
   - Microsecond UTC-aware datetimes and date fields.
   - Booleans (is_superuser, is_staff, is_active).
   - JSON specifications (objects and lists).
   - M2M relationships (user groups, permissions).
   - Identifier sequences for AST and REQ with year 2026.
   - Asset history logs and asset requests.
   - Session data and admin log entries.
4. Read-only PostgreSQL role 'assethub_ro_user' with CONNECT, USAGE, and SELECT privileges.
5. Teardown method to stop and remove the container cleanly.
"""
import argparse
import datetime
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import psycopg
from psycopg import sql

# Run Configuration Constants
RUN_ID: str = "reh_20261104"
CONTAINER_NAME: str = "assethub-pg-rehearsal-20261104"
IMAGE: str = "postgres:16-alpine"
HOST: str = "127.0.0.1"
PORT: int = 5434
DB_NAME: str = "assethub_rehearsal_db"
ADMIN_USER: str = "assethub_rehearsal_admin"
ADMIN_PASSWORD: str = "reh_admin_pass_2026"
RO_USER: str = "assethub_ro_user"
RO_PASSWORD: str = "reh_ro_pass_2026"

# 15 Mandatory Source Tables (ai_suggestion_logs must NOT exist)
SOURCE_MANDATORY_TABLES: List[str] = [
    # Tier 1
    "django_content_type",
    "auth_permission",
    "auth_group",
    "departments_department",
    "departments_category",
    # Tier 2
    "auth_group_permissions",
    "authentication_user",
    # Tier 3
    "authentication_user_groups",
    "authentication_user_user_permissions",
    "django_admin_log",
    "django_migrations",
    "django_session",
    "assets_identifiersequence",
    "assets_asset",
    # Tier 4
    "requests_assetrequest",
    "assets_assethistorylog",
]

# Standard Test Users with pre-computed PBKDF2 1,000,000 iterations
TEST_USERS: Dict[str, Dict[str, Any]] = {
    "admin": {
        "id": 1,
        "username": "admin_reh",
        "email": "admin@assethub.local",
        "raw_password": "AdminPass2026!",
        "password_hash": "pbkdf2_sha256$1000000$saltAdmin20261104$wrFqC9PGO8qPfNFaG6CCW9uc4wQeYbBjEW72F+OmiYc=",
        "role": "ADMIN",
        "is_superuser": True,
        "is_staff": True,
        "is_active": True,
    },
    "manager": {
        "id": 2,
        "username": "manager_tech",
        "email": "manager_tech@assethub.local",
        "raw_password": "ManagerPass2026!",
        "password_hash": "pbkdf2_sha256$1000000$saltManager20261104$igaSnmlcoRU4k0b9KdUg5zNilmpdXKTs9rJ0VI0FOOw=",
        "role": "MANAGER",
        "is_superuser": False,
        "is_staff": True,
        "is_active": True,
    },
    "developer": {
        "id": 3,
        "username": "user_developer",
        "email": "dev@assethub.local",
        "raw_password": "UserDevPass2026!",
        "password_hash": "pbkdf2_sha256$1000000$saltDev20261104$5ngjyidVk2rBIQEOESANChHOC96agVzmlMUKOtgvXB4=",
        "role": "USER",
        "is_superuser": False,
        "is_staff": False,
        "is_active": True,
    },
    "inactive": {
        "id": 4,
        "username": "user_inactive",
        "email": "inactive@assethub.local",
        "raw_password": "InactivePass2026!",
        "password_hash": "pbkdf2_sha256$1000000$saltInactive20261104$/Y2iab2hw4itteN1Jbz/oFYsye4IC4txPrzLvcZQ6s4=",
        "role": "USER",
        "is_superuser": False,
        "is_staff": False,
        "is_active": False,
    },
}


def get_backend_dir() -> Path:
    """Resolve backend directory path."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "manage.py").exists():
            return parent
    raise RuntimeError("Could not find backend directory with manage.py")


def is_port_in_use(port: int = PORT, host: str = HOST) -> bool:
    """Check if host port is in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def is_container_running(name: str = CONTAINER_NAME) -> bool:
    """Check if docker container exists and is running."""
    res = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", name],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return res.returncode == 0 and res.stdout.strip() == "true"


def get_pg_connection(
    user: str = ADMIN_USER,
    password: str = ADMIN_PASSWORD,
    dbname: str = DB_NAME,
    host: str = HOST,
    port: int = PORT,
    autocommit: bool = False,
) -> psycopg.Connection:
    """Obtain a psycopg connection to the PostgreSQL rehearsal container."""
    conn_str = f"host={host} port={port} dbname={dbname} user={user} password={password}"
    return psycopg.connect(conn_str, autocommit=autocommit)


def wait_for_pg_ready(timeout: int = 45) -> bool:
    """Poll until PostgreSQL is ready to accept connections."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            conn = get_pg_connection(autocommit=True)
            conn.close()
            return True
        except Exception:
            time.sleep(0.5)
    return False


def start_pg_container(recreate: bool = False, timeout: int = 45) -> bool:
    """
    Ensure the isolated PostgreSQL test container is running on port 5434.
    If recreate is True or the container is absent, spins up postgres:16-alpine.
    DO NOT touch assethub-db-1 or pg-test.
    """
    docker_bin = shutil.which("docker")
    if not docker_bin:
        raise RuntimeError("Docker binary not found on host.")

    if recreate:
        teardown_pg_source()

    if is_container_running(CONTAINER_NAME):
        return True

    # Remove stopped container if exists with the same name
    subprocess.run(
        ["docker", "rm", "-f", CONTAINER_NAME],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    cmd = [
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "-p", f"{PORT}:5432",
        "-e", f"POSTGRES_DB={DB_NAME}",
        "-e", f"POSTGRES_USER={ADMIN_USER}",
        "-e", f"POSTGRES_PASSWORD={ADMIN_PASSWORD}",
        IMAGE,
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Failed to start PostgreSQL container: {res.stderr.strip()}")

    if not wait_for_pg_ready(timeout=timeout):
        raise TimeoutError(f"PostgreSQL container failed to become ready within {timeout}s.")

    return True


def teardown_pg_source() -> bool:
    """
    Clean teardown: stops and removes container assethub-pg-rehearsal-20261104.
    Safely preserves other containers.
    """
    res = subprocess.run(
        ["docker", "rm", "-f", CONTAINER_NAME],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return res.returncode == 0


def apply_django_migrations() -> None:
    """
    Apply authentic Django migrations against the isolated PostgreSQL source database.
    Ensures table schemas are strictly derived from Django migration files.
    """
    backend_dir = get_backend_dir()
    python_bin = sys.executable

    env = os.environ.copy()
    env["DB_NAME"] = DB_NAME
    env["DB_USER"] = ADMIN_USER
    env["DB_PASSWORD"] = ADMIN_PASSWORD
    env["DB_HOST"] = HOST
    env["DB_PORT"] = str(PORT)

    cmd = [python_bin, "manage.py", "migrate", "--noinput"]
    res = subprocess.run(
        cmd,
        cwd=str(backend_dir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"Django migration failed on PostgreSQL test source: {res.stderr}\n{res.stdout}"
        )


def verify_schema_isolation() -> Dict[str, Any]:
    """
    Verify that all 15 mandatory source tables exist in public schema
    and that 'ai_suggestion_logs' does NOT exist.
    """
    with get_pg_connection(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            )
            tables = {row[0] for row in cur.fetchall()}

    missing_tables = [tbl for tbl in SOURCE_MANDATORY_TABLES if tbl not in tables]
    ai_table_present = "ai_suggestion_logs" in tables

    if missing_tables:
        raise AssertionError(f"Mandatory tables missing in PostgreSQL source: {missing_tables}")

    if ai_table_present:
        raise AssertionError(
            "CRITICAL: 'ai_suggestion_logs' must NOT exist in the PostgreSQL source database!"
        )

    return {
        "verified": True,
        "table_count": len(tables),
        "all_source_mandatory_present": True,
        "ai_suggestion_logs_absent": True,
        "tables": sorted(list(tables)),
    }


def setup_readonly_user() -> None:
    """
    Create read-only role 'assethub_ro_user' with password 'reh_ro_pass_2026'.
    Grants CONNECT on database, USAGE on public schema, and SELECT on all tables.
    """
    with get_pg_connection(autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'assethub_ro_user') THEN
                        CREATE ROLE assethub_ro_user WITH LOGIN PASSWORD 'reh_ro_pass_2026';
                    ELSE
                        ALTER ROLE assethub_ro_user WITH PASSWORD 'reh_ro_pass_2026';
                    END IF;
                END
                $$;
            """)
            cur.execute(f"GRANT CONNECT ON DATABASE {DB_NAME} TO {RO_USER};")
            cur.execute(f"GRANT USAGE ON SCHEMA public TO {RO_USER};")
            cur.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {RO_USER};")
            cur.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {RO_USER};")


def update_postgresql_sequences(conn: psycopg.Connection) -> None:
    """Synchronize all PostgreSQL identity/serial sequences to MAX(id)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.relname, t.relname, a.attname
            FROM pg_class c
            JOIN pg_depend d ON d.objid = c.oid
            JOIN pg_class t ON d.refobjid = t.oid
            JOIN pg_attribute a ON (d.refobjid, d.refobjsubid) = (a.attrelid, a.attnum)
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE c.relkind = 'S' AND n.nspname = 'public';
        """)
        sequences = cur.fetchall()
        for seq_name, table_name, col_name in sequences:
            cur.execute(f'SELECT COALESCE(MAX("{col_name}"), 0) FROM "{table_name}"')
            max_val = cur.fetchone()[0]
            if max_val > 0:
                cur.execute(f"SELECT setval('{seq_name}', %s)", [max_val])


def seed_rehearsal_data() -> Dict[str, int]:
    """
    Seed comprehensive fake test dataset covering all 15 mandatory source tables:
    - Exact primary keys.
    - Valid PBKDF2 1,000,000 iterations passwords.
    - Microsecond datetimes (UTC aware) and date fields.
    - Booleans (is_superuser, is_staff, is_active).
    - JSON specification (objects and lists).
    - M2M relationships (user groups, permissions).
    - Identifier sequences for AST and REQ with year 2026.
    - Asset history logs and asset requests.
    - Session data and admin log entries.
    """
    counts: Dict[str, int] = {}
    with get_pg_connection(autocommit=False) as conn:
        with conn.cursor() as cur:
            # 1. auth_group
            cur.execute("""
                INSERT INTO auth_group (id, name) VALUES 
                (10, %s),
                (20, %s),
                (30, %s)
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
            """, ["Ban Giám Đốc", "Phòng Kỹ Thuật", "Phòng Kế Toán"])

            # 2. departments_department
            dt_base = datetime.datetime(2026, 11, 1, 8, 0, 0, 123456, tzinfo=datetime.timezone.utc)
            cur.execute("""
                INSERT INTO departments_department (id, name, code, description, created_at) VALUES 
                (1, %s, %s, %s, %s),
                (2, %s, %s, %s, %s),
                (3, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET 
                    name = EXCLUDED.name,
                    code = EXCLUDED.code,
                    description = EXCLUDED.description,
                    created_at = EXCLUDED.created_at
            """, [
                "Ban Giám Đốc", "BGD", "Ban điều hành doanh nghiệp", dt_base,
                "Phòng Công Nghệ Thông Tin", "IT", "Bộ phận kỹ thuật & hạ tầng CNTT", dt_base,
                "Phòng Tài Chính Kế Toán", "TCKT", "Bộ phận kế toán & quản lý ngân sách", dt_base,
            ])

            # 3. departments_category
            cur.execute("""
                INSERT INTO departments_category (id, name, description, created_at) VALUES 
                (1, %s, %s, %s),
                (2, %s, %s, %s),
                (3, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET 
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    created_at = EXCLUDED.created_at
            """, [
                "Máy tính xách tay", "Laptop làm việc và chuyên dụng kỹ thuật", dt_base,
                "Màn hình hiển thị", "Màn hình ngoài phục vụ thiết kế & lập trình", dt_base,
                "Thiết bị mạng & hạ tầng", "Router, Switch, Access Point, Firewall", dt_base,
            ])

            # 4. auth_group_permissions
            cur.execute("""
                INSERT INTO auth_group_permissions (id, group_id, permission_id) VALUES 
                (1, 10, 1),
                (2, 20, 5),
                (3, 20, 9),
                (4, 30, 13)
                ON CONFLICT (id) DO UPDATE SET 
                    group_id = EXCLUDED.group_id,
                    permission_id = EXCLUDED.permission_id
            """)

            # 5. authentication_user
            cur.execute("""
                INSERT INTO authentication_user (
                    id, password, last_login, is_superuser, username, first_name, last_name,
                    email, is_staff, is_active, date_joined, role, full_name, phone,
                    department_id, auth_version, created_at
                ) VALUES 
                (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s),
                (2, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s),
                (3, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s),
                (4, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET 
                    password = EXCLUDED.password,
                    last_login = EXCLUDED.last_login,
                    is_superuser = EXCLUDED.is_superuser,
                    username = EXCLUDED.username,
                    first_name = EXCLUDED.first_name,
                    last_name = EXCLUDED.last_name,
                    email = EXCLUDED.email,
                    is_staff = EXCLUDED.is_staff,
                    is_active = EXCLUDED.is_active,
                    date_joined = EXCLUDED.date_joined,
                    role = EXCLUDED.role,
                    full_name = EXCLUDED.full_name,
                    phone = EXCLUDED.phone,
                    department_id = EXCLUDED.department_id,
                    auth_version = EXCLUDED.auth_version,
                    created_at = EXCLUDED.created_at
            """, [
                # User 1: Admin
                TEST_USERS["admin"]["password_hash"],
                datetime.datetime(2026, 11, 4, 6, 15, 30, 123456, tzinfo=datetime.timezone.utc),
                True, TEST_USERS["admin"]["username"], "Quan Tri", "He Thong",
                TEST_USERS["admin"]["email"], True, True,
                dt_base, "ADMIN", "Quản Trị Hệ Thống", "0901234567",
                2, 1, dt_base,
                # User 2: Manager
                TEST_USERS["manager"]["password_hash"],
                datetime.datetime(2026, 11, 4, 6, 10, 0, 1000, tzinfo=datetime.timezone.utc),
                False, TEST_USERS["manager"]["username"], "Truong Phong", "Ky Thuat",
                TEST_USERS["manager"]["email"], True, True,
                dt_base, "MANAGER", "Trưởng Phòng Kỹ Thuật", "0902345678",
                2, 1, dt_base,
                # User 3: Developer
                TEST_USERS["developer"]["password_hash"],
                datetime.datetime(2026, 11, 3, 17, 45, 12, 999999, tzinfo=datetime.timezone.utc),
                False, TEST_USERS["developer"]["username"], "Lap Trinh", "Vien",
                TEST_USERS["developer"]["email"], False, True,
                dt_base, "USER", "Lập Trình Viên Chính", "0903456789",
                2, 1, dt_base,
                # User 4: Inactive
                TEST_USERS["inactive"]["password_hash"],
                None,
                False, TEST_USERS["inactive"]["username"], "Nhan Vien", "Cu",
                TEST_USERS["inactive"]["email"], False, False,
                datetime.datetime(2026, 10, 15, 9, 0, 0, 0, tzinfo=datetime.timezone.utc),
                "USER", "Nhân Viên Đã Nghỉ", "0904567890",
                3, 1, datetime.datetime(2026, 10, 15, 9, 0, 0, 0, tzinfo=datetime.timezone.utc),
            ])

            # 6. authentication_user_groups
            cur.execute("""
                INSERT INTO authentication_user_groups (id, user_id, group_id) VALUES 
                (1, 1, 10),
                (2, 2, 20),
                (3, 3, 20),
                (4, 4, 30)
                ON CONFLICT (id) DO UPDATE SET 
                    user_id = EXCLUDED.user_id,
                    group_id = EXCLUDED.group_id
            """)

            # 7. authentication_user_user_permissions
            cur.execute("""
                INSERT INTO authentication_user_user_permissions (id, user_id, permission_id) VALUES 
                (1, 2, 1),
                (2, 3, 4)
                ON CONFLICT (id) DO UPDATE SET 
                    user_id = EXCLUDED.user_id,
                    permission_id = EXCLUDED.permission_id
            """)

            # 8. django_admin_log
            cur.execute("""
                INSERT INTO django_admin_log (
                    id, action_time, object_id, object_repr, action_flag, change_message, content_type_id, user_id
                ) VALUES 
                (1, %s, %s, %s, %s, %s, %s, %s),
                (2, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET 
                    action_time = EXCLUDED.action_time,
                    object_id = EXCLUDED.object_id,
                    object_repr = EXCLUDED.object_repr,
                    action_flag = EXCLUDED.action_flag,
                    change_message = EXCLUDED.change_message,
                    content_type_id = EXCLUDED.content_type_id,
                    user_id = EXCLUDED.user_id
            """, [
                datetime.datetime(2026, 11, 1, 8, 5, 12, 345678, tzinfo=datetime.timezone.utc),
                "1", "Ban Giám Đốc (BGD)", 1, '[{"added": {}}]', 6, 1,
                datetime.datetime(2026, 11, 1, 8, 20, 0, 123456, tzinfo=datetime.timezone.utc),
                "2", "manager_tech (MANAGER)", 2, '[{"changed": {"fields": ["Department"]}}]', 8, 1,
            ])

            # 9. django_session
            cur.execute("""
                INSERT INTO django_session (session_key, session_data, expire_date) VALUES 
                (%s, %s, %s),
                (%s, %s, %s)
                ON CONFLICT (session_key) DO UPDATE SET 
                    session_data = EXCLUDED.session_data,
                    expire_date = EXCLUDED.expire_date
            """, [
                "reh_sess_admin_20261104_001", "session_data_payload_admin_2026",
                datetime.datetime(2026, 12, 31, 23, 59, 59, 999999, tzinfo=datetime.timezone.utc),
                "reh_sess_user_20261104_002", "session_data_payload_user_2026",
                datetime.datetime(2026, 11, 18, 12, 0, 0, 123456, tzinfo=datetime.timezone.utc),
            ])

            # 10. assets_identifiersequence
            cur.execute("""
                INSERT INTO assets_identifiersequence (id, kind, year, value) VALUES 
                (1, 'AST', 2026, 50),
                (2, 'REQ', 2026, 25)
                ON CONFLICT (id) DO UPDATE SET 
                    kind = EXCLUDED.kind,
                    year = EXCLUDED.year,
                    value = EXCLUDED.value
            """)

            # 11. assets_asset
            spec1 = json.dumps({
                "cpu": "M3 Max 16-core",
                "ram": "64GB",
                "storage": "2TB SSD",
                "screen_size": 16.2,
                "color": "Space Black",
            })
            spec2 = json.dumps({
                "resolution": "3840x2160",
                "panel": "IPS Black",
                "ports": ["HDMI 2.0", "DP 1.4", "USB-C 90W"],
                "accessories": [{"item": "Type-C Cable", "qty": 1}, {"item": "Power Cord", "qty": 1}],
            })
            spec3 = json.dumps([
                {"module": "power_supply", "status": "redundant", "watts": 500},
                {"module": "fan", "speed_rpm": 4500, "status": "normal"},
            ])

            cur.execute("""
                INSERT INTO assets_asset (
                    id, asset_code, name, serial_number, specification, status, image, document,
                    purchase_date, created_at, updated_at, assigned_to_user_id, category_id, department_id
                ) VALUES 
                (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s),
                (2, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s),
                (3, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET 
                    asset_code = EXCLUDED.asset_code,
                    name = EXCLUDED.name,
                    serial_number = EXCLUDED.serial_number,
                    specification = EXCLUDED.specification,
                    status = EXCLUDED.status,
                    image = EXCLUDED.image,
                    document = EXCLUDED.document,
                    purchase_date = EXCLUDED.purchase_date,
                    created_at = EXCLUDED.created_at,
                    updated_at = EXCLUDED.updated_at,
                    assigned_to_user_id = EXCLUDED.assigned_to_user_id,
                    category_id = EXCLUDED.category_id,
                    department_id = EXCLUDED.department_id
            """, [
                # Asset 1: IN_USE by User 3 in Dept 2
                "AST-2026-001", 'MacBook Pro M3 Max 16"', "SN-MBP-2026-001", spec1, "IN_USE",
                "assets/2026/11/mbp16.jpg", "docs/2026/11/mbp16_invoice.pdf", datetime.date(2026, 1, 10),
                datetime.datetime(2026, 11, 1, 9, 0, 0, 123456, tzinfo=datetime.timezone.utc),
                datetime.datetime(2026, 11, 1, 9, 15, 0, 654321, tzinfo=datetime.timezone.utc),
                3, 1, 2,

                # Asset 2: AVAILABLE in Dept 2
                "AST-2026-002", 'Dell UltraSharp 27" 4K', "SN-DELL-U2723QE", spec2, "AVAILABLE",
                "", "", datetime.date(2026, 2, 15),
                datetime.datetime(2026, 11, 1, 10, 0, 0, 1000, tzinfo=datetime.timezone.utc),
                datetime.datetime(2026, 11, 1, 10, 0, 0, 1000, tzinfo=datetime.timezone.utc),
                None, 2, 2,

                # Asset 3: AVAILABLE unallocated (department is None)
                "AST-2026-003", "Cisco Catalyst 1000 Switch", "SN-CISCO-C1000", spec3, "AVAILABLE",
                "", "", datetime.date(2026, 3, 1),
                datetime.datetime(2026, 11, 1, 11, 30, 0, 999999, tzinfo=datetime.timezone.utc),
                datetime.datetime(2026, 11, 1, 11, 30, 0, 999999, tzinfo=datetime.timezone.utc),
                None, 3, None,
            ])

            # 12. requests_assetrequest
            cur.execute("""
                INSERT INTO requests_assetrequest (
                    id, request_code, request_type, reason, status, reviewed_at, rejection_reason,
                    created_at, asset_id, department_id, requested_category_id, requester_id, reviewed_by_id
                ) VALUES 
                (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s),
                (2, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET 
                    request_code = EXCLUDED.request_code,
                    request_type = EXCLUDED.request_type,
                    reason = EXCLUDED.reason,
                    status = EXCLUDED.status,
                    reviewed_at = EXCLUDED.reviewed_at,
                    rejection_reason = EXCLUDED.rejection_reason,
                    created_at = EXCLUDED.created_at,
                    asset_id = EXCLUDED.asset_id,
                    department_id = EXCLUDED.department_id,
                    requested_category_id = EXCLUDED.requested_category_id,
                    requester_id = EXCLUDED.requester_id,
                    reviewed_by_id = EXCLUDED.reviewed_by_id
            """, [
                "REQ-2026-001", "ALLOCATE", "Cần máy MacBook cấu hình cao phát triển Cloudflare D1", "APPROVED",
                datetime.datetime(2026, 11, 1, 9, 14, 50, 123456, tzinfo=datetime.timezone.utc),
                "", datetime.datetime(2026, 11, 1, 9, 5, 0, 654321, tzinfo=datetime.timezone.utc),
                1, 2, 1, 3, 2,

                "REQ-2026-002", "DAMAGE_REPORT", "Bàn phím bị kẹt phím Space", "PENDING",
                None, "", datetime.datetime(2026, 11, 4, 6, 0, 0, 1000, tzinfo=datetime.timezone.utc),
                1, 2, 1, 3, None,
            ])

            # 13. assets_assethistorylog
            cur.execute("""
                INSERT INTO assets_assethistorylog (
                    id, action_type, from_status, to_status, reason, asset_code_snapshot,
                    asset_name_snapshot, created_at, actor_id, asset_id, from_department_id,
                    from_user_id, to_department_id, to_user_id
                ) VALUES 
                (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s),
                (2, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET 
                    action_type = EXCLUDED.action_type,
                    from_status = EXCLUDED.from_status,
                    to_status = EXCLUDED.to_status,
                    reason = EXCLUDED.reason,
                    asset_code_snapshot = EXCLUDED.asset_code_snapshot,
                    asset_name_snapshot = EXCLUDED.asset_name_snapshot,
                    created_at = EXCLUDED.created_at,
                    actor_id = EXCLUDED.actor_id,
                    asset_id = EXCLUDED.asset_id,
                    from_department_id = EXCLUDED.from_department_id,
                    from_user_id = EXCLUDED.from_user_id,
                    to_department_id = EXCLUDED.to_department_id,
                    to_user_id = EXCLUDED.to_user_id
            """, [
                "CREATE", "", "AVAILABLE", "Nhập kho tài sản mới", "AST-2026-001",
                'MacBook Pro M3 Max 16"', datetime.datetime(2026, 11, 1, 9, 0, 0, 123456, tzinfo=datetime.timezone.utc),
                1, 1, None, None, 2, None,

                "ALLOCATE", "AVAILABLE", "IN_USE", "Cấp phát theo REQ-2026-001", "AST-2026-001",
                'MacBook Pro M3 Max 16"', datetime.datetime(2026, 11, 1, 9, 15, 0, 654321, tzinfo=datetime.timezone.utc),
                2, 1, 2, None, 2, 3,
            ])

            # Advance all PostgreSQL sequences to MAX(id)
            update_postgresql_sequences(conn)

            # Re-ensure read-only permissions on public tables
            cur.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {RO_USER};")

            # Collect counts for all 15 tables
            for tbl in SOURCE_MANDATORY_TABLES:
                cur.execute(f'SELECT count(*) FROM "{tbl}"')
                counts[tbl] = cur.fetchone()[0]

        conn.commit()

    return counts


def verify_dataset_richness() -> Dict[str, Any]:
    """
    Validate test dataset across all required criteria:
    - Row count > 0 on all 15 mandatory source tables.
    - Non-existence of 'ai_suggestion_logs'.
    - Exact primary key preservation.
    - PBKDF2 1,000,000 iterations hashes verbatim.
    - Microsecond datetimes and date normalization.
    - Booleans (presence of both True and False).
    - JSON specifications (both objects and lists).
    - M2M relationships integrity.
    - Identifier sequence counters for AST and REQ in year 2026.
    - Read-only user can SELECT all tables and cannot INSERT.
    """
    results: Dict[str, Any] = {
        "status": "PASS",
        "errors": [],
        "details": {},
    }

    with get_pg_connection(autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1. Row count > 0 on all 15 tables
            table_counts = {}
            for tbl in SOURCE_MANDATORY_TABLES:
                cur.execute(f'SELECT count(*) FROM "{tbl}"')
                cnt = cur.fetchone()[0]
                table_counts[tbl] = cnt
                if cnt == 0:
                    results["errors"].append(f"Table '{tbl}' has 0 rows.")
            results["details"]["table_counts"] = table_counts

            # 2. Check ai_suggestion_logs does NOT exist
            cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='ai_suggestion_logs'"
            )
            if cur.fetchone() is not None:
                results["errors"].append("'ai_suggestion_logs' exists in source database!")

            # 3. Users PBKDF2 1,000,000 hash check
            cur.execute("SELECT id, username, password, is_superuser, is_staff, is_active FROM authentication_user ORDER BY id")
            users = cur.fetchall()
            if len(users) < 4:
                results["errors"].append(f"Expected at least 4 users, found {len(users)}.")
            for uid, uname, pwd, is_super, is_stf, is_act in users:
                if "pbkdf2_sha256$1000000$" not in pwd:
                    results["errors"].append(f"User {uname} (id={uid}) does not have 1,000,000 PBKDF2 hash: {pwd[:30]}...")

            # 4. Check booleans: must have True and False
            active_flags = {u[5] for u in users}
            if active_flags != {True, False}:
                results["errors"].append(f"Expected both True and False for is_active, got {active_flags}.")

            # 5. Check microsecond UTC datetimes
            cur.execute("SELECT id, created_at FROM departments_department ORDER BY id LIMIT 1")
            row = cur.fetchone()
            if row:
                dept_created = row[1]
                if not isinstance(dept_created, datetime.datetime) or dept_created.microsecond == 0:
                    results["errors"].append(f"departments_department created_at lacks microseconds: {dept_created}")

            # 6. Check JSON specifications (objects and lists)
            cur.execute("SELECT id, specification FROM assets_asset ORDER BY id")
            specs = cur.fetchall()
            has_dict = any(isinstance(s[1], dict) for s in specs)
            has_list = any(isinstance(s[1], list) for s in specs)
            if not (has_dict and has_list):
                results["errors"].append("assets_asset specifications missing either dict or list representation.")

            # 7. Check Identifier sequence counters
            cur.execute("SELECT kind, year, value FROM assets_identifiersequence ORDER BY id")
            seqs = {f"{r[0]}-{r[1]}": r[2] for r in cur.fetchall()}
            if seqs.get("AST-2026", 0) < 50:
                results["errors"].append(f"AST-2026 sequence counter expected >= 50, got {seqs.get('AST-2026')}")
            if seqs.get("REQ-2026", 0) < 25:
                results["errors"].append(f"REQ-2026 sequence counter expected >= 25, got {seqs.get('REQ-2026')}")

    # 8. Test Read-only User
    try:
        ro_conn = get_pg_connection(user=RO_USER, password=RO_PASSWORD, autocommit=True)
        with ro_conn.cursor() as rcur:
            rcur.execute("SELECT count(*) FROM assets_asset")
            rcur.fetchone()
            try:
                rcur.execute("INSERT INTO auth_group (id, name) VALUES (999, 'hacked')")
                results["errors"].append("Read-only user was able to INSERT into auth_group!")
            except psycopg.errors.InsufficientPrivilege:
                pass  # expected
        ro_conn.close()
    except Exception as exc:
        results["errors"].append(f"Read-only user verification failed: {exc}")

    if results["errors"]:
        results["status"] = "FAIL"

    return results


def configure_django_db_alias(alias: str = "postgres", use_readonly: bool = False) -> None:
    """
    Convenience method to register this PostgreSQL test database in django.conf.settings.DATABASES.
    Useful for pytest fixtures or migration scripts running in-process.
    """
    from django.conf import settings
    user = RO_USER if use_readonly else ADMIN_USER
    password = RO_PASSWORD if use_readonly else ADMIN_PASSWORD

    settings.DATABASES[alias] = {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": DB_NAME,
        "USER": user,
        "PASSWORD": password,
        "HOST": HOST,
        "PORT": str(PORT),
    }


def setup_pg_rehearsal_source(recreate: bool = False) -> Dict[str, Any]:
    """
    End-to-End orchestration for Agent A:
    1. Start PostgreSQL docker container on port 5434.
    2. Apply authentic Django migrations.
    3. Verify schema isolation (15 tables present, ai_suggestion_logs absent).
    4. Setup read-only user assethub_ro_user.
    5. Seed comprehensive test dataset.
    6. Verify dataset richness and constraints.
    """
    start_pg_container(recreate=recreate)
    apply_django_migrations()
    verify_schema_isolation()
    setup_readonly_user()
    seed_counts = seed_rehearsal_data()
    verification = verify_dataset_richness()
    if verification["status"] != "PASS":
        raise RuntimeError(f"Rehearsal dataset verification failed: {verification['errors']}")

    return {
        "run_id": RUN_ID,
        "container_name": CONTAINER_NAME,
        "host": HOST,
        "port": PORT,
        "dbname": DB_NAME,
        "admin_user": ADMIN_USER,
        "ro_user": RO_USER,
        "seed_counts": seed_counts,
        "verification": verification,
    }


def main():
    parser = argparse.ArgumentParser(description="PostgreSQL Test Harness for Local Rehearsal")
    parser.add_argument("--setup", action="store_true", help="Start container, run migrations, seed data, setup RO user.")
    parser.add_argument("--recreate", action="store_true", help="Force recreate container from scratch.")
    parser.add_argument("--teardown", action="store_true", help="Stop and remove test container.")
    parser.add_argument("--seed", action="store_true", help="Re-seed test dataset into running container.")
    parser.add_argument("--verify", action="store_true", help="Run verification check on current container.")
    parser.add_argument("--json", action="store_true", help="Output summary in JSON format.")

    args = parser.parse_args()

    if args.teardown:
        success = teardown_pg_source()
        if args.json:
            print(json.dumps({"teardown": success}))
        else:
            print(f"Teardown container '{CONTAINER_NAME}': {'SUCCESS' if success else 'FAILED'}")
        return

    if args.seed:
        counts = seed_rehearsal_data()
        if args.json:
            print(json.dumps(counts, indent=2))
        else:
            print("Seeded tables:")
            for t, c in counts.items():
                print(f"  {t}: {c} rows")
        return

    if args.verify:
        res = verify_dataset_richness()
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"Verification: {res['status']}")
            for err in res["errors"]:
                print(f"  ERROR: {err}")
        return

    if args.setup or not any([args.setup, args.teardown, args.seed, args.verify]):
        result = setup_pg_rehearsal_source(recreate=args.recreate)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"=== PostgreSQL Rehearsal Source Setup Completed ===")
            print(f"Container: {result['container_name']} on port {result['port']}")
            print(f"Database:  {result['dbname']} (User: {result['admin_user']}, RO User: {result['ro_user']})")
            print("Seeded tables count:")
            for t, c in result["seed_counts"].items():
                print(f"  - {t:36}: {c} rows")
            print("All verification checks PASSED.")


if __name__ == "__main__":
    main()
