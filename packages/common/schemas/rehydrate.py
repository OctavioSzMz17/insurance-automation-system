"""Rebuild a strict schema from its own JSON representation.

The inter-service models are `strict=True` on purpose: strictness is the boundary that
rejects sloppy LLM output, where a UUID arriving as a loosely-formatted string is a
signal, not a convenience to absorb. But that same strictness also rejects *our own*
`model_dump(mode="json")` output, because JSON has no UUID, datetime or Decimal types —
everything comes back as a string.

Anything that crosses a serialization boundary and then needs the typed object again —
Redis for the HITL handoff, JSONB columns, an HTTP payload — must therefore rehydrate in
non-strict mode. That is not a loosening of the contract: the value was produced by the
strict model, and every field constraint (patterns, lengths, enums, `extra="forbid"`) is
still enforced on the way back in. The only thing waived is the refusal to parse a string
into the type it was serialised from.

    stored = json.loads(await redis.get(key))
    proposal = from_stored(ClaimProposal, stored)
"""
from __future__ import annotations

from typing import Any, Mapping, TypeVar

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)


def from_stored(model: type[M], payload: Mapping[str, Any]) -> M:
    """Validate `payload` into `model`, parsing JSON scalars back into their types.

    Raises:
        pydantic.ValidationError: if the payload violates any field constraint.
    """
    return model.model_validate(dict(payload), strict=False)
