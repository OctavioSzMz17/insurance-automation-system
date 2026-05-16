"""Shared agent state container.

Carries data through an agent's run lifecycle and exposes the hooks
that the validation pipeline (B2.4) uses to enforce Rule 5
(self-healing retries up to 3 attempts → MANUAL_REVIEW).

Rules applied:
    Rule 4  — reasoning_trail captures the agent's full chain-of-thought
    Rule 5  — retry_count + last_error are the pipeline's retry hooks
    Rule 11 — Type hints + dataclass for clarity
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AgentState:
    """Mutable container threaded through an agent's run.

    Attributes:
        claim_input:      Original input given to the agent (e.g. {"pdf_path": "..."}).
        observations:     Accumulated tool-call results.
        reasoning_trail:  Ordered structured log of agent decisions (think/act/observe).
        iteration:        Current iteration of the ReAct loop (1-based).
        is_done:          Set True when the agent has produced a final result.
        result:           Final structured output when is_done is True.
        retry_count:      Hook for Rule 5 — incremented by validation_pipeline (B2.4).
        last_error:       Hook for Rule 5 — feedback message on retry.
    """
    claim_input:     dict[str, Any]
    observations:    list[dict[str, Any]]  = field(default_factory=list)
    reasoning_trail: list[dict[str, Any]]  = field(default_factory=list)
    iteration:       int                   = 0
    is_done:         bool                  = False
    result:          dict[str, Any] | None = None
    retry_count:     int                   = 0
    last_error:      str | None            = None

    def log_step(self, step: str, **fields: Any) -> None:
        """Append a timestamped entry to the reasoning trail."""
        entry = {
            "step":      step,
            "iteration": self.iteration,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **fields,
        }
        self.reasoning_trail.append(entry)
        logger.debug(f"[agent step] {entry}")
