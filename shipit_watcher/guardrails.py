"""
Content guardrails — refusing a call, not just recording it.

Watcher already blocks on two things: cost (:mod:`shipit_watcher.budgets`) and
provenance (``WATCHER_GOVERNANCE=enforce``). This is the third: what is
*inside* the request.

The detectors are the same checksum-validated ones that drive masking, which
matters more than it first appears. A guardrail built on bare patterns fires
on every ten-digit number in a fleet database and gets switched off within a
week; one that validates the check digit fires when a national ID is genuinely
about to leave the building. A guardrail nobody trusts is worse than none,
because it teaches people to ignore it.

Masking and guarding answer different questions, so both exist:

* **Masking** protects the *trace* — the prompt still reaches the model, but
  the recorded copy is redacted.
* **A guardrail** protects the *call* — the content never leaves the process
  at all.

Off by default. Turning it on is a deliberate act, and every decision it makes
is recorded as a ``PolicyEvent`` so ``audit`` mode tells you what ``block``
mode would have refused, before you switch it on.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import get_config
from .masking import MaskingPolicy, detect_in_payload

logger = logging.getLogger(__name__)

__all__ = ["GuardrailViolation", "check_content", "guard"]


class GuardrailViolation(RuntimeError):
    """Raised when a guardrail refuses to let content leave the process.

    Deliberately not a provider error: this is our decision, made before any
    request was sent, and a caller should be able to tell the two apart.
    """

    def __init__(self, findings: dict[str, int], name: str):
        self.findings = findings
        self.policy_name = name
        detail = ", ".join(f"{count}x {rule}" for rule, count in sorted(findings.items()))
        super().__init__(
            f"guardrail {name!r} refused the call: {detail}. "
            f"Redact the content, or set WATCHER_GUARDRAIL=audit to record "
            f"rather than block."
        )


def _policy_for(rules: frozenset[str] | None) -> MaskingPolicy | None:
    return MaskingPolicy(enabled_rules=rules) if rules else None


def check_content(
    value: Any,
    *,
    name: str = "content_egress",
    mode: str | None = None,
    rules: frozenset[str] | None = None,
) -> dict[str, int]:
    """Scan *value*, record the decision, and block when configured to.

    Returns what was found, so a caller can act on it without re-scanning.
    Modes:

    ``off``     do nothing — the default, and free.
    ``audit``   record a ``PolicyEvent`` when something is found; allow.
    ``block``   record and raise :class:`GuardrailViolation`.

    Run ``audit`` first. It answers "what would this have refused?" against
    real traffic, which is the only honest way to find out whether ``block``
    is safe to turn on.
    """
    config = get_config()
    resolved = (mode or config.guardrail_mode or "off").strip().lower()
    if resolved == "off":
        return {}

    active_rules = rules if rules is not None else config.guardrail_rule_set
    findings = detect_in_payload(value, _policy_for(active_rules))
    if not findings:
        return {}

    blocked = resolved == "block"
    reason = "detected " + ", ".join(
        f"{count}x {rule}" for rule, count in sorted(findings.items())
    )
    try:
        from .tracer import get_tracer

        get_tracer().policy(name, blocked=blocked, reason=reason, **findings)
    except Exception:
        # A guardrail that cannot record its decision still has to make it.
        logger.debug("watcher: could not record a guardrail decision", exc_info=True)

    if blocked:
        raise GuardrailViolation(findings, name)
    logger.warning("watcher: guardrail %s — %s", name, reason)
    return findings


def guard(
    value: Any,
    *,
    name: str = "content_egress",
    rules: frozenset[str] | None = None,
) -> Any:
    """Refuse *value* if it carries a detected identifier, else return it.

        prompt = wt.guard(user_message)

    Explicit ``block`` regardless of configuration, for the call sites where
    the answer is never "record it and carry on" — an outbound email body, a
    payload bound for a third party. Returns the value so it can be used
    inline.
    """
    check_content(value, name=name, mode="block", rules=rules)
    return value
