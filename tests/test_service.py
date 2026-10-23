"""Whole pipeline against an in-memory GitHub: PR opened -> comments appear."""

import pytest

from app.config import Settings
from app.devtools.fake_github import FakeGitHub
from app.fixer import apply_replacement
from app.publisher import SUMMARY_MARKER
from app.sandbox import LocalSandbox
from app.service import ReviewService
from app.store import Store
from tests.fakes import FakeLLM
from tests.test_analysis import REPO

GOOD = {**REPO, "shop/cart.py": REPO["shop/cart.py"].replace("aply_discount", "apply_discount")}
# GOOD's test_total passes; the buggy head has a typo on line 9.
FIX = "return apply_discount(raw, discount)"


def claim():
    return {
        "title": "Typo in function name",
        "category": "bug",
        "severity": "high",
        "path": "shop/cart.py",
        "line": 9,
        "evidence_quote": "return aply_discount(raw, discount)",
        "explanation": "`aply_discount` is undefined; the callee is `apply_discount`.",
        "confidence": 0.95,
        "certainty": "confirmed",
        "suggested_fix": FIX,
    }


def service(tmp_path, fake, llm, **settings):
    s = Settings(
        sandbox="local", allow_unsafe_local_sandbox=True, data_dir=str(tmp_path), **settings
    )
    return ReviewService(
        s, github_factory=fake.factory(), llm=llm, sandbox=LocalSandbox(), store=Store(tmp_path)
    )


@pytest.fixture
def fake():
    return FakeGitHub(GOOD, REPO)


def test_bug_is_found_fixed_validated_and_posted(tmp_path, fake):
    llm = FakeLLM({"summary": "typo", "findings": [claim()]})
    out = service(tmp_path, fake, llm).run(fake.event())

    assert out.status == "reviewed"
    f = out.review.findings[0]
    assert f.status == "confirmed" and f.line == 9
    fix = out.fixes.fixes[f.id]
    assert fix.status == "verified"
    assert "apply_discount" in fix.new_text

    # an inline comment with a one-click suggestion, on the right line
    assert len(fake.review_comments) == 1
    c = fake.review_comments[0]
    assert (c["path"], c["line"]) == ("shop/cart.py", 9)
    assert "```suggestion" in c["body"] and "apply_discount" in c["body"]
    # and exactly one summary comment
    assert len(fake.issue_comments) == 1 and SUMMARY_MARKER in fake.issue_comments[0]["body"]
    assert service(tmp_path, fake, FakeLLM()).store.recent()[0]["status"] == "reviewed"


def test_rerun_does_not_duplicate_comments(tmp_path, fake):
    for _ in range(2):
        service(tmp_path, fake, FakeLLM({"summary": "", "findings": [claim()]})).run(fake.event())
    assert len(fake.review_comments) == 1
    assert len(fake.issue_comments) == 1  # summary was updated in place, not re-posted


def test_stale_head_posts_nothing(tmp_path, fake):
    fake.current_head_sha = "c" * 40
    out = service(tmp_path, fake, FakeLLM()).run(fake.event())
    assert out.status == "stale"
    assert not fake.review_comments and not fake.issue_comments


def test_works_without_llm_using_tools_only(tmp_path, fake):
    out = service(tmp_path, fake, None).run(fake.event())
    assert out.status == "reviewed"
    assert any(f.source == "tool" for f in out.review.findings)
    assert any("no API key" in n for n in out.review.notes)


def test_clean_pr_gets_a_summary_not_comments(tmp_path):
    fake = FakeGitHub(
        GOOD,
        {
            **GOOD,
            "shop/cart.py": GOOD["shop/cart.py"]
            + "\n    def empty(self):\n        return not self.items\n",
        },
    )
    out = service(tmp_path, fake, FakeLLM()).run(fake.event())
    assert out.status == "reviewed" and not fake.review_comments
    assert len(fake.issue_comments) == 1


def test_dry_run_posts_nothing(tmp_path, fake):
    out = service(
        tmp_path, fake, FakeLLM({"summary": "", "findings": [claim()]}), dry_run=True
    ).run(fake.event())
    assert out.markdown and not fake.review_comments and not fake.issue_comments


def test_unexpected_failure_is_reported_not_raised(tmp_path, fake):
    fake.base_files = None  # type: ignore[assignment]
    fake.head_files = None  # type: ignore[assignment]
    out = service(tmp_path, fake, None).run(fake.event())
    assert out.status == "error" and out.error


def test_apply_replacement_reindents_and_bounds():
    src = "def f():\n    x = 1\n    return x\n"
    patched, text = apply_replacement(src, 2, 2, "y = 2")
    assert patched == "def f():\n    y = 2\n    return x\n" and text == "    y = 2"
    with pytest.raises(ValueError):
        apply_replacement(src, 2, 9, "z")
