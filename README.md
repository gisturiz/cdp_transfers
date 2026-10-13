# CDP Transfers API — contract test suite

Tests for the Coinbase CDP [Transfers API](https://docs.cdp.coinbase.com/api-reference/v2/rest-api/transfers/transfers).
They check each endpoint's request/response shape, its status lifecycle, its validation rules and its
error codes, so we can rebuild the same API on our side. The same tests can then run against our
implementation by changing `base_url`.

| Endpoint | Test file | Run |
|---|---|---|
| `POST /v2/transfers` | `tests/test_create.py` | `uv run python run.py create` |
| `GET /v2/transfers` | `tests/test_list.py` | `uv run python run.py list` |
| `GET /v2/transfers/{transferId}` | `tests/test_get.py` | `uv run python run.py get` |
| `POST /v2/transfers/{transferId}/execute` | `tests/test_execute.py` | `uv run python run.py execute` |
| `POST /v2/transfers/{transferId}/travel-rule` | `tests/test_travel_rule.py` | `uv run python run.py travel-rule` |
| All of the above | | `uv run python run.py all` |

You can also run each file directly (`uv run python tests/test_list.py`) or use pytest directly (`uv run pytest`).
Extra arguments go straight to pytest: `-k idempotency`, `-x`, `--junitxml=reports/junit.xml`, etc.

## Setup

1. Install [uv](https://docs.astral.sh/uv/), then run `uv sync`.
2. `cp .env.example .env` and point `CDP_KEY_FILE` at your CDP API key JSON. You can set
   `CDP_API_KEY_ID` / `CDP_API_KEY_SECRET` instead.
3. The key needs the **`transfers:read`** and **`transfers:write`** scopes, set in CDP Portal → API Keys.
   If they're missing, the run stops right away with a preflight message.
4. `cp config.example.toml config.toml` and fill in real account IDs, addresses, emails and payment
   method IDs under `[[scenarios]]`. A scenario with any blank value is skipped, along with the tests
   that depend on it.

`.env`, `config.toml`, `travel_rule_payload.json` and `reports/` are gitignored.

## Safety: what moves money

The account is **live**. By default the suite never moves funds:

- **readonly**: GET requests only.
- **quote**: creates transfers with `execute=false` (quotes that simply expire) or `validateOnly=true`.
- **negative**: requests the API should reject. They always send `execute=false`.
- **moves_funds** / **travel_rule**: skipped unless you pass `--allow-execute`.
- **slow**: waits about 15 minutes for a quote to expire. Skipped unless you pass `--run-slow`.

Even with `--allow-execute`, `TransfersClient` refuses any execute where the amount is above
`safety.max_amount` (default `0`, which blocks everything). To run the real lifecycle test:

```toml
[safety]
max_amount = "0.05"
execute_scenario = "usdc_to_base_address"
```

```sh
uv run python run.py execute --allow-execute
```

Every transfer the suite creates has `metadata.test_run_id` and `metadata.created_by = "cdp-transfers-suite"`.

## Recorded traffic = reference fixtures

Each run writes every request/response pair to `reports/<run_id>/<test>/NN.json`, with the auth
header redacted. These files show exactly how CDP responds. Use them as fixtures when building and
testing our own implementation. Pass `--no-record` to turn this off.

## Testing our own implementation

```sh
TRANSFERS_BASE_URL=http://localhost:8000 uv run python run.py all
```

In `config.toml`, set `[api] auth = "bearer"` and `TRANSFERS_API_TOKEN` in `.env` (or `auth = "none"`).
Paths stay the same (`/v2/transfers...`), so mount the API at the same prefix.

## How responses are validated

- **Schemas** come from the official OpenAPI spec. `transfers_suite/spec/transfers_openapi.json` is
  the Transfers subset of the spec. Regenerate it with `uv run python tools/refresh_spec.py`.
- **Invariants** come from the docs, and live in `transfers_suite/assertions.py`. For example:
  - a `quoted` transfer has `expiresAt`
  - only a `failed` transfer has `failureReason`
  - a `completed` transfer has `completedAt`
  - amounts are decimal strings
  - `amountType=source/target` fee math adds up
  - `estimate` doesn't change after execution

### Known deviations from the spec

Deviations show up as `SpecDeviation` warnings in the pytest summary, not as failures:

- **401 body**: when auth is missing or invalid, CDP returns a plain-text `Unauthorized` body. The
  spec documents a JSON `Error` object.
- **Example IDs**: some spec examples use transfer IDs that don't match the documented pattern
  `^transfer_[a-f0-9-]{36}$` (e.g. `...-5gf8-...`). The suite enforces the pattern.
- **`executedAt`**: the spec's own `completed` example has no `executedAt`, so the suite doesn't require it.

## Layout

```
run.py                     entry point (one endpoint or all)
config.example.toml        scenarios, safety caps, travel-rule inputs
transfers_suite/
  client.py                HTTP client: CDP JWT auth, retries on 429/502/503, funds guard, recording
  config.py                config + credential loading
  schemas.py               JSON Schema validation against the extracted spec
  assertions.py            shared response assertions
  testing.py               shared test constants/helpers
tests/                     one file per endpoint + conftest.py (fixtures, flags, preflight)
tools/refresh_spec.py      re-extract the Transfers subset from the published spec
```
