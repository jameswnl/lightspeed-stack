"""Verify examples/workflows coherence (plan stack#51 gate, steps 3-5).

1. YAML-parse all example files.
2. WorkflowDefinition.model_validate both definitions (logical provider names
   pass the shape check by design: InferenceProviderSpec.name is min_length=1).
3. Cross-reference every logical name in the definitions against the
   lightspeed-stack.yaml catalogs (governance, plan step 4).
4. Check the intended role's policy rules grant every referenced item
   (policy, plan step 5), including the same-entry rule.
5. Validate the non-PLAN subset of lightspeed-stack.yaml against the CURRENT
   Configuration schema (scratch-only strip of PLAN keys).
"""

import copy
import sys
from pathlib import Path

import yaml

WF_DIR = Path(__file__).resolve().parent
REPO_ROOT = WF_DIR.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
INTENDED_ROLE = {
    "triage-github-issue": "agent-user",
    "kb-answer-with-approval": "agent-support",
}
RUN_PROVIDER = {
    "triage-github-issue": ("claude-prod", "claude-sonnet-4-5"),
    "kb-answer-with-approval": ("openai-team-b", "gpt-4o"),
}
SPAWNER_DEFAULT_IMAGE = "quay.io/example/lightspeed-agentic-sandbox:1.4"

failures = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


# --- 1. parse ---
stack = yaml.safe_load(open(f"{WF_DIR}/lightspeed-stack.yaml"))
defs = {}
for name in INTENDED_ROLE:
    defs[name] = yaml.safe_load(open(f"{WF_DIR}/{name}.yaml"))
    list(yaml.safe_load_all(open(f"{WF_DIR}/k8s-secrets.yaml")))  # multi-doc sanity
check(True, "all YAML files parse")

# --- 2. typed shape check with real cloud-agents models ---
from cloud_agents.workflow.core.definition import WorkflowDefinition

for name, raw in defs.items():
    WorkflowDefinition.model_validate(raw)
    check(True, f"{name}: WorkflowDefinition shape valid")

# --- catalog indexes ---
we = stack["workflow_engine"]
providers = {p["name"]: p for p in we["providers"]}
secret_names = {s["name"] for s in we["secrets"]}
mcp = {m["name"]: m for m in stack["mcp_servers"]}
rules = {r["roles"][0]: r for r in we["policy"]["rules"]}
# resolve inherit
for role, rule in rules.items():
    merged = {}
    for parent in rule.get("inherit", []):
        for k, v in rules[parent].items():
            if k in ("roles", "inherit"):
                continue
            merged.setdefault(k, [])
            if isinstance(v, list):
                merged[k] = sorted(set(merged[k]) | set(v))
            else:
                merged[k] = v
    for k, v in rule.items():
        if k in ("roles", "inherit"):
            continue
        if isinstance(v, list) and k in merged:
            merged[k] = sorted(set(merged[k]) | set(v))
        else:
            merged[k] = v
    rule["effective"] = merged

grants = lambda role, key: rules[role]["effective"].get(key, [])


def eff_list(step_val, wf_val):
    """Merge rule: step wins; None inherits; [] explicitly empty."""
    return wf_val if step_val is None else step_val


