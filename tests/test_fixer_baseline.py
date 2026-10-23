from app.analysis import run_analysis
from app.baseline import mark_pre_existing
from app.fixer import validate_patch
from app.reviewer import review_changes
from app.sandbox import LocalSandbox
from tests.fakes import FakeLLM
from tests.helpers import make_tarball
from tests.test_analysis import REPO, changeset_for_total
from tests.test_reviewer import llm_claim


def _reviewed(repo=REPO):
    cs, ctx = changeset_for_total(repo)
    archive = make_tarball(repo)
    report = run_analysis(archive, cs, ctx, LocalSandbox(), analysis_timeout=60)
    return archive, cs, ctx, report


def test_failing_test_on_base_is_marked_pre_existing_and_not_confirmed():
    archive, cs, ctx, report = _reviewed()
    # base == head here, so the failure exists before the PR too
    assert mark_pre_existing(report, archive, LocalSandbox()) >= 1
    review = review_changes(cs, ctx, report, {}, None)
    # ruff still confirms the bug, but a test that already failed on base is not cited as proof
    assert review.findings[0].status == "confirmed"
    assert not any("pytest" in t for f in review.findings for t in f.corroborated_by)
    assert all(f.pre_existing for f in report.findings if f.tool == "pytest")


def test_bad_fix_is_rejected_and_good_fix_verified():
    archive, cs, ctx, report = _reviewed()
    review = review_changes(
        cs, ctx, report, cs.parsed, FakeLLM({"summary": "", "findings": [llm_claim()]})
    )
    f = review.findings[0]
    src = cs.parsed["shop/cart.py"].source
    bad = src.replace("aply_discount(raw, discount)", "aply_discount(raw, discount) +")
    assert validate_patch(archive, "shop/cart.py", bad, f, report, LocalSandbox())[0] == "failed"
    good = src.replace("aply_discount", "apply_discount")
    assert validate_patch(archive, "shop/cart.py", good, f, report, LocalSandbox())[0] == "verified"
