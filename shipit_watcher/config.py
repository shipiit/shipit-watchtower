"""
Configuration.

Resolution order is environment → explicit ``configure()`` → defaults, because
the common case is a container with env vars and no bootstrap code at all. An
app that wants to be explicit calls :func:`configure` at startup and wins.

Every setting is safe by default: masking on, sampling at 100%, and the SDK
disabled when no credentials are present rather than erroring.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

__all__ = ["WatcherConfig", "configure", "get_config", "reset_config"]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class WatcherConfig:
    """Immutable runtime configuration.

    Frozen so it cannot drift mid-process — reconfiguring means replacing the
    whole object, which keeps behaviour reproducible when reading a trace back.
    """

    # ── Identity of the emitting system ──────────────────────────────
    # Every trace is tagged with this. Multiple apps (the host application, iFlota) ship
    # to one Langfuse project, so without it the traces are unattributable.
    service_name: str = field(
        default_factory=lambda: os.getenv("WATCHER_SERVICE", "unknown-service")
    )
    environment: str = field(
        default_factory=lambda: os.getenv("WATCHER_ENV", "development")
    )
    release: str = field(default_factory=lambda: os.getenv("WATCHER_RELEASE", ""))

    # ── Master switch ────────────────────────────────────────────────
    enabled: bool = field(default_factory=lambda: _env_bool("WATCHER_ENABLED", True))

    # ── Langfuse ─────────────────────────────────────────────────────
    langfuse_public_key: str = field(
        default_factory=lambda: os.getenv("LANGFUSE_PUBLIC_KEY", "")
    )
    langfuse_secret_key: str = field(
        default_factory=lambda: os.getenv("LANGFUSE_SECRET_KEY", "")
    )
    langfuse_host: str = field(
        default_factory=lambda: os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
    )

    # ── Privacy ──────────────────────────────────────────────────────
    # On by default. Turning masking off is a deliberate, auditable act.
    mask_pii: bool = field(default_factory=lambda: _env_bool("WATCHER_MASK_PII", True))
    # Capture prompt/response bodies at all. Off means metrics-only traces,
    # which some regulated deployments require.
    capture_content: bool = field(
        default_factory=lambda: _env_bool("WATCHER_CAPTURE_CONTENT", True)
    )
    max_content_chars: int = 50_000

    # ── Volume control ───────────────────────────────────────────────
    # Fraction of traces kept. Errors bypass sampling — see Tracer.
    sample_rate: float = field(
        default_factory=lambda: _env_float("WATCHER_SAMPLE_RATE", 1.0)
    )

    # ── Local ledger ─────────────────────────────────────────────────
    # The Django sink. Off unless the host app opts in, so the SDK stays
    # usable outside Django.
    persist_to_database: bool = field(
        default_factory=lambda: _env_bool("WATCHER_PERSIST_DB", False)
    )
    #: Persist *every* event, not just generations, so the whole trace tree —
    #: and therefore the decision path — is reconstructable from the local
    #: database. Higher volume: a single agent turn emits a dozen events, so
    #: enable it for systems under audit rather than globally.
    persist_all_events: bool = field(
        default_factory=lambda: _env_bool("WATCHER_PERSIST_ALL_EVENTS", False)
    )

    #: Which model the Django sink writes to, as ``app_label.ModelName``.
    #:
    #: The sink used to import one hard-coded path, so the ledger only
    #: worked in the application it was extracted from — any other Django
    #: project got an ImportError swallowed by the sink's own except, and
    #: therefore an empty ledger with a warning in the log. The default is
    #: the original path, so existing deployments are unaffected.
    ledger_model: str = field(
        default_factory=lambda: os.getenv(
            "WATCHER_LEDGER_MODEL", "agent.LLMCallRecord"
        )
    )

    #: Where ``persist_all_events`` writes the trace tree. Same reasoning
    #: as :attr:`ledger_model`, and separate because a project may want the
    #: cost ledger without the full tree.
    ledger_event_model: str = field(
        default_factory=lambda: os.getenv(
            "WATCHER_LEDGER_EVENT_MODEL", "agent.TraceEventRecord"
        )
    )

    # ── Governance (cost allocation) ─────────────────────────────────────
    # audit  — record everything, block nothing
    # warn   — record and flag unregistered prompts
    # enforce— refuse calls whose prompt is not registered
    governance_mode: str = field(
        default_factory=lambda: os.getenv("WATCHER_GOVERNANCE", "audit")
    )

    # ── Gateway (LiteLLM proxy) ──────────────────────────────────────
    #: Forward attribution and prompt dimensions to a LiteLLM **proxy** in
    #: ``extra_body.metadata``.
    #:
    #: ``existing_trace_id`` alone tells a server-side gateway which trace to
    #: join, but nothing about *whose* call it was. A gateway that allocates
    #: cost per cost centre, or refuses an unregistered prompt, has to read
    #: those dimensions off the request — it cannot see the caller's
    #: contextvars. Without this they must be re-attached by hand at every
    #: call site that goes through a proxy, which is exactly the kind of
    #: per-call-site duty that leaves attribution half-applied.
    gateway_attribution: bool = field(
        default_factory=lambda: _env_bool("WATCHER_GATEWAY_ATTRIBUTION", True)
    )
    #: Wire names the gateway expects, mapped from this SDK's vocabulary.
    #: Overridable because "cost centre" is spelled differently in every
    #: organisation, and the gateway is usually not ours to change.
    gateway_key_map: dict = field(
        default_factory=lambda: {
            "system_id": "service_name",
            "environment": "environment",
            "mpk": "cost_center",
            "client_id": "company_id",
        }
    )

    #: Who writes the generation record when a proxy is in the path.
    #:
    #: ``app``     — this SDK does, and the gateway's server-side logging is
    #:               expected to be off. Default; correct for a proxy you own.
    #: ``gateway`` — the gateway does, and the SDK stays quiet about
    #:               generations. For a proxy that logs server-side and cannot
    #:               be silenced by a client: otherwise both record the same
    #:               call — the duplicate this module exists to prevent, one
    #:               layer further out.
    #:
    #: Application events (tools, decisions, retrievals) are emitted either
    #: way; only the generation is contested.
    generation_owner: str = field(
        default_factory=lambda: os.getenv("WATCHER_GENERATION_OWNER", "app")
    )

    # ── Langfuse transport ───────────────────────────────────────────
    # sdk  — the classic ingestion API via the Langfuse client. Works on any
    #        server version; every observation is a SPAN or a GENERATION.
    # otlp — OpenTelemetry export. The only route that carries semantic
    #        observation types (agent / tool / retriever / guardrail), which
    #        is what makes Langfuse render the agent graph. Needs a v3 server;
    #        does NOT need the v3 Python SDK.
    langfuse_transport: str = field(
        default_factory=lambda: os.getenv("WATCHER_LANGFUSE_TRANSPORT", "sdk")
    )

    # ── Datasets ─────────────────────────────────────────────────────
    #: Default dataset for `capture()`. Naming it once in the environment
    #: means a capture call at the point of failure does not have to repeat
    #: the dataset name — and moving to a new dataset is a config change
    #: rather than a sweep through every call site.
    dataset: str = field(
        default_factory=lambda: os.getenv("WATCHER_DATASET", "")
    )

    @property
    def has_langfuse_credentials(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def is_active(self) -> bool:
        """Whether anything should actually be emitted."""
        return self.enabled and (self.has_langfuse_credentials or self.persist_to_database)


_config: WatcherConfig | None = None


def get_config() -> WatcherConfig:
    """Current configuration, built from the environment on first access."""
    global _config
    if _config is None:
        _config = WatcherConfig()
    return _config


def configure(**overrides) -> WatcherConfig:
    """Override configuration explicitly. Call once at application startup.

    Unknown keys are ignored rather than raising, so a newer host app can pass
    settings an older SDK does not know about without crashing on boot.
    """
    global _config
    base = get_config()
    known = {k: v for k, v in overrides.items() if hasattr(base, k)}
    _config = replace(base, **known)
    return _config


def reset_config() -> None:
    """Drop the cached configuration. Primarily for tests."""
    global _config
    _config = None
