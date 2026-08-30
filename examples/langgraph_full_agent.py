"""LangGraph run with automatic root, node, generation, retrieval, and tool traces."""

from __future__ import annotations

import hashlib
import os
from typing import TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

import shipit_watcher as wt

MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")


class AgentState(TypedDict, total=False):
    question: str
    route: str
    context: str
    answer: str


def plan(state: AgentState) -> AgentState:
    route = "knowledge-search" if "policy" in state["question"].lower() else "direct"
    wt.decision(
        "research.route",
        chosen=route,
        options=["knowledge-search", "direct", "human-review"],
        rationale="Policy questions require a versioned internal source.",
        confidence=0.93,
    )
    return {"route": route}


@wt.observe_tool("knowledge.search", system="vector-store")
def search_knowledge(query: str) -> str:
    """Replace this body with the application's real retriever."""
    text = "Refund policy v7: business accounts have a 30-day review window."
    wt.retrieval(
        "knowledge.results",
        query=query,
        knowledge_base="company-policy",
        chunks=[
            wt.RetrievedChunk(
                source="policies/refunds-v7.md",
                version="7",
                score=0.96,
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
                snippet=text,
            )
        ],
    )
    return text


def research(state: AgentState) -> AgentState:
    if state["route"] == "direct":
        return {"context": "No retrieval was required."}
    return {"context": search_knowledge(state["question"])}


def build_graph():
    # Construct provider clients at application startup, not module import.
    # This keeps tooling/docs imports safe before credentials are loaded.
    llm = ChatOpenAI(model=MODEL, temperature=0)

    def answer(state: AgentState) -> AgentState:
        response = llm.invoke(
            [
                ("system", "Answer only from the supplied context and cite its version."),
                ("user", f"Context: {state['context']}\nQuestion: {state['question']}"),
            ]
        )
        return {"answer": str(response.content)}

    builder = StateGraph(AgentState)
    builder.add_node("plan", plan)
    builder.add_node("research", research)
    builder.add_node("answer", answer)
    builder.add_edge(START, "plan")
    builder.add_edge("plan", "research")
    builder.add_edge("research", "answer")
    builder.add_edge("answer", END)
    return wt.instrument_langgraph(builder.compile())


if __name__ == "__main__":
    wt.setup(service_name="langgraph-research-example", environment="development")
    graph = build_graph()
    try:
        result = graph.invoke(
            {"question": "What is the refund policy?"},
            config={
                "configurable": {"thread_id": "session-langgraph"},
                "metadata": {
                    "user_id": "user-123",
                    "company_id": "acme",
                    "cost_center": "research",
                    "channel": "api",
                },
                "tags": ["agent:research", "example:langgraph"],
            },
        )
        print(result["answer"])
    finally:
        wt.flush()
