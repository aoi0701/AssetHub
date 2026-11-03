"""
Cloudflare D1 Database Backend for Django
Connects Django ORM to Cloudflare D1 via the Secure Worker Bridge.
"""
import re
import uuid
from typing import Any, List, Optional, Sequence, Tuple
from django.db import (
    DatabaseError,
    DataError,
    Error,
    IntegrityError,
    InterfaceError,
    InternalError,
    NotSupportedError,
    OperationalError,
    ProgrammingError,
)
from django.db.backends.sqlite3.base import DatabaseWrapper as SQLiteDatabaseWrapper
from django.db.backends.sqlite3.features import DatabaseFeatures as SQLiteDatabaseFeatures
from django.db.backends.sqlite3.operations import DatabaseOperations as SQLiteDatabaseOperations
from django.db.backends.sqlite3.schema import DatabaseSchemaEditor as SQLiteDatabaseSchemaEditor
from django.db.backends.sqlite3.introspection import DatabaseIntrospection as SQLiteDatabaseIntrospection
from django.db.backends.sqlite3.creation import DatabaseCreation as SQLiteDatabaseCreation
from django.db.backends.sqlite3.client import DatabaseClient as SQLiteDatabaseClient
from django.db.utils import DatabaseErrorWrapper
from django.utils.functional import cached_property
from .client import D1Client, D1IncompleteBatchError, is_read_only_query


def replace_date_trunc_in_sql(sql: str) -> str:
    """Replace django_date_trunc calls with standard SQLite equivalents."""
    if "django_date_trunc" not in sql and "django_datetime_trunc" not in sql:
        return sql

    pattern = r"django_(?:date|datetime)_trunc\(%s,\s*(.*?),\s*%s,\s*%s\)"

    def replace_func(match):
        field = match.group(1).strip()
        return (
            f"(CASE %s "
            f"WHEN 'year' THEN STRFTIME('%Y-01-01', {field}) "
            f"WHEN 'quarter' THEN CASE ((CAST(STRFTIME('%m', {field}) AS INTEGER) - 1) / 3 + 1) "
            f"  WHEN 1 THEN STRFTIME('%Y-01-01', {field}) "
            f"  WHEN 2 THEN STRFTIME('%Y-04-01', {field}) "
            f"  WHEN 3 THEN STRFTIME('%Y-07-01', {field}) "
            f"  WHEN 4 THEN STRFTIME('%Y-10-01', {field}) END "
            f"WHEN 'month' THEN STRFTIME('%Y-%m-01', {field}) "
            f"WHEN 'week' THEN DATE({field}, '-' || CAST((CAST(STRFTIME('%w', {field}) AS INTEGER) + 6) % 7 AS TEXT) || ' days') "
            f"WHEN 'day' THEN DATE({field}) "
            f"WHEN 'hour' THEN STRFTIME('%Y-%m-%d %H:00:00', {field}) "
            f"WHEN 'minute' THEN STRFTIME('%Y-%m-%d %H:%M:00', {field}) "
            f"WHEN 'second' THEN STRFTIME('%Y-%m-%d %H:%M:%S', {field}) "
            f"WHEN 'date' THEN DATE({field}) "
            f"WHEN 'time' THEN TIME({field}) "
            f"END || (CASE WHEN %s IS NULL THEN '' ELSE '' END) || (CASE WHEN %s IS NULL THEN '' ELSE '' END))"
        )

    return re.sub(pattern, replace_func, sql)


