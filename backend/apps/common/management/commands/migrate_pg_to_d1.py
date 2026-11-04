# -*- coding: utf-8 -*-
"""
Django management command: migrate_pg_to_d1
Extracts data from PostgreSQL (or test source), migrates to Cloudflare D1 (or testbed)
following strict 4-Tier topological order, preserves 100% original primary keys and
PBKDF2 1,000,000 iterations password hashes, synchronizes sqlite_sequence,
and executes 1:1 automated reconciliation across 7 technical criteria.
"""
import datetime
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from django.core.management.base import BaseCommand, CommandError
from django.db import connections, DEFAULT_DB_ALIAS, transaction
from django.utils import timezone

try:
    from assethub_api.backends.d1 import d1_batch
except ImportError:
    d1_batch = None

# Strict 4-Tier Topological Order
TOPO_TIERS: Dict[int, List[str]] = {
    1: [
        "django_content_type",
        "auth_permission",
        "auth_group",
        "departments_department",
        "departments_category",
    ],
    2: [
        "auth_group_permissions",
        "authentication_user",
    ],
    3: [
        "authentication_user_groups",
        "authentication_user_user_permissions",
        "django_admin_log",
        "django_migrations",
        "django_session",
        "assets_identifiersequence",
        "assets_asset",
    ],
    4: [
        "requests_assetrequest",
        "assets_assethistorylog",
        "ai_suggestion_logs",
    ],
}

ALL_TABLES: List[str] = [
    table for tier in sorted(TOPO_TIERS.keys()) for table in TOPO_TIERS[tier]
]

SOURCE_MANDATORY_TABLES: List[str] = [
    table for table in ALL_TABLES if table != "ai_suggestion_logs"
]

TARGET_NEW_TABLES: Set[str] = {"ai_suggestion_logs"}

AUTOINCREMENT_TABLES: Set[str] = set(ALL_TABLES) - {
    "django_session",
    "ai_suggestion_logs",
}

PK_COLUMNS: Dict[str, str] = {
    table: ("session_key" if table == "django_session" else "id")
    for table in ALL_TABLES
}

# Schema metadata for type-safe reconciliation (no guessing from raw text content)
JSON_COLUMNS: Set[Tuple[str, str]] = {
    ("assets_asset", "specification"),
}

DATETIME_COLUMNS: Set[Tuple[str, str]] = {
    ("django_admin_log", "action_time"),
    ("django_migrations", "applied"),
    ("django_session", "expire_date"),
    ("authentication_user", "last_login"),
    ("authentication_user", "date_joined"),
    ("authentication_user", "created_at"),
    ("departments_department", "created_at"),
    ("departments_category", "created_at"),
    ("assets_asset", "created_at"),
    ("assets_asset", "updated_at"),
    ("requests_assetrequest", "reviewed_at"),
    ("requests_assetrequest", "created_at"),
    ("assets_assethistorylog", "created_at"),
    ("ai_suggestion_logs", "created_at"),
}

DATE_COLUMNS: Set[Tuple[str, str]] = {
    ("assets_asset", "purchase_date"),
}

BOOL_COLUMNS: Set[Tuple[str, str]] = {
    ("authentication_user", "is_superuser"),
    ("authentication_user", "is_staff"),
    ("authentication_user", "is_active"),
}

PASSWORD_COLUMNS: Set[Tuple[str, str]] = {
    ("authentication_user", "password"),
}


def normalize_value(val: Any) -> Any:
    """Normalize Python/DB types for SQLite/D1 storage."""
    if isinstance(val, bool):
        return 1 if val else 0
    if isinstance(val, datetime.datetime):
        if timezone.is_naive(val):
            val = timezone.make_aware(val, datetime.timezone.utc)
        else:
            val = val.astimezone(datetime.timezone.utc)
        return val.isoformat()
    if isinstance(val, datetime.date):
        return val.isoformat()
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    if isinstance(val, uuid.UUID):
        return str(val)
    if isinstance(val, memoryview):
        return bytes(val)
    return val


def is_same_database(source_alias: str, target_alias: str) -> bool:
    """Check if source and target aliases or physical DB paths point to the same database."""
    if source_alias == target_alias:
        return True

    src_conf = connections.databases.get(source_alias, {})
    tgt_conf = connections.databases.get(target_alias, {})
    src_name = src_conf.get("NAME")
    tgt_name = tgt_conf.get("NAME")

    if src_name and tgt_name:
        if str(src_name) == str(tgt_name):
            return True
        try:
            if os.path.exists(str(src_name)) and os.path.exists(str(tgt_name)):
                if os.path.samefile(str(src_name), str(tgt_name)):
                    return True
        except (ValueError, OSError):
            pass
        if os.path.isabs(str(src_name)) and os.path.isabs(str(tgt_name)):
            if os.path.normpath(str(src_name)) == os.path.normpath(str(tgt_name)):
                return True
    return False


