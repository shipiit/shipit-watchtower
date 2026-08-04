"""
Prompt identity — the precondition for prompt governance.

The AI Watch Tower requirements put this first: *"Prompt identity carried in
every call — precondition for enforce, nothing else works without it."* Without
it a trace can say which model ran and what it cost, but not **which prompt
version produced this answer** — so prompt governance, regression comparison
and "why this answer" all have nothing to hang off.

FleetFlow's prompts live in the database (``Agent.system_prompt``,
``DocumentType.extraction_prompt``) and in code, not in a registry. Rather than
block on migrating everything into Langfuse Prompt Management, this module
gives every prompt a stable identity *now*:

``name``
    Where the prompt came from — ``agent:inbox-manager``, ``doctype:invoice``.
``version``
    The registry version when one exists; otherwise ``None``.
``fingerprint``
    SHA-256 over the normalised prompt text. Content-addressed, so it changes
    exactly when the prompt changes, needs no registry, and survives a prompt
    being edited in the admin with nobody bumping a version.

The fingerprint is what makes a compliance gap report possible on day one:
group traces by fingerprint and any prompt with no registry entry is, by
definition, unregistered.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Optional

__all__ = ["PromptIdentity", "fingerprint_text", "identify_prompt"]

#: Collapse insignificant edits so a fingerprint tracks meaning, not whitespace.
_WHITESPACE = re.compile(r"\s+")

#: Long enough to make collisions irrelevant, short enough to read in a tag.
_FINGERPRINT_CHARS = 16


def fingerprint_text(text: str) -> str:
    """Content hash of a prompt, stable across trivial reformatting.

    Normalises whitespace before hashing so re-indenting a prompt does not
    present as a new version, while any change to the words does.
    """
    normalised = _WHITESPACE.sub(" ", (text or "").strip())
    digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()
    return digest[:_FINGERPRINT_CHARS]


@dataclass(frozen=True)
class PromptIdentity:
    """Who this prompt is, in a form a trace can carry and a report can group by."""

    name: str
    fingerprint: str
    version: Optional[str] = None
    #: True when the prompt resolved from a managed registry rather than
    #: free-form DB/code text. Drives the compliance gap report.
    registered: bool = False

    @property
    def label(self) -> str:
        """Compact human-readable id, e.g. ``agent:inbox-manager@v3``."""
        suffix = f"@{self.version}" if self.version else f"#{self.fingerprint}"
        return f"{self.name}{suffix}"

    def as_metadata(self) -> dict[str, Any]:
        """Flat metadata block to merge into a trace or generation."""
        return {
            "prompt_name": self.name,
            "prompt_version": self.version,
            "prompt_fingerprint": self.fingerprint,
            "prompt_registered": self.registered,
        }

    def as_tags(self) -> list[str]:
        """Tags so Langfuse can filter by prompt without a metadata query."""
        tags = [f"prompt:{self.name}", f"prompt_fp:{self.fingerprint}"]
        if self.version:
            tags.append(f"prompt_v:{self.version}")
        if not self.registered:
            tags.append("prompt:unregistered")
        return tags


def identify_prompt(
    text: str,
    *,
    name: str,
    version: Optional[str] = None,
    registered: bool = False,
) -> PromptIdentity:
    """Build a :class:`PromptIdentity` for a prompt about to be sent.

    ``registered`` is the caller's assertion that ``text`` came from a managed
    registry. It defaults to False so an un-migrated prompt is reported as
    unregistered rather than quietly counted as compliant — the gap report is
    only useful if its default is honest.
    """
    return PromptIdentity(
        name=name,
        fingerprint=fingerprint_text(text),
        version=version,
        registered=registered,
    )