class D1DatabaseErrorWrapper(DatabaseErrorWrapper):
    """
    D1 DatabaseErrorWrapper preserving distributed transaction and recovery attributes
    (code, committed, idempotency_key, payload, raw_response) across Django's error wrapping layer.
    """

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            return

        for dj_exc_type in (
            DataError,
            OperationalError,
            IntegrityError,
            InternalError,
            ProgrammingError,
            NotSupportedError,
            DatabaseError,
            InterfaceError,
            Error,
        ):
            db_exc_type = getattr(self.wrapper.Database, dj_exc_type.__name__, None)
            if db_exc_type and issubclass(exc_type, db_exc_type):
                if issubclass(exc_type, D1IncompleteBatchError):
                    dj_exc_value = D1IncompleteBatchError(
                        *exc_value.args,
                        code=getattr(exc_value, "code", "INCOMPLETE_BATCH_STATE"),
                        committed=getattr(exc_value, "committed", True),
                        idempotency_key=getattr(exc_value, "idempotency_key", None),
                        payload=getattr(exc_value, "payload", None),
                        raw_response=getattr(exc_value, "raw_response", None),
                    )
                else:
                    dj_exc_value = dj_exc_type(*exc_value.args)

                # Copy recovery metadata to wrapped exception
                for attr in ("code", "committed", "idempotency_key", "payload", "raw_response"):
                    if hasattr(exc_value, attr):
                        setattr(dj_exc_value, attr, getattr(exc_value, attr))

                if dj_exc_type not in (DataError, IntegrityError):
                    self.wrapper.errors_occurred = True
                raise dj_exc_value.with_traceback(traceback) from exc_value

        raise exc_value.with_traceback(traceback)


class D1DatabaseOperations(SQLiteDatabaseOperations):
    def _quote_params_for_last_executed_query(self, params):
        quoted = []
        for p in params:
            if p is None:
                quoted.append("NULL")
            elif isinstance(p, bool):
                quoted.append("1" if p else "0")
            elif isinstance(p, (int, float)):
                quoted.append(str(p))
            elif isinstance(p, (bytes, bytearray)):
                quoted.append(f"X'{p.hex()}'")
            else:
                s = str(p).replace("'", "''")
                quoted.append(f"'{s}'")
        return tuple(quoted)


class D1DatabaseFeatures(SQLiteDatabaseFeatures):
    atomic_transactions = False
    supports_transactions = False
    uses_savepoints = False
    can_release_savepoints = False
    has_select_for_update = False
    supports_atomic_references_rename = False
    can_clone_databases = False
    can_rollback_ddl = False
    supports_pragma_foreign_key_check = False
    can_defer_constraint_checks = False


class D1Cursor:
    def __init__(self, connection: "D1Connection"):
        self.connection = connection
        self._rows: List[Tuple[Any, ...]] = []
        self._columns: List[str] = []
        self._pos = 0
        self.rowcount = -1
        self.lastrowid: Optional[int] = None
        self.description: Optional[Tuple[Tuple[str, None, None, None, None, None, None], ...]] = None

    def execute(self, query: str, params: Optional[Sequence[Any]] = None) -> "D1Cursor":
        query = replace_date_trunc_in_sql(query)

        # Database Guard: Verify allowed target DB before sending destructive operations
        db_name = getattr(self.connection, "database_name", "")
        upper_q = query.strip().upper()
        is_destructive = any(upper_q.startswith(kw) for kw in ("DROP", "ALTER", "DELETE", "TRUNCATE"))
        if is_destructive and db_name and db_name != "assethub-test-d1":
            raise DatabaseError(
                f"D1 Database Isolation Guard: Destructive query blocked on unauthorized database target '{db_name}'. "
                "Only 'assethub-test-d1' is authorized for test execution."
            )

        # Handle params formatting and boolean/None normalization
        clean_params: List[Any] = []
        if params is not None:
            for p in params:
                if isinstance(p, bool):
                    clean_params.append(1 if p else 0)
                else:
                    clean_params.append(p)

        # Convert Django %s format placeholders to SQLite ? if needed
        if "%s" in query and (params is not None or not clean_params):
            # Only replace if query uses %s format (Django default)
            query = query.replace("%s", "?")

        import uuid
        from .client import is_read_only_query
        is_read_only = is_read_only_query(query)
        idempotency_key = None if is_read_only else f"orm-{uuid.uuid4()}"

        res = self.connection.client.query(query, clean_params, idempotency_key=idempotency_key)

        results = res.get("results", [])
        meta = res.get("meta", {})

        if results:
            self._columns = list(results[0].keys())
            self._rows = [tuple(r.get(c) for c in self._columns) for r in results]
            self.description = tuple((col, None, None, None, None, None, None) for col in self._columns)
        else:
            self._columns = []
            self._rows = []
            self.description = None

        upper_q = query.strip().upper()
        if any(upper_q.startswith(kw) for kw in ("INSERT", "UPDATE", "DELETE")):
            self.rowcount = meta.get("changes", 0)
        else:
            self.rowcount = len(self._rows)

        self.lastrowid = meta.get("last_row_id", None)
        self._pos = 0
        return self

    def executemany(self, query: str, seq_of_params: Sequence[Sequence[Any]]) -> "D1Cursor":
        for params in seq_of_params:
            self.execute(query, params)
        return self

    def fetchone(self) -> Optional[Tuple[Any, ...]]:
        if self._pos < len(self._rows):
            row = self._rows[self._pos]
            self._pos += 1
            return row
        return None

    def fetchall(self) -> List[Tuple[Any, ...]]:
        res = self._rows[self._pos :]
        self._pos = len(self._rows)
        return res

    def fetchmany(self, size: Optional[int] = None) -> List[Tuple[Any, ...]]:
        if size is None or size <= 0:
            size = 1
        end = min(self._pos + size, len(self._rows))
        res = self._rows[self._pos : end]
        self._pos = end
        return res

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class D1Connection:
    def __init__(self, settings_dict: dict):
        self.settings_dict = settings_dict
        self.database_name = settings_dict.get("NAME") or settings_dict.get("OPTIONS", {}).get("NAME", "")
        base_url = settings_dict.get("BRIDGE_URL") or settings_dict.get("OPTIONS", {}).get("BRIDGE_URL")
        token = settings_dict.get("BRIDGE_TOKEN") or settings_dict.get("OPTIONS", {}).get("BRIDGE_TOKEN")
        timeout = float(settings_dict.get("TIMEOUT", 15.0))
        self.client = D1Client(base_url=base_url, token=token, timeout=timeout)

    def cursor(self) -> D1Cursor:
        return D1Cursor(self)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class D1DatabaseModule:
    """Mock DB-API module exposing standard database exceptions."""
    DatabaseError = DatabaseError
    IntegrityError = IntegrityError
    OperationalError = OperationalError
    ProgrammingError = ProgrammingError
    DataError = DataError
    InterfaceError = InterfaceError
    InternalError = InternalError
    NotSupportedError = NotSupportedError
    Error = Error
    D1IncompleteBatchError = D1IncompleteBatchError


