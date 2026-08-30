<div align="center">

<img src="https://raw.githubusercontent.com/shipiit/shipit-watchtower/main/hero.png" alt="shipit-watcher — observability for LLM applications: tracing, agent graphs, prompt governance, cost allocation, PII masking" width="100%">

**Observability for LLM applications.**

Tracing · Agent graphs · Prompt governance · Cost allocation · PII masking

[![PyPI version](https://img.shields.io/pypi/v/shipit-watcher?label=pypi&color=37E5B6)](https://pypi.org/project/shipit-watcher/)
[![Downloads](https://img.shields.io/pypi/dm/shipit-watcher?label=downloads%2Fmonth&color=37E5B6)](https://pypistats.org/packages/shipit-watcher)
[![Python](https://img.shields.io/pypi/pyversions/shipit-watcher?color=9FD9FF)](https://pypi.org/project/shipit-watcher/)
[![Wheel](https://img.shields.io/pypi/wheel/shipit-watcher?color=9FD9FF)](https://pypi.org/project/shipit-watcher/#files)
[![License](https://img.shields.io/pypi/l/shipit-watcher?color=9FD9FF)](LICENSE)

`Python 3.11+` · zero required dependencies · framework-agnostic · 427 tests

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
- [Install](#install) — core, integrations, and prerequisites
- [Quick start](#quick-start-sdk--ui) — SDK + real local UI
- [Set up for your stack](#set-up-for-your-stack) — OpenAI, Anthropic, LiteLLM, LangGraph
- [Track everything](#track-everything-not-just-the-model-call) — tools, retrievals, decisions
- [Watcher dashboard](#watcher-dashboard--two-commands) — two commands
- [Core concepts](#core-concepts)
- [Prompt identity](#prompt-identity)
- [Prompt registry](#prompt-registry)
- [Agent graphs](#agent-graphs)
- [Datasets and experiments](#datasets-and-experiments)
- [PII masking](#pii-masking)
- [Content guardrails](#content-guardrails)
- [Cost for models nobody prices](#cost-for-models-nobody-prices)
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

Watcher requires **Python 3.11 or newer**. The local dashboard additionally
requires **Node.js 22.13 or newer**. You do not need Node when exporting only
to Langfuse, Phoenix or LangSmith.

Install the dependency-free core inside the Python project you want to watch:

```bash
python -m pip install shipit-watcher
```

Add only the integrations that project uses, or install everything:

```bash
python -m pip install "shipit-watcher[litellm]"
python -m pip install "shipit-watcher[langgraph]"
python -m pip install "shipit-watcher[langfuse]"
python -m pip install "shipit-watcher[phoenix]"
python -m pip install "shipit-watcher[langsmith]"
python -m pip install "shipit-watcher[all]"
```

The OpenAI and Anthropic instrumentors use the SDK already installed by your
application, so they do not need separate Watcher extras. Missing integrations
remain inactive rather than making import or startup fail.

| Goal | Install |
|---|---|
| Watch an existing OpenAI/Anthropic application | `shipit-watcher` |
| Use the built-in Watcher UI | `shipit-watcher` + the dashboard steps below |
| Use LiteLLM | `shipit-watcher[litellm]` |
| Use LangGraph/LangChain callbacks | `shipit-watcher[langgraph]` |
| Export to one vendor | the matching `langfuse`, `phoenix` or `langsmith` extra |
| Enable every supported integration | `shipit-watcher[all]` |

---

## Quick start: SDK + UI

This is the complete local setup. It uses a real local D1 database, generates
an ingest secret, sends an actual trace, and displays it in the UI.

### 1. Start the dashboard

Clone the repository once if you installed the Python package from PyPI:

```bash
git clone https://github.com/shipiit/shipit-watchtower.git
cd shipit-watchtower/dashboard
npm run setup
npm run dev
```

If you already cloned this repository, start at `cd dashboard`. `npm run setup`
installs dashboard dependencies when needed and creates an ignored
`dashboard/.dev.vars` file containing the local ingest key. It is idempotent:
running it again reuses the key instead of silently breaking connected apps.

Open **http://localhost:3000**. The local dashboard is intentionally open; a
deployed dashboard requires a password.

### 2. Copy the connection values into your Python project

`npm run setup` prints the exact token. Export these values in the shell that
starts the watched application:

```bash
export WATCHER_DASHBOARD_URL=http://localhost:3000
export WATCHER_DASHBOARD_TOKEN=<paste the token printed by npm run setup>
export WATCHER_SERVICE=my-app
export WATCHER_PROJECT=default
export WATCHER_ENV=development
export WATCHER_CONTENT_POLICY=redacted
```

For a `.env` file, use the same lines without `export` and make sure the host
application actually loads that file; Watcher reads the process environment
and does not install a competing dotenv loader.

Do not commit the token. For team or production use, open **Settings & setup**
in the dashboard and create a project-scoped `wtk_…` key. Its plaintext is
shown once; replace `WATCHER_DASHBOARD_TOKEN` with that key.

### 3. Configure Watcher once at application startup

```python
import shipit_watcher as wt

report = wt.setup(service_name="my-app", environment="development")
print(report)
```

Keep the rest of the application unchanged. `setup()` discovers and
instruments installed **OpenAI, Anthropic and LiteLLM** SDKs, detects all
configured destinations, rebuilds lazy sinks and flushes queued exports at
shutdown:

```python
client = OpenAI()                           # unchanged application code
client.chat.completions.create(...)         # now traced, costed and attributed
```

### 4. Send and verify the first real trace

Run this from the same shell/environment that contains the variables above:

```bash
watcher doctor
watcher connect
```

Open **Traces** and search for `watcher.connect`. If it appears, the
SDK, authentication, ingest API and database are all connected. Then run one
normal application request and refresh the page to see its real trace.

If `watcher` is not on the shell path, use:

```bash
python -m shipit_watcher.cli doctor
python -m shipit_watcher.cli connect
```

### Everything the agent did, not just the model call

An LLM call on its own does not explain an answer. Wrap the turn, and the
tools, retrievals and branch points inside it attach themselves:

```python
with wt.trace("chat.request", user_id=user.email, session_id=session.id,
              company_id=str(company.id), cost_center="support-ops"):

    with wt.tool("list_orders") as tool:              # → tool node
        tool.output = list_orders(company)

    wt.retrieval("kb.search", query=q, chunks=chunks)  # → retriever node

    wt.decision("route.expert",                        # → the road not taken
        chosen="billing-analysis",
        options=["billing-analysis", "customer-outreach"],
        rationale="query mentions billing")

    answer = client.chat.completions.create(...)       # → generation
```

Nothing needs a `trace_id` threaded through it — the context propagates
through `contextvars`, so it follows the logical flow of execution across
`await` boundaries and into tasks.

Or skip the `with` blocks entirely and decorate:

```python
@wt.observe_agent("planner")     # opens a root trace
async def run_agent(query): ...

@wt.observe_tool("search_docs")  # records a tool invocation
def search_docs(q): ...
```

For a framework that owns the loop, hand it the graph:

```python
graph = wt.instrument_langgraph(graph)
```

### Pick your backend with environment variables

Watcher does not have a backend of its own to sell you. Set the credentials
for the vendor you already use — or several, and one canonical trace fans out
to all of them with identical ids, prompt identity, tokens, cost and privacy
policy:

```bash
# Langfuse
LANGFUSE_PUBLIC_KEY=pk-lf-…
LANGFUSE_SECRET_KEY=sk-lf-…
LANGFUSE_HOST=https://cloud.langfuse.com

# Phoenix
PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006

# LangSmith
LANGSMITH_API_KEY=lsv2_…

# Watcher's own dashboard
WATCHER_DASHBOARD_URL=https://your-watcher.example
WATCHER_DASHBOARD_TOKEN=the-dashboard-ingest-secret
```

No code changes between them. `FanOutSink` isolates each destination, so if
Langfuse is unreachable the Phoenix export and the local ledger still happen.

| You have | You get |
|---|---|
| LiteLLM | every completion, embedding and streamed call, once — not three times |
| OpenAI SDK | chat completions, responses, embeddings, sync + async, streaming with time-to-first-token |
| Anthropic SDK | messages and streams, with cache reads and cache writes priced apart |
| LangGraph / LangChain | chains, agents, tools, retrievers, streaming, real parent/child run ids |
| None of the above | `wt.trace`, `wt.tool`, `wt.generation` by hand — nothing is required |

### Check it worked

```bash
watcher init      # print a safe environment template
watcher connect   # send one trace and confirm it actually arrived
watcher doctor    # validate the effective configuration
watcher config    # non-secret effective settings
```

`connect` is the one to reach for. `doctor` reads configuration; `connect`
exercises it — and the difference matters, because a token can be present,
well-formed and *wrong*, which every configuration check calls healthy while
the destination rejects every trace:

```
shipit-watcher
  sending to    dashboard
  instrumented  openai, litellm

watcher: the Watcher dashboard rejected the trace: not authorised.
Check the credentials on both sides match — WATCHER_DASHBOARD_TOKEN against
the dashboard's own WATCHER_INGEST_KEY.
```

Both exit non-zero on failure, so either works as a container health check.

`doctor()` returns the same report `setup()` does. Credentials are never
included in it — configured backends are reported as booleans:

```python
report = wt.setup(service_name="my-app")
print(report.instrumented)     # {'openai', 'litellm'}
print(report.warnings)         # things that are wrong
print(report.notes)            # deliberate choices worth stating out loud
```

`report.ok` is False until at least one backend is configured, so it is a
readiness check for a deployment — not something to `assert` on in a quick
start, where it would fail on a fresh install with an empty message.

### Langfuse, Phoenix and LangSmith together

Watcher can fan one canonical trace out to all three backends. Trace ids, root
span ids, prompt identity, tokens, cost, tenant dimensions and privacy policy
remain identical across every export:

```bash
# Langfuse
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=https://cloud.langfuse.com

# Phoenix (local, self-hosted or cloud collector)
PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006
PHOENIX_PROJECT_NAME=my-app
PHOENIX_API_KEY=...

# LangSmith
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=my-app
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

Phoenix and LangSmith use OTLP/HTTP with OpenInference and OpenTelemetry GenAI
attributes. Langfuse uses its Langfuse-specific OTLP mapping. Every backend is
optional and imported lazily.

For explicit construction or custom combinations:

```python
wt.setup(backends=(
    wt.PhoenixBackend.from_env(),
    wt.LangSmithBackend.from_env(),
))
```

Tracing fans out, but prompts and datasets need one authoritative store. It is
selected automatically (Langfuse → Phoenix → LangSmith), or explicitly:

```bash
WATCHER_MANAGEMENT_BACKEND=phoenix   # langfuse | phoenix | langsmith
```

The public calls stay the same: `get_prompt`, `create_prompt`, `capture`,
`get_items` and `run_experiment` do not acquire vendor-specific call sites.

### Watcher dashboard — two commands

Watcher ships its own control plane, so "see my traces" does not require a
vendor account:

```bash
cd dashboard
npm run setup     # installs, generates an ingest secret, prints your env
npm run dev       # http://localhost:3000
```

`setup` prints the two variables the application needs, already filled in:

```bash
WATCHER_DASHBOARD_URL=http://localhost:3000
WATCHER_DASHBOARD_TOKEN=<the secret it generated>
```

Then `wt.setup(...)` as normal, and traces start arriving. **There is no
migration step** — the D1 schema and its indexes create themselves on first
access, so there is nothing to remember and nothing to run twice.

To put it on Cloudflare:

```bash
npm run deploy    # creates the D1 database, uploads the secret, deploys
```

The deploy command also generates/reuses `WATCHER_DASHBOARD_PASSWORD`, uploads
it as a Worker secret, and prints the password needed for the first sign-in.
It does not silently rotate either credential on subsequent deploys.

Both commands are idempotent — `deploy` is also the redeploy command, and
`setup` reports the existing secret rather than rotating it, because a setup
script that quietly invalidates a running deployment's credentials is worse
than one you have to read twice.

The local dashboard is open by design. For a reachable deployment, set
`WATCHER_DASHBOARD_PASSWORD`; the Worker then protects every page and human
read API with a signed, 12-hour session cookie and fails closed if production
has no password. Generate project-scoped write keys from **Settings & setup**.
The plaintext is shown once and only its SHA-256 hash is stored. The legacy
`WATCHER_INGEST_KEY` remains supported so upgrades do not interrupt delivery.

#### What it shows

It stores the same privacy-sanitised canonical trace bundle sent to external
vendors. The overview has real multi-series charts for trace volume, model
cost, observation types, evaluator trends, user consumption and latency
percentiles. The trace browser filters server-side on time, identity, context,
metadata, model, provider, status, observation type, latency, tokens, cost and
quality, with persisted column preferences, traces/observations tabs,
pagination and JSON export.

Opening a trace gives graph, timeline/Gantt and conversation views with a
linked observation inspector for input, output, metadata, raw payload, token
usage, cost, status and scores. Sessions, users, models, evaluations,
policies, prompts, datasets and cost allocation read the same D1 database.
Empty databases render honest empty states; the UI never inserts sample
records.

Delivery from the SDK is queued, bounded, retried with backoff, flushed at
shutdown, and ordered so trace rows exist before the scores that reference
them.

#### First UI walkthrough

1. Open **Overview** and choose the time range and environment. Every chart is
   computed from stored traces; hover a line, point or bar for its exact value.
2. Open **Traces**. Search names/IDs, switch between traces and observations,
   combine server-side filters, change the time range, and paginate real rows.
3. Select **Columns** to open the right-side column drawer. Toggle fields and
   drag them into the preferred order; the browser remembers the layout.
4. Click a trace row. The detail screen shows the observation tree and a
   resizable inspector. Switch between **Graph**, **Timeline** and **Messages**,
   then select any observation to inspect formatted input/output, metadata,
   raw JSON, tokens, cost, latency, status and scores.
5. Use **Sessions** to replay a conversation and **Users** to inspect activity,
   consumption, latency, cost and quality for one identity.
6. Use **Models**, **Costs & budgets** and **Backends** for provider/model usage,
   spend allocation and exporter health.
7. Use **Prompts**, **Datasets** and **Evaluations** to manage prompt versions,
   captured examples, experiment runs and quality scores.
8. Use **Policies** for governance signals and **Settings & setup** for
   project-scoped keys and copy-ready SDK configuration. The UI initially
   follows the system theme; use the moon/sun button to override it and `⌘K`
   for navigation.

| Page | Primary question it answers |
|---|---|
| Overview | Is traffic, latency, quality or cost changing? |
| Traces | Exactly what happened during this request? |
| Sessions | What happened across the full conversation? |
| Users | Which users consume tokens and where do they struggle? |
| Models | Which providers/models are used, slow or expensive? |
| Prompts | Which prompt/version produced the result? |
| Datasets | Which real cases can be replayed as an experiment? |
| Evaluations | Are quality scores improving or regressing? |
| Policies | Which guardrails fired or blocked execution? |
| Costs & budgets | Where is spend allocated? |
| Backends | Which destinations are configured and healthy? |

The UI never fabricates demo rows. A blank page means no stored records match
the current time range and filters. Run `watcher connect`, widen the time
range, or clear active filter chips before troubleshooting charts.

#### Common first-run fixes

| Symptom | Check |
|---|---|
| `watcher connect` says no destination | Export `WATCHER_DASHBOARD_URL` and `WATCHER_DASHBOARD_TOKEN` in that shell |
| Dashboard returns `401` on ingest | The app token must match `.dev.vars`, or use an active `wtk_…` key from Settings |
| Dashboard opens but has no traces | Run `watcher connect`, select the current time range, then clear filters |
| Model calls appear twice | Remove native LiteLLM/Langfuse callbacks and let Watcher own instrumentation |
| Input/output is intentionally absent | Check `WATCHER_CONTENT_POLICY`; `none` and `metadata` omit content |
| Port 3000 is busy | Run `npm run dev -- --port 3001` and update `WATCHER_DASHBOARD_URL` |
| Process exits before delivery | Call `wt.flush()` in the shutdown hook or worker teardown |

### Cost budgets

Budgets follow the current request context, so the same policy naturally
applies to a tenant, agent, prompt, or cost centre without global mutable
state:

```python
with wt.budget(
    0.25,
    action="fallback",
    fallback_model="openai/gpt-4.1-mini",
    tenant="acme",
):
    answer = client.complete(messages)
```

Use `action="warn"` for an observability-only rollout or `action="block"`
for a hard pre-call gate. Provider-reported cost is charged automatically by
`LLMClient`.

### Portable replay bundles

Attach a local bundle sink when reproducing a difficult trace. The universal
content policy is applied before the bundle sees any data:

```python
bundle = wt.TraceBundleSink("failed-trace.json")
tracer = wt.Tracer(sinks=[bundle])

with tracer.trace("support.turn", input=question):
    ...

recording = wt.load_bundle("failed-trace.json")
new_output = wt.replay_bundle(recording, replacement_task)
```

### Distributed traces

Watcher uses W3C Trace Context across HTTP, queues and workers:

```python
# sender
headers = wt.inject_trace_context()

# receiver
with wt.trace("worker.handle", **wt.extract_trace_context(request.headers)):
    ...
```

The receiving service creates its own root span beneath the upstream span;
local tools and generations remain children of the receiving service.

### Content policy

One policy applies before data reaches traces, scores, datasets or experiments:

```python
wt.setup(content_policy="redacted")
```

| Policy | Behavior |
|---|---|
| `none` | no caller content or metadata |
| `metadata` | masked metadata; inputs, outputs and free text omitted |
| `redacted` | masked content and metadata (default) |
| `full` | full content; explicit trusted-environment opt-in |

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

## Set up for your stack

Every example below is complete. Pick the one that matches what you already
use — the rest of the page is detail you can reach for later.

The one line they all share:

```python
import shipit_watcher as wt

wt.setup(service_name="my-app", environment="production")
```

`setup()` looks at what is installed and instruments it. Nothing else in the
application changes.

| Project type | Put `setup()` here | Add a root request/turn trace with |
|---|---|---|
| FastAPI / Starlette / ASGI | lifespan/startup | `WatcherASGIMiddleware` |
| Django | `AppConfig.ready()` | `WatcherWSGIMiddleware` in `wsgi.py` |
| Flask / other WSGI | application factory | wrap `app.wsgi_app` |
| LangGraph / LangChain | graph construction | `instrument_langgraph(graph)` |
| Celery / RQ / worker | worker startup | `@observe_agent` on each job |
| CLI / cron / script | `main()` | `with wt.trace(...)` |
| Reusable library | host application startup | `@observe`, `@observe_tool` inside the library |

Only initialise Watcher once per process. Middleware/decorators create traces
for each request or job; they do not call `setup()` repeatedly.

### OpenAI SDK

```python
from openai import OpenAI
import shipit_watcher as wt

wt.setup(service_name="my-app")
client = OpenAI()

# Traced, costed and attributed. No wrapper, no decorator.
client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "How many orders are open?"}],
)
```

Covers `chat.completions`, `responses` and `embeddings`, sync and async. The
patch goes on the SDK's own classes, so a client constructed inside a library
you do not control is covered too.

Streaming keeps the span open for the whole stream and records
time-to-first-token — for a streamed answer that is the latency a user
actually feels:

```python
stream = client.chat.completions.create(model="gpt-4o", messages=[...], stream=True)
for chunk in stream:
    ...
```

### Anthropic SDK

```python
import anthropic
import shipit_watcher as wt

wt.setup(service_name="my-app")
client = anthropic.Anthropic()

client.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=1024,
    system="You are a support assistant.",
    messages=[{"role": "user", "content": "Why was invoice 4471 rejected?"}],
)

# `messages.stream()` is covered too — the manager is left intact and what it
# yields is what gets traced.
with client.messages.stream(model="claude-sonnet-4-5", max_tokens=1024,
                            messages=[...]) as stream:
    for text in stream.text_stream:
        ...
```

Cache reads are billed at roughly a tenth of input and cache writes at a
premium, so the two are priced apart rather than lumped into one number.

### LiteLLM

```python
import litellm
import shipit_watcher as wt

wt.setup(service_name="my-app")

litellm.completion(model="gpt-4o", messages=[...])     # once, not three times
```

> **⚠️ Do not run both.** `setup()` removes LiteLLM's own Langfuse callback,
> because keeping it means the same call is traced twice — flat, unparented,
> with no tenant and no prompt identity. To keep LiteLLM's native tracing
> instead, call `wt.setup(instrument_litellm=False)` and do not create
> application traces.

### LangChain and LangGraph

```python
import shipit_watcher as wt

wt.setup(service_name="research-agent")
graph = wt.instrument_langgraph(graph)

result = graph.invoke(
    {"messages": messages},
    config={
        "configurable": {"thread_id": session_id},
        "metadata": {"user_id": user_id, "company_id": company_id},
    },
)
```

No surrounding `wt.trace(...)` needed — the adapter owns the root trace and
closes it with the graph's output or its error. Already inside a Watcher
trace, it reuses that one instead of opening a duplicate. LangGraph's
`thread_id` becomes the session id.

For per-call attachment, pass `wt.langgraph_callback()` in LangChain's
`callbacks` config.

### No framework at all

Nothing here requires a model SDK. Record the work directly:

```python
with wt.trace("chat.request", user_id=user.email, session_id=session.id):
    with wt.tool("list_orders") as tool:
        tool.output = list_orders(company)

    with wt.get_tracer().generation("llm.answer", model="my-local-model") as gen:
        gen.prompt_tokens, gen.completion_tokens = 420, 96
        gen.output = answer
```

A model nobody prices reports **no** cost rather than a guessed one, so say
what it costs — an asserted zero and an unknown are different facts:

```python
wt.set_model_price("acme-internal-7b", input=0.0, output=0.0)
```

### Where `setup()` goes

Once, at startup — not per request, and not beside the first model call. A
background worker that reaches the provider directly would otherwise run
uninstrumented and its calls would go unrecorded.

```python
# Django — myapp/apps.py
class MyAppConfig(AppConfig):
    def ready(self):
        import shipit_watcher as wt
        wt.setup(service_name="my-app", environment=os.getenv("ENV", "development"))
```

```python
# FastAPI — lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    wt.setup(service_name="my-app")
    yield
    wt.flush()          # let queued exports finish
```

Add request-level tracing once around the application. This opens the root
trace that provider calls, LangGraph nodes, tools and retrievals attach to:

```python
# FastAPI / Starlette / any ASGI 3 app
app.add_middleware(
    wt.WatcherASGIMiddleware,
    # Only map headers set by a trusted authentication proxy.
    identity_headers={
        "user_id": "x-authenticated-user-id",
        "session_id": "x-session-id",
        "company_id": "x-company-id",
    },
)
```

For identity resolved inside the application, use a resolver instead of
trusting public headers:

```python
app.add_middleware(
    wt.WatcherASGIMiddleware,
    context_resolver=lambda scope, headers: {
        "user_id": scope.get("state", {}).get("user_id"),
        "tags": ["surface:api"],
        "metadata": {"region": "eu"},
    },
)
```

WSGI applications use the same dependency-free instrumentation:

```python
# Flask
app.wsgi_app = wt.WatcherWSGIMiddleware(app.wsgi_app)

# Django — wsgi.py, after get_wsgi_application()
application = wt.WatcherWSGIMiddleware(application)
```

Both middleware classes extract incoming W3C `traceparent`, keep streaming
responses inside the trace, return `x-watcher-trace-id`, and exclude common
health/readiness/metrics endpoints. They never capture bodies, cookies,
authorization values, query strings, or arbitrary headers. Numeric, UUID and
long-hex path segments are normalized to `{id}` in the trace name to prevent
high-cardinality dashboards; the masked input still carries the request path.

```python
# Celery, RQ, a script — main(), or the worker-ready signal
wt.setup(service_name="my-worker")
```

`setup()` registers a flush at exit, so a normal shutdown does not lose the
last few traces. Call `wt.flush()` yourself where the process ends abruptly.

---

## Track everything, not just the model call

An LLM call on its own does not explain an answer. These are the pieces —
each one is a node in the agent graph, and the type comes from which helper
you reach for, not from an extra argument.

| You want to record | Call | Renders as |
|---|---|---|
| One unit of work | `wt.trace("chat.request", user_id=…)` | agent |
| A tool the agent called | `with wt.tool("list_orders") as t:` | tool |
| A RAG lookup, with provenance | `wt.retrieval("kb.search", query=…, chunks=[…])` | retriever |
| A branch, **and the roads not taken** | `wt.decision("route", chosen=…, options=[…])` | chain |
| Delegation to another agent | `wt.handoff(from_agent=…, to_agent=…)` | agent |
| A guardrail firing | `wt.policy("pii_masking", blocked=False)` | guardrail |
| An LLM call you make yourself | `with wt.generation("llm.answer", model=…)` | generation |
| Arbitrary timing | `with wt.span("parse.invoice"):` | span |

```python
with wt.trace("support.turn",
              user_id=user.email,
              session_id=session.id,
              company_id=str(company.id),
              cost_center="support-ops",
              channel="web") as ctx:

    with wt.tool("list_orders") as tool:
        tool.output = list_orders(company)

    wt.retrieval("kb.search", query=question, knowledge_base="policies",
                 chunks=[wt.RetrievedChunk(source="policy.pdf", score=0.94,
                                           version="4", content_hash="9f2a1b")])

    wt.decision("route.expert",
                chosen="billing",
                options=["billing", "support", "escalate"],
                rationale="the question mentions an invoice",
                confidence=0.87)

    answer = client.chat.completions.create(model="gpt-4o", messages=messages)
    ctx.set_output({"answer": answer.choices[0].message.content})

    wt.score("user_feedback", 1, comment="helpful")
```

Everything inside attaches automatically — the context travels through
`contextvars`, so it follows `await` boundaries and tasks without a `trace_id`
threaded through anything.

Or decorate, if that suits the code better:

```python
@wt.observe_agent("planner")      # opens a root trace
async def run_agent(query): ...

@wt.observe_tool("search_docs")   # records a tool invocation
def search_docs(q): ...

@wt.observe("summarise")          # a plain span
def summarise(text): ...
```

Sync, async, generators and async generators are all handled. Generators are
consumed *inside* the span — returning one unconsumed would record a 0 ms span
that never sees the work or its errors.

### Adding dimensions without touching call sites

```python
with wt.bind(company_id="acme", cost_center="support-ops"):
    ...        # every event in here carries both
```

### Across a service boundary

```python
headers = wt.inject_trace_context()                       # sender
with wt.trace("worker.handle", **wt.extract_trace_context(request.headers)):
    ...                                                    # receiver
```

W3C Trace Context, so it works across HTTP, queues and workers — including
with services that are not Watcher.

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

### Through a gateway

A LiteLLM **proxy** sits between your code and the provider, and that is where
a lot of teams do cost allocation and prompt governance. `existing_trace_id`
tells the gateway which trace to join — but nothing about *whose* call it was,
so the gateway ends up allocating spend it cannot attribute.

Attribution and prompt identity are forwarded with the call:

```python
with wt.bind(company_id="acme", cost_center="support-ops"):
    litellm.completion(model="gpt-4o", messages=[...])   # via your proxy
```

The gateway receives the cost centre, the client, the user, the session, the
channel, the service and the environment — plus `prompt_name`,
`prompt_version`, `prompt_fingerprint` and `prompt_registered`, forwarded
verbatim so a gateway in enforce mode can decide on them without a translation
table.

Everything comes from the ambient context, never from a lookup afterwards. A
cost centre resolved after the fact may since have changed, and an attribution
that is only *usually* right is not one you can bill from.

On by default. The wire names are yours to choose:

```python
wt.configure(
    gateway_attribution=True,          # WATCHER_GATEWAY_ATTRIBUTION
    gateway_key_map={                  # wire name → context field
        "system_id": "service_name",
        "environment": "environment",
        "mpk": "cost_center",
        "client_id": "company_id",
    },
    generation_owner="app",            # WATCHER_GENERATION_OWNER: app | gateway
)
```

`generation_owner` decides who writes the generation record when a proxy is in
the path. Leave it `app` for a proxy you own, with the gateway's own
server-side logging off. Set it to `gateway` when the proxy already writes
them — otherwise every call is recorded twice and the cost doubles on paper.

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

### LangGraph and LangChain callbacks

Use the callback adapter when a framework owns the execution loop. It records
real parent/child run ids for chains and agents, chat/LLM generations,
retrievers, tools, streaming first-token latency, token usage, errors, inputs,
and outputs into the same canonical trace. LangGraph `thread_id` becomes the
vendor-neutral session id; common user, tenant, channel, tag, node, and step
metadata is preserved across every destination.

```python
import shipit_watcher as wt

wt.setup(service_name="research-agent", environment="production")
graph = wt.instrument_langgraph(graph)

result = graph.invoke(
    {"messages": messages},
    config={
        "configurable": {"thread_id": session_id},
        "metadata": {"user_id": user_id, "company_id": company_id},
    },
)
```

No surrounding `wt.trace(...)` block is required: the adapter owns the root
trace lifecycle and closes it with the graph output or error. If the graph is
already inside a Watcher trace, it reuses that trace instead of creating a
duplicate. For per-call attachment, pass `wt.langgraph_callback()` in
LangChain's `callbacks` config. The adapter imports LangChain lazily, so
applications that do not use LangGraph do not acquire the dependency.

### Why OTLP is the default

Semantic types cannot be sent over Langfuse's legacy ingestion API. Offered
one, a Langfuse v3 server replies:

```
"Invalid option: expected one of \"GENERATION\"|\"SPAN\"|\"EVENT\""
```

They exist over OTLP as the span attribute `langfuse.observation.type`.
Watcher therefore speaks OTLP directly over HTTP, without requiring an
OpenTelemetry SDK in your application.

Requirements: a Langfuse server ≥ 3.x. Check yours with
`curl $LANGFUSE_HOST/api/public/health`.

### `sdk` vs `otlp`

| | `otlp` (default) | `sdk` (legacy compatibility) |
|---|---|---|
| Agent graph | ✓ | ✗ |
| Observation types | all ten | span / generation |
| Dependency | direct HTTP | Langfuse client |

Both carry user, session, tags, tokens, cost and prompt version. Set
`WATCHER_LANGFUSE_TRANSPORT=sdk` only while migrating an older self-hosted
installation.

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

## Content guardrails

Masking protects the *trace*: the prompt still reaches the model, and the
recorded copy is redacted. A guardrail protects the *call* — the content never
leaves the process at all.

```bash
WATCHER_GUARDRAIL=audit     # off (default) | audit | block
```

```python
wt.guard(outbound_email_body)      # explicit block, whatever the mode
```

In `block` mode a call carrying a validated identifier raises
`GuardrailViolation` before the request is made; in `audit` it records a
`PolicyEvent` and allows it. Run `audit` first — against real traffic it
answers "what would this have refused?", which is the only honest way to find
out whether `block` is safe to turn on.

The detectors are the checksum-validated ones from
[PII masking](#pii-masking), and that is the point. A guardrail built on bare
patterns fires on every ten-digit number in a business database and gets
switched off within a week. One that validates the check digit fires when a
national ID is genuinely about to leave the building.

---

## Cost for models nobody prices

Cost is taken from the provider or gateway when they report one — they know
the contract you are billed under. When they do not, and LiteLLM's pricing map
has never heard of the model either, Watcher prices it from a small built-in
table:

```python
wt.set_model_price("acme-internal-7b", input=0.0, output=0.0)
```

```bash
WATCHER_MODEL_PRICES='{"my-finetune": {"input": 3.0, "output": 15.0}}'
```

Prices are USD per million tokens. Cached input is billed separately where a
provider offers it, so an Anthropic call that reads 900 cached tokens is not
charged as 900 fresh ones — roughly a tenfold difference on a long system
prompt.

An unknown model reports **no** cost rather than a guessed one. A zero that
means "we decline to guess" and a zero that means "this genuinely cost
nothing" are different facts, and only one of them should be trusted in a
report — which is why a local model is worth declaring explicitly.

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
| `LangfuseOTLPSink` | Langfuse semantic observations and agent graphs |
| `PhoenixOTLPSink` | Phoenix/OpenInference traces |
| `LangSmithOTLPSink` | LangSmith OpenTelemetry traces |
| `DashboardSink` | queued delivery to the Watcher D1 control plane |
| `OpenInferenceOTLPSink` | the shared OTLP transport under Phoenix and LangSmith |
| `LangfuseSink` | legacy Langfuse SDK compatibility |
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
# Spend per cost centre
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
| `WATCHER_LANGFUSE_TRANSPORT` | `otlp` | `sdk` only for legacy compatibility |
| `WATCHER_DATASET` | – | default dataset, or prefix for [`agent_dataset_name()`](#datasets-and-experiments) |
| `WATCHER_SERVICE` | `unknown-service` | tags every trace |
| `WATCHER_PROJECT` | `default` | the tenancy scope reported to the dashboard |
| `WATCHER_RELEASE` | – | release/version tag carried on every trace |
| `WATCHER_ENV` | `development` | environment |
| `WATCHER_ENABLED` | `true` | master switch |
| `WATCHER_MASK_PII` | `true` | redact before persistence |
| `WATCHER_CAPTURE_CONTENT` | `true` | `false` = metrics-only traces |
| `WATCHER_CONTENT_POLICY` | `redacted` | `none` / `metadata` / `redacted` / `full` |
| `WATCHER_SAMPLE_RATE` | `1.0` | fraction of traces kept |
| `WATCHER_SLOW_TRACE_MS` | `0` | tail-retain traces at/above threshold; `0` disables |
| `WATCHER_EXPENSIVE_TRACE_USD` | `0` | tail-retain costly traces; `0` disables |
| `WATCHER_TAIL_BUFFER_MAX_EVENTS` | `1000` | bounded events held for a sampled-out trace |
| `WATCHER_EXPORTER_ASYNC` | `true` | non-blocking OTLP delivery |
| `WATCHER_EXPORTER_QUEUE_SIZE` | `256` | bounded traces waiting per exporter |
| `WATCHER_EXPORTER_MAX_RETRIES` | `3` | exponential-backoff delivery attempts |
| `WATCHER_DASHBOARD_URL` | — | Watcher dashboard origin; enables its trace sink |
| `WATCHER_DASHBOARD_TOKEN` | — | project-scoped `wtk_…` bearer key from dashboard Settings; the legacy shared ingest key also works |
| `WATCHER_PERSIST_DB` | `false` | enable the local ledger |
| `WATCHER_PERSIST_ALL_EVENTS` | `false` | persist the whole tree |
| `WATCHER_GOVERNANCE` | `audit` | `audit` / `warn` / `enforce` |
| `WATCHER_GUARDRAIL` | `off` | `off` / `audit` / `block` — refuse content carrying an identifier |
| `WATCHER_GUARDRAIL_RULES` | – | narrow the guardrail, e.g. `PESEL,NIP,IBAN` |
| `WATCHER_MODEL_PRICES` | – | JSON price overrides, USD per million tokens |
| `WATCHER_LEDGER_MODEL` | `agent.LLMCallRecord` | the cost-ledger model, as `app_label.ModelName` |
| `WATCHER_LEDGER_EVENT_MODEL` | `agent.TraceEventRecord` | where `persist_all_events` writes the tree |
| `WATCHER_TAIL_BUFFER_MAX_TRACES` | `500` | how many sampled-out traces may buffer at once |
| `LANGFUSE_PUBLIC_KEY`<br>`LANGFUSE_SECRET_KEY`<br>`LANGFUSE_HOST` | — | Langfuse |
| `PHOENIX_COLLECTOR_ENDPOINT`<br>`PHOENIX_BASE_URL`<br>`PHOENIX_API_KEY`<br>`PHOENIX_PROJECT_NAME` | — | Phoenix OTLP |
| `LANGSMITH_API_KEY`<br>`LANGSMITH_ENDPOINT`<br>`LANGSMITH_OTEL_ENDPOINT`<br>`LANGSMITH_PROJECT` | — | LangSmith OTLP (falls back to `LANGCHAIN_PROJECT`) |
| `WATCHER_MANAGEMENT_BACKEND` | auto | authoritative prompt/dataset backend |

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
    └───┬──────────┬──────────┬───────────┘
        ▼          ▼          ▼           ▼
   Langfuse     Phoenix   LangSmith   Watcher D1
   analysis     analysis  analysis    system of record
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

**427 tests**, and CI runs them across Python 3.11–3.14 alongside `ruff`,
`mypy`, and a coverage floor. A second job lints, type-checks and
production-builds the dashboard, because a claim about quality that nothing
enforces decays into a claim about somebody's afternoon.

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

report = wt.setup(
    service_name="my-app",
    environment=os.getenv("ENV", "development"),
)
if not report.ok:
    # `ok` requires at least one *configured* backend, so this is a real
    # readiness check — and a fresh install fails it deliberately, rather
    # than starting up and quietly recording nothing.
    logger.warning("watcher: %s", report.warnings or "no backend configured")
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
| Compliance gap report (which calls used an unregistered prompt) | ⚠️ the data is there — `prompt_registered` on every generation — but the report is a query you write |
| Refuse unregistered prompts (`governance=enforce`) | ✅ blocks pre-call |
| Agent graphs (typed observations over OTLP) | ✅ Langfuse, Phoenix, LangSmith, Watcher UI |
| LangGraph / LangChain callback capture | ✅ automatic root trace, sessions/users, chains, agents, chat/LLMs, streaming, tools, retrievers, parents, usage, errors |
| Prompt registry: fetch, publish, per-agent keys, stale fallback | ✅ |
| Event model with retrieval provenance and decision paths | ✅ SDK + full Watcher trace UI |
| Cost-centre tagging and allocation | ✅ tagging; hierarchies and rules stay yours |
| Cost budgets that block or downgrade a call pre-request | ✅ `warn` / `fallback` / `block`, scoped to the request context |
| Alerting, monitors, notification channels | ❌ not built — no thresholds, no webhooks, no schedule |
| PII masking before persistence | ✅ |
| LLM-as-a-judge scoring | ✅ |
| Langfuse datasets / experiment runs | ✅ |
| Watcher dashboard | ✅ real D1 data, advanced filters, charts, graph/timeline/messages, session replay, user analytics, evaluation coverage |
| Content guardrails (refuse a call carrying a national ID) | ✅ `WATCHER_GUARDRAIL=audit\|block`, checksum-validated |
| Auto-instrumentation of OpenAI and Anthropic SDKs | ✅ patched at class level, streaming included |
| Model price table for models nobody prices | ✅ `wt.set_model_price()` / `WATCHER_MODEL_PRICES` |
| Dashboard authentication and project isolation | ✅ signed browser sessions; hashed, revocable project write keys across traces, scores, prompts, datasets and experiments |
| OTLP *ingest* into the Watcher dashboard | ❌ not built — it accepts `watcher.trace.v1` only |
| Online evaluation on live traffic, human annotation UI | ❌ not built — evaluators run where you call them |
| Retention / TTL / deletion API | ❌ not built — the D1 database grows until you prune it |
| Non-Python SDK | ❌ not built |

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
WATCHER_LANGFUSE_TRANSPORT=otlp     # default; preserves the full agent graph
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
