"""
Cloudflare D1 Django Database Engine.
"""
from django.db import connections
from django.db.utils import OperationalError, DatabaseError
from .client import D1Client, D1IncompleteBatchError
from .base import DatabaseWrapper, D1DatabaseErrorWrapper, replace_date_trunc_in_sql


def d1_batch(statements, using="default", idempotency_key=None, **kwargs):
    """
    Execute a multi-statement batch on Cloudflare D1 via Worker Bridge.
    Wraps execution inside the connection's wrap_database_errors to preserve
    D1 transaction and recovery metadata (committed, code, idempotency_key, etc.).
    """
    conn = connections[using]
    conn.ensure_connection()
    with conn.wrap_database_errors:
        client = getattr(conn.connection, "client", None)
        if not client:
            raise OperationalError(f"Database connection '{using}' does not support D1 batch operations.")

        # Guard check destructive queries against unauthorized database
        db_name = getattr(conn.connection, "database_name", "")
        if db_name and db_name != "assethub-test-d1":
            for st in statements:
                sql = st[0] if isinstance(st, (tuple, list)) else (st.get("sql") if isinstance(st, dict) else st)
                upper_q = str(sql).strip().upper()
                if any(upper_q.startswith(kw) for kw in ("DROP", "ALTER", "DELETE", "TRUNCATE")):
                    raise DatabaseError(
                        f"D1 Database Isolation Guard: Destructive query blocked on unauthorized database target '{db_name}'. "
                        "Only 'assethub-test-d1' is authorized for test execution."
                    )

        # Apply date trunc replacement if statements contain django date trunc calls
        formatted_stmts = []
        for st in statements:
            if isinstance(st, (tuple, list)):
                raw_sql = replace_date_trunc_in_sql(st[0])
                formatted_stmts.append((raw_sql, st[1]) if len(st) > 1 else (raw_sql,))
            elif isinstance(st, dict):
                raw_sql = replace_date_trunc_in_sql(st["sql"])
                formatted_stmts.append({**st, "sql": raw_sql})
            elif isinstance(st, str):
                formatted_stmts.append(replace_date_trunc_in_sql(st))
            else:
                formatted_stmts.append(st)

        return client.batch(formatted_stmts, idempotency_key=idempotency_key, **kwargs)


__all__ = [
    "DatabaseWrapper",
    "D1Client",
    "D1IncompleteBatchError",
    "D1DatabaseErrorWrapper",
    "d1_batch",
    "replace_date_trunc_in_sql",
]
