"""
Tests for local D1 Target and Worker Bridge harness.
"""
from pathlib import Path
import pytest

from apps.common.tests.harness_d1_target import (
    DEFAULT_PORT,
    DEFAULT_PERSIST_DIR,
    DEFAULT_ENV_FILE,
    DEFAULT_TOKEN,
    TARGET_TABLES,
    D1TargetHarness,
    create_env_file,
    assert_safe_environment,
)


def test_safe_environment_guard():
    """Verify isolation guards block remote or deploy flags."""
    with pytest.raises(RuntimeError, match="--remote is strictly forbidden"):
        assert_safe_environment(["wrangler", "dev", "--remote"])

    with pytest.raises(RuntimeError, match="--remote is strictly forbidden"):
        assert_safe_environment(["wrangler", "d1", "execute", "db", "-r"])

    with pytest.raises(RuntimeError, match="wrangler deploy is strictly forbidden"):
        assert_safe_environment(["wrangler", "deploy"])


def test_create_env_file(tmp_path):
    """Verify env file creation with token validation."""
    test_env = tmp_path / "test.env"
    created = create_env_file(test_env, DEFAULT_TOKEN)
    assert created.exists()
    content = created.read_text()
    assert f"BRIDGE_SECRET_TOKEN={DEFAULT_TOKEN}" in content

    with pytest.raises(ValueError, match="at least 32 characters"):
        create_env_file(tmp_path / "short.env", "short_token")


def test_d1_target_harness_lifecycle():
    """Verify D1 harness setup, query, table verification, and teardown."""
    with D1TargetHarness() as harness:
        assert harness.proc is not None
        assert harness.proc.poll() is None

        # Verify tables and clean state
        counts = harness.verify_clean()
        assert len(counts) >= 17
        assert "ai_suggestion_logs" in counts
        for tbl in TARGET_TABLES:
            assert tbl in counts
            assert counts[tbl] == 0

        # Query execution test
        res = harness.query("SELECT 100 as val;")
        assert res.get("success") is True
        assert res.get("results") == [{"val": 100}]

    # Post-context verification: persist dir and env file cleaned up
    assert not DEFAULT_PERSIST_DIR.exists()
    assert not DEFAULT_ENV_FILE.exists()
