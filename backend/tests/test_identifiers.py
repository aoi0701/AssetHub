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


@pytest.mark.django_db(transaction=True)
def test_generate_identifier_concurrent_threads_unique():
    """
    Behavioral concurrency test: Multiple concurrent threads calling generate_identifier
    must all produce strictly unique identifiers with zero collision and correct formatting.
    Verifies atomic UPSERT RETURNING behavior under concurrent load.
    """
    from concurrent.futures import ThreadPoolExecutor
    from django.db import connections

    year = 2030
    num_workers = 10
    total_calls = 30

    def worker(_):
        try:
            return generate_identifier('AST', year=year)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        results = list(executor.map(worker, range(total_calls)))

    assert len(results) == total_calls
    assert len(set(results)) == total_calls

    # Format correctness: all must start with AST-2030- followed by 3+ digits
    for code in results:
        assert code.startswith(f"AST-{year}-")
        num_str = code.split("-")[-1]
        assert num_str.isdigit()
        assert len(num_str) >= 3

    # Ensure all sequential numbers 1..total_calls were generated
    generated_numbers = sorted(int(code.split("-")[-1]) for code in results)
    assert generated_numbers == list(range(1, total_calls + 1))


@pytest.mark.django_db(transaction=True)
def test_generate_identifier_multi_kind_concurrency_isolation():
    """
    Behavioral test: Concurrent generation across multiple kinds (AST and REQ)
    in the same year maintains completely isolated sequence counters without interference.
    """
    from concurrent.futures import ThreadPoolExecutor
    from django.db import connections

    year = 2031
    calls_per_kind = 15

    def worker(kind):
        try:
            return kind, generate_identifier(kind, year=year)
        finally:
            connections.close_all()

    tasks = ['AST'] * calls_per_kind + ['REQ'] * calls_per_kind
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(worker, tasks))

    ast_codes = [code for kind, code in results if kind == 'AST']
    req_codes = [code for kind, code in results if kind == 'REQ']

    assert len(ast_codes) == calls_per_kind
    assert len(set(ast_codes)) == calls_per_kind

    assert len(req_codes) == calls_per_kind
    assert len(set(req_codes)) == calls_per_kind

    # Verify both kinds independently generated sequences 1..calls_per_kind
    ast_nums = sorted(int(code.split("-")[-1]) for code in ast_codes)
    req_nums = sorted(int(code.split("-")[-1]) for code in req_codes)

    assert ast_nums == list(range(1, calls_per_kind + 1))
    assert req_nums == list(range(1, calls_per_kind + 1))

    # All AST codes match AST-2031-NNN, all REQ codes match REQ-2031-NNN
    for code in ast_codes:
        assert code.startswith(f"AST-{year}-")
    for code in req_codes:
        assert code.startswith(f"REQ-{year}-")
