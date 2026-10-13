"""GET /v2/transfers — list, paginate and filter transfers.

Read-only, except tests using the `quoted_transfer` fixture (one execute=false quote per run).
Run alone: `uv run python run.py list` or `uv run python tests/test_list.py`.
"""

from datetime import timedelta

import pytest

from transfers_suite.assertions import assert_auth_rejected, assert_error, assert_list, now, parse_ts
from transfers_suite.testing import (
    MALFORMED_TRANSFER_ID, MISSING_ACCOUNT_ID, MISSING_TRANSFER_ID, SAMPLE_ADDRESS, require, run_file,
)

pytestmark = pytest.mark.readonly


def involves(t: dict, account_id: str) -> bool:
    return account_id in (t["source"].get("accountId"), t["target"].get("accountId"))


# ---- shape & pagination --------------------------------------------------------------------

def test_list_shape(client):
    assert_list(client.list())


def test_default_page_size_is_at_most_20(client):
    assert len(assert_list(client.list())) <= 20


def test_page_size_respected(client):
    assert len(assert_list(client.list(pageSize=2))) <= 2


def test_sorted_newest_first(client, existing_transfers):
    require(len(existing_transfers) >= 2, "need at least 2 transfers on the account")
    created = [parse_ts(t["createdAt"]) for t in existing_transfers]
    assert created == sorted(created, reverse=True), "transfers not sorted by createdAt desc"


def test_pagination_is_stable_and_disjoint(client, existing_transfers):
    require(len(existing_transfers) >= 3, "need at least 3 transfers on the account to paginate")
    seen, token, pages = [], None, 0
    while pages < 3:
        resp = client.list(pageSize=1, pageToken=token)
        page = assert_list(resp)
        seen += [t["transferId"] for t in page]
        token = resp.body.get("nextPageToken")
        pages += 1
        if not token:
            break
    assert len(seen) == len(set(seen)), f"duplicate transfers across pages: {seen}"
    assert seen == [t["transferId"] for t in existing_transfers[: len(seen)]], "pages disagree with a single larger page"


@pytest.mark.negative
def test_invalid_page_token(client):
    assert_error(client.list(pageToken="definitely-not-a-token"), 400, ("invalid_request",))


@pytest.mark.negative
@pytest.mark.parametrize("size", [0, -1, "abc"])
def test_invalid_page_size(client, size):
    assert_error(client.list(pageSize=size), 400, ("invalid_request",))


# ---- filters -------------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["quoted", "processing", "completed", "failed"])
def test_filter_by_status(client, status):
    for t in assert_list(client.list(status=status)):
        assert t["status"] == status


@pytest.mark.quote
def test_filter_by_transfer_id(client, quoted_transfer):
    tid = quoted_transfer["transferId"]
    items = assert_list(client.list(transferId=tid))
    assert [t["transferId"] for t in items] == [tid]


def test_filter_by_transfer_id_unknown(client):
    assert assert_list(client.list(transferId=MISSING_TRANSFER_ID)) == []


def test_filter_by_account_id(client, cfg):
    account = require(cfg.source_account_id, "no source account configured")
    for t in assert_list(client.list(accountId=account)):
        assert involves(t, account)


def test_filter_by_source_account_id(client, cfg):
    account = require(cfg.source_account_id, "no source account configured")
    for t in assert_list(client.list(sourceAccountId=account)):
        assert t["source"].get("accountId") == account


def test_filter_by_target_account_id(client, cfg):
    s = require(next((s for s in cfg.enabled_scenarios if "accountId" in s.target), None),
                "no account_to_account scenario configured")
    account = s.target["accountId"]
    for t in assert_list(client.list(targetAccountId=account)):
        assert t["target"].get("accountId") == account


def test_filter_by_target_address(client, cfg):
    s = require(next((s for s in cfg.enabled_scenarios if "address" in s.target), None),
                "no account_to_address scenario configured")
    address = s.target["address"]
    for t in assert_list(client.list(targetAddress=address)):
        assert t["target"].get("address", "").lower() == address.lower()


def test_filter_by_target_email(client, cfg):
    s = require(next((s for s in cfg.enabled_scenarios if "email" in s.target), None),
                "no account_to_email scenario configured")
    email = s.target["email"]
    for t in assert_list(client.list(targetEmail=email)):
        assert t["target"].get("email", "").lower() == email.lower()


@pytest.mark.parametrize("param,field", [("sourceAsset", "sourceAsset"), ("targetAsset", "targetAsset")])
def test_filter_by_asset(client, existing_transfers, param, field):
    t0 = require(next((t for t in existing_transfers if t.get(field)), None), f"no existing transfer with {field}")
    asset = t0[field]
    items = assert_list(client.list(**{param: asset}))
    assert items, f"filter {param}={asset} returned nothing, but {t0['transferId']} matches"
    for t in items:
        assert t[field].lower() == asset.lower()


