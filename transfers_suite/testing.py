"""Small helpers shared by the test modules."""

import sys

import pytest

# Well-formed IDs that should not exist; used for 404 cases.
MISSING_TRANSFER_ID = "transfer_00000000-0000-4000-8000-000000000000"
MISSING_ACCOUNT_ID = "account_00000000-0000-4000-8000-000000000000"
MALFORMED_TRANSFER_ID = "not-a-transfer-id"
SAMPLE_ADDRESS = "0x742d35Cc6634C0532925a3b844Bc454e4438f44e"


def require(value, reason: str):
    if not value:
        pytest.skip(reason)
    return value


def run_file(path: str) -> None:
    """Lets each test module be run directly: `uv run python tests/test_list.py [pytest args]`."""
    sys.exit(pytest.main([path, *sys.argv[1:]]))
