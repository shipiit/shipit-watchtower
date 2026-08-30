"""Complete Anthropic Messages API agent trace with tool and policy nodes."""

from __future__ import annotations

import os
from typing import Any

import anthropic

import shipit_watcher as wt

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")


@wt.observe_tool("account.permissions", system="identity")
def permissions(user_id: str) -> dict[str, Any]:
    """Replace this body with the application's real authorisation check."""
    return {"user_id": user_id, "can_view_billing": True}


def answer_billing_question(
    question: str,
    *,
    user_id: str,
    session_id: str,
    company_id: str,
) -> str:
    client = anthropic.Anthropic()
    with wt.trace(
        "agent.billing",
        input={"question": question},
        user_id=user_id,
        session_id=session_id,
        company_id=company_id,
        cost_center="finance-support",
        tags=["agent:billing", "provider:anthropic"],
    ) as trace:
        access = permissions(user_id)
        wt.policy(
            "billing-access",
            blocked=not access["can_view_billing"],
            reason="Checked authenticated account permissions before model execution.",
        )
        if not access["can_view_billing"]:
            trace.set_output({"blocked": True})
            return "This account cannot access billing information."

        with wt.span(
            "billing.answer",
            event=wt.Event(name="billing.answer", type=wt.EventType.AGENT),
        ):
            message = client.messages.create(
                model=MODEL,
                max_tokens=600,
                temperature=0,
                system="You are a billing assistant. Never invent account data.",
                messages=[{"role": "user", "content": question}],
            )
            answer = "".join(
                str(getattr(block, "text", ""))
                for block in message.content
                if getattr(block, "type", "") == "text"
            )

        trace.set_output({"answer": answer})
        wt.score(
            "access.allowed",
            True,
            source=wt.ScoreSource.PROGRAMMATIC,
            comment="Authorisation policy passed before generation.",
        )
        return answer


if __name__ == "__main__":
    wt.setup(service_name="anthropic-billing-example", environment="development")
    try:
        print(
            answer_billing_question(
                "Explain the latest invoice adjustment.",
                user_id="user-123",
                session_id="session-anthropic",
                company_id="acme",
            )
        )
    finally:
        wt.flush()
