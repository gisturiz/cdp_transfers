"""Extract the Transfers API subset from the CDP v2 OpenAPI spec.

Usage:
    uv run python tools/refresh_spec.py              # download the latest spec
    uv run python tools/refresh_spec.py spec.yaml    # use a local copy

Writes transfers_suite/spec/transfers_openapi.json, containing the /v2/transfers
paths plus every component schema they reference (transitively). The test suite
validates responses against these schemas.
"""

import json
import sys
from pathlib import Path

import httpx
import yaml

SPEC_URL = "https://docs.cdp.coinbase.com/api-reference/v2/cdp-api-v2.yaml"
OUT = Path(__file__).resolve().parent.parent / "transfers_suite" / "spec" / "transfers_openapi.json"


def load_spec(src: str | None) -> dict:
    if src:
        return yaml.safe_load(Path(src).read_text())
    resp = httpx.get(SPEC_URL, timeout=60, follow_redirects=True)
    resp.raise_for_status()
    return yaml.safe_load(resp.text)


def collect_refs(node, components: dict, seen: dict[str, set[str]]) -> None:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/"):
            _, _, kind, name = ref.split("/", 3)
            if name not in seen.setdefault(kind, set()):
                seen[kind].add(name)
                collect_refs(components[kind][name], components, seen)
        for value in node.values():
            collect_refs(value, components, seen)
    elif isinstance(node, list):
        for item in node:
            collect_refs(item, components, seen)


def main() -> None:
    spec = load_spec(sys.argv[1] if len(sys.argv) > 1 else None)
    paths = {p: ops for p, ops in spec["paths"].items() if p.startswith("/v2/transfers")}
    seen: dict[str, set[str]] = {}
    collect_refs(paths, spec["components"], seen)
    # Always include the response roots the suite validates against.
    for name in ("Transfer", "Error", "DepositTravelRuleResponse", "TransferStatus"):
        if name not in seen.setdefault("schemas", set()):
            seen["schemas"].add(name)
            collect_refs(spec["components"]["schemas"][name], spec["components"], seen)

    subset = {
        "openapi": spec["openapi"],
        "info": {"title": "CDP Transfers API (extracted)", "version": spec.get("info", {}).get("version", "")},
        "paths": paths,
        "components": {
            kind: {name: spec["components"][kind][name] for name in sorted(names)}
            for kind, names in sorted(seen.items())
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(subset, indent=1, sort_keys=False) + "\n")
    counts = ", ".join(f"{len(v)} {k}" for k, v in sorted(seen.items()))
    print(f"Wrote {OUT} ({len(paths)} paths, {counts})")


if __name__ == "__main__":
    main()
