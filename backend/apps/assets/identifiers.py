from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.assets.models import IdentifierSequence


def generate_identifier(kind: str, year: int = None) -> str:
    """
    Generate sequential unique identifier in format {kind}-{year}-{value:03d}
    (e.g., AST-2026-001, REQ-2026-001, allowing values >= 1000).

    Uses select_for_update() with an exclusive lock inside an atomic transaction.
    Handles first-record race conditions using a retry loop over atomic savepoints.
    """
    if kind not in IdentifierSequence.Kind.values:
        raise ValueError(
            f"Invalid identifier kind: '{kind}'. Expected one of {IdentifierSequence.Kind.values}"
        )

    if year is None:
        year = timezone.now().year

    max_retries = 5
    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                seq = (
                    IdentifierSequence.objects.select_for_update()
                    .filter(kind=kind, year=year)
                    .first()
                )
                if seq is not None:
                    seq.value += 1
                    seq.save(update_fields=['value'])
                    next_val = seq.value
                else:
                    # Initial counter creation for this (kind, year)
                    seq = IdentifierSequence.objects.create(
                        kind=kind,
                        year=year,
                        value=1,
                    )
                    next_val = seq.value

                return f"{kind}-{year}-{next_val:03d}"
        except IntegrityError:
            # Another transaction inserted the record first; retry to select_for_update
            if attempt == max_retries - 1:
                raise
            continue

    raise RuntimeError("Failed to generate identifier after maximum retries.")
