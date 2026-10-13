import uuid
from datetime import datetime, timezone

import pytest

from transfers_suite.assertions import assert_transfer
from transfers_suite.client import Recorder, TransfersClient
from transfers_suite.config import ROOT, load_config

RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]


def pytest_addoption(parser):
    group = parser.getgroup("transfers")
    group.addoption("--allow-execute", action="store_true",
                    help="Allow tests that execute transfers or submit travel rule data (moves real funds).")
    group.addoption("--run-slow", action="store_true", help="Run tests that wait for quote expiry or settlement.")
    group.addoption("--config", default=None, help="Path to config TOML (default: config.toml).")
    group.addoption("--no-record", action="store_true", help="Do not write request/response files to reports/.")
    group.addoption("--skip-preflight", action="store_true", help="Do not stop the run when the credentials are rejected.")


def pytest_collection_modifyitems(config, items):
    allow = config.getoption("--allow-execute")
    slow = config.getoption("--run-slow")
    for item in items:
        if not allow and ({"moves_funds", "travel_rule"} & set(item.keywords)):
            item.add_marker(pytest.mark.skip(reason="moves funds / submits compliance data: pass --allow-execute"))
        if not slow and "slow" in item.keywords:
            item.add_marker(pytest.mark.skip(reason="slow: pass --run-slow"))


def pytest_report_header(config):
    cfg = load_config(config.getoption("--config"))
    names = [s.name for s in cfg.enabled_scenarios] or ["none configured"]
    return [
        f"transfers: base_url={cfg.base_url} auth={cfg.auth_mode} run_id={RUN_ID}",
        f"transfers: scenarios={', '.join(names)} allow_execute={config.getoption('--allow-execute')} "
        f"max_amount={cfg.max_amount}",
    ]


@pytest.fixture(scope="session")
def cfg(pytestconfig):
    return load_config(pytestconfig.getoption("--config"))


@pytest.fixture(scope="session")
def recorder(pytestconfig):
    return Recorder(None if pytestconfig.getoption("--no-record") else ROOT / "reports" / RUN_ID)


@pytest.fixture(scope="session")
def client(cfg, recorder, pytestconfig):
    c = TransfersClient(cfg, recorder, allow_execute=pytestconfig.getoption("--allow-execute"))
    yield c
    c.close()


@pytest.fixture(scope="session", autouse=True)
def _preflight(client, pytestconfig):
    """Stop early with one clear message if the API rejects our credentials, instead of failing every test."""
    if pytestconfig.getoption("--skip-preflight"):
        return
    resp = client.list(pageSize=1)
    if resp.status in (401, 403):
        detail = resp.body.get("errorMessage") if isinstance(resp.body, dict) else resp.body
        pytest.exit(
            f"Preflight GET /v2/transfers returned {resp.status}: {detail}\n"
            "The API key needs the transfers:read and transfers:write scopes (CDP Portal > API Keys). "
            "Use --skip-preflight to run anyway.",
            returncode=3,
        )


@pytest.fixture(autouse=True)
def _record_as_test(request, recorder):
    recorder.test_name = request.node.nodeid.split("::", 1)[-1]
    yield
    recorder.test_name = "session"


@pytest.fixture(scope="session")
def run_metadata():
    """Tag every transfer the suite creates so test traffic can be found later."""
    return {"test_run_id": RUN_ID, "created_by": "cdp-transfers-suite"}


@pytest.fixture
def scenario(cfg):
    s = cfg.first_scenario()
    if not s:
        pytest.skip("no scenario configured in config.toml")
    return s


@pytest.fixture(scope="session")
def quoted_transfer(cfg, client, run_metadata):
    """One quote (execute=false) shared by get/list tests. Never executed, so it simply expires."""
    s = cfg.first_scenario()
    if not s:
        pytest.skip("no scenario configured in config.toml")
    body = s.body(metadata=run_metadata)
    resp = client.create(body)
    return assert_transfer(resp, status="quoted")


@pytest.fixture(scope="session")
def existing_transfers(client):
    """Most recent page of transfers already on the account, for read-only checks (may be empty)."""
    resp = client.list(pageSize=20)
    if not resp.ok:
        pytest.fail(f"list transfers failed: {resp!r}")
    return resp.body.get("transfers", [])

