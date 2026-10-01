"""Size and count limits for workflow submissions."""

from typing import Final

# Workflow definition limits. Sized against the multi-step transcripts from
# issues #52/#53 (largest: a handful of steps, one or two MCP servers each,
# a few KiB of definition); these leave two orders of magnitude of headroom.
MAX_DEFINITION_BYTES: Final[int] = 262_144
MAX_WORKFLOW_STEPS: Final[int] = 64
MAX_MCP_SERVERS_PER_STEP: Final[int] = 16
MAX_SECRET_HEADERS_PER_SERVER: Final[int] = 32