class D1DatabaseCreation(SQLiteDatabaseCreation):
    def create_test_db(self, *args, **kwargs):
        return self.connection.settings_dict.get("NAME", "assethub-test-d1")

    def destroy_test_db(self, *args, **kwargs):
        pass


class DatabaseWrapper(SQLiteDatabaseWrapper):
    vendor = "cloudflare_d1"
    display_name = "Cloudflare D1"
    Database = D1DatabaseModule

    features_class = D1DatabaseFeatures
    ops_class = D1DatabaseOperations
    schema_editor_class = SQLiteDatabaseSchemaEditor
    introspection_class = SQLiteDatabaseIntrospection
    creation_class = D1DatabaseCreation
    client_class = SQLiteDatabaseClient

    transaction_modes = frozenset([])

    @cached_property
    def wrap_database_errors(self):
        return D1DatabaseErrorWrapper(self)

    def __init__(self, settings_dict, *args, **kwargs):
        settings_dict.setdefault("CONN_HEALTH_CHECKS", False)
        settings_dict.setdefault("CONN_MAX_AGE", 0)
        settings_dict.setdefault("TIME_ZONE", None)
        settings_dict.setdefault("AUTOCOMMIT", True)
        settings_dict.setdefault("ATOMIC_REQUESTS", False)
        settings_dict.setdefault("OPTIONS", {})
        test_dict = settings_dict.setdefault("TEST", {})
        test_dict.setdefault("NAME", settings_dict.get("NAME", "assethub-test-d1"))
        test_dict.setdefault("CHARSET", None)
        test_dict.setdefault("COLLATION", None)
        test_dict.setdefault("MIGRATE", False)
        test_dict.setdefault("MIRROR", None)
        super().__init__(settings_dict, *args, **kwargs)

    def get_connection_params(self):
        return self.settings_dict

    def get_new_connection(self, conn_params):
        return D1Connection(conn_params)

    def create_cursor(self, name=None):
        return self.connection.cursor()

    def get_database_version(self):
        return (3, 45, 0)

    def check_database_version_supported(self):
        pass

    def is_usable(self):
        return True

    def _set_autocommit(self, autocommit):
        pass

    def _start_transaction_under_autocommit(self):
        pass

    def _savepoint_allowed(self):
        return False
