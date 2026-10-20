from app.changes import ChangedUnit
from app.context import build_unit_context
from app.github_client import CommitInfo
from app.index import RepoIndex
from tests.helpers import FAKE_REPO


def changed(index: RepoIndex, path: str, qualified: str) -> ChangedUnit:
    unit = next(u for u in index.files[path].units if u.qualified_name == qualified)
    return ChangedUnit(path=path, unit=unit, changed_lines=[unit.start_line + 1])


COMMITS = [CommitInfo("a1b2c3d4e5", "Fix rounding in totals", "Dana", "2026-09-30T10:00:00Z")]


def test_callers_exclude_tests_and_carry_call_lines():
    index = RepoIndex(FAKE_REPO)
    ctx = build_unit_context(index, changed(index, "shop/cart.py", "Cart.total"))
    assert [(r.key, r.lines) for r in ctx.callers] == [("shop/checkout.py::checkout", [4])]
    assert not ctx.callers[0].ambiguous


def test_callees_resolve_across_files():
    index = RepoIndex(FAKE_REPO)
    ctx = build_unit_context(index, changed(index, "shop/cart.py", "Cart.total"))
    assert [r.key for r in ctx.callees] == ["shop/pricing.py::apply_discount"]
    assert not ctx.callees[0].ambiguous  # 'sum' is not defined in the repo, so it is ignored


def test_related_tests_by_call_and_by_convention():
    index = RepoIndex(FAKE_REPO)
    ctx = build_unit_context(index, changed(index, "shop/cart.py", "Cart.total"))
    by_name = {t.name: t.reason for t in ctx.tests}
    assert by_name["test_total_empty"] == "calls total()"
    assert by_name["test_other"] == "test module for cart.py"
    assert not any("No tests" in n for n in ctx.notes)


def test_missing_tests_are_called_out():
    index = RepoIndex(FAKE_REPO)
    ctx = build_unit_context(index, changed(index, "shop/checkout.py", "checkout"))
    assert ctx.tests == []
    assert any("No tests reference 'checkout'" in n for n in ctx.notes)
    # checkout has no callers in this repo either
    assert any("No callers found" in n for n in ctx.notes)


def test_constructor_callers_use_class_name():
    index = RepoIndex(FAKE_REPO)
    ctx = build_unit_context(index, changed(index, "shop/cart.py", "Cart.__init__"))
    assert any(t.name == "test_total_empty" and t.reason == "calls Cart()" for t in ctx.tests)


def test_ambiguous_names_are_flagged_not_guessed():
    repo = {**FAKE_REPO, "shop/report.py": "def total(rows):\n    return len(rows)\n"}
    index = RepoIndex(repo)
    ctx = build_unit_context(index, changed(index, "shop/cart.py", "Cart.total"))
    assert ctx.callers and all(r.ambiguous for r in ctx.callers)
    assert any("defined 2 times" in n for n in ctx.notes)


def test_same_file_definition_wins_for_callees():
    repo = {
        "a.py": "def helper():\n    return 1\n\ndef main():\n    return helper()\n",
        "b.py": "def helper():\n    return 2\n",
    }
    index = RepoIndex(repo)
    ctx = build_unit_context(index, changed(index, "a.py", "main"))
    assert [(r.key, r.ambiguous) for r in ctx.callees] == [("a.py::helper", False)]


def test_unresolved_callee_in_other_file_is_ambiguous_when_multiple():
    repo = {
        "a.py": "def main():\n    return helper()\n",
        "b.py": "def helper():\n    return 1\n",
        "c.py": "def helper():\n    return 2\n",
    }
    index = RepoIndex(repo)
    ctx = build_unit_context(index, changed(index, "a.py", "main"))
    assert len(ctx.callees) == 1 and ctx.callees[0].ambiguous


def test_to_text_is_prompt_ready():
    index = RepoIndex(FAKE_REPO)
    ctx = build_unit_context(index, changed(index, "shop/cart.py", "Cart.total"), COMMITS)
    text = ctx.to_text()
    assert "## Context for shop/cart.py::Cart.total" in text
    assert "shop/checkout.py::checkout (lines 4)" in text
    assert "apply_discount" in text
    assert "a1b2c3d Fix rounding in totals (Dana, 2026-09-30)" in text


def test_caller_cap_is_reported():
    callers = "".join(f"def c{i}():\n    return target()\n\n" for i in range(14))
    index = RepoIndex({"t.py": "def target():\n    return 1\n", "c.py": callers})
    ctx = build_unit_context(index, changed(index, "t.py", "target"))
    assert len(ctx.callers) == 10
    assert any("4 more callers omitted" in n for n in ctx.notes)
