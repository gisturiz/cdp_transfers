"""POST /v2/transfers — create a transfer (quote).

Every request here uses execute=false and/or validateOnly=true, so no funds move.
Run alone: `uv run python run.py create` or `uv run python tests/test_create.py`.
"""

from decimal import Decimal

import pytest

from transfers_suite.assertions import (
    assert_auth_rejected,
    assert_error, assert_matches_request, assert_status, assert_transfer, now, parse_ts, target_amount, total_fees,
)
from transfers_suite.client import new_idempotency_key
from transfers_suite.schemas import assert_schema
from transfers_suite.testing import MISSING_ACCOUNT_ID, SAMPLE_ADDRESS, require, run_file

pytestmark = pytest.mark.quote

SCENARIO_KINDS = ["account_to_address", "account_to_account", "account_to_email", "account_to_payment_method"]


def scenarios_of(cfg, kind):
    return [s for s in cfg.enabled_scenarios if s.kind == kind]


# ---- happy paths, one per configured scenario kind ------------------------------------------

@pytest.mark.parametrize("kind", SCENARIO_KINDS)
def test_validate_only_accepts_valid_request(cfg, client, kind, run_metadata):
    """validateOnly=true returns 2xx for a valid request and does not create a transfer."""
    for s in require(scenarios_of(cfg, kind), f"no enabled {kind} scenario in config.toml"):
        resp = client.create(s.body(validateOnly=True, metadata=run_metadata))
        assert_status(resp, 200, 201, 204)
        if isinstance(resp.body, dict) and resp.body:
            assert_schema(resp.body, "Transfer")
            if tid := resp.body.get("transferId"):
                # If an ID is returned at all, it must not be a persisted transfer.
                assert client.get(tid).status == 404, "validateOnly should not persist a transfer"


@pytest.mark.parametrize("kind", SCENARIO_KINDS)
def test_create_quote(cfg, client, kind, run_metadata):
    """execute=false creates a transfer in `quoted` status with fees and an expiry."""
    for s in require(scenarios_of(cfg, kind), f"no enabled {kind} scenario in config.toml"):
        body = s.body(metadata=run_metadata)
        t = assert_transfer(client.create(body), status="quoted")
        assert_matches_request(t, body)

        expires = parse_ts(t["expiresAt"])
        assert now() < expires, "quote already expired"
        assert (expires - parse_ts(t["createdAt"])).total_seconds() <= 3600, "quote validity longer than expected"
        assert "executedAt" not in t and "completedAt" not in t
        assert isinstance(t.get("fees", []), list)
        assert t.get("sourceAmount"), "quote should include sourceAmount"
        assert target_amount(t) is not None, "quote should include targetAmount or estimate.targetAmount"
        if s.source.get("asset", "").lower() != s.target.get("asset", "").lower():
            assert t.get("exchangeRate") or t.get("estimate", {}).get("exchangeRate"), \
                "conversion quote should include an exchange rate"


def test_amount_type_source(client, scenario, run_metadata):
    """amountType=source: `amount` is taken from the source; target receives amount minus fees."""
    body = scenario.body(amountType="source", metadata=run_metadata)
    t = assert_transfer(client.create(body), status="quoted")
    assert Decimal(t["sourceAmount"]) == Decimal(scenario.amount)
    same_asset = scenario.source["asset"].lower() == scenario.target["asset"].lower()
    if same_asset and (received := target_amount(t)) is not None:
        assert received == Decimal(scenario.amount) - total_fees(t, scenario.source["asset"])


def test_amount_type_target(client, scenario, run_metadata):
    """amountType=target: target receives exactly `amount`; fees are added on the source side."""
    body = scenario.body(amountType="target", metadata=run_metadata)
    t = assert_transfer(client.create(body), status="quoted")
    assert target_amount(t) == Decimal(scenario.amount)
    same_asset = scenario.source["asset"].lower() == scenario.target["asset"].lower()
    if same_asset:
        assert Decimal(t["sourceAmount"]) == Decimal(scenario.amount) + total_fees(t, scenario.source["asset"])


def test_metadata_round_trip(client, scenario, run_metadata):
    metadata = {**run_metadata, "order_reference": "order-67890", "empty": ""}
    t = assert_transfer(client.create(scenario.body(metadata=metadata)), status="quoted")
    assert t.get("metadata") == metadata


# ---- idempotency --------------------------------------------------------------------------

def test_idempotent_replay_returns_same_transfer(client, scenario, run_metadata):
    key = new_idempotency_key()
    body = scenario.body(metadata=run_metadata)
    first = assert_transfer(client.create(body, idempotency_key=key))
    second = assert_transfer(client.create(body, idempotency_key=key))
    assert first["transferId"] == second["transferId"]


def test_idempotency_key_reused_with_different_body(client, scenario, run_metadata):
    key = new_idempotency_key()
    assert_transfer(client.create(scenario.body(metadata=run_metadata), idempotency_key=key))
    changed = scenario.body(metadata=run_metadata, amount=str(Decimal(scenario.amount) * 2))
    assert_error(client.create(changed, idempotency_key=key), 422, ("idempotency_error",))


