import pytest
from unittest.mock import patch
from django.db import IntegrityError
from django.utils import timezone
from apps.assets.identifiers import generate_identifier
from apps.assets.models import IdentifierSequence


@pytest.mark.django_db
def test_generate_identifier_format_and_sequence():
    """Verify generated identifier matches format <KIND>-<YEAR>-<NNN>."""
    year = 2026
    id1 = generate_identifier('AST', year=year)
    id2 = generate_identifier('AST', year=year)
    id3 = generate_identifier('AST', year=year)

    assert id1 == "AST-2026-001"
    assert id2 == "AST-2026-002"
    assert id3 == "AST-2026-003"


@pytest.mark.django_db
def test_generate_identifier_request_kind():
    """Verify sequence generation for REQ kind."""
    year = 2026
    req_id1 = generate_identifier('REQ', year=year)
    req_id2 = generate_identifier('REQ', year=year)

    assert req_id1 == "REQ-2026-001"
    assert req_id2 == "REQ-2026-002"


@pytest.mark.django_db
def test_generate_identifier_default_year():
    """Verify default year is current year."""
    current_year = timezone.now().year
    code = generate_identifier('AST')
    assert code == f"AST-{current_year}-001"


@pytest.mark.django_db
def test_generate_identifier_invalid_kind():
    """Verify invalid kind raises ValueError."""
    with pytest.raises(ValueError, match="Invalid identifier kind"):
        generate_identifier('INVALID')


@pytest.mark.django_db
def test_generate_identifier_exceeds_999():
    """Verify sequence numbers >= 1000 are supported without limitation."""
    year = 2026
    seq = IdentifierSequence.objects.create(kind=IdentifierSequence.Kind.AST, year=year, value=999)
    code = generate_identifier('AST', year=year)
    assert code == "AST-2026-1000"

    code2 = generate_identifier('AST', year=year)
    assert code2 == "AST-2026-1001"


@pytest.mark.django_db
def test_generate_identifier_race_retry():
    """Verify retry handles IntegrityError on first counter creation."""
    year = 2026
    original_create = IdentifierSequence.objects.create
    call_count = [0]

    def flaky_create(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise IntegrityError("duplicate key value violates unique constraint")
        return original_create(*args, **kwargs)

    with patch.object(IdentifierSequence.objects, 'create', side_effect=flaky_create):
        code = generate_identifier('AST', year=year)
        assert code == "AST-2026-001"
        assert call_count[0] == 2


@pytest.mark.django_db
def test_generate_identifier_race_retry_finds_concurrent_record():
    """Verify retry finds concurrently created counter and increments it."""
    year = 2026
    call_count = [0]

    # Pre-create the sequence that another transaction would have created
    concurrent_seq = IdentifierSequence.objects.create(
        kind=IdentifierSequence.Kind.AST,
        year=year,
        value=1,
    )

    original_filter = IdentifierSequence.objects.filter

    class MockFilterQuerySet:
        def __init__(self, qs):
            self.qs = qs

        def select_for_update(self):
            return self

        def first(self):
            call_count[0] += 1
            if call_count[0] == 1:
                return None  # First check didn't see the row
            return concurrent_seq  # Retry check finds it

    def fake_filter(*args, **kwargs):
        return MockFilterQuerySet(original_filter(*args, **kwargs))

    def fail_create(*args, **kwargs):
        raise IntegrityError("duplicate key value violates unique constraint")

    with patch.object(IdentifierSequence.objects, 'filter', side_effect=fake_filter), \
         patch.object(IdentifierSequence.objects, 'create', side_effect=fail_create):
        code = generate_identifier('AST', year=year)
        # First attempt saw None, tried create, got IntegrityError.
        # Second attempt saw concurrent_seq with value=1, incremented to 2 -> AST-2026-002!
        assert code == "AST-2026-002"
