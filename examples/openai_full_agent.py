"""Complete OpenAI Responses API agent trace with automatic SDK instrumentation."""

from __future__ import annotations

import os

from openai import OpenAI

import shipit_watcher as wt

MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")


@wt.observe_tool("orders.lookup", system="orders-db")
def lookup_order(order_id: str) -> dict[str, str]:
    """Replace this body with the application's real order query."""
    return {"order_id": order_id, "status": "in_transit", "eta": "2026-09-02"}


def answer_order_question(
    question: str,
    *,
    order_id: str,
    user_id: str,
    session_id: str,
) -> str:
    client = OpenAI()
    with wt.trace(
        "agent.order-support",
        input={"question": question, "order_id": order_id},
        user_id=user_id,
        session_id=session_id,
        channel="web",
        tags=["agent:order-support"],
    ) as trace:
        with wt.span(
            "order.plan",
            event=wt.Event(name="order.plan", type=wt.EventType.CHAIN),
        ):
            wt.decision(
                "order.action",
                chosen="lookup-order",
                options=["lookup-order", "search-policy", "handoff-human"],
                rationale="The answer depends on current order state.",
                confidence=0.98,
            )

        order = lookup_order(order_id)

        with wt.span(
            "order.compose-answer",
            event=wt.Event(name="order.compose-answer", type=wt.EventType.AGENT),
        ):
            response = client.responses.create(
                model=MODEL,
                instructions="Answer concisely using only the supplied order record.",
                input=f"Order record: {order}\nCustomer question: {question}",
            )
            answer = response.output_text

        trace.set_output({"answer": answer, "order_status": order["status"]})
        wt.score(
            "order_id.mentioned",
            order_id in answer,
            source=wt.ScoreSource.PROGRAMMATIC,
        )
        return answer


if __name__ == "__main__":
    wt.setup(service_name="openai-order-example", environment="development")
    try:
        print(
            answer_order_question(
                "Where is order ORD-2048?",
                order_id="ORD-2048",
                user_id="user-123",
                session_id="session-openai",
            )
        )
    finally:
        wt.flush()
