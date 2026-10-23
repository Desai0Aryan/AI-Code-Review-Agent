"""End to end: a PR with a seeded bug goes through context -> sandbox -> normalized findings."""

from app.analysis import run_analysis, select_test_files
from app.changes import ChangedUnit, ChangeSet
from app.context import build_unit_context
from app.index import RepoIndex
from app.sandbox import LocalSandbox
from tests.helpers import make_tarball

REPO = {
    "shop/__init__.py": "",
    "shop/pricing.py": (
        "def apply_discount(amount, discount):\n"  # 1
        "    return amount - discount\n"  # 2
    ),
    "shop/cart.py": (
        "from shop.pricing import apply_discount\n"  # 1
        "\n"  # 2
        "class Cart:\n"  # 3
        "    def __init__(self):\n"  # 4
        "        self.items = []\n"  # 5
        "\n"  # 6
        "    def total(self, discount=0):\n"  # 7
        "        raw = sum(self.items)\n"  # 8
        "        return aply_discount(raw, discount)\n"  # 9  <- the seeded bug (typo)
        "\n"  # 10
        "    def count(self):\n"  # 11
        "        return len(self.items)\n"  # 12
    ),
    "tests/test_cart.py": (
        "from shop.cart import Cart\n"
        "\n"
        "def test_count():\n"
        "    assert Cart().count() == 0\n"
        "\n"
        "def test_total():\n"
        "    assert Cart().total() == 0\n"
    ),
}


def changeset_for_total(repo=None):
    index = RepoIndex(repo or REPO)
    unit = next(u for u in index.files["shop/cart.py"].units if u.qualified_name == "Cart.total")
    cu = ChangedUnit("shop/cart.py", unit, changed_lines=[8, 9])
    cs = ChangeSet(changed_units=[cu], parsed={"shop/cart.py": index.files["shop/cart.py"]})
    ctx = {cu.key: build_unit_context(index, cu)}
    return cs, ctx


def test_seeded_bug_is_found_by_lint_and_by_failing_test():
    cs, contexts = changeset_for_total()
    report = run_analysis(make_tarball(REPO), cs, contexts, LocalSandbox(), analysis_timeout=60)

    assert report.tests is not None
    assert (report.tests.tests, report.tests.passed, report.tests.failed) == (2, 1, 1)

    top = report.relevant
    by_tool = {f.tool: f for f in top if f.rule in ("F821", "test-failure", "name-defined")}
    assert {"ruff", "pytest"} <= set(by_tool)

    for f in (by_tool["ruff"], by_tool["pytest"]):
        assert (f.path, f.line) == ("shop/cart.py", 9)
        assert f.scope == "changed-line" and f.unit_key == "shop/cart.py::Cart.total"
    assert "aply_discount" in by_tool["pytest"].evidence  # traceback proves it at runtime
    assert "9:         return aply_discount(raw, discount)" in by_tool["ruff"].evidence
    assert report.notes == []


def test_findings_in_untouched_code_are_kept_but_not_relevant():
    repo = dict(REPO)
    repo["shop/pricing.py"] = "def old_bug(x):\n    return missing_name\n"  # pre-existing problem
    cs, contexts = changeset_for_total()
    cs.parsed["shop/pricing.py"] = RepoIndex(repo).files["shop/pricing.py"]
    report = run_analysis(make_tarball(repo), cs, contexts, LocalSandbox(), analysis_timeout=60)

    pre_existing = [f for f in report.findings if f.path == "shop/pricing.py"]
    assert pre_existing and all(f.scope == "other" for f in pre_existing)
    assert all(f.path != "shop/pricing.py" for f in report.relevant)


def test_no_related_tests_is_called_out():
    index = RepoIndex({"a.py": "def f():\n    return nope\n"})
    cu = ChangedUnit("a.py", index.files["a.py"].units[0], changed_lines=[2])
    cs = ChangeSet(changed_units=[cu], parsed=index.files)
    report = run_analysis(
        make_tarball({"a.py": "def f():\n    return nope\n"}), cs, {}, LocalSandbox()
    )
    assert report.tests is None
    assert "no related tests were found to run" in report.notes
    assert any(f.rule == "F821" and f.scope == "changed-line" for f in report.relevant)


def test_oversized_repo_is_skipped_with_a_note(monkeypatch):
    from app import analysis

    def too_big(archive, dest):
        raise ValueError("repository exceeds workspace size limits")

    monkeypatch.setattr(analysis, "extract_workspace", too_big)
    cs, contexts = changeset_for_total()
    report = run_analysis(make_tarball(REPO), cs, contexts, LocalSandbox())
    assert report.findings == [] and "size limits" in report.notes[0]


def test_select_test_files_uses_context_and_changed_tests():
    cs, contexts = changeset_for_total()
    assert select_test_files(cs, contexts) == ["tests/test_cart.py"]
    cs.parsed["tests/test_other.py"] = RepoIndex({"tests/test_other.py": "x = 1\n"}).files[
        "tests/test_other.py"
    ]
    assert select_test_files(cs, contexts) == ["tests/test_cart.py", "tests/test_other.py"]


def test_empty_changeset_does_nothing():
    report = run_analysis(make_tarball(REPO), ChangeSet(), {}, LocalSandbox())
    assert report.findings == [] and report.tests is None


def test_failing_assertion_in_related_test_is_relevant():
    repo = dict(REPO)
    repo["shop/cart.py"] = (
        "class Cart:\n"  # 1
        "    def __init__(self):\n"  # 2
        "        self.items = []\n"  # 3
        "\n"  # 4
        "    def count(self):\n"  # 5
        "        return len(self.items) + 1\n"  # 6  <- wrong value, raises nothing
    )
    index = RepoIndex(repo)
    unit = next(u for u in index.files["shop/cart.py"].units if u.name == "count")
    cu = ChangedUnit("shop/cart.py", unit, changed_lines=[6])
    cs = ChangeSet(changed_units=[cu], parsed={"shop/cart.py": index.files["shop/cart.py"]})
    contexts = {cu.key: build_unit_context(index, cu)}
    report = run_analysis(make_tarball(repo), cs, contexts, LocalSandbox(), analysis_timeout=60)

    failing = [f for f in report.relevant if f.tool == "pytest" and "test_count" in f.message]
    assert len(failing) == 1
    assert failing[0].scope == "related-test"
    assert failing[0].unit_key == "shop/cart.py::Cart.count"
    assert failing[0].path == "tests/test_cart.py"
