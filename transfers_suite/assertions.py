"""Reusable assertions for Transfers API responses."""

import re
import warnings
from datetime import datetime, timezone
from decimal import Decimal

from .client import ApiResponse
from .schemas import assert_schema, list_response_errors

TRANSFER_ID_RE = re.compile(r"^transfer_[a-f0-9\-]{36}$")
DECIMAL_RE = re.compile(r"^\d+(\.\d+)?$")
STATUSES = {"quoted", "processing", "completed", "failed"}


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def now() -> datetime:
    return datetime.now(timezone.utc)


def assert_status(resp: ApiResponse, *expected: int) -> None:
    assert resp.status in expected, f"expected HTTP {expected}, got {resp!r}"


def assert_error(resp: ApiResponse, statuses: tuple[int, ...] | int, error_types: tuple[str, ...] | None = None) -> None:
    """Asserts an error response: expected HTTP status, Error schema, and (optionally) errorType."""
    statuses = (statuses,) if isinstance(statuses, int) else statuses
    assert_status(resp, *statuses)
    assert_schema(resp.body, "Error")
    if error_types:
        assert resp.error_type in error_types, f"expected errorType in {error_types}, got {resp.error_type!r}"


class SpecDeviation(UserWarning):
    """Observed API behavior that differs from the published OpenAPI spec."""


def assert_auth_rejected(resp: ApiResponse) -> None:
    """401 for missing/invalid credentials.

    Observed on CDP (Oct 2026): the gateway answers with a plain-text "Unauthorized" body rather than the
    JSON Error object the spec documents, so a non-JSON body is reported as a SpecDeviation warning.
    """
    assert_status(resp, 401)
    if isinstance(resp.body, dict):
        assert_schema(resp.body, "Error")
    else:
        warnings.warn(SpecDeviation(f"401 body is not a JSON Error object: {resp.body!r}"))


def assert_decimal_string(value, field: str) -> Decimal:
    assert isinstance(value, str) and DECIMAL_RE.match(value), f"{field} should be a decimal string, got {value!r}"
    return Decimal(value)


def assert_transfer(resp: ApiResponse, *, status: str | set[str] | None = None) -> dict:
    """Asserts a 200 response with a full Transfer resource and checks the invariants the docs describe."""
    assert_status(resp, 200)
    t = resp.body
    assert_schema(t, "Transfer")

    assert TRANSFER_ID_RE.match(t.get("transferId", "")), f"bad transferId {t.get('transferId')!r}"
    assert t.get("status") in STATUSES, f"unexpected status {t.get('status')!r}"
    if status:
        wanted = {status} if isinstance(status, str) else status
        assert t["status"] in wanted, f"expected status in {wanted}, got {t['status']!r}"

    for f in ("createdAt", "updatedAt"):
        assert f in t, f"missing {f}"
        parse_ts(t[f])
    assert parse_ts(t["updatedAt"]) >= parse_ts(t["createdAt"]), "updatedAt precedes createdAt"

    if "sourceAmount" in t:
        assert_decimal_string(t["sourceAmount"], "sourceAmount")
    if "targetAmount" in t:
        assert_decimal_string(t["targetAmount"], "targetAmount")
    for i, fee in enumerate(t.get("fees", [])):
        assert_decimal_string(fee["amount"], f"fees[{i}].amount")

    # Status-dependent fields, per the API reference.
    if t["status"] == "quoted":
        assert "expiresAt" in t, "quoted transfer must have expiresAt"
    if t["status"] == "failed":
        assert t.get("failureReason"), "failed transfer must have failureReason"
    else:
        assert "failureReason" not in t, "failureReason only allowed when status=failed"
    if t["status"] == "completed":
        assert "completedAt" in t, "completed transfer must have completedAt"
    # executedAt is documented but omitted in the spec's own `completed` example, so only check consistency.
    if "executedAt" in t:
        assert t["status"] != "quoted", "quoted transfer must not have executedAt"
    return t


def assert_list(resp: ApiResponse) -> list[dict]:
    assert_status(resp, 200)
    errors = list_response_errors(resp.body)
    assert not errors, "List response does not match schema:\n  " + "\n  ".join(errors[:20])
    return resp.body["transfers"]


def assert_matches_request(transfer: dict, body: dict) -> None:
    """The created transfer should echo back what was requested."""
    for side in ("source", "target"):
        for key, value in body[side].items():
            got = transfer[side].get(key)
            assert str(got).lower() == str(value).lower(), f"{side}.{key}: sent {value!r}, got {got!r}"
    for key, value in body.get("metadata", {}).items():
        assert transfer.get("metadata", {}).get(key) == value, f"metadata.{key} not echoed back"


def target_amount(transfer: dict) -> Decimal | None:
    """targetAmount may be omitted in `quoted` state; fall back to estimate.targetAmount."""
    value = transfer.get("targetAmount") or transfer.get("estimate", {}).get("targetAmount")
    return Decimal(value) if value else None


def total_fees(transfer: dict, asset: str) -> Decimal:
    return sum((Decimal(f["amount"]) for f in transfer.get("fees", []) if f["asset"].lower() == asset.lower()), Decimal(0))
