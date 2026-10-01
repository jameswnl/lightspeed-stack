"""Reference model of the #51 submission gate and load-time checks.

This is an executable spec, not the implementation: it encodes the design in
https://github.com/jameswnl/lightspeed-stack/issues/51#issuecomment-5911977694
so ``cases.yaml`` can pin the expected behaviour before the stack implements
it. Later phases should run the same cases against the real endpoint
(``run_cases.py`` style) and then delete the matching part of this file.

Stages: ``pre269`` (names only, env-bound inference credentials) and
``post269`` (credential leases, any backend).
"""

import json
import re
from typing import Any, Optional
from urllib.parse import urlsplit

from cloud_agents.workflow.core.definition import WorkflowDefinition
from cloud_agents.workflow.core.execution import APPROVED_INFERENCE_PROVIDERS
from pydantic import ValidationError

from workflow.limits import (
    MAX_DEFINITION_BYTES,
    MAX_MCP_SERVERS_PER_STEP,
    MAX_SECRET_HEADERS_PER_SERVER,
    MAX_WORKFLOW_STEPS,
)

REQUEST_BOUND = {"client", "oauth", "kubernetes"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9/_.-]{0,127}$")


class Denied(Exception):
    """A gate rejection with its status code and reasons."""

    def __init__(self, status: int, *reasons: str) -> None:
        super().__init__(status, reasons)
        self.status = status
        self.reasons = list(reasons)


# ----------------------------------------------------------------- load time
def generated_mcp_allowed_secrets(stack: dict) -> str:
    """Value the stack writes to MCP_ALLOWED_SECRETS at startup."""
    secrets = {s["name"]: s for s in stack["workflow_engine"]["secrets"]}
    names = set()
    refs = {
        ref["name"]
        for server in stack["mcp_servers"]
        if server.get("workflow_enabled")
        for ref in (server.get("secret_headers") or {}).values()
    }
    # Inline MCP refs granted by policy are mounted too, so the runtime
    # guardrail must allow them as well.
    for rule in stack["workflow_engine"].get("policy", {}).get("rules", []):
        refs |= set(rule.get("mcp_secrets", []))
    for ref in refs:
        binding = secrets.get(ref, {})
        if binding.get("backend") == "k8s":
            names.add(binding["secret_name"])
    return ",".join(sorted(names))


def load_errors(stack: dict, stage: str = "pre269") -> list[str]:
    """Return the config-load failures the plan requires."""
    we = stack["workflow_engine"]
    providers = we.get("providers") or []
    secrets = {s["name"]: s for s in we.get("secrets", [])}
    mcp = {m["name"]: m for m in stack.get("mcp_servers", [])}
    errors: list[str] = []
    seen = set()
    for p in providers:
        if p["name"] in seen:
            errors.append(f"duplicate provider {p['name']}")
        seen.add(p["name"])
        if p["executor_type"] not in APPROVED_INFERENCE_PROVIDERS:
            errors.append(f"{p['name']}: executor_type not approved")
        ref = (p.get("credential") or {}).get("name")
        if ref is not None and ref not in secrets:
            errors.append(f"{p['name']}: credential {ref} has no secrets binding")
        binding = secrets.get(ref or "", {})
        if ref and stage == "pre269" and binding.get("backend") != "env":
            errors.append(f"{p['name']}: pre-269 inference credential must be env")
        if p.get("allowed_models") == []:
            errors.append(f"{p['name']}: allowed_models [] is rejected")
    by_name = {p["name"]: p for p in providers}
    dp = we.get("default_provider")
    if providers and dp not in by_name:
        errors.append("default_provider not in catalog")
    elif dp:
        allowed = by_name[dp].get("allowed_models")
        if allowed is not None and we.get("default_model") not in allowed:
            errors.append("default_model not allowed by default_provider")
    for name, server in mcp.items():
        for ref in (server.get("secret_headers") or {}).values():
            if ref["name"] not in secrets:
                errors.append(f"mcp {name}: ref {ref['name']} has no secrets binding")
        if server.get("workflow_enabled"):
            bad = REQUEST_BOUND & set((server.get("authorization_headers") or {}).values())
            if bad:
                errors.append(f"mcp {name}: workflow_enabled with {sorted(bad)}")
            if server.get("headers"):
                errors.append(f"mcp {name}: workflow_enabled with propagated headers")
    for ref in secrets:
        if not NAME_RE.match(ref):
            errors.append(f"secret name {ref} invalid")
    for rule in (we.get("policy") or {}).get("rules", []):
        for key, known in (("providers", by_name), ("mcp_servers", mcp), ("mcp_secrets", secrets)):
            for item in rule.get(key, []):
                if item not in known:
                    errors.append(f"policy {rule['roles']}: unknown {key[:-1]} {item}")
    return errors