# ---- validation errors ------------------------------------------------------------------

@pytest.mark.negative
@pytest.mark.parametrize("field", ["source", "target", "amount", "asset", "execute"])
def test_missing_required_field(client, scenario, field):
    body = scenario.body()
    del body[field]
    assert_error(client.create(body), 400, ("invalid_request",))


@pytest.mark.negative
@pytest.mark.parametrize("amount", ["abc", "-1", "0", "", "1e3", "1,000.00"])
def test_invalid_amount(client, scenario, amount):
    assert_error(client.create(scenario.body(amount=amount)), 400, ("invalid_request",))


@pytest.mark.negative
def test_amount_as_number_not_string(client, scenario):
    assert_error(client.create(scenario.body(amount=float(scenario.amount))), 400, ("invalid_request",))


@pytest.mark.negative
def test_unknown_asset(client, scenario):
    assert_error(client.create(scenario.body(asset="notarealasset")), 400, ("invalid_request",))


@pytest.mark.negative
def test_invalid_amount_type(client, scenario):
    assert_error(client.create(scenario.body(amountType="both")), 400, ("invalid_request",))


@pytest.mark.negative
def test_execute_must_be_boolean(client, scenario):
    assert_error(client.create(scenario.body(execute="false")), 400, ("invalid_request",))


@pytest.mark.negative
def test_malformed_source_account_id(client, scenario):
    body = scenario.body()
    body["source"]["accountId"] = "account_not-a-uuid"
    assert_error(client.create(body), 400, ("invalid_request",))


@pytest.mark.negative
def test_unknown_source_account(client, scenario):
    body = scenario.body()
    body["source"]["accountId"] = MISSING_ACCOUNT_ID
    assert_error(client.create(body), (400, 403, 404))


@pytest.mark.negative
def test_target_with_multiple_kinds(client, scenario):
    """target is oneOf Account | PaymentMethod | OnchainAddress | EmailInstrument."""
    body = scenario.body()
    body["target"] = {"accountId": MISSING_ACCOUNT_ID, "address": SAMPLE_ADDRESS, "network": "base",
                      "asset": body["target"]["asset"]}
    assert_error(client.create(body), 400, ("invalid_request",))


@pytest.mark.negative
def test_onchain_target_without_network(client, scenario):
    body = scenario.body()
    body["target"] = {"address": SAMPLE_ADDRESS, "asset": body["target"]["asset"]}
    assert_error(client.create(body), 400, ("invalid_request",))


@pytest.mark.negative
def test_onchain_target_unknown_network(client, scenario):
    body = scenario.body()
    body["target"] = {"address": SAMPLE_ADDRESS, "network": "notanetwork", "asset": body["target"]["asset"]}
    assert_error(client.create(body), 400, ("invalid_request",))


@pytest.mark.negative
def test_invalid_target_email(client, scenario):
    body = scenario.body()
    body["target"] = {"email": "not-an-email", "asset": body["target"]["asset"]}
    assert_error(client.create(body), 400, ("invalid_request",))


@pytest.mark.negative
def test_metadata_too_many_keys(client, scenario):
    metadata = {f"k{i}": "v" for i in range(11)}
    assert_error(client.create(scenario.body(metadata=metadata)), 400, ("invalid_request",))


@pytest.mark.negative
def test_metadata_key_too_long(client, scenario):
    assert_error(client.create(scenario.body(metadata={"k" * 41: "v"})), 400, ("invalid_request",))


@pytest.mark.negative
def test_metadata_value_too_long(client, scenario):
    assert_error(client.create(scenario.body(metadata={"k": "v" * 501})), 400, ("invalid_request",))


@pytest.mark.negative
def test_metadata_non_string_value(client, scenario):
    assert_error(client.create(scenario.body(metadata={"k": 1})), 400, ("invalid_request",))


@pytest.mark.negative
def test_travel_rule_attest_requires_intermediary(client, scenario):
    """attestVerifiedWalletOwnership=true without isIntermediary=true must return 400."""
    body = scenario.body(validateOnly=True, travelRule={"attestVerifiedWalletOwnership": True})
    assert_error(client.create(body), 400, ("invalid_request",))


@pytest.mark.negative
def test_empty_body(client):
    resp = client.request("POST", "/v2/transfers", headers={"Content-Type": "application/json"})
    assert_error(resp, 400, ("invalid_request",))


# ---- auth ----
# Rejected before validation, so the body doesn't need real accounts. execute=false: never moves funds.
AUTH_PROBE_BODY = {"source": {"accountId": MISSING_ACCOUNT_ID, "asset": "usdc"},
                   "target": {"accountId": MISSING_ACCOUNT_ID, "asset": "usdc"},
                   "amount": "1.00", "asset": "usdc", "execute": False}
# ----------------------------------------------------------------------------------

@pytest.mark.negative
def test_missing_auth(client):
    assert_auth_rejected(client.create(AUTH_PROBE_BODY, auth="none"))


@pytest.mark.negative
def test_invalid_token(client):
    assert_auth_rejected(client.create(AUTH_PROBE_BODY, auth="Bearer not.a.jwt"))


if __name__ == "__main__":
    run_file(__file__)
