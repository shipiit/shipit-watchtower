# Full-system integration examples

These examples show complete application traces, not isolated model calls.
Each one records the agent turn, planning stages, branch decisions, tool calls,
retrieval provenance, provider generation, token/cost data, final output and a
quality signal. Open the result in the Watcher UI to see the full graph.

## Start the real local dashboard

From the repository root:

```bash
cd dashboard
npm run setup
npm run dev
```

Open `http://localhost:3000`. Copy the token printed by `npm run setup` into
the shell where the example will run:

```bash
export WATCHER_DASHBOARD_URL=http://localhost:3000
export WATCHER_DASHBOARD_TOKEN=<paste-the-generated-token>
export WATCHER_CONTENT_POLICY=redacted
```

[`examples/.env.example`](.env.example) contains the complete non-secret
template. If the application uses a dotenv loader, copy it to `.env`, insert
the generated token and provider key, and let that application load it.
Watcher itself reads the process environment and does not add a competing
dotenv implementation.

Confirm the entire connection before adding a provider:

```bash
watcher doctor
watcher connect
```

`watcher.connect` should appear on the **Traces** page. This proves the SDK,
token, ingest API and D1 database are connected.

## Choose an example

| Existing stack | Source | Install and run |
|---|---|---|
| LiteLLM (OpenAI, Anthropic, Gemini, Bedrock, Azure and more) | [`litellm_full_agent.py`](litellm_full_agent.py) | `python -m pip install "shipit-watcher[litellm]"`<br>`python examples/litellm_full_agent.py` |
| OpenAI Python SDK | [`openai_full_agent.py`](openai_full_agent.py) | `python -m pip install shipit-watcher openai`<br>`python examples/openai_full_agent.py` |
| Anthropic Python SDK | [`anthropic_full_agent.py`](anthropic_full_agent.py) | `python -m pip install shipit-watcher anthropic`<br>`python examples/anthropic_full_agent.py` |
| LangGraph + LangChain | [`langgraph_full_agent.py`](langgraph_full_agent.py) | `python -m pip install "shipit-watcher[langgraph]" langchain-openai`<br>`python examples/langgraph_full_agent.py` |
| Local/custom/OpenAI-compatible HTTP model | [`custom_model_full_agent.py`](custom_model_full_agent.py) | `python -m pip install shipit-watcher`<br>`python examples/custom_model_full_agent.py` |

Add the provider credential required by the selected example:

```bash
# OpenAI or an OpenAI route through LiteLLM
export OPENAI_API_KEY=...

# Anthropic or an Anthropic route through LiteLLM
export ANTHROPIC_API_KEY=...
```

The LiteLLM example accepts any LiteLLM model route:

```bash
MODEL=anthropic/claude-sonnet-4-5 python examples/litellm_full_agent.py
MODEL=gemini/gemini-2.5-flash python examples/litellm_full_agent.py
MODEL=azure/my-deployment python examples/litellm_full_agent.py
```

Provider-specific environment variables remain LiteLLM's responsibility.
Watcher observes the canonical call after LiteLLM resolves the route.

## What the full trace contains

The LiteLLM example intentionally creates this shape:

```text
agent.support                              root trace
├── support.plan                          chain
│   └── support.route                     decision + rejected options
├── support.context                       chain
│   ├── tool.customer.lookup              tool invocation
│   └── knowledge.search                  retrieval + source/version/hash
└── support.answer                        agent stage
    └── litellm.completion                generation + model/tokens/cost
```

The trace also carries `user_id`, `session_id`, `company_id`, cost centre,
channel, tags and metadata. The final answer is stored on the root, and the
programmatic score is linked to the same trace.

In the UI:

1. Open **Traces** and search for `agent.support`.
2. Click the row and choose **Graph** to inspect parent/child execution.
3. Choose **Timeline** to compare stage and provider latency.
4. Choose **Messages** for conversational input/output.
5. Select a node to inspect Preview, Input, Output, Metadata and Raw JSON.
6. Follow its session/user links to aggregate the same real data.

## LiteLLM: one owner, no duplicate traces

Call `wt.setup()` once before the application makes model calls:

```python
import litellm
import shipit_watcher as wt

wt.setup(service_name="my-agent")

with wt.trace("agent.turn") as trace:
    response = litellm.completion(model="openai/gpt-4.1-mini", messages=[...])
    answer = response.choices[0].message.content
    trace.set_output(answer)
```

