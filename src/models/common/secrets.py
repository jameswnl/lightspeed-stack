"""Logical secret references.

A ``SecretRef`` names an operator-approved secret; it never carries a value
or a physical identifier (env var name, K8s Secret name, namespace, path).
The operator maps each ref to a backend location in ``workflow_engine.secrets``.
"""

from typing import Final, Optional

from pydantic import BaseModel, ConfigDict, Field

SECRET_NAME_PATTERN: Final[str] = r"^[a-z0-9][a-z0-9/_.-]{0,127}$"


class SecretRef(BaseModel):
    """Reference to an operator-registered secret by logical name.

    Attributes:
        name: Logical name, e.g. ``inference/anthropic-prod``.
        key: Optional key within a multi-value secret.
        version: Optional secret version.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(
        ...,
        pattern=SECRET_NAME_PATTERN,
        title="Secret name",
        description="Logical secret name resolved by the operator's registry.",
    )
    key: Optional[str] = Field(
        default=None,
        title="Key",
        description="Optional key within a multi-value secret.",
    )
    version: Optional[str] = Field(
        default=None,
        title="Version",
        description="Optional secret version.",
    )
