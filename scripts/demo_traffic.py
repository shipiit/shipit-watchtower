"""
Generate realistic traffic through the SDK, for looking at the dashboard.

This is not a fixture loader. Every trace here is produced by the real tracer,
masked by the real content policy, and delivered over the real ingest API — so
what shows up on screen is the same path a production application takes. The
only thing that is pretend is the work itself.

    WATCHER_DASHBOARD_URL=http://localhost:3000 \
    WATCHER_DASHBOARD_TOKEN=... \
    python scripts/demo_traffic.py

Timestamps are all "now", because the tracer clocks its own spans. The daily
charts will therefore show one busy day rather than a week — which is the
truth about when this traffic happened.
"""

from __future__ import annotations

import random
import time

import shipit_watcher as wt

AGENTS = ["support.turn", "billing.review", "inbox.triage", "research.answer"]
MODELS = [
    ("gpt-4o", "openai"),
    ("gpt-4.1-mini", "openai"),
    ("claude-sonnet-4-5", "anthropic"),
    ("gemini-2.5-flash", "google"),
]
USERS = ["ana@example.com", "bob@example.com", "carla@example.com", "dan@example.com"]
TOOLS = ["list_orders", "search_docs", "check_inventory", "fetch_invoice"]
QUESTIONS = [
    "How many orders do we have open?",
    "Why was invoice 4471 rejected?",
    "Summarise this thread for the account manager.",
    "Which policy covers a refund after 30 days?",
]


class Clock:
    """Lays spans out one after another, without actually waiting for them.

    The tracer clocks wall time, and this script does no real work, so every
    span would otherwise be 0ms and the timeline would be a row of dots at the
    same instant. The numbers are invented; the shape they produce on screen
    is the shape real traffic produces.
    """

    def __init__(self) -> None:
        self.now = time.time()

    def took(self, event, milliseconds: int) -> None:
        event.started_at = self.now
        self.now += milliseconds / 1000
        event.ended_at = self.now

    def gap(self, milliseconds: int) -> None:
        self.now += milliseconds / 1000


def one_turn(index: int) -> None:
    agent = random.choice(AGENTS)
    model, provider = random.choice(MODELS)
    user = random.choice(USERS)
    session = f"sess-{index // 4}"
    question = random.choice(QUESTIONS)
    # One turn in eight fails, so the error states have something to show.
    fails = index % 8 == 7

    with wt.trace(
        agent,
        user_id=user,
        session_id=session,
        company_id=random.choice(["acme", "globex", "initech"]),
        cost_center=random.choice(["support-ops", "finance", "research"]),
        channel=random.choice(["web", "email", "slack"]),
        input=question,
    ) as context:
        clock = Clock()
        with wt.tool(random.choice(TOOLS)) as tool:
            tool.output = {"rows": random.randint(1, 40)}
        clock.took(tool, random.randint(30, 400))
        clock.gap(random.randint(5, 60))

        if random.random() < 0.6:
            wt.retrieval(
                "kb.search",
                query=question,
                knowledge_base="policies",
                chunks=[
                    wt.RetrievedChunk(
                        source=f"policy_{random.randint(1, 9)}.pdf",
                        score=round(random.uniform(0.72, 0.98), 2),
                        version="4",
                        content_hash=f"{random.getrandbits(24):06x}",
                    ),
                ],
            )

        if random.random() < 0.4:
            wt.decision(
                "route.expert",
                chosen=random.choice(["billing", "support"]),
                options=["billing", "support", "escalate"],
                rationale="the question mentions an invoice",
                confidence=round(random.uniform(0.6, 0.95), 2),
            )

        prompt_tokens = random.randint(200, 1800)
        completion_tokens = random.randint(40, 400)
        with wt.get_tracer().generation(
            "llm.answer", model=model, provider=provider,
        ) as generation:
            generation.prompt_tokens = prompt_tokens
            generation.completion_tokens = completion_tokens
            generation.total_cost = wt.estimate_cost(model, prompt_tokens, completion_tokens)
            generation.metadata["time_to_first_token_ms"] = random.randint(180, 900)
            # The model call dominates the turn, which is what makes a
            # timeline worth looking at.
            clock.took(generation, random.randint(600, 4200))
            if fails:
                generation.severity = wt.Severity.ERROR
                generation.status_message = "RateLimitError: provider returned 429"
                generation.output = None
            else:
                # Deliberately carries an email, so the masking is visible on
                # screen rather than merely claimed in the README.
                generation.output = f"There are {random.randint(2, 20)} open. Contact {user}."

        wt.policy("pii_masking", blocked=False, reason="1x EMAIL")

        if fails:
            context.set_output({"error": "the model was rate limited"})
        else:
            context.set_output({"answer": "See the summary above."})
            wt.score(
                "user_feedback",
                round(random.uniform(0.4, 1.0), 2),
                comment=f"reviewed by {user}",
            )
            if random.random() < 0.5:
                wt.score(
                    "faithfulness",
                    round(random.uniform(0.5, 1.0), 2),
                    source=wt.ScoreSource.LLM_JUDGE,
                    comment="claims are supported by the retrieved policy",
                )


def main(turns: int = 60) -> None:
    report = wt.setup(service_name="demo-app", environment="production",
                      register_shutdown=False)
    print(report)
    if not report.destinations:
        raise SystemExit("\nNo backend configured — set WATCHER_DASHBOARD_URL first.")

    random.seed(7)   # so a re-run produces a comparable shape
    for index in range(turns):
        one_turn(index)
    wt.flush()
    print(f"\nSent {turns} traces.")


if __name__ == "__main__":
    main()