def canonicalize_for_reconciliation(val: Any, table: str = "", col_name: str = "") -> Any:
    """
    Canonicalizes column values for 1:1 row content parity comparison based strictly
    on schema table+column metadata. Never guesses data types from arbitrary text content.
    """
    if val is None:
        return None

    pair = (table, col_name)

    # 1. Password: verbatim comparison
    if pair in PASSWORD_COLUMNS:
        return str(val)

    # 2. Boolean: normalized to 0 or 1
    if pair in BOOL_COLUMNS:
        return 1 if val else 0

    # 3. Datetime: normalized to UTC ISO-8601 string
    if pair in DATETIME_COLUMNS:
        if isinstance(val, datetime.datetime):
            if timezone.is_naive(val):
                val = timezone.make_aware(val, datetime.timezone.utc)
            else:
                val = val.astimezone(datetime.timezone.utc)
            return val.isoformat()
        if isinstance(val, str):
            clean_str = val.strip().replace("Z", "+00:00")
            try:
                dt = datetime.datetime.fromisoformat(clean_str)
                if timezone.is_naive(dt):
                    dt = timezone.make_aware(dt, datetime.timezone.utc)
                else:
                    dt = dt.astimezone(datetime.timezone.utc)
                return dt.isoformat()
            except (ValueError, TypeError):
                return val
        return val

    # 4. Date: normalized to YYYY-MM-DD
    if pair in DATE_COLUMNS:
        if isinstance(val, (datetime.date, datetime.datetime)):
            return val.strftime("%Y-%m-%d")
        if isinstance(val, str):
            return val.strip()[:10]
        return val

    # 5. JSON: parsed and dumped with sorted keys
    if pair in JSON_COLUMNS:
        if isinstance(val, (dict, list)):
            return json.dumps(val, sort_keys=True, ensure_ascii=False)
        if isinstance(val, str):
            try:
                parsed = json.loads(val.strip())
                return json.dumps(parsed, sort_keys=True, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError, ValueError):
                return val
        return val

    # Standard types
    if isinstance(val, memoryview):
        return bytes(val)
    if isinstance(val, uuid.UUID):
        return str(val)

    # 6. All other text/varchar/int/float columns: return verbatim without guessing types!
    return val


def execute_batch_statements(target_db: str, statements: List[Any]) -> List[Any]:
    """
    Executes a list of statements via d1_batch when target is Cloudflare D1,
    or executes inside django.db.transaction.atomic() for SQLite testbeds.
    Rejects unsupported database engines before execution.
    """
    if not statements:
        return []

    conn = connections[target_db]
    conn.ensure_connection()

    is_d1 = (getattr(conn, "vendor", "") == "d1") or ("d1" in conn.settings_dict.get("ENGINE", ""))
    is_sqlite = (getattr(conn, "vendor", "") == "sqlite") or ("sqlite" in conn.settings_dict.get("ENGINE", ""))

    if is_d1:
        if d1_batch is None:
            raise CommandError("D1 batch helper is not available.")
        # Re-raise immediately if d1_batch fails; never fallback to cursor!
        return d1_batch(statements, using=target_db)
    elif is_sqlite:
        with transaction.atomic(using=target_db):
            with conn.cursor() as cursor:
                results = []
                for stmt in statements:
                    if isinstance(stmt, (tuple, list)):
                        sql = stmt[0]
                        params = stmt[1] if len(stmt) > 1 else None
                        cursor.execute(sql, params)
                    elif isinstance(stmt, dict):
                        cursor.execute(stmt["sql"], stmt.get("params"))
                    else:
                        cursor.execute(str(stmt))
                    if cursor.description:
                        results.append(cursor.fetchall())
                return results
    else:
        raise CommandError(
            f"Unsupported database engine for target '{target_db}': {conn.settings_dict.get('ENGINE')}"
        )


def run_preflight_schema_checks(conn_src, conn_tgt) -> None:
    """
    Preflight schema verification:
    1. Checks all 15 mandatory PostgreSQL source tables exist in source.
    2. Checks all 16 target tables (including ai_suggestion_logs) exist in target.
    3. Checks column schemas match on all mandatory tables.
    Aborts with CommandError BEFORE any data is extracted or written.
    """
    for table in SOURCE_MANDATORY_TABLES:
        if not check_table_exists(conn_src, table):
            raise CommandError(
                f"Preflight schema check failed: mandatory table '{table}' missing in source database."
            )

    for table in ALL_TABLES:
        if not check_table_exists(conn_tgt, table):
            raise CommandError(
                f"Preflight schema check failed: table '{table}' missing in target database."
            )

    for table in SOURCE_MANDATORY_TABLES:
        with conn_src.cursor() as s_cur, conn_tgt.cursor() as t_cur:
            s_cur.execute(f'SELECT * FROM "{table}" LIMIT 0')
            s_cols = set(d[0] for d in s_cur.description)
            t_cur.execute(f'SELECT * FROM "{table}" LIMIT 0')
            t_cols = set(d[0] for d in t_cur.description)
            if s_cols != t_cols:
                diff = (s_cols - t_cols) | (t_cols - s_cols)
                raise CommandError(
                    f"Preflight schema check failed: column mismatch on table '{table}' (differing columns: {diff})."
                )


