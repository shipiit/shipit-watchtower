"""
The event model — a typed vocabulary for what an AI system did.

The RFP's Moduł G asks for a decision path ("planning → execution → validation
→ answer") and for "why this answer, why not another". Neither is derivable
from free-form spans: if every step is just ``span(name="something")`` then the
shape of a decision is lost the moment it is written.

So spans are typed. An :class:`EventType` says *what kind of thing happened*,
which lets a UI render a decision path, a report count tool invocations, and a
reviewer ask which options were considered — without parsing names.

``DecisionEvent`` is the one that carries explainability: recording
``options_considered`` and ``rationale`` at the moment of choosing is the only
way "why not the other option" can ever be answered. Reconstructing it later
is guesswork.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

__all__ = [
    "EventType",
    "Severity",
    "Event",
    "DecisionEvent",
    "ToolInvocationEvent",
    "RetrievalEvent",
    "RetrievedChunk",
    "HandoffEvent",
    "PolicyEvent",
    "HumanReviewEvent",
    "GenerationEvent",
]


class EventType(str, Enum):
    """The kinds of step an AI system takes.

    Deliberately closed. A new kind of step should be a considered addition to
    this vocabulary, not an ad-hoc string — the downstream views are built per
    type, and an unrecognised one renders as nothing.
    """

    GENERATION = "generation"          # an LLM call
    DECISION = "decision"              # a branch point, with alternatives
    TOOL_INVOCATION = "tool_invocation"
    RETRIEVAL = "retrieval"            # RAG lookup, with provenance
    HANDOFF = "handoff"                # delegation to another agent
    POLICY = "policy"                  # a guardrail fired
    HUMAN_REVIEW = "human_review"      # a human was asked
    VALIDATION = "validation"          # output checked against criteria
    SPAN = "span"                      # generic timing, no semantics


class Severity(str, Enum):
    DEBUG = "DEBUG"
    DEFAULT = "DEFAULT"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass
class Event:
    """Base for everything recorded. One event is one observation in a trace."""

    name: str
    type: EventType = EventType.SPAN
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    parent_id: Optional[str] = None
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    severity: Severity = Severity.DEFAULT
    status_message: str = ""
    input: Any = None
    output: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)

    @property
    def duration_ms(self) -> int:
        if self.ended_at is None:
            return 0
        return int((self.ended_at - self.started_at) * 1000)

    def finish(
        self,
        *,
        output: Any = None,
        severity: Optional[Severity] = None,
        status_message: str = "",
    ) -> "Event":
        self.ended_at = time.time()
        if output is not None:
            self.output = output
        if severity is not None:
            self.severity = severity
        if status_message:
            self.status_message = status_message
        return self

    def to_payload(self) -> Dict[str, Any]:
        """Flat dict for a sink. Type and timing travel as metadata so any
        backend can carry them without a bespoke schema."""
        return {
            "event_type": self.type.value,
            "duration_ms": self.duration_ms,
            **self.metadata,
        }


@dataclass
class GenerationEvent(Event):
    """An LLM call: model, usage, cost, and which prompt produced it."""

    type: EventType = EventType.GENERATION
    model: str = ""
    provider: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_cost: float = 0.0
    #: Set from ai_watchtower.identity.PromptIdentity.as_metadata()
    prompt: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_payload(self) -> Dict[str, Any]:
        return {
            **super().to_payload(),
            "model": self.model,
            "provider": self.provider,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "total_cost": self.total_cost,
            **self.prompt,
        }


@dataclass
class DecisionEvent(Event):
    """A branch point — and, crucially, the roads not taken.

    ``options_considered`` is what turns a trace into an explanation. Recorded
    at the moment of choosing, "why not X" is answerable; reconstructed later,
    it is a guess.
    """

    type: EventType = EventType.DECISION
    chosen: str = ""
    options_considered: List[str] = field(default_factory=list)
    rationale: str = ""
    confidence: Optional[float] = None

    def to_payload(self) -> Dict[str, Any]:
        return {
            **super().to_payload(),
            "chosen": self.chosen,
            "options_considered": self.options_considered,
            "rejected": [o for o in self.options_considered if o != self.chosen],
            "rationale": self.rationale,
            "confidence": self.confidence,
        }


@dataclass
class ToolInvocationEvent(Event):
    type: EventType = EventType.TOOL_INVOCATION
    tool_name: str = ""
    arguments: Any = None
    succeeded: bool = True
    error: str = ""

    def to_payload(self) -> Dict[str, Any]:
        return {
            **super().to_payload(),
            "tool_name": self.tool_name,
            "succeeded": self.succeeded,
            "error": self.error,
        }


@dataclass
class RetrievedChunk:
    """One retrieved passage, with enough provenance to defend the answer.

    The RFP asks for source, version, timestamp, score and content hash per
    chunk. A citation without a content hash cannot prove the source said what
    the answer claims it said — the document may have changed since.
    """

    source: str
    score: Optional[float] = None
    version: Optional[str] = None
    timestamp: Optional[str] = None
    content_hash: Optional[str] = None
    snippet: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "score": self.score,
            "version": self.version,
            "timestamp": self.timestamp,
            "content_hash": self.content_hash,
            "snippet": self.snippet,
        }


@dataclass
class RetrievalEvent(Event):
    type: EventType = EventType.RETRIEVAL
    query: str = ""
    knowledge_base: str = ""
    chunks: List[RetrievedChunk] = field(default_factory=list)

    def to_payload(self) -> Dict[str, Any]:
        return {
            **super().to_payload(),
            "query": self.query,
            "knowledge_base": self.knowledge_base,
            "chunk_count": len(self.chunks),
            "chunks": [c.to_dict() for c in self.chunks],
        }


@dataclass
class HandoffEvent(Event):
    """One agent delegating to another — the edges of a multi-agent graph."""

    type: EventType = EventType.HANDOFF
    from_agent: str = ""
    to_agent: str = ""
    reason: str = ""

    def to_payload(self) -> Dict[str, Any]:
        return {
            **super().to_payload(),
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "reason": self.reason,
        }


@dataclass
class PolicyEvent(Event):
    """A guardrail decision — masking, prompt-injection filter, budget block.

    ``blocked`` is what separates audit-only from enforce, and is the field the
    compliance report counts.
    """

    type: EventType = EventType.POLICY
    policy_name: str = ""
    blocked: bool = False
    reason: str = ""

    def to_payload(self) -> Dict[str, Any]:
        return {
            **super().to_payload(),
            "policy_name": self.policy_name,
            "blocked": self.blocked,
            "reason": self.reason,
        }


@dataclass
class HumanReviewEvent(Event):
    type: EventType = EventType.HUMAN_REVIEW
    reviewer: str = ""
    verdict: str = ""
    comment: str = ""

    def to_payload(self) -> Dict[str, Any]:
        return {
            **super().to_payload(),
            "reviewer": self.reviewer,
            "verdict": self.verdict,
            "comment": self.comment,
        }