def test_filter_created_window(client, existing_transfers):
    t0 = require(existing_transfers[0] if existing_transfers else None, "no transfers on the account")
    created = parse_ts(t0["createdAt"])
    after = (created - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    before = (created + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    items = assert_list(client.list(createdAfter=after, createdBefore=before))
    assert t0["transferId"] in [t["transferId"] for t in items]
    for t in items:
        assert parse_ts(after) <= parse_ts(t["createdAt"]) <= parse_ts(before)


def test_filter_updated_after_future_is_empty(client):
    future = (now() + timedelta(days=365)).isoformat().replace("+00:00", "Z")
    assert assert_list(client.list(updatedAfter=future)) == []


def test_filter_updated_window(client, existing_transfers):
    t0 = require(existing_transfers[0] if existing_transfers else None, "no transfers on the account")
    updated = parse_ts(t0["updatedAt"])
    after = (updated - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    before = (updated + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    for t in assert_list(client.list(updatedAfter=after, updatedBefore=before)):
        assert parse_ts(after) <= parse_ts(t["updatedAt"]) <= parse_ts(before)


# ---- documented invalid filter combinations (HTTP 400) -------------------------------------

INVALID_COMBINATIONS = {
    "accountId+sourceAccountId": {"accountId": MISSING_ACCOUNT_ID, "sourceAccountId": MISSING_ACCOUNT_ID},
    "accountId+targetAccountId": {"accountId": MISSING_ACCOUNT_ID, "targetAccountId": MISSING_ACCOUNT_ID},
    "accountId+sourceAddress": {"accountId": MISSING_ACCOUNT_ID, "sourceAddress": SAMPLE_ADDRESS},
    "accountId+targetAddress": {"accountId": MISSING_ACCOUNT_ID, "targetAddress": SAMPLE_ADDRESS},
    "accountId+targetEmail": {"accountId": MISSING_ACCOUNT_ID, "targetEmail": "user@example.com"},
    "sourceAccountId+sourceAddress": {"sourceAccountId": MISSING_ACCOUNT_ID, "sourceAddress": SAMPLE_ADDRESS},
    "targetAccountId+targetAddress": {"targetAccountId": MISSING_ACCOUNT_ID, "targetAddress": SAMPLE_ADDRESS},
    "targetAccountId+targetEmail": {"targetAccountId": MISSING_ACCOUNT_ID, "targetEmail": "user@example.com"},
    "targetAddress+targetEmail": {"targetAddress": SAMPLE_ADDRESS, "targetEmail": "user@example.com"},
    "transferId+status": {"transferId": MISSING_TRANSFER_ID, "status": "quoted"},
    "transferId+accountId": {"transferId": MISSING_TRANSFER_ID, "accountId": MISSING_ACCOUNT_ID},
    "transferId+createdAfter": {"transferId": MISSING_TRANSFER_ID, "createdAfter": "2026-01-01T00:00:00Z"},
}


@pytest.mark.negative
@pytest.mark.parametrize("params", INVALID_COMBINATIONS.values(), ids=INVALID_COMBINATIONS.keys())
def test_invalid_filter_combination(client, params):
    assert_error(client.list(**params), 400, ("invalid_request",))


def test_transfer_id_allows_pagination_params(client):
    """Pagination params are permitted alongside transferId."""
    assert_list(client.list(transferId=MISSING_TRANSFER_ID, pageSize=5))


# ---- invalid filter values -----------------------------------------------------------------

INVALID_VALUES = {
    "status": {"status": "pending"},
    "transferId": {"transferId": MALFORMED_TRANSFER_ID},
    "accountId": {"accountId": "account_nope"},
    "createdAfter": {"createdAfter": "yesterday"},
    "updatedBefore": {"updatedBefore": "2026-13-45"},
    "targetEmail": {"targetEmail": "not-an-email"},
}


@pytest.mark.negative
@pytest.mark.parametrize("params", INVALID_VALUES.values(), ids=INVALID_VALUES.keys())
def test_invalid_filter_value(client, params):
    assert_error(client.list(**params), 400, ("invalid_request",))


# ---- auth ----------------------------------------------------------------------------------

@pytest.mark.negative
def test_missing_auth(client):
    assert_auth_rejected(client.list(auth="none"))


@pytest.mark.negative
def test_invalid_token(client):
    assert_auth_rejected(client.list(auth="Bearer not.a.jwt"))


if __name__ == "__main__":
    run_file(__file__)
