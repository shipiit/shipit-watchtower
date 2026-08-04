"""
Scores and evaluation.

Two things are load-bearing here.

**Source is never lost.** A human thumbs-up and a model's self-assessment are
not the same evidence, and a dashboard that averages them reports a quality
figure nobody should trust. Every score carries where it came from.

**A judge that misbehaves returns nothing.** LLM output is not guaranteed to be
JSON, and an evaluator raising into the request it is judging would be a
self-inflicted outage — the answer has already been delivered.
"""

from __future__ import annotations

import pytest

import shipit_watcher as wt
from shipit_watcher.config import reset_config
from shipit_watcher.context import bind
from shipit_watcher.scoring import (
    JUDGE_RUBRICS,
    Evaluator,
    LLMJudge,
    Score,
    ScoreDataType,
    ScoreSource,
    evaluate,
)
from shipit_watcher.tracer import Tracer


class ScoreCollectingSink:
    def __init__(self):
        self.scores, self.events = [], []

    def start_trace(self, *a, **k): pass
    def end_trace(self, *a, **k): pass
    def record(self, event, ctx): self.events.append(event)
    def flush(self): pass
    def record_score(self, score): self.scores.append(score)


@pytest.fixture(autouse=True)
def _clean():
    reset_config()
    wt.configure(service_name="test", environment="test")
    yield
    reset_config()


@pytest.fixture
def sink():
    return ScoreCollectingSink()


@pytest.fixture
def installed(sink):
    import shipit_watcher.tracer as tmod

    previous = tmod._tracer
    tmod._tracer = Tracer(sinks=[sink])
    try:
        yield tmod._tracer
    finally:
        tmod._tracer = previous


class TestScore:
    def test_numeric_by_default(self):
        assert Score(name="q", value=0.8).data_type == ScoreDataType.NUMERIC

    def test_boolean_inferred(self):
        assert Score(name="q", value=True).data_type == ScoreDataType.BOOLEAN

    def test_categorical_inferred(self):
        assert Score(name="q", value="good").data_type == ScoreDataType.CATEGORICAL

    def test_numeric_value_of_bool(self):
        assert Score(name="q", value=True).numeric_value == 1.0
        assert Score(name="q", value=False).numeric_value == 0.0

    def test_numeric_value_of_category_is_none(self):
        """A category has no meaningful average."""
        assert Score(name="q", value="good").numeric_value is None

    def test_inherits_ambient_context(self):
        with bind(trace_id="t1", user_id="u1", session_id="s1", company_id="c1"):
            s = Score(name="q", value=1)
        assert (s.trace_id, s.user_id, s.session_id, s.company_id) == ("t1", "u1", "s1", "c1")

    def test_explicit_values_win(self):
        with bind(trace_id="t1", user_id="u1"):
            s = Score(name="q", value=1, user_id="override")
        assert s.user_id == "override" and s.trace_id == "t1"

    def test_serialises_completely(self):
        d = Score(name="q", value=0.5, source=ScoreSource.HUMAN, comment="ok").to_dict()
        for key in ("id", "name", "value", "numeric_value", "source",
                    "data_type", "comment", "trace_id", "user_id",
                    "session_id", "company_id", "metadata"):
            assert key in d


class TestRecording:
    def test_score_reaches_the_sink(self, installed, sink):
        with installed.trace("req", user_id="u1"):
            wt.score("user_feedback", 1, comment="helpful")
        assert sink.scores[0].name == "user_feedback"
        assert sink.scores[0].source == ScoreSource.HUMAN

    def test_defaults_to_human_source(self, installed, sink):
        with installed.trace("req"):
            wt.score("thumbs", 1)
        assert sink.scores[0].source == ScoreSource.HUMAN

    def test_programmatic_source(self, installed, sink):
        with installed.trace("req"):
            wt.score("valid_json", True, source=ScoreSource.PROGRAMMATIC)
        assert sink.scores[0].source == ScoreSource.PROGRAMMATIC

    def test_sink_without_score_support_is_skipped(self):
        """Most sinks do not accept scores; that must not raise."""
        class Plain:
            def start_trace(self, *a, **k): pass
            def end_trace(self, *a, **k): pass
            def record(self, *a, **k): pass
            def flush(self): pass

        import shipit_watcher.tracer as tmod
        previous, tmod._tracer = tmod._tracer, Tracer(sinks=[Plain()])
        try:
            with tmod._tracer.trace("req"):
                wt.score("q", 1)          # no exception is the assertion
        finally:
            tmod._tracer = previous

    def test_failing_sink_absorbed(self, installed):
        class Exploding:
            def start_trace(self, *a, **k): pass
            def end_trace(self, *a, **k): pass
            def record(self, *a, **k): pass
            def flush(self): pass
            def record_score(self, s): raise RuntimeError("down")

        import shipit_watcher.tracer as tmod
        previous, tmod._tracer = tmod._tracer, Tracer(sinks=[Exploding()])
        try:
            with tmod._tracer.trace("req"):
                wt.score("q", 1)
        finally:
            tmod._tracer = previous