# -------------------------------------------------------------------- grants
def grants(stack: dict, roles: set[str]) -> dict[str, Any]:
    """Union of every policy rule matching the roles (inherit resolved)."""
    rules = (stack["workflow_engine"].get("policy") or {}).get("rules", [])
    by_role = {r: rule for rule in rules for r in rule["roles"]}
    out: dict[str, Any] = {}

    def add(rule: dict, depth: int = 0) -> None:
        if depth > 8:
            raise ValueError("policy inherit cycle")
        for parent in rule.get("inherit", []):
            add(by_role[parent], depth + 1)
        for key, value in rule.items():
            if key in ("roles", "inherit"):
                continue
            if isinstance(value, list):
                out[key] = sorted(set(out.get(key, [])) | set(value))
            elif isinstance(value, dict):
                merged = dict(out.get(key, {}))
                for k, v in value.items():
                    merged[k] = max(merged.get(k, v), v)
                out[key] = merged
            else:
                out[key] = out.get(key, False) or value

    for role in roles:
        if role in by_role:
            add(by_role[role])
    return out


def actions(stack: dict, roles: set[str]) -> set[str]:
    """Coarse RBAC actions for the roles (admin implies all)."""
    acts: set[str] = set()
    for rule in stack.get("authorization", {}).get("access_rules", []):
        if rule["role"] in roles:
            acts |= set(rule["actions"])
    return acts | ({"*admin*"} if "admin" in acts else set())


def _can(stack: dict, roles: set[str], action: str) -> bool:
    acts = actions(stack, roles)
    return "*admin*" in acts or action in acts


# ---------------------------------------------------------------- submission
def _steps(definition: dict) -> list[dict]:
    return definition["spec"]["steps"]


def _agent_steps(definition: dict) -> list[dict]:
    return [s for s in _steps(definition) if s.get("type", "agent") == "agent"]


def _resolve(stack: dict, name: str, model: Optional[str]) -> tuple[dict, str]:
    we = stack["workflow_engine"]
    catalog = {p["name"]: p for p in we["providers"]}
    if name not in catalog:
        raise Denied(400, f"unknown provider '{name}'")
    entry = catalog[name]
    model = model or we.get("default_model")
    allowed = entry.get("allowed_models")
    if allowed is not None and model not in allowed:
        raise Denied(400, f"model '{model}' not allowed for '{name}'")
    return entry, model


def _count_errors(definition: dict) -> list[str]:
    errors = []
    spec = definition["spec"]
    if len(spec["steps"]) > MAX_WORKFLOW_STEPS:
        errors.append("too many steps")
    for scope in [spec, *spec["steps"]]:
        servers = scope.get("mcp_servers") or []
        if len(servers) > MAX_MCP_SERVERS_PER_STEP:
            errors.append("too many mcp_servers")
        for srv in servers:
            if isinstance(srv, dict):
                if len(srv.get("secret_headers") or {}) > MAX_SECRET_HEADERS_PER_SERVER:
                    errors.append("too many secret_headers")
    return errors


