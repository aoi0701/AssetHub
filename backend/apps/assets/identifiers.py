from typing import Optional
from django.db import connections
from django.utils import timezone

from apps.assets.models import IdentifierSequence


def generate_identifier(kind: str, year: Optional[int] = None, using: Optional[str] = None) -> str:
    """
    Generate sequential unique identifier in format {kind}-{year}-{value:03d}
    (e.g., AST-2026-001, REQ-2026-001, allowing values >= 1000).

    Uses atomic SQLite / PostgreSQL UPSERT RETURNING:
    concurrency safe, zero locks, zero sleep.
    """
    if kind not in IdentifierSequence.Kind.values:
        raise ValueError(
            f"Invalid identifier kind: '{kind}'. Expected one of {IdentifierSequence.Kind.values}"
        )

    if year is None:
        year = timezone.now().year

    sql = """
INSERT INTO assets_identifiersequence (kind, year, value)
VALUES (%s, %s, 1)
ON CONFLICT(kind, year) DO UPDATE SET value = assets_identifiersequence.value + 1
RETURNING value;
""".strip()

    conn = connections[using or "default"]
    with conn.cursor() as cursor:
        cursor.execute(sql, [kind, year])
        row = cursor.fetchone()
        value = int(row[0])

    return f"{kind}-{year}-{value:03d}"
