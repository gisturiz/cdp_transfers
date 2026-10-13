"""Run the Transfers API test suite, all at once or one endpoint at a time.

    uv run python run.py all                      # every endpoint (safe tests only)
    uv run python run.py create                   # POST /v2/transfers
    uv run python run.py list                     # GET  /v2/transfers
    uv run python run.py get                      # GET  /v2/transfers/{id}
    uv run python run.py execute --allow-execute  # POST /v2/transfers/{id}/execute (moves funds!)
    uv run python run.py travel-rule              # POST /v2/transfers/{id}/travel-rule

Any extra arguments are passed straight to pytest, e.g. `-k idempotency`, `-x`, `-v`,
`--run-slow`, `--config other.toml`, `--no-record`, `--junitxml=reports/junit.xml`.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
SUITES = {
    "create": "tests/test_create.py",
    "list": "tests/test_list.py",
    "get": "tests/test_get.py",
    "execute": "tests/test_execute.py",
    "travel-rule": "tests/test_travel_rule.py",
}


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in (*SUITES, "all"):
        print(__doc__)
        return 0 if argv and argv[0] in ("-h", "--help") else 2
    target, extra = argv[0], argv[1:]
    paths = list(SUITES.values()) if target == "all" else [SUITES[target]]
    return pytest.main([*(str(ROOT / p) for p in paths), "-v", *extra])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