def _shape_copy(definition: dict) -> dict:
    """Copy for the cloud-agents shape check with inline registry refs rewritten.

    cloud-agents' MCPServerConfig only accepts ``{secret_name, key}``, so a
    registry ref ``{name: ...}`` would fail the typed parse. The stack
    validates registry refs itself (step 4), then rewrites them to the
    executor form; this models that rewrite for the step-3 shape check.
    """
    shaped = json.loads(json.dumps(definition))
    spec = shaped.get("spec", {})
    for scope in [spec, *spec.get("steps", [])]:
        for srv in scope.get("mcp_servers") or []:
            if not isinstance(srv, dict):
                continue
            for header, ref in list((srv.get("secret_headers") or {}).items()):
                if isinstance(ref, dict) and set(ref) == {"name"}:
                    srv["secret_headers"][header] = {"secret_name": ref["name"], "key": "value"}
    return shaped


def executor_form(stack: dict, definition: dict) -> dict:
    """Definition as cloud-agents' own validator sees it after the stack's rewrite.

    Logical provider names become executor types and inline registry refs the
    executor's ``{secret_name, key}`` form (pipeline steps 4 and 6).
    """
    out = _shape_copy(definition)
    catalog = {p["name"]: p["executor_type"] for p in stack["workflow_engine"]["providers"]}
    scopes = [out] + [s for s in out["spec"]["steps"] if s.get("inference_provider")]
    for scope in scopes:
        key = "provider" if scope is out else "inference_provider"
        if scope.get(key):
            scope[key]["name"] = catalog.get(scope[key]["name"], scope[key]["name"])
    return out