def check_table_exists(conn, table_name: str) -> bool:
    """Check if table exists in source database."""
    with conn.cursor() as cur:
        if conn.vendor == "postgresql":
            cur.execute(
                "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = %s",
                [table_name],
            )
            return cur.fetchone() is not None
        else:
            cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                [table_name],
            )
            return cur.fetchone() is not None


@dataclass
class ReconciliationResult:
    success: bool
    summary: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)


class D1Reconciler:
    """
    Automated 1:1 Reconciliation Engine validating destination D1 matches source PG across 7 technical criteria.
    """

    def __init__(
        self,
        source_db: str = "postgres",
        target_db: str = "d1",
        tables: Optional[List[str]] = None,
    ):
        self.source_db = source_db
        self.target_db = target_db
        self.tables = tables or ALL_TABLES

    def _get_cursor(self, db_alias: str):
        conn = connections[db_alias]
        conn.ensure_connection()
        return conn.cursor()

    def check_row_count_parity(self) -> Dict[str, Any]:
        """Criterion 1: Row count parity (COUNT(*))."""
        counts = {}
        mismatches = []
        conn_src = connections[self.source_db]
        conn_tgt = connections[self.target_db]

        for table in self.tables:
            if table == "ai_suggestion_logs" and not check_table_exists(conn_src, table):
                tgt_count = 0
                tgt_exists = check_table_exists(conn_tgt, table)
                if tgt_exists:
                    with self._get_cursor(self.target_db) as cur:
                        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                        row = cur.fetchone()
                        tgt_count = row[0] if row else 0
                counts[table] = {"source": "N/A (dest-only)", "target": tgt_count, "match": tgt_exists}
                if not tgt_exists:
                    mismatches.append(f"Table '{table}' missing in target database")
                continue

            src_exists = check_table_exists(conn_src, table)
            tgt_exists = check_table_exists(conn_tgt, table)
            if not src_exists and not tgt_exists:
                mismatches.append(f"Mandatory table '{table}' missing in both source and target databases")
                continue
            if not src_exists:
                mismatches.append(f"Table '{table}' missing in source database")
                continue
            if not tgt_exists:
                mismatches.append(f"Table '{table}' missing in target database")
                continue

            src_count = 0
            with self._get_cursor(self.source_db) as cur:
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                row = cur.fetchone()
                src_count = row[0] if row else 0

            tgt_count = 0
            with self._get_cursor(self.target_db) as cur:
                cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                row = cur.fetchone()
                tgt_count = row[0] if row else 0

            match = src_count == tgt_count
            counts[table] = {"source": src_count, "target": tgt_count, "match": match}
            if not match:
                mismatches.append(
                    f"Row count mismatch on {table}: source={src_count}, target={tgt_count}"
                )

        return {
            "match": len(mismatches) == 0,
            "counts": counts,
            "errors": mismatches,
        }

    def check_key_set_integrity(self) -> Dict[str, Any]:
        """Criterion 2: Key set integrity (SELECT id / primary key)."""
        key_results = {}
        mismatches = []
        conn_src = connections[self.source_db]
        conn_tgt = connections[self.target_db]

        for table in self.tables:
            if table == "ai_suggestion_logs" and not check_table_exists(conn_src, table):
                key_results[table] = {"match": True, "details": "Destination-only table"}
                continue

            src_exists = check_table_exists(conn_src, table)
            tgt_exists = check_table_exists(conn_tgt, table)
            if not src_exists or not tgt_exists:
                mismatches.append(f"Key set check aborted: table '{table}' missing in {'source' if not src_exists else 'target'}")
                continue

            pk_col = PK_COLUMNS.get(table, "id")
            src_keys = set()
            with self._get_cursor(self.source_db) as cur:
                cur.execute(f'SELECT "{pk_col}" FROM "{table}"')
                src_keys = {row[0] for row in cur.fetchall()}

            tgt_keys = set()
            with self._get_cursor(self.target_db) as cur:
                cur.execute(f'SELECT "{pk_col}" FROM "{table}"')
                tgt_keys = {row[0] for row in cur.fetchall()}

            missing = src_keys - tgt_keys
            unexpected = tgt_keys - src_keys
            match = len(missing) == 0 and len(unexpected) == 0

            # Mask session_key values to avoid leaking sensitive tokens
            if table == "django_session":
                missing_sample = [f"***{str(k)[-6:]}" for k in list(missing)[:5]]
                unexpected_sample = [f"***{str(k)[-6:]}" for k in list(unexpected)[:5]]
            else:
                missing_sample = list(missing)[:5]
                unexpected_sample = list(unexpected)[:5]

            key_results[table] = {
                "source_count": len(src_keys),
                "target_count": len(tgt_keys),
                "missing": missing_sample,
                "unexpected": unexpected_sample,
                "match": match,
            }

            if not match:
                mismatches.append(
                    f"Key set mismatch on {table}: missing={len(missing)}, unexpected={len(unexpected)}"
                )

        return {
            "match": len(mismatches) == 0,
            "tables": key_results,
            "errors": mismatches,
        }

    def check_foreign_key_integrity(self) -> Dict[str, Any]:
        """Criterion 3: Foreign key integrity (PRAGMA foreign_key_check)."""
        violations = []
        try:
            with self._get_cursor(self.target_db) as cur:
                cur.execute("PRAGMA foreign_key_check;")
                violations = cur.fetchall()
        except Exception as exc:
            return {
                "match": False,
                "violations": [str(exc)],
                "errors": [f"PRAGMA foreign_key_check failed: {exc}"],
            }

        return {
            "match": len(violations) == 0,
            "violations": violations,
            "errors": [
                f"Foreign key violations detected ({len(violations)}): {violations[:5]}"
            ]
            if violations
            else [],
        }

    def check_password_hash_verbatim(self) -> Dict[str, Any]:
        """Criterion 4: Password hash verbatim check (SHA-256 match, 1,000,000 PBKDF2 intact)."""
        conn_src = connections[self.source_db]
        conn_tgt = connections[self.target_db]

        s_rows = []
        if check_table_exists(conn_src, "authentication_user"):
            with self._get_cursor(self.source_db) as s_cur:
                s_cur.execute(
                    'SELECT id, password FROM "authentication_user" ORDER BY id'
                )
                s_rows = s_cur.fetchall()

        t_rows = []
        if check_table_exists(conn_tgt, "authentication_user"):
            with self._get_cursor(self.target_db) as t_cur:
                t_cur.execute(
                    'SELECT id, password FROM "authentication_user" ORDER BY id'
                )
                t_rows = t_cur.fetchall()

        if len(s_rows) != len(t_rows):
            return {
                "match": False,
                "source_hash": None,
                "target_hash": None,
                "user_count": len(t_rows),
                "errors": [
                    f"User count mismatch for password check: source={len(s_rows)}, target={len(t_rows)}"
                ],
            }

        mismatches = []
        for (s_id, s_pass), (t_id, t_pass) in zip(s_rows, t_rows):
            if s_id != t_id:
                mismatches.append(f"User ID mismatch: source={s_id}, target={t_id}")
            elif s_pass != t_pass:
                mismatches.append(f"Password hash mismatch for user id {s_id}")

        s_concat = "".join(str(p) for _, p in s_rows)
        t_concat = "".join(str(p) for _, p in t_rows)
        s_sha = hashlib.sha256(s_concat.encode("utf-8")).hexdigest()
        t_sha = hashlib.sha256(t_concat.encode("utf-8")).hexdigest()

        match = (s_sha == t_sha) and (len(mismatches) == 0)
        return {
            "match": match,
            "source_hash": s_sha,
            "target_hash": t_sha,
            "user_count": len(s_rows),
            "errors": mismatches
            if not match
            else [],
        }

    def check_datetime_and_boolean_normalization(self) -> Dict[str, Any]:
        """Criterion 5: Datetime & boolean normalization."""
        errors = []
        conn_tgt = connections[self.target_db]

        # 1. Validate boolean fields in authentication_user (is_superuser, is_staff, is_active)
        if check_table_exists(conn_tgt, "authentication_user"):
            with self._get_cursor(self.target_db) as cur:
                cur.execute(
                    'SELECT id, is_superuser, is_staff, is_active FROM "authentication_user"'
                )
                for uid, is_super, is_staff, is_active in cur.fetchall():
                    for name, val in [
                        ("is_superuser", is_super),
                        ("is_staff", is_staff),
                        ("is_active", is_active),
                    ]:
                        if val not in (0, 1):
                            errors.append(
                                f"Non-normalized boolean in authentication_user id={uid} {name}={val}"
                            )

        # 2. Validate sample datetime columns are parseable ISO strings
        datetime_checks = [
            ("departments_department", "created_at"),
            ("departments_category", "created_at"),
            ("authentication_user", "created_at"),
            ("assets_asset", "created_at"),
        ]

        for tbl, col in datetime_checks:
            if check_table_exists(conn_tgt, tbl):
                with self._get_cursor(self.target_db) as cur:
                    cur.execute(f'SELECT id, "{col}" FROM "{tbl}" WHERE "{col}" IS NOT NULL LIMIT 20')
                    for row_id, dt_str in cur.fetchall():
                        try:
                            # Normalize Z to +00:00 for standard parser
                            clean_dt = str(dt_str).replace("Z", "+00:00")
                            datetime.datetime.fromisoformat(clean_dt)
                        except Exception as exc:
                            errors.append(
                                f"Invalid ISO datetime in {tbl}.{col} for id={row_id}: '{dt_str}' ({exc})"
                            )

        return {
            "match": len(errors) == 0,
            "errors": errors,
        }

    def check_identifier_sequence_counters(self) -> Dict[str, Any]:
        """Criterion 6: Identifier sequence counters partitioned by (kind, year)."""
        conn_tgt = connections[self.target_db]
        errors = []

        if not check_table_exists(conn_tgt, "assets_identifiersequence"):
            return {
                "match": True,
                "details": "Table assets_identifiersequence absent",
                "errors": [],
            }

        seq_map: Dict[Tuple[str, int], int] = {}
        with self._get_cursor(self.target_db) as cur:
            cur.execute(
                'SELECT kind, year, MAX(value) FROM "assets_identifiersequence" GROUP BY kind, year'
            )
            for k, yr, max_v in cur.fetchall():
                if k is not None and yr is not None:
                    seq_map[(str(k), int(yr))] = max_v if max_v is not None else 0

        max_codes: Dict[Tuple[str, int], int] = {}

        # Scan assets_asset.asset_code for AST-YYYY-NNN
        if check_table_exists(conn_tgt, "assets_asset"):
            with self._get_cursor(self.target_db) as cur:
                cur.execute('SELECT asset_code FROM "assets_asset" WHERE asset_code IS NOT NULL')
                for (code,) in cur.fetchall():
                    if not code:
                        continue
                    m = re.search(r"AST-(\d{4})-(\d+)", str(code))
                    if m:
                        yr = int(m.group(1))
                        num = int(m.group(2))
                        pair = ("AST", yr)
                        if num > max_codes.get(pair, 0):
                            max_codes[pair] = num

        # Scan requests_assetrequest.request_code for REQ-YYYY-NNN
        if check_table_exists(conn_tgt, "requests_assetrequest"):
            with self._get_cursor(self.target_db) as cur:
                cur.execute(
                    'SELECT request_code FROM "requests_assetrequest" WHERE request_code IS NOT NULL'
                )
                for (code,) in cur.fetchall():
                    if not code:
                        continue
                    m = re.search(r"REQ-(\d{4})-(\d+)", str(code))
                    if m:
                        yr = int(m.group(1))
                        num = int(m.group(2))
                        pair = ("REQ", yr)
                        if num > max_codes.get(pair, 0):
                            max_codes[pair] = num

        # Verify counter >= max_code for each (kind, year) separately
        for (kind, year), max_code in sorted(max_codes.items()):
            counter = seq_map.get((kind, year), 0)
            if counter < max_code:
                errors.append(
                    f"Identifier sequence counter for {kind} year {year} out of sync: seq={counter} < max_code={max_code}"
                )

        match = len(errors) == 0
        ast_seq = max([v for (k, _), v in seq_map.items() if k == "AST"], default=0)
        max_ast_code = max([v for (k, _), v in max_codes.items() if k == "AST"], default=0)
        req_seq = max([v for (k, _), v in seq_map.items() if k == "REQ"], default=0)
        max_req_code = max([v for (k, _), v in max_codes.items() if k == "REQ"], default=0)

        return {
            "match": match,
            "seq_map": {f"{k}_{y}": v for (k, y), v in seq_map.items()},
            "max_codes": {f"{k}_{y}": v for (k, y), v in max_codes.items()},
            "ast_seq": ast_seq,
            "max_ast_code": max_ast_code,
            "req_seq": req_seq,
            "max_req_code": max_req_code,
            "errors": errors,
        }

    def check_row_content_parity(self) -> Dict[str, Any]:
        """Criterion 7: Row content reconciliation by PK and column."""
        errors = []
        table_reports = {}
        conn_src = connections[self.source_db]
        conn_tgt = connections[self.target_db]

        for table in self.tables:
            if table == "ai_suggestion_logs" and not check_table_exists(conn_src, table):
                # Destination-only table: ensure it exists on target
                tgt_exists = check_table_exists(conn_tgt, table)
                if not tgt_exists:
                    errors.append(f"Table '{table}' missing in target database")
                continue

            src_exists = check_table_exists(conn_src, table)
            tgt_exists = check_table_exists(conn_tgt, table)
            if not src_exists and not tgt_exists:
                errors.append(f"Mandatory table '{table}' missing in both source and target databases")
                continue
            if not src_exists:
                errors.append(f"Table '{table}' missing in source database")
                continue
            if not tgt_exists:
                errors.append(f"Table '{table}' missing in target database")
                continue

            with self._get_cursor(self.source_db) as s_cur:
                s_cur.execute(f'SELECT * FROM "{table}" LIMIT 0')
                src_cols = [d[0] for d in s_cur.description]
            with self._get_cursor(self.target_db) as t_cur:
                t_cur.execute(f'SELECT * FROM "{table}" LIMIT 0')
                tgt_cols = [d[0] for d in t_cur.description]

            if set(src_cols) != set(tgt_cols):
                errors.append(f"Column schema mismatch on table '{table}'")
                continue

            pk_col = PK_COLUMNS.get(table, "id")
            if pk_col not in src_cols:
                errors.append(f"Primary key column '{pk_col}' missing in table '{table}'")
                continue

            cols = list(src_cols)
            pk_idx = cols.index(pk_col)
            quoted_cols = ", ".join(f'"{c}"' for c in cols)

            with self._get_cursor(self.source_db) as s_cur:
                s_cur.execute(f'SELECT {quoted_cols} FROM "{table}" ORDER BY "{pk_col}"')
                src_rows = s_cur.fetchall()
            with self._get_cursor(self.target_db) as t_cur:
                t_cur.execute(f'SELECT {quoted_cols} FROM "{table}" ORDER BY "{pk_col}"')
                tgt_rows = t_cur.fetchall()

            src_by_pk = {r[pk_idx]: r for r in src_rows}
            tgt_by_pk = {r[pk_idx]: r for r in tgt_rows}

            table_diffs = 0
            for pk_val, s_row in src_by_pk.items():
                if pk_val not in tgt_by_pk:
                    errors.append(f"Row content mismatch on {table}: PK {pk_val} missing in target")
                    table_diffs += 1
                    continue

                t_row = tgt_by_pk[pk_val]
                for idx, col in enumerate(cols):
                    s_c = canonicalize_for_reconciliation(s_row[idx], table, col)
                    t_c = canonicalize_for_reconciliation(t_row[idx], table, col)
                    if s_c != t_c:
                        # Never leak secrets or private values, only table, PK, and column
                        errors.append(
                            f"Row content mismatch on {table} (PK {pk_val}): column '{col}' differs"
                        )
                        table_diffs += 1

            for pk_val in tgt_by_pk:
                if pk_val not in src_by_pk:
                    errors.append(f"Row content mismatch on {table}: unexpected PK {pk_val} in target")
                    table_diffs += 1

            table_reports[table] = {
                "source_rows": len(src_rows),
                "target_rows": len(tgt_rows),
                "diffs": table_diffs,
            }

        return {
            "match": len(errors) == 0,
            "tables": table_reports,
            "errors": errors,
        }

    def reconcile(self) -> ReconciliationResult:
        """Run all 7 verification checks and produce consolidated report."""
        r1 = self.check_row_count_parity()
        r2 = self.check_key_set_integrity()
        r3 = self.check_foreign_key_integrity()
        r4 = self.check_password_hash_verbatim()
        r5 = self.check_datetime_and_boolean_normalization()
        r6 = self.check_identifier_sequence_counters()
        r7 = self.check_row_content_parity()

        all_errors = (
            r1.get("errors", [])
            + r2.get("errors", [])
            + r3.get("errors", [])
            + r4.get("errors", [])
            + r5.get("errors", [])
            + r6.get("errors", [])
            + r7.get("errors", [])
        )

        success = (
            r1["match"]
            and r2["match"]
            and r3["match"]
            and r4["match"]
            and r5["match"]
            and r6["match"]
            and r7["match"]
        )

        return ReconciliationResult(
            success=success,
            summary={
                "row_count_parity": r1,
                "key_set_integrity": r2,
                "foreign_key_integrity": r3,
                "password_hash_verbatim": r4,
                "datetime_boolean_normalization": r5,
                "identifier_sequence_counters": r6,
                "row_content_parity": r7,
            },
            errors=all_errors,
        )


