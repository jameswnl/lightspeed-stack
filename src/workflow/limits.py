"""Size and count limits shared by the workflow and chat submission paths."""

from typing import Final

# Chat (/query/direct) input limits.
MAX_PROMPT_LENGTH: Final[int] = 100_000
MAX_INSTRUCTIONS_LENGTH: Final[int] = 50_000

# Workflow definition limits. Sized against the multi-step transcripts from
# issues #52/#53 (largest: a handful of steps, one or two MCP servers each,
# a few KiB of definition); these leave two orders of magnitude of headroom.
MAX_DEFINITION_BYTES: Final[int] = 262_144
MAX_WORKFLOW_STEPS: Final[int] = 64
MAX_MCP_SERVERS_PER_STEP: Final[int] = 16
MAX_SECRET_HEADERS_PER_SERVER: Final[int] = 32
