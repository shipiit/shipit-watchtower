"""One-call setup and diagnostics for production deployments.

``setup()`` is meant to be the whole integration. It detects which backends
are configured, instruments every model SDK that happens to be installed, and
registers a flush at exit — so connecting an existing application is one line
at startup rather than a pass through every call site::

    import shipit_watcher as wt

    wt.setup(service_name="my-app")

Instrumenting only what is installed is deliberate: an application that calls
the OpenAI SDK should not have to name it, and one that does not should not
pay an import for it.
"""

from __future__ import annotations

import atexit
import importlib
import importlib.util
import logging
from dataclasses import dataclass, field
from typing import Any

from .config import configure, get_config

logger = logging.getLogger(__name__)

__all__ = ["INTEGRATIONS", "BackendStatus", "SetupReport", "doctor", "setup"]

_shutdown_registered = False


#: Every model SDK Watcher can instrument, in the order they are attempted.
#: Adding a provider is one entry here plus one module — never a change to
#: `setup()` itself.
INTEGRATIONS: tuple[tuple[str, str], ...] = (
    ("litellm", "shipit_watcher.instrumentation.litellm"),
    ("openai", "shipit_watcher.instrumentation.openai"),
    ("anthropic", "shipit_watcher.instrumentation.anthropic"),
)


@dataclass(frozen=True)
class BackendStatus:
    name: str
    configured: bool
    available: bool
    detail: str = ""


@dataclass
class SetupReport:
    backends: list[BackendStatus] = field(default_factory=list)
    litellm_instrumented: bool = False
    #: Which model SDKs are being traced, e.g. ``{"openai", "litellm"}``.
    instrumented: set[str] = field(default_factory=set)
    #: Things that are wrong: a backend that cannot deliver, a setting outside
    #: its valid range, double-logging. These make :attr:`ok` False.
    warnings: list[str] = field(default_factory=list)
    #: Deliberate, documented choices worth stating out loud — an unmasked
    #: content policy, say. Kept apart from ``warnings`` because the README
    #: tells applications to ``assert report.ok``, and a trusted environment
    #: opting into full capture must not fail its own startup assertion.
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        configured = [b for b in self.backends if b.configured]
        return bool(configured) and all(b.available for b in configured) and not self.warnings

    @property
    def destinations(self) -> list[str]:
        return [backend.name for backend in self.backends if backend.configured]

    def __str__(self) -> str:
        """A readable summary, because `print(report)` is what people do.

        The dataclass repr is four lines of nested objects that answer none of
        the three questions somebody actually has: is it on, where is it
        sending, and what do I fix.
        """
        lines = ["shipit-watcher"]
        lines.append(
            f"  sending to    {', '.join(self.destinations) or 'nothing yet'}"
        )
        lines.append(
            f"  instrumented  {', '.join(sorted(self.instrumented)) or 'no model SDK found'}"
        )
        for warning in self.warnings:
            lines.append(f"  ! {warning}")
        for note in self.notes:
            lines.append(f"  - {note}")
        return "\n".join(lines)


