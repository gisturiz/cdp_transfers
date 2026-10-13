"""Loads config.toml and credentials from the environment (.env)."""

import json
import os
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Scenario:
    """A source/target pair to quote against. See config.example.toml."""

    name: str
    kind: str  # account_to_address | account_to_account | account_to_email | account_to_payment_method
    amount: str
    asset: str
    source: dict
    target: dict
    enabled: bool = True

    def body(self, **overrides) -> dict:
        body = {
            "source": dict(self.source),
            "target": dict(self.target),
            "amount": self.amount,
            "asset": self.asset,
            "execute": False,
        }
        body.update(overrides)
        return body


@dataclass
class Config:
    base_url: str
    auth_mode: str  # cdp_jwt | bearer | none
    timeout_seconds: float
    max_amount: Decimal
    poll_timeout_seconds: float
    poll_interval_seconds: float
    scenarios: list[Scenario]
    execute_scenario: str | None
    travel_rule_transfer_id: str | None
    travel_rule_payload_file: str | None
    api_key_id: str | None = field(default=None, repr=False)
    api_key_secret: str | None = field(default=None, repr=False)
    bearer_token: str | None = field(default=None, repr=False)

    @property
    def enabled_scenarios(self) -> list[Scenario]:
        return [s for s in self.scenarios if s.enabled and _scenario_complete(s)]

    def scenario(self, name: str) -> Scenario | None:
        return next((s for s in self.enabled_scenarios if s.name == name), None)

    def first_scenario(self) -> Scenario | None:
        return self.enabled_scenarios[0] if self.enabled_scenarios else None

    @property
    def source_account_id(self) -> str | None:
        s = self.first_scenario()
        return s.source.get("accountId") if s else None


def _scenario_complete(s: Scenario) -> bool:
    """A scenario only runs once every value in it is filled in."""
    values = [s.amount, s.asset, *s.source.values(), *s.target.values()]
    return all(isinstance(v, str) and v.strip() for v in values)


def _load_credentials() -> tuple[str | None, str | None]:
    key_id = os.getenv("CDP_API_KEY_ID")
    secret = os.getenv("CDP_API_KEY_SECRET")
    key_file = os.getenv("CDP_KEY_FILE")
    if (not key_id or not secret) and key_file:
        data = json.loads(Path(key_file).expanduser().read_text())
        key_id = key_id or data.get("id") or data.get("name")
        secret = secret or data.get("privateKey")
    if secret:
        secret = secret.replace("\\n", "\n")
    return key_id, secret


def load_config(path: str | os.PathLike | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    path = Path(path or os.getenv("TRANSFERS_CONFIG") or ROOT / "config.toml")
    if not path.exists():
        path = ROOT / "config.example.toml"
    raw = tomllib.loads(path.read_text())

    api = raw.get("api", {})
    safety = raw.get("safety", {})
    travel = raw.get("travel_rule", {})
    scenarios = [Scenario(**s) for s in raw.get("scenarios", [])]
    key_id, secret = _load_credentials()

    return Config(
        base_url=os.getenv("TRANSFERS_BASE_URL") or api.get("base_url", "https://api.cdp.coinbase.com/platform"),
        auth_mode=api.get("auth", "cdp_jwt"),
        timeout_seconds=float(api.get("timeout_seconds", 30)),
        max_amount=Decimal(str(safety.get("max_amount", "0"))),
        poll_timeout_seconds=float(safety.get("poll_timeout_seconds", 300)),
        poll_interval_seconds=float(safety.get("poll_interval_seconds", 5)),
        scenarios=scenarios,
        execute_scenario=safety.get("execute_scenario") or None,
        travel_rule_transfer_id=travel.get("deposit_transfer_id") or None,
        travel_rule_payload_file=travel.get("payload_file") or None,
        api_key_id=key_id,
        api_key_secret=secret,
        bearer_token=os.getenv("TRANSFERS_API_TOKEN"),
    )
