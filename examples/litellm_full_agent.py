"""Complete LiteLLM agent trace: plan -> retrieve -> tool -> generation -> score."""

from __future__ import annotations

import hashlib
import os
from typing import Any

import litellm

import shipit_watcher as wt

MODEL = os.getenv("MODEL", "openai/gpt-4.1-mini")


@wt.observe_tool("customer.lookup", system="crm")
def lookup_customer(user_id: str) -> dict[str, Any]:
    """Replace this body with the application's real CRM/database call."""
    return {"user_id": user_id, "plan": "business", "region": "eu"}


def retrieve_context(query: str) -> list[dict[str, Any]]:
    """Replace the rows with results from the application's vector store."""
    rows = [
        {
            "source": "support-handbook/refunds",
            "version": "2026-08",
            "score": 0.94,
            "text": "Business plans can request a refund within 30 days.",
        }
    ]
    wt.retrieval(
        "knowledge.search",
        query=query,
        knowledge_base="support-handbook",
        chunks=[
            wt.RetrievedChunk(
                source=row["source"],
                version=row["version"],
                score=row["score"],
                content_hash=hashlib.sha256(row["text"].encode()).hexdigest(),
                snippet=row["text"],
            )
            for row in rows
        ],
    )
    return rows


def run_support_agent(
    question: str,
    *,
    user_id: str,
    session_id: str,
    company_id: str,
) -> str:
    with wt.trace(
        "agent.support",
        input={"question": question},
        user_id=user_id,
        session_id=session_id,
        company_id=company_id,
        cost_center="customer-success",
        tags=["agent:support", "channel:api"],
        metadata={"model_route": MODEL},
    ) as trace:
        with wt.span(
            "support.plan",
            event=wt.Event(name="support.plan", type=wt.EventType.CHAIN),
        ):
            route = "refund-policy" if "refund" in question.lower() else "general-support"
            wt.decision(
                "support.route",
                chosen=route,
                options=["refund-policy", "general-support", "human-review"],
                rationale="The request is routed from explicit intent keywords.",
                confidence=0.91,
            )

        with wt.span(
            "support.context",
            event=wt.Event(name="support.context", type=wt.EventType.CHAIN),
        ):
            customer = lookup_customer(user_id)
            documents = retrieve_context(question)

        with wt.span(
            "support.answer",
            event=wt.Event(name="support.answer", type=wt.EventType.AGENT),
        ):
            response = litellm.completion(
                model=MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": "Answer only from the supplied context. Cite the source.",
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Customer: {customer}\n"
                            f"Context: {documents}\n"
                            f"Question: {question}"
                        ),
                    },
                ],
                temperature=0,
            )
            answer = str(response.choices[0].message.content or "")

        trace.set_output({"answer": answer, "route": route})
        wt.score(
            "answer.present",
            bool(answer.strip()),
            source=wt.ScoreSource.PROGRAMMATIC,
            comment="The provider returned a non-empty answer.",
        )
        return answer


if __name__ == "__main__":
    wt.setup(service_name="litellm-support-example", environment="development")
    try:
        print(
            run_support_agent(
                "Can our business account receive a refund?",
                user_id="user-123",
                session_id="session-abc",
                company_id="acme",
            )
        )
    finally:
        wt.flush()