# --- 3+4. per-definition governance + policy checks ---
for name, raw in defs.items():
    role = INTENDED_ROLE[name]
    run_provider, run_model = RUN_PROVIDER[name]
    spec = raw["spec"]
    wf_mcp = spec.get("mcp_servers") or []
    wf_skills = spec.get("allowed_skills") or []
    wf_spawn = spec.get("spawn")

    # run-level provider: catalog + allowed_models + role grant
    check(run_provider in providers, f"{name}: run provider in catalog")
    entry = providers[run_provider]
    am = entry.get("allowed_models")
    check(am is None or run_model in am, f"{name}: run model allowed")
    check(run_provider in grants(role, "providers"), f"{name}: role may use provider")
    check(
        entry["credential"]["name"] in secret_names, f"{name}: entry credential bound"
    )

    # definition.provider: same-entry rule
    if raw.get("provider"):
        check(
            raw["provider"]["name"] == run_provider,
            f"{name}: definition.provider same entry as run provider",
        )

    for step in spec["steps"]:
        s = step["name"]
        if step.get("type", "agent") != "agent":
            continue
        # step provider override: same-entry rule
        ip = step.get("inference_provider")
        if ip:
            check(ip["name"] == run_provider, f"{name}/{s}: step provider same entry")
            check(am is None or ip["model"] in am, f"{name}/{s}: step model allowed")
        # MCP: catalog + workflow_enabled + role grant
        for m in eff_list(step.get("mcp_servers"), wf_mcp):
            check(m in mcp, f"{name}/{s}: MCP {m} in catalog")
            check(
                mcp[m].get("workflow_enabled") is True,
                f"{name}/{s}: MCP {m} workflow_enabled",
            )
            check(m in grants(role, "mcp_servers"), f"{name}/{s}: role may use MCP {m}")
            for ref in (mcp[m].get("secret_headers") or {}).values():
                check(ref["name"] in secret_names, f"{name}/{s}: MCP secret ref bound")
                check(
                    ref["name"] in grants(role, "mcp_secrets"),
                    f"{name}/{s}: role granted MCP secret (principal,server,ref)",
                )
        # skills: role grant
        for sk in eff_list(step.get("allowed_skills"), wf_skills):
            check(sk in grants(role, "skills"), f"{name}/{s}: role may use skill {sk}")
        # tools
        for t in step.get("tools", []):
            check(t in grants(role, "tools"), f"{name}/{s}: role may use tool {t}")
        # spawn: step -> workflow -> ephemeral
        spawn = step.get("spawn") or wf_spawn or "ephemeral"
        check(spawn in grants(role, "spawn"), f"{name}/{s}: role may use spawn {spawn}")
        # image: run/step override or spawner default
        img = (step.get("spawn_config") or {}).get(
            "sandbox_image"
        ) or SPAWNER_DEFAULT_IMAGE
        check(img in grants(role, "sandbox_images"), f"{name}/{s}: image allowed")
        # service account: step -> workflow
        sa = step.get("service_account") or spec.get("service_account")
        check(
            sa in grants(role, "service_accounts"),
            f"{name}/{s}: service account allowed",
        )
        # namespaces
        for ns in step.get("target_namespaces", []):
            check(ns in grants(role, "namespaces"), f"{name}/{s}: namespace allowed")
        # limits
        lim = rules[role]["effective"].get("limits", {})
        if step.get("timeout_seconds"):
            check(
                step["timeout_seconds"] <= lim["max_timeout_seconds"],
                f"{name}/{s}: timeout within limit",
            )
        perms = step.get("permissions") or {}
        if perms.get("max_tokens"):
            check(
                perms["max_tokens"] <= lim["max_tokens"],
                f"{name}/{s}: tokens within limit",
            )
        check(
            step.get("max_retries", 0) <= lim.get("max_retries", 0),
            f"{name}/{s}: retries within limit",
        )
    if spec.get("timeout_seconds"):
        check(
            spec["timeout_seconds"]
            <= rules[role]["effective"]["limits"]["max_timeout_seconds"],
            f"{name}: workflow timeout within limit",
        )

# --- 5. current-schema validation of the non-PLAN subset (scratch strip) ---
stripped = copy.deepcopy(stack)
we_s = stripped["workflow_engine"]
for k in ("providers", "secrets", "default_provider", "default_model", "policy"):
    we_s.pop(k, None)
for m in stripped["mcp_servers"]:
    m.pop("secret_headers", None)
    m.pop("workflow_enabled", None)

import os
import tempfile

os.environ.setdefault("POSTGRES_PASSWORD", "test-only")

# openshell_tls_* are FilePath: in-cluster they come from the mounted
# openshell-gateway-tls Secret; stand in dummy files for local validation.
for _key in ("openshell_tls_ca", "openshell_tls_cert", "openshell_tls_key"):
    _fd, _path = tempfile.mkstemp()
    os.close(_fd)
    stripped["spawner"][_key] = _path

from models.config import Configuration

Configuration.model_validate(stripped)
check(True, "non-PLAN subset validates against CURRENT Configuration schema")

# --- 6. real role resolution + access rules (auth block is not PLAN) ---
import asyncio
import base64
import json

from authorization.resolvers import GenericAccessResolver, JwtRolesResolver
from models.config import AccessRule, Action, JwtRoleRule

_jwt = stack["authentication"]["jwk_config"]["jwt_configuration"]
_roles = JwtRolesResolver([JwtRoleRule(**r) for r in _jwt["role_rules"]])
_access = GenericAccessResolver(
    [AccessRule(**r) for r in stack["authorization"]["access_rules"]]
)


def _roles_for(token_roles):
    """Resolve roles for a token carrying the given realm roles."""
    _b64 = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    _token = ".".join([_b64({"alg": "none"}), _b64({"realm_access": {"roles": token_roles}}), "sig"])
    _auth = ("uid", "user", False, _token)
    return asyncio.run(_roles.resolve_roles(_auth))


_user = _roles_for(["lightspeed-agent-user"])
_support = _roles_for(["lightspeed-agent-support"])
_admin = _roles_for(["lightspeed-agent-admin"])
check("agent-user" in _user, "token role -> agent-user")
check("agent-support" in _support, "token role -> agent-support")
check("agent-admin" in _admin, "token role -> agent-admin")
check(_access.check_access(Action.WORKFLOW_START, _user), "agent-user may start")
check(
    not _access.check_access(Action.WORKFLOW_APPROVE, _user),
    "agent-user may not approve",
)
check(_access.check_access(Action.WORKFLOW_APPROVE, _support), "agent-support may approve")
check(not _access.check_access(Action.ADMIN, _support), "agent-support is not admin")
check(not _access.check_access(Action.ADMIN, _user), "agent-user is not admin")
check(_access.check_access(Action.ADMIN, _admin), "agent-admin is admin")

print()
if failures:
    print(f"{len(failures)} FAILURES")
    sys.exit(1)
print("ALL CHECKS PASSED")
