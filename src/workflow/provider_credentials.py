"""Provider-to-credential-env-var mapping for ephemeral sandbox injection.

Maintains lightspeed-stack's own copy of the provider -> API-key-env-var
convention rather than importing cloud_agents.workflow.executor.step.
provider's private _PROVIDER_ENV_KEYS, which isn't part of that module's
public API and could be renamed/removed without notice.
"""

from __future__ import annotations

from typing import Optional

_PROVIDER_CREDENTIALS_ENV_KEYS: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
}


def credentials_secret_for(provider_name: str) -> Optional[str]:
    """Return the env var name holding the given provider's API key.

    Parameters:
        provider_name: Provider name (e.g. "openai", "anthropic").

    Returns:
        The env var name, or None if the provider isn't recognized.
        Callers should omit credentials_secret entirely in that case
        rather than guess -- an ephemeral sandbox should fail loudly
        with no credentials instead of silently picking up an
        unrelated provider's API key.
    """
    return _PROVIDER_CREDENTIALS_ENV_KEYS.get(provider_name)
