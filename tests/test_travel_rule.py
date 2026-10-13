"""POST /v2/transfers/{transferId}/travel-rule — submit travel rule info for a held deposit.

Submissions against a real deposit are marked `travel_rule` and skipped unless --allow-execute is passed.
They need travel_rule.deposit_transfer_id (a deposit held for compliance review) in config.toml, and the
full-submission test needs travel_rule.payload_file with real originator data.
Run alone: `uv run python run.py travel-rule [--allow-execute]` or `uv run python tests/test_travel_rule.py`.
"""

import json
import re

import pytest

from transfers_suite.assertions import assert_auth_rejected, assert_error, assert_status
from transfers_suite.config import ROOT
from transfers_suite.schemas import assert_schema
from transfers_suite.testing import MALFORMED_TRANSFER_ID, MISSING_TRANSFER_ID, require, run_file

MINIMAL_BODY = {"beneficiary": {"name": "Test Beneficiary"}, "isSelf": False}
FIELD_PATH_RE = re.compile(r"^[A-Za-z]+(\.[A-Za-z]+)*$")


def assert_travel_rule_response(resp) -> dict:
    assert_status(resp, 200)
    body = resp.body
    assert_schema(body, "DepositTravelRuleResponse")
    if body["status"] == "completed":
        assert not body.get("missingFields"), "missingFields must be empty when status=completed"
    else:
        assert body["status"] == "incomplete"
        assert body.get("missingFields"), "incomplete submission must list missingFields"
        assert body.get("reason"), "incomplete submission must include a reason"
        for f in body["missingFields"]:
            assert FIELD_PATH_RE.match(f), f"missingFields entry {f!r} is not a dot-separated path"
    return body


# ---- safe ----------------------------------------------------------------------------------

@pytest.mark.negative
def test_unknown_transfer(client):
    assert_error(client.travel_rule(MISSING_TRANSFER_ID, MINIMAL_BODY), 404, ("not_found",))


@pytest.mark.negative
def test_malformed_transfer_id(client):
    assert_error(client.travel_rule(MALFORMED_TRANSFER_ID, MINIMAL_BODY), (400, 404),
                 ("invalid_request", "not_found"))


@pytest.mark.negative
def test_missing_auth(client):
    assert_auth_rejected(client.travel_rule(MISSING_TRANSFER_ID, MINIMAL_BODY, auth="none"))


@pytest.mark.quote
@pytest.mark.negative
def test_rejected_for_non_deposit_transfer(client, quoted_transfer):
    """Travel rule submission only applies to deposits held for review; an outbound quote should be rejected."""
    assert_error(client.travel_rule(quoted_transfer["transferId"], MINIMAL_BODY), (400, 404, 409, 422))


# ---- gated: submissions against a real held deposit -------------------------------------------

@pytest.fixture
def deposit_id(cfg):
    return require(cfg.travel_rule_transfer_id, "set travel_rule.deposit_transfer_id in config.toml")


@pytest.mark.travel_rule
@pytest.mark.negative
def test_attest_requires_intermediary(client, deposit_id):
    body = {**MINIMAL_BODY, "attestVerifiedWalletOwnership": True}
    assert_error(client.travel_rule(deposit_id, body), 400, ("invalid_request",))


@pytest.mark.travel_rule
def test_partial_submission_reports_missing_fields(client, deposit_id):
    """With only a beneficiary name, the API should either accept or list exactly what's missing."""
    assert_travel_rule_response(client.travel_rule(deposit_id, MINIMAL_BODY))


@pytest.mark.travel_rule
def test_full_submission_completes(client, cfg, deposit_id):
    path = require(cfg.travel_rule_payload_file, "set travel_rule.payload_file in config.toml")
    body = json.loads((ROOT / path).read_text())
    result = assert_travel_rule_response(client.travel_rule(deposit_id, body))
    assert result["status"] == "completed", f"still missing: {result.get('missingFields')} ({result.get('reason')})"


if __name__ == "__main__":
    run_file(__file__)
