## What this changes

<!-- One or two sentences. The commit log is the audience. -->

## Why

<!-- The problem, not the patch. If it fixes an issue, link it: Fixes #123 -->

## How it was verified

<!--
Delete what does not apply. "Tests pass" alone is not verification for this
library: a sink can accept an event, return success, and drop it silently —
which is exactly how the OTLP metadata bugs got in.
-->

- [ ] `pytest` passes locally
- [ ] Verified against a real Langfuse instance (trace id: `…`)
- [ ] Checked the observation actually appears, not just that export returned 200

## Checklist

- [ ] No new required dependency (the core stays dependency-free; integrations go behind an extra)
- [ ] Every new integration degrades to a no-op when its library is absent
- [ ] Nothing new can raise into the caller's request path
- [ ] Public API changes are exported in `__init__.py` and documented in the README
- [ ] PII is masked before anything is persisted or leaves the process
