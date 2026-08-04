<div align="center">

# 🗼 AI Watchtower

**Observability for LLM applications.**

Tracing · Prompt governance · Cost allocation · PII masking

`Python 3.11+` · zero required dependencies · framework-agnostic · 148 tests · 90% coverage

</div>

---

One coherent record of what an AI system did: which prompt ran, what it cost,
which tenant it belonged to, which tools it called, what it retrieved — and
why it chose what it chose.

---

## Contents

- [The problem](#the-problem)
- [Setup in 60 seconds](#setup-in-60-seconds)
- [Fields: required vs optional](#fields-what-is-required-what-is-not)
- [Quick start](#quick-start)
- [Core concepts](#core-concepts)
- [Prompt identity](#prompt-identity)
- [PII masking](#pii-masking)
- [The event model](#the-event-model)
- [Sinks](#sinks)
- [The local ledger](#the-local-ledger)
- [Reporting](#reporting)
- [Configuration](#configuration)
- [Architecture](#architecture)
- [Design rules](#design-rules)
- [Testing](#testing)
- [Integration guide](#integration-guide)

---

## The problem

A typical LLM stack records LLM calls and nothing else. Ours recorded them
*three times*:

```
litellm-acompletion   407 → 476   $0.001312   {}
litellm-completion    407 → 476   $0.001312   {}     ← same call
OpenAI-generation                             {}     ← same call again
```

Same tokens, same cost, three entries, no metadata, no tool calls.

The cause is **double instrumentation**. `litellm.success_callback = ["langfuse"]`
makes LiteLLM open its own trace, the application opens another, and a
proxy-side callback opens a third. None of them carries the tenant, the cost
centre, or which prompt produced the answer.

Watchtower settles the ownership question — **exactly one component traces** —
then adds the dimensions that let a trace answer a business question.

---

## Setup in 60 seconds

**1. Install**

```bash
pip install ai-watchtower[all]
```

**2. Set two environment variables**

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
```

**3. Start tracing**

```python
import ai_watchtower as wt

wt.configure(service_name="my-app")
wt.instrument_litellm()

with wt.trace("chat.request"):
    ...    # every LLM call inside is now traced
```

That is the whole setup. Everything below is optional refinement.

---

## Fields: what is required, what is not

### `wt.configure(...)`

| Field | Required | Default | What it does |
|---|:---:|---|---|
| `service_name` | **recommended** | `unknown-service` | Names the emitting system. Without it, traces from several apps are indistinguishable. |
| `environment` | optional | `development` | `production` / `staging`. Becomes an `env:` tag. |
| `release` | optional | `""` | Version or commit — lets you attribute a regression to a deploy. |
| `enabled` | optional | `True` | Master off switch. |
| `mask_pii` | optional | `True` | Redact before persistence. Leave on. |
| `capture_content` | optional | `True` | `False` = metrics only, no prompt/response bodies. |
| `sample_rate` | optional | `1.0` | Fraction of traces kept. Errors are always kept. |
| `persist_to_database` | optional | `False` | Write the local ledger (Django). |
| `persist_all_events` | optional | `False` | Persist the full tree, not just generations. |
| `langfuse_*` | optional | from env | Overrides the environment variables. |

### `wt.trace(name, ...)`

| Field | Required | What it does |
|---|:---:|---|
| `name` | **yes** | The only required argument — e.g. `"chat.request"`. |
| `company_id` | optional | Tenant. Needed for per-tenant cost reporting. |
| `user_id` | optional | String. Enables per-user filtering in Langfuse. |
| `session_id` | optional | Groups a multi-turn conversation. |
| `cost_center` | optional | MPK / cost centre for chargeback. |
| `channel` | optional | `web`, `api`, `voice` … |
| `tags` | optional | Extra tags, merged with the automatic ones. |
| `metadata` | optional | Extra metadata, merged. |
| `input` | optional | Recorded as the trace input (masked). |

```python
# Minimal
with wt.trace("chat.request"):
    ...

# Fully attributed
with wt.trace("chat.request",
              company_id=str(company.id),     # → per-tenant cost
              user_id=str(user.id),           # → per-user filtering
              session_id=str(session.id),     # → conversation grouping
              cost_center="fleet-ops"):       # → chargeback
    ...
```

### `tracer.generation(...)`

| Field | Required | What it does |
|---|:---:|---|
| `name` | **yes** | e.g. `"llm.completion"`. |
| `model` | recommended | Needed for per-model cost breakdown. |
| `provider` | optional | `openai`, `litellm`, `vertex` … |
| `prompt` | recommended | A `PromptIdentity` — enables prompt governance. |
| `input` | optional | The prompt sent (masked). |

Set usage on the yielded object as it becomes known:

```python
with tracer.generation("llm.completion", model="gpt-4o") as gen:
    response = call_model(...)
    gen.prompt_tokens = response.usage.prompt_tokens
    gen.completion_tokens = response.usage.completion_tokens
    gen.total_cost = response.cost
    gen.output = response.text
```

### `tracer.decision(...)`

| Field | Required | What it does |
|---|:---:|---|
| `name` | **yes** | e.g. `"route.expert"`. |
| `chosen` | **yes** | What was picked. |
| `options` | **yes** | Everything considered — this is what answers *"why not X"*. |
| `rationale` | optional | One line of reasoning. |
| `confidence` | optional | `0.0`–`1.0`. |

### `wt.score(...)`

| Field | Required | What it does |
|---|:---:|---|
| `name` | **yes** | e.g. `"user_feedback"`. |
| `value` | **yes** | Number, bool or string — the type is inferred. |
| `source` | optional | `HUMAN` (default), `LLM_JUDGE`, `PROGRAMMATIC`. |
| `comment` | optional | Free text. |

`trace_id`, `user_id`, `session_id` and `company_id` are filled from the
ambient context automatically.

### `wt.get_prompt(...)`

| Field | Required | What it does |
|---|:---:|---|
| `name` | **yes** | Prompt name in Langfuse. |
| `fallback` | **strongly recommended** | Used if the registry is unreachable *and* nothing is cached. Without it an outage yields an empty prompt. |
| `label` | optional | `production` (default) / `staging`. |
| `version` | optional | Pin an exact version. |

---

## Quick start

```python
import ai_watchtower as wt

wt.configure(service_name="fleetflow", environment="production")
wt.instrument_litellm()          # one trace per call, not three

with wt.trace("chat.request",
              company_id=str(company.id),
              user_id=str(user.id),
              cost_center="fleet-ops"):

    with wt.get_tracer().tool("df_list_cars") as tool:
        tool.output = list_cars(company)

    wt.get_tracer().decision(
        "route.expert",
        chosen="fuel-analysis",
        options=["fuel-analysis", "driver-communication"],
        rationale="query mentions fuel consumption",
        confidence=0.87,
    )
```

Everything inside the block attaches to the trace automatically. Nothing needs
a `trace_id` parameter threaded through it.

### Decorators

```python
@wt.observe_agent("planner")       # opens a root trace
async def run_agent(query: str): ...

@wt.observe_tool("search_fleet")   # records a tool invocation
def search_fleet(q: str): ...

@wt.observe("summarise")           # a plain span
def summarise(text: str): ...
```

Sync, async, generators and async generators are all handled. Generators are
consumed *inside* the span — a decorator that returned the generator object
unconsumed would record a 0 ms span and never see the real work or its errors.

---

## Core concepts

| Concept | What it is |
|---|---|
| **Trace** | One unit of work — typically one user request |
| **Event** | One observation inside a trace: a span, decision, tool call, retrieval |
| **Context** | Ambient tenant / user / cost-centre, propagated via `contextvars` |
| **Sink** | A destination: Langfuse, your database, the console |
| **Prompt identity** | Name + version + content fingerprint, carried on every call |

Context propagates through `contextvars`, so it follows the logical flow of
execution across `await` boundaries and into tasks — without being global state
shared between concurrent requests.

```python
with wt.bind(company_id="acme", cost_center="fleet-ops"):
    ...    # every event here carries both
```

---

## Prompt identity

> *"Prompt identity carried in every call — precondition for enforce, nothing
> else works without it."* — AI Watch Tower requirements

Without it a trace can say which model ran and what it cost, but not **which
prompt version produced this answer** — so governance, replay and
"why this answer" have nothing to hang off.

```python
prompt = wt.identify_prompt(
    agent.system_prompt,
    name=f"agent:{agent.slug}",
    version="v3",
    registered=True,          # came from a managed registry
)
```

| Field | Meaning |
|---|---|
| `name` | Where the prompt came from — `agent:inbox-manager` |
| `version` | Registry version, when one exists |
| `fingerprint` | SHA-256 over normalised text — changes exactly when the prompt does |
| `registered` | **Defaults to `False`** — the gap report is only useful if its default is honest |

The fingerprint makes a compliance gap report possible on day one, with no
registry: group by fingerprint, and any prompt with no registry entry is by
definition unregistered.

---

## PII masking

Applied **before** anything is persisted or leaves the process. Masking at
display time is theatre once the raw value is on someone else's infrastructure.

```python
wt.mask_text("Driver Jan Kowalski PESEL 44051401359, jan@fleet.pl")
# 'Driver Jan Kowalski PESEL [PESEL], [EMAIL]'

wt.mask_text("Odometer 1234567890 km")
# unchanged — ten digits, but not a valid NIP
```

Polish identifiers are first-class and **checksum-validated**:

| Detector | Validation |
|---|---|
| PESEL | weights 1,3,7,9 · complement mod 10 |
| NIP | weights 6,5,7,2,3,4,5,6,7 · mod 11 |
| REGON | 9- and 14-digit variants |
| IBAN | ISO 13616 mod-97 |
| Card | Luhn |
| Email / Phone / IP | pattern, digit-boundary anchored |

**Checksums are the point.** A fleet database is full of ten-digit numbers that
are not tax IDs. Validating the check digit is what stops this redacting the
data the traces exist to explain.

```python
wt.configure(mask_pii=True)                       # default
policy = wt.MaskingPolicy(enabled_rules=frozenset({"EMAIL"}))   # relax explicitly
```

---

## The event model

Typed, because a decision path cannot be rendered from `span(name="something")`.

| Event | Carries |
|---|---|
| `GenerationEvent` | model, provider, tokens, cost, prompt identity |
| `DecisionEvent` | `chosen`, **`options_considered`**, rationale, confidence |
| `ToolInvocationEvent` | tool name, arguments, success, error |
| `RetrievalEvent` | query, knowledge base, chunks with provenance |
| `HandoffEvent` | from_agent → to_agent, reason |
| `PolicyEvent` | policy name, **`blocked`**, reason |
| `HumanReviewEvent` | reviewer, verdict, comment |

Two fields do the heavy lifting:

**`options_considered`** — recorded at the moment of choosing, "why not the
other option" is answerable. Reconstructed afterwards, it is a guess.

**`RetrievedChunk.content_hash`** — a citation without one cannot prove the
source said what the answer claims. The document may have changed since.

```python
tracer.retrieval("kb.policy", query="fuel policy",
    chunks=[wt.RetrievedChunk(source="policy_2026.pdf", score=0.93,
                              version="4", content_hash="9f2a1b")])
```

---

## Sinks

| Sink | Purpose |
|---|---|
| `LangfuseSink` | analysis surface — supports both v2 and v3 client shapes |
| `DjangoSink` | the local ledger — retention, MPK reporting, your boundary |
| `ConsoleSink` | development |

`FanOutSink` isolates them: **if Langfuse is unreachable the database row is
still written**, and vice versa. A sink that raises is logged once and skipped,
never retried in-line — that would put a failing backend on the user's
critical path.

Adding ClickHouse later is a new file, not a change to any call site.

---

## The local ledger

Langfuse is where you *look* at traces. The database is where they are *kept*.

### `LLMCallRecord` — the cost ledger

One row per generation: tenant, cost centre, model, tokens, cost, prompt
identity, latency, and the `trace_id` that joins back to Langfuse.

### `TraceEventRecord` — the full tree *(opt-in)*

Every event including parent/child edges, so the decision path is
reconstructable from your own database:

```python
wt.configure(persist_to_database=True, persist_all_events=True)
```

```
├─ span         planning
│  ├─ decision      route.expert   → fuel-analysis  ✗['driver-communication']
├─ span         execution
│  ├─ tool          df_list_cars
│  ├─ retrieval     kb.policy      → policy.pdf #9f2a1b
│  ├─ generation    llm.completion → 883 tok $0.001312
├─ span         validation
│  ├─ policy        pii_masking
```

Children finish before their parents, so the parent FK is usually null at
insert time. `parent_event_id` always records the edge; resolve the relations
once the trace completes:

```python
DjangoSink.stitch_parents(trace_id)
```

Volume is real — one agent turn emits a dozen events — so `persist_all_events`
is off by default. Enable it for the systems under audit.

---

## Reporting

Because generations land in your own table, the reports are SQL:

```python
# Spend per MPK cost centre
LLMCallRecord.objects.values('cost_center').annotate(
    calls=Count('id'), tokens=Sum('total_tokens'), cost=Sum('total_cost'))

# Spend per tenant
LLMCallRecord.objects.values('company__name').annotate(cost=Sum('total_cost'))

# Prompt compliance gap
LLMCallRecord.objects.filter(prompt_registered=False).values(
    'prompt_name', 'prompt_fingerprint').annotate(n=Count('id'))

# Every decision this tenant made
TraceEventRecord.objects.filter(company=company, event_type='decision')
```

---

## Configuration

Environment first, `configure()` overrides, safe defaults throughout.

| Variable | Default | Meaning |
|---|---|---|
| `WATCHTOWER_SERVICE` | `unknown-service` | tags every trace |
| `WATCHTOWER_ENV` | `development` | environment |
| `WATCHTOWER_ENABLED` | `true` | master switch |
| `WATCHTOWER_MASK_PII` | `true` | redact before persistence |
| `WATCHTOWER_CAPTURE_CONTENT` | `true` | `false` = metrics-only traces |
| `WATCHTOWER_SAMPLE_RATE` | `1.0` | fraction of traces kept |
| `WATCHTOWER_PERSIST_DB` | `false` | enable the local ledger |
| `WATCHTOWER_PERSIST_ALL_EVENTS` | `false` | persist the whole tree |
| `WATCHTOWER_GOVERNANCE` | `audit` | `audit` / `warn` / `enforce` |
| `LANGFUSE_PUBLIC_KEY`<br>`LANGFUSE_SECRET_KEY`<br>`LANGFUSE_HOST` | — | Langfuse |

---

## Architecture

```
        your application
               │
               ▼
    ┌──────────────────────┐
    │       Tracer         │  ← contextvars: tenant, user, MPK
    └──────────┬───────────┘
               │  typed events
               ▼
    ┌──────────────────────┐
    │   masking (PII)      │  ← before anything leaves the process
    └──────────┬───────────┘
               ▼
    ┌──────────────────────┐
    │     FanOutSink       │  ← failures isolated per sink
    └───┬──────────┬───────┘
        ▼          ▼
   Langfuse    your database
   (analysis)  (system of record)
```

LiteLLM calls report into the **ambient trace** rather than opening their own,
which is what removes the duplicates.

---

## Design rules

1. **Observability never breaks the request it observes.** Every entry point
   swallows its own failures. Failing to record is a monitoring incident;
   failing a user's request because recording broke is a worse one.
2. **Exactly one component owns tracing.** `instrument_litellm()` removes
   LiteLLM's Langfuse callback. Keep both and the duplicates return.
3. **PII is masked before persistence**, never at display time.
4. **Fail closed on privacy, open on availability.** A masking error redacts
   the whole value; a sink error is dropped and logged.
5. **Exceptions propagate unchanged.** The tracer marks the span and re-raises.
6. **Sampling never drops errors.** A 10% sample that also discards 90% of
   failures is useless precisely when it is needed.

---

## Testing

```bash
pytest ai_watchtower/tests -q --cov=ai_watchtower --cov-report=term-missing
```

**148 tests · 90% coverage.**

The negative cases carry as much weight as the positive ones. Over-masking
destroys the data traces exist to explain, so *"an odometer reading survives
untouched"* is as much a requirement as *"a PESEL is redacted"*.

Bugs the suite caught during development, rather than assumptions that shipped:

- a `PHONE` pattern matching *inside* a ten-digit number
- `REGON` alternation precedence
- a double-`yield` in `trace()` that replaced the caller's exception with
  `"generator didn't stop after throw()"`
- a public `tracer()` helper shadowing the `ai_watchtower.tracer` submodule
- typed events not inheriting their parent, flattening the decision path

---

## Integration guide

```python
# settings.py or AppConfig.ready()
import ai_watchtower as wt

wt.configure(
    service_name="fleetflow",
    environment=os.getenv("ENV", "development"),
    persist_to_database=True,
)
wt.instrument_litellm()
```

Then wrap your entry point:

```python
with wt.trace("chat.request",
              company_id=str(company.id),
              user_id=str(user.id),
              session_id=str(session.id),
              cost_center=company.cost_center):
    ...
```

> **⚠️ Do not run both.** `instrument_litellm()` replaces LiteLLM's native
> Langfuse callback. To keep LiteLLM's own traces instead, pass
> `replace_langfuse_callback=False` — but then do not create application traces
> as well, or you are back to duplicates.

### Requirements coverage

| Module | Capability | Status |
|---|---|---|
| B | Filter by system, model, user, prompt, date, MPK | ✅ |
| C | Prompt identity in every call | ✅ |
| C | Compliance gap report | ✅ |
| C | Block unregistered prompts (enforce) | ⚠️ flag present, blocking not implemented |
| D | Event model + retrieval provenance | ✅ |
| D | Decision path, why this / why not | ✅ data model — UI still to build |
| E | MPK tagging and allocation | ✅ tagging — hierarchy and rules still custom |
| E | Budgets and alert ladder | ❌ not started |
| G | PII masking before persistence | ✅ |

---

<div align="center">
<sub>Built for FleetFlow · reusable anywhere</sub>
</div>