def doctor() -> SetupReport:
    """Inspect effective configuration without exposing credential values."""
    config = get_config()
    statuses = [
        BackendStatus(
            "langfuse", config.has_langfuse_credentials,
            config.has_langfuse_credentials,
            f"transport={config.langfuse_transport}; host={config.langfuse_host}",
        ),
        BackendStatus(
            "phoenix", config.has_phoenix_config,
            config.has_phoenix_config,
            f"project={config.phoenix_project or 'default'}",
        ),
        BackendStatus(
            "langsmith", config.has_langsmith_credentials,
            config.has_langsmith_credentials,
            f"project={config.langsmith_project}",
        ),
        BackendStatus(
            "dashboard", bool(config.dashboard_url), bool(config.dashboard_url),
            f"url={config.dashboard_url or 'not configured'}",
        ),
        BackendStatus(
            "django", config.persist_to_database,
            importlib.util.find_spec("django") is not None
            if config.persist_to_database else False,
            f"model={config.ledger_model}",
        ),
    ]
    warnings: list[str] = []
    active: set[str] = set()
    for name, module_path in INTEGRATIONS:
        if importlib.util.find_spec(name) is None:
            continue
        try:
            if importlib.import_module(module_path).is_instrumented():
                active.add(name)
        except Exception:
            warnings.append(f"{name} is installed but its state is unreadable")

    instrumented = "litellm" in active
    if importlib.util.find_spec("litellm") is not None:
        try:
            import litellm

            native = [
                c for c in (getattr(litellm, "success_callback", []) or [])
                if c in {"langfuse", "langsmith", "phoenix"}
            ]
            if instrumented and native:
                warnings.append(
                    "LiteLLM native observability callbacks remain active: "
                    + ", ".join(sorted(set(native)))
                )
        except Exception:
            warnings.append("LiteLLM callbacks could not be inspected")
    if config.sample_rate < 0 or config.sample_rate > 1:
        warnings.append("sample_rate should be between 0 and 1")

    # The single most common first-run state, and it used to report
    # `ok=False` with an empty warnings list — a failure with no explanation
    # attached to it.
    if not any(backend.configured for backend in statuses):
        warnings.append(
            "no backend configured, so traces are only printed to the console. "
            "Set WATCHER_DASHBOARD_URL for Watcher's own dashboard, or "
            "LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY, PHOENIX_COLLECTOR_ENDPOINT "
            "or LANGSMITH_API_KEY for a vendor. `watcher init` prints a template."
        )
    # Reachable but unauthorised is worse than unconfigured: it looks like it
    # is working, and every trace is silently refused.
    if config.dashboard_url and not config.dashboard_token:
        warnings.append(
            "WATCHER_DASHBOARD_URL is set but WATCHER_DASHBOARD_TOKEN is not. "
            "If the dashboard has an ingest key, every trace will be rejected."
        )

    notes: list[str] = []
    # Read the *effective* policy rather than `mask_pii`. Both spellings reach
    # the same unmasked state, and flagging only the legacy switch left the
    # documented one — `content_policy="full"` — passing silently.
    if config.effective_content_policy == "full":
        notes.append(
            "content is captured unmasked (content_policy=full); "
            "PII will reach every configured backend verbatim"
        )
    elif config.effective_content_policy == "none":
        notes.append("content capture is off (content_policy=none); traces carry metrics only")
    return SetupReport(statuses, instrumented, active, warnings, notes)


def setup(*, instrument_litellm: bool = True, register_shutdown: bool = True,
          integrations: bool = True, **overrides: Any) -> SetupReport:
    """Configure Watcher, detect backends, instrument model SDKs, flush at exit.

    ``integrations=False`` leaves every SDK untouched — for an application
    that wants to choose its own, or that already instruments through
    something else and would otherwise record each call twice.
    """
    global _shutdown_registered
    if overrides:
        configure(**overrides)

    # Rebuild lazy sinks if setup follows an early import/use of the tracer.
    from .tracer import get_tracer

    tracer = get_tracer()
    tracer._sink = None

    # A registry created before setup may be bound to a different vendor.
    # Rebuild it so management_backend changes take effect immediately.
    from . import prompts

    prompts._registry = None

    if integrations:
        for name, module_path in INTEGRATIONS:
            if name == "litellm" and not instrument_litellm:
                continue
            if importlib.util.find_spec(name) is None:
                continue
            try:
                importlib.import_module(module_path).instrument()
            except Exception:
                # One SDK failing to instrument must not stop the others, and
                # certainly must not stop the application from starting.
                logger.warning("watcher: could not instrument %s", name, exc_info=True)

    if register_shutdown and not _shutdown_registered:
        atexit.register(tracer.flush)
        _shutdown_registered = True
    return doctor()