class Command(BaseCommand):
    help = (
        "Migrate PostgreSQL data to Cloudflare D1 across 4 Topo Tiers with exact PK preservation "
        "and automated 1:1 reconciliation."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Chỉ trích xuất, in kế hoạch 4-Tier, tính toán checksums, không ghi dòng nào vào CSDL đích.",
        )
        parser.add_argument(
            "--target-check",
            action="store_true",
            help="Kiểm tra nếu CSDL đích đã có dữ liệu thì chặn dừng lại (mặc định luôn kích hoạt).",
        )
        parser.add_argument(
            "--allow-non-empty",
            action="store_true",
            help="Cho phép ghi tiếp ngay cả khi CSDL đích không rỗng.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=50,
            help="Chia nhỏ batch nạp tối đa 50 bản ghi / batch qua d1_batch (mặc định 50).",
        )
        parser.add_argument(
            "--source-db",
            type=str,
            default="postgres",
            help="Nguồn dữ liệu (mặc định 'postgres', hỗ trợ chỉ định alias test).",
        )
        parser.add_argument(
            "--target-db",
            type=str,
            default="d1",
            help="Đích nạp (mặc định 'd1').",
        )

    def handle(self, *args, **options):
        dry_run: bool = options["dry_run"]
        target_check: bool = options["target_check"]
        allow_non_empty: bool = options["allow_non_empty"]
        batch_size: int = max(1, min(options.get("batch_size") or 50, 50))
        source_db: str = options["source_db"]
        target_db: str = options["target_db"]

        # Validate source database alias - strictly no implicit fallback
        if source_db not in connections.databases:
            raise CommandError(
                f"Source database alias '{source_db}' not configured in settings.DATABASES."
            )

        # Validate target database alias
        if target_db not in connections.databases:
            if not dry_run:
                raise CommandError(
                    f"Target database alias '{target_db}' not configured in settings.DATABASES."
                )
        elif not dry_run:
            tgt_engine = connections.databases.get(target_db, {}).get("ENGINE", "")
            if "d1" not in tgt_engine and "sqlite" not in tgt_engine:
                raise CommandError(
                    f"Unsupported database engine for target '{target_db}': {tgt_engine}"
                )

        # Prevent source and target collision (checked before opening target connection)
        if is_same_database(source_db, target_db):
            raise CommandError(
                f"Source database '{source_db}' and target database '{target_db}' resolve to the same database."
            )

        conn_src = connections[source_db]
        conn_src.ensure_connection()

        if target_db in connections.databases:
            conn_tgt = connections[target_db]
            conn_tgt.ensure_connection()
        else:
            conn_tgt = None

        # Preflight schema check: Verify source and target schema before any writes
        if conn_tgt is not None:
            run_preflight_schema_checks(conn_src, conn_tgt)

        # Step 1: Target Non-Empty Guard (runs by default on migration unless --allow-non-empty is set)
        if not dry_run and not allow_non_empty:
            if conn_tgt is None:
                raise CommandError(
                    f"Cannot perform target check: target database '{target_db}' not configured in settings.DATABASES."
                )
            non_empty_tables = []
            for table in ALL_TABLES:
                if check_table_exists(conn_tgt, table):
                    with conn_tgt.cursor() as cur:
                        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                        row = cur.fetchone()
                        cnt = row[0] if row else 0
                        if cnt > 0:
                            non_empty_tables.append((table, cnt))

            if non_empty_tables:
                details = ", ".join(f"{t} ({c})" for t, c in non_empty_tables[:5])
                raise CommandError(
                    f"Target database '{target_db}' is not empty: {details}. "
                    "Aborted by non-empty target guard. Use --allow-non-empty to proceed anyway."
                )

        # Step 2: Extract metadata and construct 4-Tier Plan
        plan_details = {}
        total_source_rows = 0
        user_passwords = []
        max_ids_to_sync: Dict[str, int] = {}

        for tier_num in sorted(TOPO_TIERS.keys()):
            tier_tables = TOPO_TIERS[tier_num]
            plan_details[tier_num] = []
            for table in tier_tables:
                if check_table_exists(conn_src, table):
                    with conn_src.cursor() as cur:
                        cur.execute(f'SELECT COUNT(*) FROM "{table}"')
                        cnt = cur.fetchone()[0]
                        pk_col = PK_COLUMNS.get(table, "id")
                        max_id = None
                        if table in AUTOINCREMENT_TABLES and cnt > 0:
                            cur.execute(f'SELECT MAX("{pk_col}") FROM "{table}"')
                            max_id = cur.fetchone()[0]
                            if max_id:
                                max_ids_to_sync[table] = max_id

                        if table == "authentication_user" and cnt > 0:
                            cur.execute('SELECT password FROM "authentication_user" ORDER BY id')
                            user_passwords = [r[0] for r in cur.fetchall()]

                    plan_details[tier_num].append((table, cnt, max_id))
                    total_source_rows += cnt
                else:
                    plan_details[tier_num].append((table, 0, None))

        # Checksum of user passwords
        pw_checksum = hashlib.sha256(
            "".join(str(p) for p in user_passwords).encode("utf-8")
        ).hexdigest()

        # Output Plan
        self.stdout.write("=" * 70)
        mode_label = "[DRY-RUN]" if dry_run else "[EXECUTE]"
        self.stdout.write(f"MIGRATION PLAN (4-TIER TOPOLOGICAL ORDER) {mode_label}")
        self.stdout.write("=" * 70)

        for tier_num in sorted(plan_details.keys()):
            self.stdout.write(f"Tier {tier_num}:")
            for t_name, count, max_id in plan_details[tier_num]:
                seq_info = f", max_id={max_id}" if max_id is not None else ""
                self.stdout.write(f"  - {t_name:38} : {count:5d} rows{seq_info}")

        self.stdout.write("-" * 70)
        self.stdout.write(f"Total source records to migrate: {total_source_rows}")
        self.stdout.write(f"Source password hash SHA-256:   {pw_checksum}")
        self.stdout.write(f"Autoincrement tables to sync:   {len(max_ids_to_sync)}")
        self.stdout.write("=" * 70)

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"DRY-RUN completed. 0 records written to target database '{target_db}'."
                )
            )
            return

        # Step 3: Migration Execution across 4 Tiers
        self.stdout.write(f"Executing migration into target '{target_db}' (batch_size={batch_size})...")

        for tier_num in sorted(TOPO_TIERS.keys()):
            tables = TOPO_TIERS[tier_num]
            self.stdout.write(f"--- Migrating Tier {tier_num} ({len(tables)} tables) ---")

            for table in tables:
                if not check_table_exists(conn_src, table):
                    continue

                pk_col = PK_COLUMNS.get(table, "id")
                with conn_src.cursor() as cur:
                    cur.execute(f'SELECT * FROM "{table}" ORDER BY "{pk_col}"')
                    col_names = [d[0] for d in cur.description]
                    quoted_cols = ", ".join(f'"{c}"' for c in col_names)
                    placeholders = ", ".join(["?"] * len(col_names))
                    insert_sql = (
                        f'INSERT INTO "{table}" ({quoted_cols}) VALUES ({placeholders})'
                    )

                    while True:
                        rows = cur.fetchmany(batch_size)
                        if not rows:
                            break

                        statements = []
                        for row in rows:
                            params = [normalize_value(v) for v in row]
                            statements.append((insert_sql, params))

                        execute_batch_statements(target_db, statements)

                # Synchronize sqlite_sequence for autoincrement table
                if table in AUTOINCREMENT_TABLES:
                    with conn_tgt.cursor() as t_cur:
                        t_cur.execute(f'SELECT MAX("{pk_col}") FROM "{table}"')
                        max_row = t_cur.fetchone()
                        max_val = max_row[0] if max_row and max_row[0] is not None else 0

                        t_cur.execute("SELECT seq FROM sqlite_sequence WHERE name = ?", [table])
                        seq_rows = t_cur.fetchall()
                        existing_seq = max([r[0] for r in seq_rows if r[0] is not None], default=0)

                    target_seq = max(existing_seq, max_val)
                    if target_seq > 0:
                        seq_stmts = [
                            ("DELETE FROM sqlite_sequence WHERE name = ?", [table]),
                            ("INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)", [table, target_seq]),
                        ]
                        execute_batch_statements(target_db, seq_stmts)

        self.stdout.write(self.style.SUCCESS("All tables migrated successfully. Running 1:1 reconciliation..."))

        # Step 4: Automated 1:1 Reconciliation
        reconciler = D1Reconciler(source_db=source_db, target_db=target_db)
        rec_result = reconciler.reconcile()

        self.stdout.write("=" * 70)
        self.stdout.write("D1 RECONCILIATION REPORT (7 Criteria 1:1 Parity)")
        self.stdout.write("=" * 70)
        summary = rec_result.summary

        r1 = summary["row_count_parity"]
        r2 = summary["key_set_integrity"]
        r3 = summary["foreign_key_integrity"]
        r4 = summary["password_hash_verbatim"]
        r5 = summary["datetime_boolean_normalization"]
        r6 = summary["identifier_sequence_counters"]
        r7 = summary.get("row_content_parity", {"match": True})

        self.stdout.write(f"1. Row Count Parity:             [{'PASS' if r1['match'] else 'FAIL'}]")
        self.stdout.write(f"2. Key Set Integrity:            [{'PASS' if r2['match'] else 'FAIL'}]")
        self.stdout.write(f"3. Foreign Key Integrity:        [{'PASS' if r3['match'] else 'FAIL'}]")
        self.stdout.write(f"4. Password Hash Verbatim Check: [{'PASS' if r4['match'] else 'FAIL'}]")
        self.stdout.write(f"5. Datetime & Boolean Normal:    [{'PASS' if r5['match'] else 'FAIL'}]")
        self.stdout.write(f"6. Identifier Sequence Counters: [{'PASS' if r6['match'] else 'FAIL'}]")
        self.stdout.write(f"7. Row Content Parity:           [{'PASS' if r7['match'] else 'FAIL'}]")
        self.stdout.write("=" * 70)

        if not rec_result.success:
            for err in rec_result.errors:
                self.stderr.write(self.style.ERROR(f"  [ERROR] {err}"))
            raise CommandError(
                f"D1 Reconciliation failed with {len(rec_result.errors)} violations."
            )

        self.stdout.write(self.style.SUCCESS("Reconciliation PASSED: 100% 1:1 parity confirmed."))
