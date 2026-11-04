import os
import pytest


def pytest_addoption(parser):
    """Register custom command-line flags."""
    parser.addoption(
        "--rehearsal",
        action="store_true",
        default=False,
        help="Run end-to-end local rehearsal tests requiring isolated Docker and Wrangler.",
    )


def pytest_collection_modifyitems(config, items):
    """
    Opt-in gate for rehearsal marker.
    Cleanly skips all @pytest.mark.rehearsal tests unless --rehearsal is passed
    or ASSETHUB_RUN_REHEARSAL=1 is set in the environment.
    """
    opt_in = config.getoption("--rehearsal", False) or os.environ.get("ASSETHUB_RUN_REHEARSAL") == "1"
    if not opt_in:
        skip_marker = pytest.mark.skip(
            reason="Rehearsal test skipped. Opt-in with --rehearsal or ASSETHUB_RUN_REHEARSAL=1."
        )
        for item in items:
            if item.get_closest_marker("rehearsal"):
                item.add_marker(skip_marker)


def pytest_runtest_setup(item):
    """Runtime guard for rehearsal marker."""
    if item.get_closest_marker("rehearsal"):
        opt_in = item.config.getoption("--rehearsal", False) or os.environ.get("ASSETHUB_RUN_REHEARSAL") == "1"
        if not opt_in:
            pytest.skip("Rehearsal test skipped. Opt-in with --rehearsal or ASSETHUB_RUN_REHEARSAL=1.")