def submit(
    stack: dict,
    body: dict,
    roles: set[str],
    stage: str = "pre269",
    spawner: bool = True,
) -> list[str]:
    """Run POST /v1/workflows/run steps 1-5; return [] when it would be 202.

    Raises Denied(status, reasons) otherwise.
    """
    we = stack["workflow_engine"]
    definition = body["definition"]
    if not _can(stack, roles, "workflow_start"):  # step 1
        raise Denied(403, "workflow_start not granted")
    if len(json.dumps(definition).encode()) > MAX_DEFINITION_BYTES:  # step 2
        raise Denied(413, "definition too large")
    try:  # step 3
        WorkflowDefinition.model_validate(_shape_copy(definition))
    except ValidationError as exc:
        raise Denied(422, f"shape: {exc.error_count()} errors") from exc
    if errs := _count_errors(definition):
        raise Denied(422, *errs)
    run = body.get("provider") or {}
    def_provider = definition.get("provider") or {}
    if "credentials_secret" in run or "credentials_secret" in def_provider:
        raise Denied(400, "credentials_secret cannot be set by callers")
    extra = set(run) - {"name", "model", "credential_ref"}
    if extra:
        raise Denied(400, f"unknown provider keys {sorted(extra)}")

    # step 4: catalog (400)
    name = run.get("name") or we["default_provider"]
    entry, model = _resolve(stack, name, run.get("model"))
    cref = (run.get("credential_ref") or {}).get("name")
    if cref is not None and cref != entry["credential"]["name"]:
        raise Denied(400, "credential_ref does not match the provider's credential")
    overrides = [def_provider] + [
        s.get("inference_provider") or {} for s in _agent_steps(definition)
    ]
    for ov in overrides:
        if ov.get("name"):
            ov_entry, _ = _resolve(stack, ov["name"], ov.get("model"))
            if ov_entry["name"] != entry["name"]:
                raise Denied(400, "override must use the same catalog entry as the run provider")
    mcp = {m["name"]: m for m in stack["mcp_servers"]}
    refs = {s["name"] for s in we["secrets"]}
    inline: list[dict] = []
    for scope in [definition["spec"], *_agent_steps(definition)]:
        for srv in scope.get("mcp_servers") or []:
            if isinstance(srv, str):
                if srv not in mcp or not mcp[srv].get("workflow_enabled"):
                    raise Denied(400, f"unknown MCP server '{srv}'")
            else:
                inline.append(srv)
                for ref in (srv.get("secret_headers") or {}).values():
                    if not isinstance(ref, dict) or ref.get("name") not in refs:
                        raise Denied(400, "inline secret_headers must be registry refs")

    # step 5: policy (403)
    g = grants(stack, roles)
    why: list[str] = []
    if entry["name"] not in g.get("providers", []):
        why.append(f"provider '{entry['name']}' not granted")
    spec = definition["spec"]
    default_image = stack["spawner"]["sandbox_image"]
    images = {body.get("sandbox_image"), (spec.get("spawn_config") or {}).get("sandbox_image"),
              (definition.get("skills") or {}).get("image")}
    for step in _agent_steps(definition):
        images.add((step.get("spawn_config") or {}).get("sandbox_image"))
    for img in images - {None}:
        if img not in g.get("sandbox_images", []):
            why.append(f"image '{img}' not granted")
    if default_image not in g.get("sandbox_images", []):
        why.append("spawner default image not granted")
    if definition.get("advisory") and not g.get("advisory"):
        why.append("advisory not granted")
    for step in _agent_steps(definition):
        s = step["name"]
        spawn = step.get("spawn") or spec.get("spawn") or "ephemeral"
        if spawn not in g.get("spawn", []):
            why.append(f"{s}: spawn '{spawn}' not granted")
        if spawn == "ephemeral" and not spawner:
            why.append(f"{s}: ephemeral needs spawner_configuration")
        servers = step["mcp_servers"] if step.get("mcp_servers") is not None else spec.get("mcp_servers") or []
        for srv in servers:
            if isinstance(srv, str):
                if srv not in g.get("mcp_servers", []):
                    why.append(f"{s}: MCP '{srv}' not granted")
                secret_headers = mcp[srv].get("secret_headers") or {}
                for ref in secret_headers.values():
                    if ref["name"] not in g.get("mcp_secrets", []):
                        why.append(f"{s}: MCP secret '{ref['name']}' not granted for '{srv}'")
                if secret_headers and spawn != "ephemeral" and stage == "pre269":
                    why.append(f"{s}: secret_headers need ephemeral before cloud-agents#269")
            else:
                if not g.get("inline_mcp"):
                    why.append(f"{s}: inline MCP not granted")
                parts = urlsplit(srv.get("url", ""))
                if parts.scheme != "https" or parts.username or parts.password:
                    why.append(f"{s}: inline MCP must be https without userinfo")
                if srv.get("headers"):
                    why.append(f"{s}: inline MCP literal header values are not allowed")
                if parts.hostname not in g.get("inline_mcp_hosts", []):
                    why.append(f"{s}: inline MCP host '{parts.hostname}' not allowed")
                for ref in (srv.get("secret_headers") or {}).values():
                    if ref["name"] not in g.get("mcp_secrets", []):
                        why.append(f"{s}: inline MCP secret '{ref['name']}' not granted")
        skills = step["allowed_skills"] if step.get("allowed_skills") is not None else spec.get("allowed_skills") or []
        for sk in skills:
            if sk not in g.get("skills", []):
                why.append(f"{s}: skill '{sk}' not granted")
        perms = step.get("permissions") or spec.get("permissions") or {}
        for tool in [*step.get("tools", []), *(perms.get("allowed_tools") or [])]:
            if tool not in g.get("tools", []):
                why.append(f"{s}: tool '{tool}' not granted")
        sa = step.get("service_account") or perms.get("service_account") or spec.get("service_account")
        if sa and sa not in g.get("service_accounts", []):
            why.append(f"{s}: service account '{sa}' not granted")
        for ns in step.get("target_namespaces") or []:
            if ns not in g.get("namespaces", []):
                why.append(f"{s}: namespace '{ns}' not granted")
        lim = g.get("limits", {})
        if (step.get("timeout_seconds") or 0) > lim.get("max_timeout_seconds", 0):
            why.append(f"{s}: timeout over limit")
        if (perms.get("max_tokens") or 0) > lim.get("max_tokens", 0):
            why.append(f"{s}: max_tokens over limit")
        if step.get("max_retries", 0) > lim.get("max_retries", 0):
            why.append(f"{s}: max_retries over limit")
    if (spec.get("timeout_seconds") or 0) > g.get("limits", {}).get("max_timeout_seconds", 0):
        why.append("workflow timeout over limit")
    if why:
        raise Denied(403, *why)
    return []

