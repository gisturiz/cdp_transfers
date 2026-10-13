"""POST /v2/transfers/{transferId}/execute — execute a quoted transfer.

Tests marked `moves_funds` are skipped unless --allow-execute is passed, and the client refuses any
amount above safety.max_amount in config.toml. The remaining tests only hit IDs that cannot execute.
Run alone: `uv run python run.py execute [--allow-execute]` or `uv run python tests/test_execute.py`.
"""

import time

import pytest

from transfers_suite.assertions import assert_auth_rejected, assert_error, assert_transfer, now, parse_ts
from transfers_suite.client import new_idempotency_key
from transfers_suite.testing import MALFORMED_TRANSFER_ID, MISSING_TRANSFER_ID, require, run_file


def execute_path(transfer_id: str) -> str:
    return f"/v2/transfers/{transfer_id}/execute"


# ---- safe: requests that cannot move funds -------------------------------------------------

@pytest.mark.negative
def test_execute_unknown_transfer(client):
    assert_error(client.request("POST", execute_path(MISSING_TRANSFER_ID)), 404, ("not_found",))


@pytest.mark.negative
def test_execute_malformed_id(client):
    assert_error(client.request("POST", execute_path(MALFORMED_TRANSFER_ID)), (400, 404),
                 ("invalid_request", "not_found"))


@pytest.mark.negative
def test_execute_missing_auth(client):
    assert_auth_rejected(client.request("POST", execute_path(MISSING_TRANSFER_ID), auth="none"))


@pytest.mark.negative
def test_execute_completed_transfer_rejected(client):
    """A completed transfer can no longer be executed (it is not in `quoted` status)."""
    items = client.list(status="completed", pageSize=1).body.get("transfers", [])
    t = require(items[0] if items else None, "no completed transfers on the account")
    assert_error(client.request("POST", execute_path(t["transferId"])), (400, 409, 422))


# ---- gated: real execution -----------------------------------------------------------------

@pytest.fixture
def exec_scenario(cfg):
    name = require(cfg.execute_scenario, "set safety.execute_scenario in config.toml")
    return require(cfg.scenario(name), f"execute_scenario {name!r} is not an enabled, complete scenario")


@pytest.mark.moves_funds
def test_execute_quote_lifecycle(client, exec_scenario, run_metadata):
    """quoted -> processing -> completed|failed, with timestamps and failureReason per status."""
    quote = assert_transfer(client.create(exec_scenario.body(metadata=run_metadata)), status="quoted")
    key = new_idempotency_key()

    executed = assert_transfer(client.execute(quote["transferId"], idempotency_key=key),
                               status={"processing", "completed", "failed"})
    assert executed["transferId"] == quote["transferId"]
    if "executedAt" in executed:
        assert parse_ts(executed["executedAt"]) <= now()

    # Replaying the same idempotency key returns the same transfer instead of executing twice.
    replay = assert_transfer(client.execute(quote["transferId"], idempotency_key=key))
    assert replay["transferId"] == quote["transferId"]

    final = client.wait_for_status(quote["transferId"], {"completed", "failed"})
    assert final["status"] in ("completed", "failed"), f"still {final['status']} after poll timeout"
    final = assert_transfer(client.get(quote["transferId"]))
    if final["status"] == "completed":
        assert final.get("targetAmount"), "completed transfer must have the actual targetAmount"
        if "executedAt" in final:
            assert parse_ts(final["completedAt"]) >= parse_ts(final["executedAt"])
    # The estimate is an immutable pre-execution snapshot.
    assert final.get("estimate") == quote.get("estimate")


@pytest.mark.moves_funds
def test_execute_twice_without_key_rejected(client, exec_scenario, run_metadata):
    quote = assert_transfer(client.create(exec_scenario.body(metadata=run_metadata)), status="quoted")
    assert_transfer(client.execute(quote["transferId"]))
    assert_error(client.execute(quote["transferId"]), (400, 409, 422))


@pytest.mark.moves_funds
def test_create_with_execute_true(client, exec_scenario, run_metadata):
    """execute=true skips the manual execute step: the transfer is already past `quoted`."""
    t = assert_transfer(client.create(exec_scenario.body(execute=True, metadata=run_metadata)),
                        status={"processing", "completed", "failed"})
    assert "expiresAt" not in t or t["status"] == "quoted"


@pytest.mark.moves_funds
@pytest.mark.slow
def test_execute_expired_quote_rejected(client, exec_scenario, run_metadata):
    """Quotes are valid ~10-15 minutes; executing after expiresAt must fail."""
    quote = assert_transfer(client.create(exec_scenario.body(metadata=run_metadata)), status="quoted")
    wait = (parse_ts(quote["expiresAt"]) - now()).total_seconds() + 5
    time.sleep(max(wait, 0))
    assert_error(client.execute(quote["transferId"]), (400, 409, 422))


if __name__ == "__main__":
    run_file(__file__)
