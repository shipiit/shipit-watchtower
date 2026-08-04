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
from typing import Optional

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

    # ── Governance (cost allocation) ─────────────────────────────────────
    # audit  — record everything, block nothing
    # warn   — record and flag unregistered prompts
    # enforce— refuse calls whose prompt is not registered
    governance_mode: str = field(
        default_factory=lambda: os.getenv("WATCHER_GOVERNANCE", "audit")
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

    @property
    def has_langfuse_credentials(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def is_active(self) -> bool:
        """Whether anything should actually be emitted."""
        return self.enabled and (self.has_langfuse_credentials or self.persist_to_database)


_config: Optional[WatcherConfig] = None


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
