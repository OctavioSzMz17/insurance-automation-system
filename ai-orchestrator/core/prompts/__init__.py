"""Per-agent system prompts.

Each agent has its own module here. The prompt is a constant string
exported from the module — no logic, no formatting. Agents import the
prompt and pass it directly to the LLM.
"""

from .analyst import ANALYST_SYSTEM_PROMPT
from .auditor import AUDITOR_SYSTEM_PROMPT

__all__ = ["ANALYST_SYSTEM_PROMPT", "AUDITOR_SYSTEM_PROMPT"]
