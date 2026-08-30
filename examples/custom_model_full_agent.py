"""Trace any local or OpenAI-compatible HTTP model without a provider SDK."""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

import shipit_watcher as wt

MODEL = os.getenv("CUSTOM_MODEL", "acme-local-8b")
ENDPOINT = os.getenv("CUSTOM_MODEL_URL", "http://localhost:8000/v1/chat/completions")


def call_model(messages: list[dict[str, str]]) -> str:
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps({"model": MODEL, "messages": messages}).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )

    with wt.generation(
        "llm.custom-model",
        model=MODEL,
        provider="self-hosted",
        input={"messages": messages},
        endpoint=ENDPOINT,
    ) as generation:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload: dict[str, Any] = json.loads(response.read())

        answer = str(payload["choices"][0]["message"]["content"])
        usage = payload.get("usage") or {}
        generation.prompt_tokens = int(usage.get("prompt_tokens") or 0)
        generation.completion_tokens = int(usage.get("completion_tokens") or 0)
        generation.total_cost = wt.estimate_cost(
            MODEL,
            generation.prompt_tokens,
            generation.completion_tokens,
        )
        generation.output = {"answer": answer}
        return answer


def run_local_agent(question: str, *, user_id: str, session_id: str) -> str:
    with wt.trace(
        "agent.local-model",
        input={"question": question},
        user_id=user_id,
        session_id=session_id,
        cost_center="private-inference",
        tags=["agent:local", "deployment:on-prem"],
    ) as trace:
        wt.decision(
            "model.route",
            chosen=MODEL,
            options=[MODEL, "cloud-fallback"],
            rationale="Private data must remain in the on-premise inference boundary.",
            confidence=1.0,
        )
        answer = call_model(
            [
                {"role": "system", "content": "Answer accurately and concisely."},
                {"role": "user", "content": question},
            ]
        )
        trace.set_output({"answer": answer})
        return answer


if __name__ == "__main__":
    wt.setup(service_name="custom-model-example", environment="development")
    # USD per million tokens. Set the real internal chargeback rates here.
    wt.set_model_price(MODEL, input=0.20, output=0.40)
    try:
        print(
            run_local_agent(
                "Summarise the incident report.",
                user_id="user-123",
                session_id="session-local",
            )
        )
    finally:
        wt.flush()
