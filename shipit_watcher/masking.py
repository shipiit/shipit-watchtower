"""
PII masking applied *before* data is persisted to an observability backend.

Traces carry email bodies, driver names, recipient addresses and company tax
IDs (a security review). Those leave the host application's boundary the moment they
are shipped to Langfuse, so masking at display time is too late — by then the
raw value is already stored on someone else's infrastructure.

Everything here is deliberately dependency-free and synchronous. It sits on the
hot path of every LLM call, so it must not add a network hop, and it must never
raise: a masking failure has to degrade to *more* redaction, never to leaking
the raw value.

Polish identifiers are first-class because the fleet domain is Polish: PESEL
(national ID), NIP (tax ID), REGON (business registry) and IBAN all appear
routinely in fleet documents and email traffic.

Checksum validation matters as much as the pattern. A NIP is ten digits, and so
is a phone number, an odometer reading and half the IDs in a fleet database.
Validating the check digit is what stops this from redacting the data the
traces exist to explain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Pattern, Sequence

__all__ = [
    "MaskingPolicy",
    "Redactor",
    "default_redactor",
    "mask_text",
    "mask_payload",
]

# Recursion / size guards. Trace payloads are attacker-influenceable (an email
# body becomes a prompt), so traversal is bounded rather than trusting shape.
_MAX_DEPTH = 12
_MAX_ITEMS = 2_000
_MAX_TEXT = 200_000


def _luhn_ok(digits: str) -> bool:
    """Luhn checksum — payment cards."""
    total, parity = 0, len(digits) % 2
    for index, char in enumerate(digits):
        value = ord(char) - 48
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _pesel_ok(digits: str) -> bool:
    """PESEL checksum (weights 1,3,7,9 repeating; complement of the sum)."""
    if len(digits) != 11:
        return False
    weights = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
    total = sum(int(d) * w for d, w in zip(digits[:10], weights))
    return (10 - total % 10) % 10 == int(digits[10])


def _nip_ok(digits: str) -> bool:
    """NIP checksum (weights 6,5,7,2,3,4,5,6,7; mod 11)."""
    if len(digits) != 10:
        return False
    weights = (6, 5, 7, 2, 3, 4, 5, 6, 7)
    total = sum(int(d) * w for d, w in zip(digits[:9], weights))
    check = total % 11
    return check != 10 and check == int(digits[9])


def _regon_ok(digits: str) -> bool:
    """REGON checksum — 9-digit and 14-digit variants."""
    def _check(body: str, weights: Sequence[int]) -> bool:
        total = sum(int(d) * w for d, w in zip(body, weights))
        return total % 11 % 10 == int(body[len(weights)])

    if len(digits) == 9:
        return _check(digits, (8, 9, 2, 3, 4, 5, 6, 7))
    if len(digits) == 14:
        return _check(digits, (2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8))
    return False


def _iban_ok(value: str) -> bool:
    """ISO 13616 mod-97 check."""
    compact = value.replace(" ", "").upper()
    if len(compact) < 15 or not compact[:2].isalpha():
        return False
    rearranged = compact[4:] + compact[:4]
    try:
        numeric = "".join(
            str(ord(c) - 55) if c.isalpha() else c for c in rearranged
        )
        return int(numeric) % 97 == 1
    except ValueError:
        return False


@dataclass(frozen=True)
class _Rule:
    """One detector: a pattern, a label, and an optional validator."""

    name: str
    pattern: Pattern[str]
    validator: Callable[[str], bool] | None = None
    #: Which capture group holds the value to validate (0 = whole match).
    group: int = 0

    def redact(self, text: str, placeholder: Callable[[str], str]) -> tuple[str, int]:
        hits = 0

        def _replace(match: "re.Match[str]") -> str:
            nonlocal hits
            raw = match.group(self.group)
            if self.validator is not None:
                digits = re.sub(r"[^0-9A-Za-z]", "", raw)
                if not self.validator(digits):
                    return match.group(0)  # not a real identifier — leave it
            hits += 1
            return match.group(0).replace(raw, placeholder(self.name))

        return self.pattern.sub(_replace, text), hits


# Order matters: the most specific patterns run first so a NIP inside an IBAN
# is not clipped out from under it.
_RULES: tuple[_Rule, ...] = (
    _Rule(
        "IBAN",
        re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}[ ]?[A-Z0-9]{1,4}\b"),
        _iban_ok,
    ),
    _Rule(
        "CARD",
        re.compile(r"\b(?:\d{4}[ -]?){3}\d{1,4}\b"),
        lambda d: len(d) in (13, 14, 15, 16, 19) and _luhn_ok(d),
    ),
    # (?<!\d) / (?!\d) rather than \b: a word boundary still matches *inside* a
    # longer digit run, so "\b\d{9}\b" happily fired on the last nine digits of
    # a ten-digit odometer reading and rewrote it mid-number.
    _Rule("PESEL", re.compile(r"(?<!\d)\d{11}(?!\d)"), _pesel_ok),
    _Rule(
        "NIP",
        re.compile(
            r"(?:NIP[:\s]*)?(?<!\d)(\d{3}-\d{3}-\d{2}-\d{2}|\d{10})(?!\d)", re.I
        ),
        _nip_ok,
        group=1,
    ),
    _Rule("REGON", re.compile(r"(?<!\d)(?:\d{14}|\d{9})(?!\d)"), _regon_ok),
    _Rule(
        "EMAIL",
        re.compile(r"\b[\w.%+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    ),
    # Anchored on a real phone shape: an explicit +48, or separated groups.
    # A bare run of nine digits is far more often an ID than a number, and the
    # REGON rule already covers that case with a checksum behind it.
    _Rule(
        "PHONE",
        re.compile(
            r"(?<![\d+])(?:\+48[ -]?\d{3}[ -]?\d{3}[ -]?\d{3}"
            r"|\d{3}[ -]\d{3}[ -]\d{3})(?!\d)"
        ),
    ),
    _Rule(
        "IP",
        re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),
    ),
)


@dataclass
class MaskingPolicy:
    """What to redact, and how loudly to say so.

    ``enabled_rules`` of ``None`` means every rule. Naming rules explicitly is
    the supported way to relax masking for a trusted environment — there is no
    "mask less by accident" path.
    """

    enabled_rules: frozenset[str] | None = None
    #: Keys whose values are redacted wholesale regardless of content.
    sensitive_keys: frozenset[str] = frozenset(
        {
            "password", "passwd", "secret", "token", "api_key", "apikey",
            "authorization", "auth", "access_token", "refresh_token",
            "client_secret", "private_key", "session_key", "otp", "code",
            "device_token", "temporary_token", "credit_card", "cvv",
        }
    )
    #: Keys that are safe to keep verbatim even if they look like an identifier.
    allowlist_keys: frozenset[str] = frozenset(
        {"model", "provider", "trace_id", "session_id", "tool_name", "agent_id"}
    )
    placeholder: str = "[{label}]"

    def label_for(self, rule_name: str) -> str:
        return self.placeholder.format(label=rule_name)

    def rules(self) -> Iterable[_Rule]:
        if self.enabled_rules is None:
            return _RULES
        return tuple(r for r in _RULES if r.name in self.enabled_rules)


@dataclass
class Redactor:
    """Applies a :class:`MaskingPolicy` to text and to nested payloads.

    Stateless with respect to calls — safe to share across threads and to hold
    as a module-level default.
    """

    policy: MaskingPolicy = field(default_factory=MaskingPolicy)

    # -- text ----------------------------------------------------------

    def text(self, value: str) -> str:
        """Redact every enabled identifier in ``value``.

        Never raises. On an unexpected failure the whole string is dropped
        rather than returned unmasked — the safe direction is *less* data.
        """
        if not value:
            return value
        try:
            if len(value) > _MAX_TEXT:
                value = value[:_MAX_TEXT] + "…[truncated]"
            for rule in self.policy.rules():
                value, _ = rule.redact(value, self.policy.label_for)
            return value
        except Exception:  # pragma: no cover — defensive by design
            return "[REDACTION_FAILED]"

    # -- structured ----------------------------------------------------

    def payload(self, value: Any, *, _depth: int = 0) -> Any:
        """Recursively redact a JSON-shaped payload.

        Traversal is depth- and size-bounded: trace payloads originate from
        model output and inbound email, so their shape is not trusted.
        """
        if _depth > _MAX_DEPTH:
            return "[MAX_DEPTH]"

        if isinstance(value, str):
            return self.text(value)

        if isinstance(value, Mapping):
            masked: Dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= _MAX_ITEMS:
                    masked["…"] = "[TRUNCATED]"
                    break
                lowered = str(key).lower()
                if lowered in self.policy.sensitive_keys:
                    masked[key] = "[REDACTED]"
                elif lowered in self.policy.allowlist_keys:
                    masked[key] = item
                else:
                    masked[key] = self.payload(item, _depth=_depth + 1)
            return masked

        if isinstance(value, (list, tuple, set)):
            items: List[Any] = []
            for index, item in enumerate(value):
                if index >= _MAX_ITEMS:
                    items.append("[TRUNCATED]")
                    break
                items.append(self.payload(item, _depth=_depth + 1))
            return items

        # int / float / bool / None / anything else — no free-text to mask.
        return value


#: Shared default. Import this rather than constructing per call.
default_redactor = Redactor()


def mask_text(value: str) -> str:
    """Redact identifiers in a string using the default policy."""
    return default_redactor.text(value)


def mask_payload(value: Any) -> Any:
    """Redact identifiers throughout a nested payload using the default policy."""
    return default_redactor.payload(value)
