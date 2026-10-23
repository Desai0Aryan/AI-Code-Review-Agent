from app.render import render_markdown
from app.review_models import RejectedFinding, ReviewFinding, ReviewResult, TraceStep


def finding(status, title, **kw) -> ReviewFinding:
    base = {
        "id": title, "source": "llm", "title": title, "category": "bug", "severity": "high",
        "path": "shop/cart.py", "line": 9, "end_line": None, "evidence": "9: return x",
        "explanation": "because", "confidence": 0.9, "status": status, "scope": "changed-line",
    }  # fmt: skip
    return ReviewFinding(**{**base, **kw})


def test_markdown_groups_by_status_and_shows_evidence():
    result = ReviewResult(
        findings=[
            finding("confirmed", "Typo", corroborated_by=["ruff:F821"], suggested_fix="return y",
                    trace=[TraceStep("shop/cart.py", 8, "raw computed")]),
            finding("likely", "Edge case"),
            finding("uncertain", "Maybe", uncertainty_reason="cannot see callers"),
        ],
        rejected=[RejectedFinding("u", "x", "a.py", 1, "not in code")],
        notes=["analysis: mypy timed out"],
        units_reviewed=2,
    )  # fmt: skip
    md = render_markdown(result)
    assert (
        md.index("### Confirmed")
        < md.index("### Likely issues")
        < md.index("### Needs a human check")
    )
    assert "1 confirmed, 1 likely, 1 need a human check" in md
    assert "```\n9: return x\n```" in md
    assert "_Evidence:_ ruff:F821" in md and "_Why unsure:_ cannot see callers" in md
    assert "Suggested fix (not tested)" in md
    assert "- `shop/cart.py:8` raw computed" in md
    assert "1 model claim(s) were discarded" in md
    assert "_Note: analysis: mypy timed out_" in md


def test_clean_review_says_so_without_inventing_problems():
    md = render_markdown(ReviewResult(units_reviewed=3))
    assert "found nothing I can support with evidence" in md
    assert "###" not in md