Watcher removes LiteLLM's native Langfuse/LangSmith/Phoenix callbacks by
default. Do not add those callbacks again: two instrumentation owners create
duplicate, flat traces with different IDs. To deliberately keep LiteLLM's
native callback instead, use `wt.setup(instrument_litellm=False)` and do not
also create Watcher application traces.

Sync, async, completion, embedding and streaming calls are supported. For a
stream, consume the iterator inside the trace so the span stays open until the
last chunk:

```python
with wt.trace("agent.stream") as trace:
    stream = litellm.completion(model=MODEL, messages=messages, stream=True)
    parts = []
    for chunk in stream:
        text = chunk.choices[0].delta.content or ""
        parts.append(text)
        print(text, end="", flush=True)
    trace.set_output("".join(parts))
```

Watcher records streamed chunk count, first-token latency, final usage and the
assembled output when the provider supplies them.

## OpenAI: automatic SDK instrumentation

`wt.setup()` patches the OpenAI SDK resource classes, so clients created by
application libraries are covered too. Chat Completions, Responses,
Embeddings, sync, async and streaming calls are supported.

```python
import shipit_watcher as wt
from openai import OpenAI

wt.setup(service_name="my-openai-agent")
client = OpenAI()

with wt.trace("agent.turn", user_id="u-1", session_id="s-1") as trace:
    response = client.responses.create(model="gpt-4.1-mini", input="Explain the result")
    trace.set_output(response.output_text)
```

The generation observation records model, provider, input/output, prompt and
completion tokens, cached tokens when reported, cost, duration and errors. A
provider exception is marked as an error and re-raised unchanged.

For streaming Chat Completions:

```python
with wt.trace("agent.stream") as trace:
    stream = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[{"role": "user", "content": "Explain the result"}],
        stream=True,
        stream_options={"include_usage": True},
    )
    parts = []
    for chunk in stream:
        text = (chunk.choices[0].delta.content or "") if chunk.choices else ""
        parts.append(text)
    trace.set_output("".join(parts))
```

## Anthropic: messages, streaming and cache-aware cost

`wt.setup()` instruments `messages.create()` and `messages.stream()` without
replacing Anthropic's stream manager:

```python
import anthropic
import shipit_watcher as wt

wt.setup(service_name="my-anthropic-agent")
client = anthropic.Anthropic()

with wt.trace("agent.stream") as trace:
    parts = []
    with client.messages.stream(
        model="claude-sonnet-4-5",
        max_tokens=800,
        messages=[{"role": "user", "content": "Explain the result"}],
    ) as stream:
        for text in stream.text_stream:
            parts.append(text)
    trace.set_output("".join(parts))
```

The observation includes input/output tokens, cache-read tokens, cache-write
tokens, stop reason, latency and calculated cost. Cache reads and writes are
priced separately rather than being reported as ordinary input.

## LangGraph/LangChain: automatic execution graph

Instrument the compiled graph once:

```python
graph = wt.instrument_langgraph(builder.compile())

result = graph.invoke(
    {"question": question},
    config={
        "configurable": {"thread_id": session_id},
        "metadata": {
            "user_id": user_id,
            "company_id": company_id,
            "cost_center": "research",
        },
    },
)
```

No surrounding root trace is required. The adapter creates it, uses
`thread_id` as `session_id`, preserves LangGraph run IDs as observation IDs,
and records chains, graph nodes, chat/LLM calls, tools, retrievers, streaming
chunks, token usage, output and errors with their real parent relationships.

If a request middleware already opened a Watcher trace, the graph reuses it
instead of opening a duplicate. For a single LangChain call rather than a
compiled graph, pass `wt.langgraph_callback()` through its callbacks config.

## Custom, local and unsupported model SDKs

Use `wt.generation()` around the HTTP/client call and fill the provider facts
when the response arrives:

```python
with wt.generation(
    "llm.private",
    model="acme-local-8b",
    provider="self-hosted",
    input={"messages": messages},
) as generation:
    response = call_private_model(messages)
    generation.prompt_tokens = response.usage.prompt_tokens
    generation.completion_tokens = response.usage.completion_tokens
    generation.total_cost = wt.estimate_cost(
        generation.model,
        generation.prompt_tokens,
        generation.completion_tokens,
    )
    generation.output = response.answer
```

