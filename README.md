<div align="center">

<img src="https://raw.githubusercontent.com/shipiit/shipit-watchtower/main/hero.png" alt="shipit-watcher — observability for LLM applications: tracing, agent graphs, prompt governance, cost allocation, PII masking" width="100%">

**Observability for LLM applications.**

Tracing · Agent graphs · Prompt governance · Cost allocation · PII masking

[![PyPI version](https://img.shields.io/pypi/v/shipit-watcher?label=pypi&color=37E5B6)](https://pypi.org/project/shipit-watcher/)
[![Downloads](https://img.shields.io/pypi/dm/shipit-watcher?label=downloads%2Fmonth&color=37E5B6)](https://pypistats.org/packages/shipit-watcher)
[![Python](https://img.shields.io/pypi/pyversions/shipit-watcher?color=9FD9FF)](https://pypi.org/project/shipit-watcher/)
[![Wheel](https://img.shields.io/pypi/wheel/shipit-watcher?color=9FD9FF)](https://pypi.org/project/shipit-watcher/#files)
[![License](https://img.shields.io/pypi/l/shipit-watcher?color=9FD9FF)](LICENSE)

`Python 3.11+` · zero required dependencies · framework-agnostic · 290 tests

```bash
pip install shipit-watcher
```

</div>

---

One coherent record of what an AI system did: which prompt ran, what it cost,
which tenant it belonged to, which tools it called, what it retrieved — and
why it chose what it chose.

---

## Contents

- [The problem](#the-problem)
- [Install](#install)
- [Quick start](#quick-start)
- [Core concepts](#core-concepts)
- [Prompt identity](#prompt-identity)
- [Prompt registry](#prompt-registry)
- [Agent graphs](#agent-graphs)
- [Datasets and experiments](#datasets-and-experiments)
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
- [Going to production](#going-to-production)

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

Watcher settles the ownership question — **exactly one component traces** —
then adds the dimensions that let a trace answer a business question.

---

## Install

```bash
pip install shipit-watcher[all]     # langfuse + litellm + django
pip install shipit-watcher          # core only, no dependencies
```

Every integration degrades to a no-op when its library is absent, so the core
is safe to import anywhere.

---

## Quick start

```python
import shipit_watcher as wt

wt.configure(service_name="my-app", environment="production")
wt.instrument_litellm()          # one trace per call, not three

with wt.trace("chat.request",
              company_id=str(company.id),
              user_id=str(user.id),
              cost_center="support-ops"):

    with wt.get_tracer().tool("list_orders") as tool:
        tool.output = list_cars(company)

    wt.get_tracer().decision(
        "route.expert",
        chosen="billing-analysis",
        options=["billing-analysis", "customer-outreach"],
        rationale="query mentions billing",
        confidence=0.87,
    )
```

Everything inside the block attaches to the trace automatically. Nothing needs
a `trace_id` parameter threaded through it.

### Decorators

```python
@wt.observe_agent("planner")       # opens a root trace
async def run_agent(query: str): ...

@wt.observe_tool("search_docs")   # records a tool invocation
def search_docs(q: str): ...

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
with wt.bind(company_id="acme", cost_center="support-ops"):
    ...    # every event here carries both
```

---

## Prompt identity

> *"Prompt identity carried in every call — precondition for enforce, nothing
> else works without it."* — design note

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

## Prompt registry

Fingerprints tell you *which* prompt ran. The registry decides *what runs* —
so a prompt change becomes a release someone can review, label and roll back,
instead of a diff buried in a Python string.

### The whole turn, in one call

```python
answer = wt.run_prompt(
    "support-assistant",
    variables={"company": "Acme", "vehicles": 142, "drivers": 100},
    user_message="How many items do we have?",
)

answer.text            # the answer
answer.total_cost      # what it cost
answer.total_tokens    # 467 → 121
```

`run_prompt` resolves the live prompt, compiles it, takes `model`,
`temperature` and `max_tokens` from the prompt's own `config`, calls the
model, links the generation to that exact version, and records all of it.
Credentials default to `LITELLM_API_BASE` / `LITELLM_API_KEY`, so the common
case passes neither.

Everything below is that call taken apart, for when you need the pieces.

### One prompt per agent

An application with several agents should not share one prompt namespace.
Prompts are keyed `agent:<slug>`, and the slug is derived from the agent so
`"Support Assistant"` and `"support-assistant"` can never resolve to two
different prompts:

```python
prompt = wt.get_agent_prompt(agent, fallback=agent.system_prompt)
system = prompt.compile(company=company.name, items=142)
```

Passing the agent's own `system_prompt` as `fallback` makes adoption
incremental — agents with a registry entry are managed from Langfuse, agents
without one keep working exactly as before, and `prompt.registered` tells the
compliance report which is which.

```python
wt.agent_prompt_name("Billing Expert")       # → "agent:billing-expert"
```

### Attributing calls you cannot reach

A `litellm.completion` three frames deep inside a tool has no way to pass a
prompt identity. Bind it to the scope instead:

```python
with wt.use_prompt(prompt):
    ...                       # every LLM call in here is attributed
```

Without this, prompt attribution only covers the call sites you remembered to
annotate — which is exactly the gap governance cannot have. Works for
`LLMClient` and for direct `litellm` calls under `instrument_litellm()`.

### Fetch the latest stable prompt

This is the call an agent makes on every turn:

```python
import shipit_watcher as wt

prompt = wt.get_prompt("support-assistant", fallback=LOCAL_DEFAULT)

system = prompt.compile(
    company="Acme",
    items=142,
    customers=100,
    language="English",
)
```

`get_prompt` returns the version currently labelled **`production`** — not the
newest version. Publishing and releasing are separate acts: a new version goes
live only when the `production` label moves onto it, which you do from the
Langfuse UI or from code, with no deploy.

| Argument | Default | What it selects |
|---|---|---|
| `label` | `"production"` | The deployment channel. `"staging"` to try one first. |
| `version` | – | An exact version. Pins a run; overrides `label`. |
| `fallback` | – | Template used if the prompt cannot be resolved at all. |

### Why it does not fail your request

Prompts sit on the critical path of every turn, so resolution degrades in
steps rather than raising:

```
fresh cache  →  registry  →  stale cache  →  fallback
   0.01ms        ~30ms       last good      your string
```

The **stale** rung is the one that earns its keep. If Langfuse is unreachable
the agent keeps answering with the last prompt it successfully fetched, marked
`prompt.stale is True` so a dashboard can show the degradation instead of the
outage hiding.

Fallbacks are cached too. A prompt missing from the registry is missing on
every request, so without caching each one pays a round-trip and a 404 to
learn the same thing.

### Wire it into an agent

The identity travels with the call, so every generation in Langfuse says which
prompt version produced it — and `config` lets the prompt carry its own model
settings, so tuning temperature is also a registry change, not a deploy:

```python
prompt = wt.get_prompt("support-assistant", fallback=LOCAL_DEFAULT)

client = wt.LLMClient(
    model=prompt.config.get("model", "gemini-2.5-pro"),
    api_base=os.environ["LITELLM_API_BASE"],
    api_key=os.environ["LITELLM_API_KEY"],
    temperature=prompt.config.get("temperature", 0.2),
)

with wt.trace("agent.turn", user_id=user.email, session_id=session.id) as ctx:
    answer = client.complete(
        [{"role": "system", "content": prompt.compile(**variables)},
         {"role": "user", "content": question}],
        prompt=prompt.identity,        # ← links the generation to the version
    )
    ctx.set_output({"answer": answer.text})
```

With `WATCHER_GOVERNANCE=enforce`, a prompt that did not come from the
registry is refused **before** the call is made — see
[Configuration](#configuration).

### Publish a version

```python
wt.create_prompt(
    "support-assistant",
    "You are {{company}}'s support assistant. Scope: {{items}} vehicles.",
    labels=["production"],                 # omit to stage without releasing
    tags=["my-app", "agent"],
    config={"model": "gemini-2.5-pro", "temperature": 0.2, "max_tokens": 2000},
    commit_message="Tighten citation rule",
)
```

Langfuse versions by name — this never overwrites, it appends version *n+1*.
Publishing with `labels=[]` stages the prompt for review; moving the
`production` label is the release.

Unlike `get_prompt`, this **raises** on failure. A write that did not land is
a release that did not happen, and a release script must not report success
having changed nothing.

### Chat prompts

Pass a message list and the type is inferred:

```python
wt.create_prompt("triage", [
    {"role": "system", "content": "You triage support incidents."},
    {"role": "user", "content": "{{incident}}"},
])
```

They fingerprint like text prompts — the messages are joined before hashing —
so a chat prompt is just as traceable as a string one.

---

## Agent graphs

Langfuse draws a graph of a trace only when its observations carry a *semantic
type* — `agent`, `tool`, `retriever`, `embedding`, `guardrail`, `chain`,
`evaluator`. A trace of undifferentiated spans renders as a list, because
nothing in it says which box is an agent and which is a tool it called.

Turn it on:

```bash
export WATCHER_LANGFUSE_TRANSPORT=otlp
```

Then write the turn as you would anyway — the types come from which helper you
reach for, not from extra arguments:

```python
with wt.trace("app.agent.turn", user_id=user.email, session_id=sid) as ctx:
    with wt.tool("list_orders") as t:          # → tool node
        t.output = runner.run("list_orders")

    wt.retrieval("kb.search", query=q, chunks=chunks)   # → retriever node
    answer = client.complete(messages, prompt=prompt.identity)   # → generation
    ctx.set_output({"answer": answer.text})
```

renders as:

```
[AGENT]      app.agent.turn      4.20s
  ├ [TOOL]       list_orders            0.55s
  ├ [TOOL]       list_customers         1.40s
  └ [GENERATION] llm.support_assistant     1.22s   571 tok   $0.000400
```

| Watcher call | Langfuse node |
|---|---|
| `wt.trace(...)` | `agent` (the graph's entry point) |
| `wt.tool(...)` | `tool` |
| `wt.retrieval(...)` | `retriever` |
| `wt.generation(...)` / `LLMClient` | `generation` |
| `wt.policy(...)` | `guardrail` |
| `wt.decision(...)` | `chain` |
| `wt.handoff(...)` | `agent` |
| `wt.span(...)` | `span` — no graph node, by design |

### Why a separate transport

Semantic types cannot be sent over the classic ingestion API. Offered one, a
Langfuse v3 server replies:

```
"Invalid option: expected one of \"GENERATION\"|\"SPAN\"|\"EVENT\""
```

They exist only over OTLP, as the span attribute
`langfuse.observation.type`. The Langfuse Python SDK exposes this as `as_type=`
from **3.3.1** — but v3 also removed `client.trace()`, which most existing
integrations call. So this sink speaks OTLP directly over plain HTTP, with no
OpenTelemetry dependency and no SDK upgrade: only the *server* has to be v3.

Requirements: a Langfuse server ≥ 3.x. Check yours with
`curl $LANGFUSE_HOST/api/public/health`.

### `sdk` vs `otlp`

| | `sdk` (default) | `otlp` |
|---|---|---|
| Server needed | any | v3+ |
| Agent graph | ✗ | ✓ |
| Observation types | span / generation | all ten |
| Delivery | Langfuse client's own batching | one request per finished trace |

Both carry user, session, tags, tokens, cost and prompt version. The only
difference is the graph — so `sdk` stays the default, and `otlp` is a
one-variable upgrade when your server supports it.

---

## Datasets and experiments

The Langfuse UI has an **Add to dataset** button on every trace. Right idea,
wrong ergonomics: the cases worth keeping are the ones nobody was watching,
and by the time you notice a bad answer you are scrolling for it.

### Capture as it happens

```python
with wt.trace("agent.turn", user_id=user.email) as ctx:
    answer = run_agent(question)
    ctx.set_output({"answer": answer})

    if user_reported_it_wrong:
        wt.capture("regressions", input=question, metadata={"reported_by": user.email})
```

Called inside a trace, the origin fills itself in — the row keeps a link back
to the trace that produced it, so a failing example can be re-examined rather
than just re-read. The dataset is created on first use, so a capture path
never fails because nobody clicked "New dataset" first.

### Replay and compare

```python
results = wt.run_experiment(
    "regressions",
    task=lambda item: agent.answer(item.input),
    run_name="prompt-v7",
    evaluators=[wt.LLMJudge(judge, criterion="faithfulness")],
)
```

Each item gets its own trace, linked to the dataset row under `run_name`. That
is the difference between *"the new prompt feels better"* and *"the new prompt
scores 0.82 against 0.71 on the same 40 cases"*.

An item whose task raises is recorded as a failure and the run continues —
aborting would throw away the results already gathered, and a task that fails
on one input is itself a finding.

| Call | What it does |
|---|---|
| `wt.capture(dataset, …)` | add the current turn, linked to its trace |
| `wt.add_item(dataset, …)` | add an example directly; `item_id` makes it idempotent |
| `wt.get_items(dataset)` | read every example back |
| `wt.create_dataset(name)` | create; existing datasets are left alone |
| `wt.run_experiment(…)` | replay, score, and record as a named run |

---

## PII masking

Applied **before** anything is persisted or leaves the process. Masking at
display time is theatre once the raw value is on someone else's infrastructure.

```python
wt.mask_text("Jan Kowalski PESEL 44051401359, jan@example.pl")
# 'Jan Kowalski PESEL [PESEL], [EMAIL]'

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

**Checksums are the point.** A business database is full of ten-digit numbers that
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
| `DjangoSink` | the local ledger — retention, cost-centre reporting, your boundary |
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

Enable it with `WATCHER_PERSIST_DB=true` (Django apps only — the sink is
imported lazily, so nothing here loads in a process without an ORM).

**User identity is not assumed to be a primary key.** `user` is a FK and
resolves only when the value *is* a pk; `user_ref` always holds the raw
identifier you passed, whether that is a UUID, an email, or an SSO subject.
Both are indexed. Without this split, Django rejects the **entire row** for a
non-UUID user — so tracing by email lost the record altogether, with only a
warning in the log:

```python
with wt.trace("turn", user_id="user@example.com", session_id=session.id):
    ...
# LLMCallRecord(user=None, user_ref="user@example.com", session_id=…)
```

`session_id` is stored verbatim, so a conversation in your database and a
session in Langfuse are the same string and join without a mapping table.

### `TraceEventRecord` — the full tree *(opt-in)*

Every event including parent/child edges, so the decision path is
reconstructable from your own database:

```python
wt.configure(persist_to_database=True, persist_all_events=True)
```

```
├─ span         planning
│  ├─ decision      route.expert   → billing-analysis  ✗['customer-outreach']
├─ span         execution
│  ├─ tool          list_orders
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
# Spend per cost centre cost centre
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

### The three you actually have to set

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-…
export LANGFUSE_SECRET_KEY=sk-lf-…
export LANGFUSE_HOST=https://langfuse.example.com   # your instance
```

`LANGFUSE_HOST` **is** the base URL, and it is not optional for self-hosting:
it defaults to Langfuse Cloud, so leaving it unset silently ships your traces
to `cloud.langfuse.com` — where the keys do not work and nothing appears. The
keys are two rather than one because Langfuse itself issues them that way: the
public key identifies the project, the secret key authorises the write, and
they are sent as an HTTP Basic pair.

Everything below has a working default. Nothing else is required to start.

| Variable | Default | Meaning |
|---|---|---|
| `WATCHER_LANGFUSE_TRANSPORT` | `sdk` | `otlp` for the [agent graph](#agent-graphs) |
| `WATCHER_DATASET` | – | default dataset, or prefix for [`agent_dataset_name()`](#datasets-and-experiments) |
| `WATCHER_SERVICE` | `unknown-service` | tags every trace |
| `WATCHER_ENV` | `development` | environment |
| `WATCHER_ENABLED` | `true` | master switch |
| `WATCHER_MASK_PII` | `true` | redact before persistence |
| `WATCHER_CAPTURE_CONTENT` | `true` | `false` = metrics-only traces |
| `WATCHER_SAMPLE_RATE` | `1.0` | fraction of traces kept |
| `WATCHER_PERSIST_DB` | `false` | enable the local ledger |
| `WATCHER_PERSIST_ALL_EVENTS` | `false` | persist the whole tree |
| `WATCHER_GOVERNANCE` | `audit` | `audit` / `warn` / `enforce` |
| `LANGFUSE_PUBLIC_KEY`<br>`LANGFUSE_SECRET_KEY`<br>`LANGFUSE_HOST` | — | Langfuse |

---

## Architecture

```
        your application
               │
               ▼
    ┌──────────────────────┐
    │       Tracer         │  ← contextvars: tenant, user, cost centre
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
pytest shipit_watcher/tests -q --cov=shipit_watcher --cov-report=term-missing
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
- a public `tracer()` helper shadowing the `shipit_watcher.tracer` submodule
- typed events not inheriting their parent, flattening the decision path

---

## Integration guide

```python
# settings.py or AppConfig.ready()
import shipit_watcher as wt

wt.configure(
    service_name="my-app",
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

### What is and is not built

Honest status, so nobody discovers a gap in production.

| Capability | Status |
|---|---|
| Filter by service, model, user, prompt, date, cost centre | ✅ |
| Prompt identity on every call | ✅ |
| Compliance gap report (which calls used an unregistered prompt) | ✅ |
| Refuse unregistered prompts (`governance=enforce`) | ✅ blocks pre-call |
| Agent graphs (typed observations over OTLP) | ✅ needs a Langfuse v3 server |
| Prompt registry: fetch, publish, per-agent keys, stale fallback | ✅ |
| Event model with retrieval provenance and decision paths | ✅ data model — bring your own UI |
| Cost-centre tagging and allocation | ✅ tagging; hierarchies and rules stay yours |
| Budgets and alert ladders | ❌ not started |
| PII masking before persistence | ✅ |
| LLM-as-a-judge scoring | ✅ |
| Langfuse datasets / experiment runs | ✅ |

---

## Going to production

Four things to set up, in order. Each is idempotent — safe to run on every
deploy.

### 1. Environment

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-…
LANGFUSE_SECRET_KEY=sk-lf-…
LANGFUSE_HOST=https://langfuse.your-company.com   # not optional when self-hosting

WATCHER_ENV=production
WATCHER_LANGFUSE_TRANSPORT=otlp     # agent graphs; needs a Langfuse v3 server
WATCHER_PERSIST_DB=true             # the local ledger
WATCHER_DATASET=myapp-eval          # prefix for per-agent capture datasets
WATCHER_GOVERNANCE=audit            # warn / enforce once prompts are registered
```

`LANGFUSE_HOST` defaults to Langfuse Cloud. Leaving it unset on a self-hosted
setup sends your traces to `cloud.langfuse.com`, where the keys do not work
and nothing appears — a silent failure, so check it first.

### 2. Configure once, at startup

Not per request, and not per entry point. Observability that depends on
which code path you came through is worse than none:

```python
# Django: AppConfig.ready()   ·   FastAPI: a startup hook   ·   scripts: main()
import shipit_watcher as wt

wt.configure(
    service_name="myapp",
    environment=os.getenv("WATCHER_ENV", "production"),
    langfuse_transport=os.getenv("WATCHER_LANGFUSE_TRANSPORT", "otlp"),
    persist_to_database=True,
    dataset=os.getenv("WATCHER_DATASET", ""),
)
wt.instrument_litellm()
```

`instrument_litellm()` belongs at startup too, not beside the first LLM call —
a background worker that reaches LiteLLM directly would otherwise run
uninstrumented and its calls would go unrecorded.

### 3. Migrate the ledger

The local ledger is a table in *your* database, so it needs a migration like
any other model. On Django, the models live in your app; run your normal
`migrate`. Verify it landed:

```python
from myapp.models import LLMCallRecord
LLMCallRecord.objects.count()
```

Nothing else has to happen for cost and prompt-compliance reporting — every
generation writes a row from then on.

### 4. Seed the prompt registry

Publish what each agent runs *today*, so nothing changes behaviourally and the
text becomes editable without a deploy:

```python
for agent in agents:
    key = wt.agent_prompt_name(agent)            # "agent:inbox-manager"
    current = wt.get_prompt(key, fallback=agent.system_prompt)
    if not current.registered:
        wt.create_prompt(key, agent.system_prompt, labels=["production"],
                         config={"model": agent.model, "temperature": agent.temperature})
```

Two rules worth keeping when you wrap this in a command:

- **Skip an agent that already matches.** Otherwise a scheduled run fills the
  registry with identical versions.
- **Never overwrite a prompt that has diverged.** If the registry text differs
  from the database, somebody edited it in Langfuse — publishing over it from
  code discards their work silently. Report it and require an explicit
  `--force`.

### 5. Let the datasets fill themselves

Capture at the moment something goes wrong, rather than hunting for it later:

```python
if user_disliked_the_answer:
    wt.add_item(
        wt.agent_dataset_name(agent),          # "myapp-eval-inbox-manager"
        input=question,
        expected_output=None,                  # for a human to fill in
        metadata={"reason": "user_disliked", "feedback": feedback},
        source_trace_id=message.trace_id,
        item_id=f"msg-{message.id}",           # idempotent: no duplicates
    )
```

One dataset per agent — a bad answer from the scheduler tells you nothing
about the inbox manager, and a shared dataset makes every run an average of
unrelated cases.

### Verify the whole thing

```python
c = wt.get_config()
assert c.has_langfuse_credentials          # keys reached the process
assert c.langfuse_transport == "otlp"      # graphs will render
assert c.persist_to_database               # ledger is on
from shipit_watcher.instrumentation.litellm import is_instrumented
assert is_instrumented()                   # LLM calls will be recorded

import litellm
assert litellm.success_callback == []      # nothing double-logs
```

The last one matters more than it looks. If another package has installed
Langfuse's OpenAI drop-in (`from langfuse.openai import openai`), it patches
the SDK **globally** and every call is traced a second time. Disable it after
import:

```python
from langfuse.openai import openai
openai.langfuse_enabled = False
```

---

<div align="center">
<sub>MIT licensed · framework-agnostic · works with any Python LLM stack</sub>
</div>
