"""GET /v2/transfers/{transferId} — fetch a single transfer.

Read-only, except tests using the `quoted_transfer` fixture (one execute=false quote per run).
Run alone: `uv run python run.py get` or `uv run python tests/test_get.py`.
"""

import pytest

from transfers_suite.assertions import assert_auth_rejected, assert_error, assert_transfer
from transfers_suite.testing import MALFORMED_TRANSFER_ID, MISSING_TRANSFER_ID, require, run_file

pytestmark = pytest.mark.readonly

STABLE_FIELDS = ["transferId", "source", "target", "sourceAmount", "sourceAsset", "targetAsset", "fees",
                 "estimate", "expiresAt", "createdAt", "metadata"]


@pytest.mark.quote
def test_get_matches_create(client, quoted_transfer):
    t = assert_transfer(client.get(quoted_transfer["transferId"]))
    for field in STABLE_FIELDS:
        assert t.get(field) == quoted_transfer.get(field), f"{field} differs between create and get"
    # The quote is never executed, so it stays quoted (or moves to failed if it expired).
    assert t["status"] in ("quoted", "failed")


def test_get_matches_list_item(client, existing_transfers):
    item = require(existing_transfers[0] if existing_transfers else None, "no transfers on the account")
    t = assert_transfer(client.get(item["transferId"]))
    for field in STABLE_FIELDS + ["status", "completedAt", "executedAt", "failureReason"]:
        if field in item or field in t:
            assert t.get(field) == item.get(field), f"{field} differs between list and get"


@pytest.mark.parametrize("status", ["completed", "failed", "processing"])
def test_get_existing_by_status(client, status):
    """Validates the Transfer schema and status invariants for each status present on the account."""
    items = client.list(status=status, pageSize=1).body.get("transfers", [])
    item = require(items[0] if items else None, f"no {status} transfers on the account")
    assert_transfer(client.get(item["transferId"]), status=status)


@pytest.mark.negative
def test_get_unknown_transfer(client):
    assert_error(client.get(MISSING_TRANSFER_ID), 404, ("not_found",))


@pytest.mark.negative
@pytest.mark.parametrize("bad_id", [MALFORMED_TRANSFER_ID, "transfer_123", "account_00000000-0000-4000-8000-000000000000"])
def test_get_malformed_id(client, bad_id):
    assert_error(client.get(bad_id), (400, 404), ("invalid_request", "not_found"))


@pytest.mark.negative
def test_missing_auth(client):
    assert_auth_rejected(client.get(MISSING_TRANSFER_ID, auth="none"))


if __name__ == "__main__":
    run_file(__file__)
