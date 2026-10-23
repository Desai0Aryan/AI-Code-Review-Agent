from app.config import Settings
from benchmark.corpus import CASES
from benchmark.runner import render_report, run_benchmark, summarize


def test_corpus_is_well_formed():
    assert len({c.id for c in CASES}) == len(CASES)
    for c in CASES:
        c.head_files()  # `old` occurs exactly once
        assert (c.bug_lines() is None) == (not c.buggy)
    assert sum(c.buggy for c in CASES) >= 12 and sum(not c.buggy for c in CASES) >= 5


def test_tools_only_run_scores_a_bug_and_a_clean_pr():
    results, summary = run_benchmark(
        use_llm=False, only=["off-by-one-range", "clean-rename"], settings=Settings(sandbox="local")
    )
    assert summary["bugs_caught"] == 1 and summary["false_alarms"] == 0
    assert summary["recall"] == 1.0 and summary["clean_pr_false_alarm_rate"] == 0.0
    assert "off-by-one-range" in render_report(results, summary)


def test_summary_handles_empty_denominators():
    assert summarize([])["recall"] is None
