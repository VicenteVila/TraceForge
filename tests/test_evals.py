import pytest

from traceforge import trace
from traceforge.collector.memory import MemoryCollector
from traceforge.evals import (
    evaluate_span,
    factuality_score,
    lcs_length,
    llm_judge_score,
    openai_judge,
    run_evals,
    summary,
    tokenize,
    toxicity_score,
)


def test_tokenize_lowercases_and_splits():
    assert tokenize("Hello WORLD, again") == ["hello", "world", "again"]


def test_lcs_length():
    assert lcs_length("the cat sat", "the dog sat") == 2
    assert lcs_length("abc", "xyz") == 0
    assert lcs_length("a", "") == 0


def test_factuality_score_perfect():
    assert factuality_score("the quick brown fox", "the quick brown fox") == pytest.approx(1.0)


def test_factuality_score_partial():
    score = factuality_score("the cat sat on the mat", "the dog sat on the mat")
    assert 0.0 < score < 1.0


def test_factuality_score_no_overlap():
    assert factuality_score("hello world", "completely different") == 0.0


def test_toxicity_score_clean():
    assert toxicity_score("this is a perfectly friendly message") == 0.0


def test_toxicity_score_toxic():
    assert toxicity_score("you are such an idiot, shut up") > 0.0


def test_llm_judge_score_parses_integer():
    score = llm_judge_score("input", "output", "rubric", judge=lambda prompt: "8")
    assert score == pytest.approx(0.8)


def test_llm_judge_score_clamps_and_falls_back():
    assert llm_judge_score("i", "o", "rubric", judge=lambda prompt: "100") == 1.0
    assert llm_judge_score("i", "o", "rubric", judge=lambda prompt: "not a number") == 0.0


def _collector_with_span(agent="eval_agent", output="a good answer", **kw):
    c = MemoryCollector()

    @trace(agent=agent, model="gemini-2.5-flash", collector=c)
    def f():
        return output

    f()
    return c


def test_evaluate_span_factuality_with_reference():
    c = _collector_with_span(output="the cat sat on the mat")
    span = c.query()[0]
    results = evaluate_span(span, references={span.span_id: "the dog sat on the mat"})
    names = {r.name for r in results}
    assert "factuality" in names
    fact = next(r for r in results if r.name == "factuality")
    assert 0.0 < fact.score < 1.0


def test_evaluate_span_lexicon_toxicity():
    c = _collector_with_span(output="you are an idiot")
    results = evaluate_span(c.query()[0])
    tox = next(r for r in results if r.name == "toxicity")
    assert tox.score == 0.0
    assert tox.passed is False


def test_evaluate_span_with_judge():
    c = _collector_with_span()
    results = evaluate_span(c.query()[0], judge=lambda prompt: "9")
    names = {r.name for r in results}
    assert {"toxicity", "llm_as_judge"} <= names


def test_run_evals_over_collector():
    c = _collector_with_span()
    results = run_evals(c)
    assert all(r.span_id == c.query()[0].span_id for r in results)
    assert results


def test_run_evals_by_span_ids():
    c = _collector_with_span()
    span = c.query()[0]
    results = run_evals(c, span_ids=[span.span_id, "missing"])
    assert len(results) >= 1
    assert all(r.span_id == span.span_id for r in results)


def test_summary_aggregates():
    c = _collector_with_span()
    results = run_evals(c)
    agg = summary(results)
    assert set(agg) == {"toxicity"}
    assert agg["toxicity"]["count"] == len(results)
    assert 0.0 <= agg["toxicity"]["avg_score"] <= 1.0


def test_judge_builders_raise_without_sdk():
    with pytest.raises(ImportError):
        openai_judge()("hi")