class TestLLMJudge:
    def test_parses_plain_json(self):
        judge = LLMJudge(lambda p: '{"score": 0.9, "reasoning": "well grounded"}')
        s = judge(input="q", output="a")
        assert s.value == 0.9
        assert s.comment == "well grounded"
        assert s.source == ScoreSource.LLM_JUDGE

    def test_parses_fenced_json(self):
        """Real models wrap JSON in ``` fences — observed with gemini-2.5-flash."""
        judge = LLMJudge(lambda p: '```json\n{"score": 0.75, "reasoning": "ok"}\n```')
        assert judge(input="q", output="a").value == 0.75

    def test_parses_json_embedded_in_prose(self):
        judge = LLMJudge(lambda p: 'Here you go: {"score": 0.4, "reasoning": "weak"} — done')
        assert judge(input="q", output="a").value == 0.4

    def test_clamps_out_of_range(self):
        judge = LLMJudge(lambda p: '{"score": 1.7, "reasoning": "x"}')
        assert judge(input="q", output="a").value == 1.0

    def test_unparseable_returns_none(self):
        """A judge that answers in prose is a failed evaluation, not a crash."""
        judge = LLMJudge(lambda p: "I think it was pretty good, honestly.")
        assert judge(input="q", output="a") is None

    def test_empty_output_returns_none(self):
        """Reasoning models can spend the whole token budget thinking."""
        assert LLMJudge(lambda p: "")(input="q", output="a") is None

    def test_non_numeric_score_returns_none(self):
        judge = LLMJudge(lambda p: '{"score": "great", "reasoning": "x"}')
        assert judge(input="q", output="a") is None

    def test_judge_exception_returns_none(self):
        def boom(prompt): raise RuntimeError("provider down")
        assert LLMJudge(boom)(input="q", output="a") is None

    def test_threshold_flagged(self):
        judge = LLMJudge(lambda p: '{"score": 0.3, "reasoning": "poor"}', threshold=0.7)
        assert judge(input="q", output="a").metadata["below_threshold"] is True

    def test_above_threshold_not_flagged(self):
        judge = LLMJudge(lambda p: '{"score": 0.9, "reasoning": "good"}', threshold=0.7)
        assert judge(input="q", output="a").metadata["below_threshold"] is False

    def test_context_included_in_prompt(self):
        seen = {}
        def capture(prompt):
            seen["prompt"] = prompt
            return '{"score": 1.0, "reasoning": "ok"}'
        LLMJudge(capture)(input="q", output="a", context="the source docs")
        assert "the source docs" in seen["prompt"]

    def test_criterion_and_rubric_in_prompt(self):
        seen = {}
        def capture(prompt):
            seen["prompt"] = prompt
            return '{"score": 1.0, "reasoning": "ok"}'
        LLMJudge(capture, criterion="relevance")(input="q", output="a")
        assert "relevance" in seen["prompt"]
        assert JUDGE_RUBRICS["relevance"] in seen["prompt"]

    def test_default_name_from_criterion(self):
        assert LLMJudge(lambda p: "", criterion="toxicity").name == "judge.toxicity"

    @pytest.mark.parametrize("criterion", sorted(JUDGE_RUBRICS))
    def test_every_rubric_usable(self, criterion):
        judge = LLMJudge(lambda p: '{"score": 1.0, "reasoning": "ok"}', criterion=criterion)
        assert judge(input="q", output="a").value == 1.0


class TestEvaluateBatch:
    def test_runs_all_evaluators(self, installed, sink):
        judges = [
            LLMJudge(lambda p: '{"score": 0.9, "reasoning": "a"}', criterion="faithfulness"),
            LLMJudge(lambda p: '{"score": 0.7, "reasoning": "b"}', criterion="relevance"),
        ]
        with installed.trace("req"):
            scores = evaluate(judges, input="q", output="a")
        assert len(scores) == 2
        assert {s.name for s in scores} == {"judge.faithfulness", "judge.relevance"}

    def test_one_failure_does_not_stop_the_rest(self, installed, sink):
        judges = [
            LLMJudge(lambda p: "not json", criterion="faithfulness"),
            LLMJudge(lambda p: '{"score": 0.8, "reasoning": "ok"}', criterion="relevance"),
        ]
        with installed.trace("req"):
            scores = evaluate(judges, input="q", output="a")
        assert len(scores) == 1
        assert scores[0].name == "judge.relevance"

    def test_custom_programmatic_evaluator(self, installed, sink):
        class JsonCheck(Evaluator):
            name = "valid_json"

            def evaluate(self, *, input, output, **_):
                import json
                try:
                    json.loads(output)
                    ok = True
                except Exception:
                    ok = False
                return Score(name=self.name, value=ok,
                             source=ScoreSource.PROGRAMMATIC)

        with installed.trace("req"):
            good = evaluate([JsonCheck()], input="q", output='{"a": 1}')
            bad = evaluate([JsonCheck()], input="q", output="nope")
        assert good[0].value is True
        assert bad[0].value is False

    def test_base_evaluator_requires_implementation(self):
        assert Evaluator()(input="q", output="a") is None   # absorbed, not raised
