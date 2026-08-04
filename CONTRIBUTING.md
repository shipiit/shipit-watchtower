# Contributing

Thanks for taking the time. This document is short because most of what
matters is in four rules.

## The rules

**1. Observability never breaks the thing it observes.** Every entry point
swallows its own failures and logs at WARNING. A tracing bug must never turn
into a 500 for a user who only wanted an answer.

**2. The core has no required dependencies.** It must be importable in an
application that ships neither Langfuse nor LiteLLM nor Django. Integrations
live behind extras and degrade to a no-op when their library is absent.

**3. Exactly one component owns tracing.** Double instrumentation is the
problem this library was built to fix — the same call logged twice under
different names, neither carrying the tenant or the prompt. Do not add a
second path that records the same event.

**4. PII is masked before anything is persisted or leaves the process.**
Masking at display time is theatre once the raw value is on someone else's
infrastructure.

## Getting set up

```bash
git clone https://github.com/shipiit/shipit-watchtower
cd shipit-watchtower
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all,dev]"
pytest
```

## Branching and review

`main` is protected. Push a branch, open a pull request, get one approval.
Direct pushes are rejected — admins can bypass, but please don't.

Keep history linear; merges to `main` are squashed or rebased.

## Tests

New behaviour needs a test. Two things specific to this codebase:

**Assert on payload shape when the backend accepts anything.** The OTLP
endpoint answers `200` to a malformed span and its ingestion job discards the
trace seconds later — there is no error to catch. Both encoding bugs in
`test_otel_sink.py` presented identically: successful export, then a 404 on
lookup. That is why shape is tested there rather than discovered in a UI.

**Test the failure path.** A sink that raises, a registry that is unreachable,
a judge that answers in prose instead of JSON. The degradation ladder is the
contract; the happy path mostly tests itself.

## Commit messages

Say what changed and why it was wrong before. A future reader debugging a
missing trace at 2am is the audience — "fix sink" tells them nothing, "the
dotted metadata form is accepted with a 200 and then silently discarded" tells
them everything.

No AI or tooling attribution in commit messages.

## Reporting a security problem

Do not open a public issue. Use
[a private advisory](https://github.com/shipiit/shipit-watchtower/security/advisories/new).
This library handles API keys and PII-masked payloads.
