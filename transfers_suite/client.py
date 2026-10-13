"""HTTP client for the Transfers API, with auth, a funds-safety guard and request recording."""

import json
import re
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cdp.auth.utils.jwt import JwtOptions, generate_jwt

from .config import Config

TRANSFERS_PATH = "/v2/transfers"
RETRY_STATUSES = {429, 502, 503}
MAX_RETRIES = 3


class FundsGuardError(RuntimeError):
    """Raised when a request would move funds without explicit permission."""


@dataclass
class ApiResponse:
    status: int
    body: dict | list | str | None
    headers: dict
    method: str
    url: str
    request_body: dict | None
    elapsed_ms: int

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def error_type(self) -> str | None:
        return self.body.get("errorType") if isinstance(self.body, dict) else None

    def __repr__(self) -> str:
        return f"<{self.method} {self.url} -> {self.status} {json.dumps(self.body)[:500]}>"


class Recorder:
    """Writes every request/response pair to reports/<run_id>/<test>/NN.json."""

    def __init__(self, root: Path | None):
        self.root = root
        self.test_name = "session"
        self._counters: dict[str, int] = {}

    def record(self, resp: ApiResponse, request_headers: dict) -> None:
        if not self.root:
            return
        n = self._counters.get(self.test_name, 0) + 1
        self._counters[self.test_name] = n
        folder = self.root / re.sub(r"[^\w.\-\[\]]+", "_", self.test_name)
        folder.mkdir(parents=True, exist_ok=True)
        safe_headers = {k: ("<redacted>" if k.lower() == "authorization" else v) for k, v in request_headers.items()}
        (folder / f"{n:02d}.json").write_text(json.dumps({
            "request": {"method": resp.method, "url": resp.url, "headers": safe_headers, "body": resp.request_body},
            "response": {"status": resp.status, "headers": resp.headers, "body": resp.body, "elapsed_ms": resp.elapsed_ms},
        }, indent=2, default=str))


