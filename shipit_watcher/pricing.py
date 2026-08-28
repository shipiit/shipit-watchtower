"""
Model pricing — so a call costs something even when nobody reports a figure.

Cost arrives from three places, in descending order of trust:

1. **The provider or gateway said so.** A LiteLLM proxy computes cost
   server-side and returns it; that number is authoritative because it knows
   the contract you are actually billed under.
2. **LiteLLM's pricing map**, when the library is installed and recognises the
   model.
3. **This table.**

The third rung exists because the first two miss exactly the cases that matter
most: a self-hosted model, a fine-tune, a gateway alias, or a model released
last week. Reporting ``$0.00`` for those is worse than reporting nothing — it
makes an expensive model look free in a cost report, and nobody audits a zero.

Prices are USD per million tokens, the unit every provider publishes, and are
overridable without a release::

    wt.set_model_price("my-finetune", input=3.0, output=15.0)

    WATCHER_MODEL_PRICES='{"my-finetune": {"input": 3.0, "output": 15.0}}'

The bundled figures are a floor, not a billing system: list prices drift, and
the override is the supported way to be exact.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass

logger = logging.getLogger(__name__)

__all__ = ["ModelPrice", "estimate_cost", "get_model_price", "set_model_price"]


@dataclass(frozen=True)
class ModelPrice:
    """USD per million tokens."""

    input: float
    output: float
    #: Cached input, when the provider bills it separately. Falls back to
    #: ``input`` so a caller never has to special-case its absence.
    cached_input: float | None = None

    def cost(self, prompt_tokens: int, completion_tokens: int,
             cached_tokens: int = 0) -> float:
        billable_prompt = max(0, prompt_tokens - cached_tokens)
        cached_rate = self.cached_input if self.cached_input is not None else self.input
        return (
            billable_prompt * self.input
            + cached_tokens * cached_rate
            + completion_tokens * self.output
        ) / 1_000_000


#: Published list prices, USD per million tokens. Deliberately small: this is
#: a fallback for models the ecosystem does not price, not a catalogue to keep
#: in sync with every vendor's page.
_BUILTIN: dict[str, ModelPrice] = {
    # Anthropic
    "claude-opus-4-5": ModelPrice(5.0, 25.0, 0.5),
    "claude-sonnet-4-5": ModelPrice(3.0, 15.0, 0.3),
    "claude-haiku-4-5": ModelPrice(1.0, 5.0, 0.1),
    "claude-3-5-haiku": ModelPrice(0.8, 4.0, 0.08),
    # OpenAI
    "gpt-4.1": ModelPrice(2.0, 8.0, 0.5),
    "gpt-4.1-mini": ModelPrice(0.4, 1.6, 0.1),
    "gpt-4.1-nano": ModelPrice(0.1, 0.4, 0.025),
    "gpt-4o": ModelPrice(2.5, 10.0, 1.25),
    "gpt-4o-mini": ModelPrice(0.15, 0.6, 0.075),
    "o3": ModelPrice(2.0, 8.0, 0.5),
    "o4-mini": ModelPrice(1.1, 4.4, 0.275),
    # Google
    "gemini-2.5-pro": ModelPrice(1.25, 10.0),
    "gemini-2.5-flash": ModelPrice(0.3, 2.5),
    "gemini-2.0-flash": ModelPrice(0.1, 0.4),
}

_overrides: dict[str, ModelPrice] = {}
_lock = threading.Lock()
_env_loaded = False


def _load_env_prices() -> None:
    """Read ``WATCHER_MODEL_PRICES`` once, tolerating a malformed value."""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    raw = os.getenv("WATCHER_MODEL_PRICES", "").strip()
    if not raw:
        return
    try:
        for name, price in json.loads(raw).items():
            _overrides[_normalise(name)] = ModelPrice(
                input=float(price["input"]),
                output=float(price["output"]),
                cached_input=(
                    float(price["cached_input"]) if "cached_input" in price else None
                ),
            )
    except Exception:
        # A bad price table must not stop the process it is meant to measure.
        logger.warning("watcher: WATCHER_MODEL_PRICES is not valid", exc_info=True)


def _normalise(model: str) -> str:
    """Strip the routing prefix and any dated suffix.

    ``openai/gpt-4o`` is an instruction about *how* to reach a model, not its
    name, and ``claude-sonnet-4-5-20260101`` is the same model as
    ``claude-sonnet-4-5``. Both would otherwise miss the table and price at
    zero.
    """
    name = str(model or "").strip().lower()
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    return name


def set_model_price(model: str, *, input: float, output: float,
                    cached_input: float | None = None) -> ModelPrice:
    """Register a price for a model this SDK does not know.

        wt.set_model_price("acme-internal-7b", input=0.0, output=0.0)

    A local model that genuinely costs nothing is worth stating explicitly:
    an asserted zero and an unknown are different facts, and only one of them
    should be trusted in a report.
    """
    price = ModelPrice(input=input, output=output, cached_input=cached_input)
    with _lock:
        _load_env_prices()
        _overrides[_normalise(model)] = price
    return price


def get_model_price(model: str) -> ModelPrice | None:
    """The price for *model*: explicit override, then prefix match, then None."""
    with _lock:
        _load_env_prices()
        name = _normalise(model)
        if name in _overrides:
            return _overrides[name]
        if name in _BUILTIN:
            return _BUILTIN[name]
        # Longest prefix wins, so `claude-sonnet-4-5-20260101` resolves to
        # `claude-sonnet-4-5` rather than to a shorter, cheaper family member.
        candidates = [
            (key, price)
            for key, price in (*_overrides.items(), *_BUILTIN.items())
            if name.startswith(key)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: len(item[0]))[1]


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int,
                  cached_tokens: int = 0) -> float:
    """Cost in USD, or ``0.0`` when the model is unknown.

    Zero here means "we decline to guess". Callers treat a reported cost as
    authoritative and only fall back to this, so inventing a rate for an
    unrecognised model would be worse than admitting ignorance — an invented
    number looks exactly as confident as a real one.
    """
    price = get_model_price(model)
    if price is None:
        return 0.0
    return price.cost(prompt_tokens, completion_tokens, cached_tokens)
