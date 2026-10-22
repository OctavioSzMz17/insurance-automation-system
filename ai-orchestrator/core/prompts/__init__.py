"""System prompts, versioned by content.

The prompts stay here, in Python, as the single source of truth. A trace store records
*which* version ran; it never serves the prompt. That ordering is deliberate: fetching a
prompt over the network puts an observability dependency in the critical path of a
regulated pipeline, and the thing it would buy — editing a prompt without a deploy — is
not worth a pipeline that stops when the trace store does.

The version is derived from the content, so nobody has to remember to bump a number and
it is impossible for the recorded version to disagree with the text that actually ran.
Change a comma, get a new version.
"""
from __future__ import annotations

import hashlib


def prompt_version(prompt: str) -> str:
    """A stable short identifier for exactly this prompt text."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]


__all__ = ["prompt_version"]