class TransfersClient:
    def __init__(self, config: Config, recorder: Recorder | None = None, allow_execute: bool = False):
        self.config = config
        self.recorder = recorder or Recorder(None)
        self.allow_execute = allow_execute
        self._http = httpx.Client(base_url=config.base_url.rstrip("/"), timeout=config.timeout_seconds)

    def close(self) -> None:
        self._http.close()

    # ---- auth -----------------------------------------------------------------

    def _auth_header(self, method: str, url: str, auth: str | None) -> dict:
        """`auth` overrides the configured mode: "none" or a literal "Bearer ..." value."""
        if auth == "none":
            return {}
        if auth is not None:
            return {"Authorization": auth}
        mode = self.config.auth_mode
        if mode == "none":
            return {}
        if mode == "bearer":
            return {"Authorization": f"Bearer {self.config.bearer_token}"}
        if not (self.config.api_key_id and self.config.api_key_secret):
            raise RuntimeError("Missing CDP credentials: set CDP_KEY_FILE or CDP_API_KEY_ID/CDP_API_KEY_SECRET in .env")
        parts = urlsplit(url)
        token = generate_jwt(JwtOptions(
            api_key_id=self.config.api_key_id,
            api_key_secret=self.config.api_key_secret,
            request_method=method,
            request_host=parts.netloc,
            request_path=parts.path,
        ))
        return {"Authorization": f"Bearer {token}"}

    # ---- transport ------------------------------------------------------------

    def request(self, method: str, path: str, *, json_body: dict | None = None, params: dict | None = None,
                headers: dict | None = None, auth: str | None = None) -> ApiResponse:
        """Unguarded raw request. Prefer the endpoint methods below, which enforce the funds guard."""
        url = str(self._http.build_request(method, path, params=params).url)
        resp = None
        for attempt in range(MAX_RETRIES + 1):
            req_headers = {"Accept": "application/json", **(headers or {}), **self._auth_header(method, url, auth)}
            if json_body is not None:
                req_headers["Content-Type"] = "application/json"
            started = time.monotonic()
            raw = self._http.request(method, url, json=json_body, headers=req_headers)
            try:
                body = raw.json()
            except ValueError:
                body = raw.text or None
            resp = ApiResponse(
                status=raw.status_code, body=body, headers=dict(raw.headers), method=method, url=url,
                request_body=json_body, elapsed_ms=int((time.monotonic() - started) * 1000),
            )
            self.recorder.record(resp, req_headers)
            if raw.status_code not in RETRY_STATUSES or attempt == MAX_RETRIES:
                break
            time.sleep(float(raw.headers.get("Retry-After", 2 ** attempt)))
        return resp

    # ---- endpoints ------------------------------------------------------------

    def create(self, body: dict, idempotency_key: str | None = None, **kw) -> ApiResponse:
        """POST /v2/transfers"""
        if body.get("execute") is True and body.get("validateOnly") is not True:
            self._guard_amount(body.get("amount"), "create with execute=true")
        return self.request("POST", TRANSFERS_PATH, json_body=body, headers=_idem(idempotency_key), **kw)

    def list(self, **params) -> ApiResponse:
        """GET /v2/transfers"""
        auth = params.pop("auth", None)
        return self.request("GET", TRANSFERS_PATH, params={k: v for k, v in params.items() if v is not None}, auth=auth)

    def get(self, transfer_id: str, **kw) -> ApiResponse:
        """GET /v2/transfers/{transferId}"""
        return self.request("GET", f"{TRANSFERS_PATH}/{transfer_id}", **kw)

    def execute(self, transfer_id: str, idempotency_key: str | None = None, **kw) -> ApiResponse:
        """POST /v2/transfers/{transferId}/execute — guarded: looks up the quote and enforces max_amount."""
        current = self.get(transfer_id)
        if current.ok:
            self._guard_amount(current.body.get("sourceAmount"), f"execute {transfer_id}")
        elif not self.allow_execute:
            raise FundsGuardError("execute requires --allow-execute")
        return self.request("POST", f"{TRANSFERS_PATH}/{transfer_id}/execute", headers=_idem(idempotency_key), **kw)

    def travel_rule(self, transfer_id: str, body: dict, idempotency_key: str | None = None, **kw) -> ApiResponse:
        """POST /v2/transfers/{transferId}/travel-rule"""
        return self.request("POST", f"{TRANSFERS_PATH}/{transfer_id}/travel-rule", json_body=body,
                            headers=_idem(idempotency_key), **kw)

    def list_all(self, max_pages: int = 5, **params) -> list[dict]:
        """Follows nextPageToken for up to `max_pages` pages."""
        items, token = [], None
        for _ in range(max_pages):
            resp = self.list(**params, pageToken=token)
            assert resp.ok, resp
            items.extend(resp.body.get("transfers", []))
            token = resp.body.get("nextPageToken")
            if not token:
                break
        return items

    def wait_for_status(self, transfer_id: str, statuses: set[str]) -> dict:
        deadline = time.monotonic() + self.config.poll_timeout_seconds
        while True:
            resp = self.get(transfer_id)
            assert resp.ok, resp
            if resp.body.get("status") in statuses or time.monotonic() > deadline:
                return resp.body
            time.sleep(self.config.poll_interval_seconds)

    def _guard_amount(self, amount, action: str) -> None:
        if not self.allow_execute:
            raise FundsGuardError(f"Refusing to {action}: pass --allow-execute to move funds")
        try:
            value = Decimal(str(amount))
        except InvalidOperation:
            raise FundsGuardError(f"Refusing to {action}: unparseable amount {amount!r}")
        if self.config.max_amount <= 0 or value > self.config.max_amount:
            raise FundsGuardError(f"Refusing to {action}: amount {value} exceeds safety.max_amount {self.config.max_amount}")


def _idem(key: str | None) -> dict:
    return {"X-Idempotency-Key": key} if key else {}


def new_idempotency_key() -> str:
    return str(uuid.uuid4())
