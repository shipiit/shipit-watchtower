"""
Command-line diagnostics.

These exist because "is it working?" is otherwise answered by staring at an
empty dashboard and guessing which of six things is wrong. Every command is
safe to run against production and prints no credential values.
"""

from __future__ import annotations

import argparse
import json
import logging

from .config import get_config
from .setup import doctor, setup


def _doctor() -> int:
    report = doctor()
    for backend in report.backends:
        state = (
            "ready" if backend.available
            else ("configured" if backend.configured else "off")
        )
        print(f"{backend.name:10} {state:10} {backend.detail}")
    print(f"{'sdks':10} {', '.join(sorted(report.instrumented)) or 'none found'}")
    for warning in report.warnings:
        print(f"warning     {warning}")
    for note in report.notes:
        print(f"note        {note}")
    return 0 if report.ok else 1


def _config() -> int:
    config = get_config()
    safe = {
        "service_name": config.service_name,
        "project": config.project,
        "environment": config.environment,
        "release": config.release,
        "enabled": config.enabled,
        "content_policy": config.effective_content_policy,
        "guardrail_mode": config.guardrail_mode,
        "sample_rate": config.sample_rate,
        "langfuse": config.has_langfuse_credentials,
        "phoenix": config.has_phoenix_config,
        "langsmith": config.has_langsmith_credentials,
        "dashboard": bool(config.dashboard_url),
        "persist_to_database": config.persist_to_database,
    }
    print(json.dumps(safe, indent=2))
    return 0


def _init() -> int:
    print("""# One of these is enough. Watcher fans out to every one you set.

# Watcher's own dashboard — `cd dashboard && npm run setup` prints both values
# WATCHER_DASHBOARD_URL=http://localhost:3000
# WATCHER_DASHBOARD_TOKEN=

# ...or a vendor you already use
# LANGFUSE_PUBLIC_KEY=pk-lf-...
# LANGFUSE_SECRET_KEY=sk-lf-...
# PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006
# LANGSMITH_API_KEY=lsv2_...

WATCHER_SERVICE=my-app
WATCHER_PROJECT=default
WATCHER_ENV=development
WATCHER_CONTENT_POLICY=redacted
""")
    print("Then, once, at application startup:\n")
    print("    import shipit_watcher as wt")
    print('    wt.setup(service_name="my-app")\n')
    print("Verify it with `watcher connect`.")
    return 0


def _connect() -> int:
    """Send one real trace and report whether it actually arrived.

    `doctor` reads configuration; this exercises it. The distinction matters:
    a token can be present, well-formed and wrong, and every other check here
    would call that healthy while the destination rejects every trace.
    """
    report = setup(integrations=False, register_shutdown=False)
    print(report)
    print()

    if not report.destinations:
        print("Nothing to connect to yet. Run `watcher init` for a template.")
        return 1

    from .tracer import get_tracer

    tracer = get_tracer()
    with tracer.trace("watcher.connect", input={"source": "watcher CLI"}) as context:
        tracer.policy("diagnostic", blocked=False, reason="connectivity check")
        context.set_output({"ok": True})
    tracer.flush()

    # Ask the sinks that can answer. A sink with no delivery counters cannot
    # confirm anything, and saying so is better than implying success.
    delivered = failed = 0
    unknown: list[str] = []
    for sink in getattr(tracer.sink, "_sinks", []):
        stats = getattr(sink, "delivery_stats", None)
        if isinstance(stats, dict):
            delivered += stats.get("delivered", 0)
            failed += stats.get("failed", 0) + stats.get("dropped", 0)
        else:
            unknown.append(type(sink).__name__)

    if failed:
        print(f"{failed} export(s) failed — the reason is logged above.")
        return 1
    if delivered:
        print(f"Delivered: {delivered} export(s) accepted. "
              f"Look for 'watcher.connect' in your dashboard.")
        return 0
    if unknown:
        print("Sent to " + ", ".join(sorted(set(unknown))) +
              ", which do not report delivery. Check the destination for a "
              "'watcher.connect' trace.")
        return 0
    print("Nothing was sent. Is WATCHER_ENABLED off?")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="watcher",
        description="Diagnostics for shipit-watcher. Nothing here prints a secret.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="print a safe environment template")
    sub.add_parser("connect", help="send one trace and confirm it arrived")
    sub.add_parser("doctor", help="validate the effective backend configuration")
    sub.add_parser("backends", help="alias for doctor")
    sub.add_parser("config", help="show effective non-secret configuration")
    sub.add_parser("test-trace", help="alias for connect")
    args = parser.parse_args(argv)

    # The library logs its diagnoses through `logging` and stays silent unless
    # the host application configures it. A CLI has no host application, so
    # `connect` would print "the reason is logged above" above nothing at all.
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.WARNING, format="%(message)s")

    if args.command == "init":
        return _init()
    if args.command in {"connect", "test-trace"}:
        return _connect()
    if args.command in {"doctor", "backends"}:
        return _doctor()
    return _config()


if __name__ == "__main__":
    raise SystemExit(main())
