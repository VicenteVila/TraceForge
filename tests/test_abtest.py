from traceforge import trace
from traceforge.abtest import compare_prompts


def _make_fn():
    @trace(agent="ab_agent", model="gemini-2.5-flash")
    def run(prompt, sample):
        out = f"{prompt}::{(sample or '')}"
        sum(range(200000))
        return out

    return run


def test_compare_prompts_metrics_per_variant():
    fn = _make_fn()
    result = compare_prompts(
        fn,
        {"fast": "do it fast", "slow": "do it very slowly and carefully"},
    )
    assert {v.name for v in result.variants} == {"fast", "slow"}
    assert all(v.runs == 1 for v in result.variants)
    assert all(v.avg_duration_ms > 0 for v in result.variants)
    assert all(v.avg_eval_score is not None for v in result.variants)
    assert result.winner in {"fast", "slow"}


def test_compare_prompts_picks_better_eval_score():
    fn = _make_fn()

    def judge(prompt):
        if "toxic" in prompt:
            return "1"  # content is not toxic
        return "9" if "good" in prompt else "3"

    result = compare_prompts(
        fn,
        {"good": "good answer", "bad": "bad answer"},
        judge=judge,
        references=["the correct reference answer"],
    )
    assert result.winner == "good"
    good = next(v for v in result.variants if v.name == "good")
    bad = next(v for v in result.variants if v.name == "bad")
    assert good.avg_eval_score > bad.avg_eval_score


def test_compare_prompts_error_variant_never_wins():
    @trace(agent="ab_sel")
    def run(prompt, sample):
        if prompt == "bad":
            raise ValueError("boom")
        return "ok"

    result = compare_prompts(run, {"bad": "bad", "good": "good"})
    assert result.winner == "good"
    assert next(v for v in result.variants if v.name == "bad").error_rate == 1.0


def test_compare_prompts_handles_multiple_samples():
    fn = _make_fn()
    result = compare_prompts(
        fn,
        {"v1": "one", "v2": "two"},
        samples=["a", "b", "c"],
        references=["ra", "rb", "rc"],
        judge=lambda prompt: "7",
    )
    assert all(v.runs == 3 for v in result.variants)
    assert all(v.avg_eval_score is not None for v in result.variants)


def test_compare_prompts_single_string_prompt():
    fn = _make_fn()
    result = compare_prompts(fn, "just one prompt")
    assert len(result.variants) == 1


def test_result_string_representation():
    fn = _make_fn()
    result = compare_prompts(fn, {"v": "p"})
    text = str(result)
    assert "winner:" in text
    assert "v:" in text
