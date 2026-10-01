"""Helpers shared by verify.py and the contract test for cases.yaml."""

import copy
from pathlib import Path
from typing import Any

import yaml

WF_DIR = Path(__file__).resolve().parent


def load(name: str) -> Any:
    """Load one YAML document from this folder."""
    with open(WF_DIR / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_all(name: str) -> list[dict]:
    """Load a multi-document YAML file from this folder."""
    with open(WF_DIR / name, encoding="utf-8") as fh:
        return [d for d in yaml.safe_load_all(fh) if d]


def deep_merge(base: Any, over: Any) -> Any:
    """Maps merge; lists and scalars are replaced."""
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for key, value in over.items():
            out[key] = deep_merge(base[key], value) if key in base else value
        return out
    return copy.deepcopy(over)


def _key(container: Any, token: str) -> Any:
    """Resolve a path token: dict key, list index, or list item by name."""
    if isinstance(container, list):
        if token.isdigit():
            return int(token)
        for i, item in enumerate(container):
            if isinstance(item, dict) and item.get("name") == token:
                return i
        raise KeyError(token)
    return token


def patch(doc: Any, patches: dict[str, Any] | None) -> Any:
    """Return a copy of ``doc`` with dotted-path ``patches`` applied."""
    doc = copy.deepcopy(doc)
    for path, value in (patches or {}).items():
        tokens = path.split(".")
        cur = doc
        for tok in tokens[:-1]:
            k = _key(cur, tok)
            if isinstance(cur, dict) and k not in cur:
                cur[k] = {}
            cur = cur[k]
        cur[_key(cur, tokens[-1])] = value
    return doc


def generate(definition: dict, spec: dict[str, int]) -> dict:
    """Build oversized inputs for the limit cases."""
    steps = definition["spec"]["steps"]
    if "pad_bytes" in spec:
        definition["metadata"]["padding"] = "x" * spec["pad_bytes"]
    while len(steps) < spec.get("steps", 0):
        clone = copy.deepcopy(steps[0])
        clone["name"] = f"gen{len(steps)}"
        steps.append(clone)
    if "mcp_servers" in spec:
        definition["spec"]["mcp_servers"] = [
            f"srv{i}" for i in range(spec["mcp_servers"])
        ]
    if "secret_headers" in spec:
        server = steps[0]["mcp_servers"][0]
        server["secret_headers"] = {
            f"H{i}": {"name": "mcp/incidents"} for i in range(spec["secret_headers"])
        }
    return definition


def case_body(case: dict, definitions: dict, defaults: dict) -> dict:
    """Request body for a workflow case (definition, provider, overrides)."""
    dflt = defaults[case["workflow"]]
    definition = generate(
        patch(definitions[case["workflow"]], case.get("set")), case.get("generate", {})
    )
    return patch(
        {"definition": definition, "provider": copy.deepcopy(dflt["provider"])},
        case.get("body_set"),
    )
