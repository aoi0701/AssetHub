"""
Test Suite: Test Isolation Guard (Agent 2)
Verifies:
1. Direct D1Client calls to unauthorized endpoints are blocked before sending any mutation.
2. Missing or insecure configurations (token < 32 chars, missing URL) are rejected immediately.
3. Django ORM cursor path blocks mutations when pointed to unauthorized endpoints.
4. Production runtime is not falsely blocked when is_test_environment() is False.
5. Authorized test endpoint operations are permitted.
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from unittest.mock import patch

# Load test environment without exposing or printing secrets
env_paths = [
    Path("/app/.env.test"),
    Path(__file__).resolve().parents[3] / ".env.test",
    Path(__file__).resolve().parents[4] / "spikes" / "cf-d1-spike" / ".env.test",
]
for p in env_paths:
    if p.exists():
        load_dotenv(p)
        break

import pytest
from django.db import OperationalError, connections
from assethub_api.backends.d1.client import D1Client, is_test_environment, ALLOWED_TEST_BRIDGE_URLS
from apps.departments.models import Department


@pytest.fixture(scope="module")
def valid_test_credentials():
    url = os.environ.get("D1_BRIDGE_URL", "https://assethub-test-worker.hnhoa494-assethub.workers.dev")
    token = os.environ.get("D1_BRIDGE_TOKEN")
    if not token or len(token.strip()) < 32:
        pytest.skip("D1_BRIDGE_TOKEN not configured with >= 32 chars in test environment.")
    return url, token


def test_guard_blocks_direct_client_mutation_to_unauthorized_url():
    """Direct D1Client mutation to unauthorized URL must be blocked before sending any HTTP request."""
    unauthorized_url = "https://unauthorized-production-worker.workers.dev"
    client = D1Client(
        base_url=unauthorized_url,
        token="secure-random-test-token-at-least-32-chars-long",
    )

    mutation_sql = "INSERT INTO departments_department (code, name) VALUES ('UNAUTH_01', 'Blocked Dept')"
    with patch("urllib.request.urlopen") as mock_urlopen:
        with pytest.raises(OperationalError) as exc_info:
            client.query(mutation_sql)

        # Transport must never be reached! Neither token nor SQL leaked over wire
        mock_urlopen.assert_not_called()

    assert "Test Isolation Guard: Mutation blocked to unauthorized bridge endpoint" in str(exc_info.value)
    assert unauthorized_url in str(exc_info.value)


@pytest.mark.parametrize("malicious_url, expected_err", [
    (
        "https://assethub-test-worker.hnhoa494-assethub.workers.dev.evil-attacker.com",
        "Test Isolation Guard: Mutation blocked to unauthorized bridge endpoint",
    ),
    (
        "http://assethub-test-worker.hnhoa494-assethub.workers.dev",
        "Test Isolation Guard: Mutation blocked to unauthorized bridge endpoint",
    ),
    (
        "https://assethub-test-worker.hnhoa494-assethub.workers.dev:8443",
        "Test Isolation Guard: Mutation blocked to unauthorized bridge endpoint",
    ),
    (
        "https://admin:supersecret@assethub-test-worker.hnhoa494-assethub.workers.dev",
        "Test Isolation Guard: Bridge URL with userinfo is not permitted",
    ),
    (
        "ftp://assethub-test-worker.hnhoa494-assethub.workers.dev",
        "Test Isolation Guard: Unsupported bridge URL scheme",
    ),
])
def test_guard_blocks_spoofed_or_malformed_urls_before_transport(malicious_url, expected_err):
    """
    Verifies that hostname suffix spoofing, scheme spoofing, port spoofing,
    and userinfo injection are strictly rejected before any network transport.
    """
    client = D1Client(
        base_url=malicious_url,
        token="secure-random-test-token-at-least-32-chars-long",
    )

    mutation_sql = "INSERT INTO departments_department (code, name) VALUES ('SPOOF_01', 'Hacker Dept')"
    with patch("urllib.request.urlopen") as mock_urlopen:
        with pytest.raises(OperationalError) as exc_info:
            client.query(mutation_sql)

        # Crucial: transport is NOT called, no credentials/SQL sent across network
        mock_urlopen.assert_not_called()

    assert expected_err in str(exc_info.value)


def test_guard_blocks_missing_or_short_token_and_missing_url():
    """Missing or short token and missing URL must raise OperationalError immediately."""
    # 1. Missing URL
    with pytest.raises(OperationalError) as exc1:
        D1Client(base_url="", token="valid-secure-token-32-characters-long")
    assert "D1_BRIDGE_URL environment variable is required" in str(exc1.value)

    # 2. Missing Token
    with pytest.raises(OperationalError) as exc2:
        D1Client(base_url="https://assethub-test-worker.hnhoa494-assethub.workers.dev", token="")
    assert "D1_BRIDGE_TOKEN must be configured" in str(exc2.value)

    # 3. Token too short (< 32 chars)
    with pytest.raises(OperationalError) as exc3:
        D1Client(base_url="https://assethub-test-worker.hnhoa494-assethub.workers.dev", token="short-token-123")
    assert ">=32 chars" in str(exc3.value)


@pytest.mark.django_db(databases="__all__")
def test_guard_blocks_orm_path_with_unauthorized_endpoint():
    """Django ORM DatabaseWrapper and cursor executing mutations against unauthorized endpoint must be blocked."""
    from assethub_api.backends.d1.base import DatabaseWrapper

    unauthorized_url = "https://evil-unauthorized-db.workers.dev"
    wrapper = DatabaseWrapper({
        "ENGINE": "assethub_api.backends.d1",
        "NAME": "assethub-test-d1",
        "BRIDGE_URL": unauthorized_url,
        "BRIDGE_TOKEN": "valid-secure-token-32-characters-long",
        "TIMEOUT": 5.0,
    })

    # Attempt ORM cursor insert via unauthorized connection
    with patch("urllib.request.urlopen") as mock_urlopen:
        with wrapper.cursor() as cursor:
            with pytest.raises(OperationalError) as exc_info:
                cursor.execute("INSERT INTO departments_department (code, name) VALUES (%s, %s)", ["UNAUTH_ORM", "Blocked via ORM"])

        # ORM layer must also block before sending any HTTP packet
        mock_urlopen.assert_not_called()

    assert "Test Isolation Guard: Mutation blocked to unauthorized bridge endpoint" in str(exc_info.value)
    assert unauthorized_url in str(exc_info.value)


def test_guard_does_not_block_production_runtime():
    """When running outside of test environment, the test isolation guard does NOT block production URLs."""
    prod_url = "https://assethub-prod-worker.assethub.workers.dev"
    client = D1Client(
        base_url=prod_url,
        token="production-secure-token-32-characters-long",
    )

    # Mock is_test_environment to False (simulating production execution)
    with patch("assethub_api.backends.d1.client.is_test_environment", return_value=False):
        # We also mock urllib.request.urlopen to verify that execution reaches the network layer without guard block
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = mock_urlopen.return_value.__enter__.return_value
            mock_resp.read.return_value = b'{"success": true, "meta": {"changes": 1}}'

            res = client.query("INSERT INTO departments_department (code) VALUES ('PROD_TEST')")
            assert res.get("success") is True
            # Verify urlopen was called (meaning guard did NOT block the request)
            assert mock_urlopen.called is True


@pytest.mark.django_db(databases="__all__")
def test_guard_does_not_block_orm_production_runtime():
    """Django ORM cursor targeting production endpoint is NOT blocked outside test environment."""
    from assethub_api.backends.d1.base import DatabaseWrapper

    prod_url = "https://assethub-prod-worker.assethub.workers.dev"
    wrapper = DatabaseWrapper({
        "ENGINE": "assethub_api.backends.d1",
        "NAME": "assethub-prod-d1",
        "BRIDGE_URL": prod_url,
        "BRIDGE_TOKEN": "production-secure-token-32-characters-long",
        "TIMEOUT": 5.0,
    })

    with patch("assethub_api.backends.d1.client.is_test_environment", return_value=False):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = mock_urlopen.return_value.__enter__.return_value
            mock_resp.read.return_value = b'{"success": true, "meta": {"changes": 1}}'

            with wrapper.cursor() as cursor:
                cursor.execute("INSERT INTO departments_department (code, name) VALUES (%s, %s)", ["PROD_CODE", "Dept"])

            # Verify request successfully reached network transport
            assert mock_urlopen.called is True


def test_guard_permits_authorized_test_endpoint(valid_test_credentials):
    """Mutations to approved test bridge worker are permitted and succeed."""
    url, token = valid_test_credentials
    client = D1Client(base_url=url, token=token)

    # Verify query executes cleanly on approved test worker
    res = client.query("SELECT 1 as num")
    assert res.get("success") is True
    assert res["results"][0]["num"] == 1