Register the actual price once so an unknown model does not look free:

```python
wt.set_model_price("acme-local-8b", input=0.20, output=0.40)
```

The same pattern covers Gemini's native SDK, Bedrock clients, Ollama, vLLM,
TGI, internal gateways and future providers until a dedicated instrumentor is
added. If the provider is already routed through LiteLLM, prefer the LiteLLM
instrumentor instead of wrapping it manually.

## Attach every request, session and user

For FastAPI, Starlette or another ASGI 3 application:

```python
app.add_middleware(
    wt.WatcherASGIMiddleware,
    # Trust identity headers only when an authenticated proxy sets them.
    identity_headers={
        "user_id": "x-authenticated-user-id",
        "session_id": "x-session-id",
        "company_id": "x-company-id",
    },
)
```

For Flask, Django or another WSGI application:

```python
# Flask
app.wsgi_app = wt.WatcherWSGIMiddleware(app.wsgi_app)

# Django wsgi.py, after get_wsgi_application()
application = wt.WatcherWSGIMiddleware(application)
```

The middleware propagates W3C `traceparent`, keeps streamed HTTP responses
inside the trace, records status, returns `x-watcher-trace-id`, excludes common
health endpoints and never captures request bodies, cookies, authorization
headers or query strings by default.

For a queue worker or scheduled job, bind dynamic identity before the
decorated agent starts:

```python
@wt.observe_agent("invoice-worker")
def execute_invoice_job(invoice_id: str):
    return process_invoice(invoice_id)

def worker_entry(job):
    with wt.bind(
        user_id=job.user_id,
        company_id=job.company_id,
        session_id=job.correlation_id,
        cost_center="finance-automation",
    ):
        return execute_invoice_job(job.invoice_id)
```

## Record the reasoning path explicitly

Provider SDKs can expose calls and usage, but they cannot infer why the
application chose a route or what a retriever returned. Record those facts at
the moment they are known:

```python
wt.decision(
    "agent.route",
    chosen="billing-agent",
    options=["billing-agent", "support-agent", "human-review"],
    rationale="The request references an invoice.",
    confidence=0.95,
)

wt.retrieval(
    "policy.search",
    query=question,
    knowledge_base="policy-vectors",
    chunks=[
        wt.RetrievedChunk(
            source="refunds.md",
            version="7",
            score=0.94,
            content_hash="sha256-of-the-exact-content",
            snippet="The passage supplied to the model.",
        )
    ],
)

with wt.tool("orders.lookup", arguments={"order_id": order_id}) as tool:
    tool.output = lookup_order(order_id)

wt.handoff(
    from_agent="triage",
    to_agent="billing",
    reason="Invoice-specific permissions are required.",
)

wt.policy(
    "pii-output",
    blocked=False,
    reason="Output passed the configured PII rules.",
)
```

Do not record hidden chain-of-thought. Record operationally useful decisions:
the selected route, alternatives, concise rationale, tool arguments/results,
retrieval provenance, guardrail outcome and final answer.

## Add quality and regression data

Record feedback while the trace context is still active:

```python
wt.score("user.feedback", 1, comment="Solved the issue")
wt.score(
    "valid.json",
    True,
    source=wt.ScoreSource.PROGRAMMATIC,
)
```

Capture difficult real cases into a dataset:

```python
wt.capture(
    "support-regressions",
    input=question,
    expected_output=None,
    metadata={"reason": "user-reported failure"},
)
```

The score, dataset item and experiment run retain their trace/session/user
links, so the UI can move from an aggregate regression to the exact execution
that caused it.

## Production checklist

- Call `wt.setup()` once per process, before provider clients are used.
- Keep exactly one instrumentation owner; remove duplicate vendor callbacks.
- Open one root trace per HTTP request, agent turn, queue job or scheduled run.
- Bind `user_id`, `session_id`, `company_id`, cost centre and channel early.
- Set the root output; otherwise the trace detail correctly shows no result.
- Record decisions, tools, retrievals, handoffs and policies when they happen.
- Keep `WATCHER_CONTENT_POLICY=redacted` unless a reviewed environment needs more.
- Call `wt.flush()` during worker/server shutdown.
- Run `watcher connect` after changing URL, token or backend credentials.
- Verify Graph, Timeline, Messages, tokens, cost and scores in the UI.
