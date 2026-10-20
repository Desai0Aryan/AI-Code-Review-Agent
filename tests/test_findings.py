from app.changes import ChangedUnit, ChangeSet
from app.findings import Finding, attach_evidence, classify_scope, relevant
from app.index import RepoIndex

SRC = (
    "import os\n"  # 1
    "\n"  # 2
    "def a():\n"  # 3
    "    x = 1\n"  # 4
    "    return y\n"  # 5
    "\n"  # 6
    "def b():\n"  # 7
    "    return 2\n"  # 8
)


def make_changeset():
    parsed = RepoIndex({"m.py": SRC}).files
    unit_a = next(u for u in parsed["m.py"].units if u.name == "a")
    return ChangeSet(
        changed_units=[ChangedUnit("m.py", unit_a, changed_lines=[5])],
        module_level={"m.py": [1]},
        parsed=parsed,
    )


def finding(line, sev="medium", conf=0.9, path="m.py", **kw):
    return Finding("ruff", "F821", sev, conf, "bug", path, line, "msg", **kw)


def test_scope_classification():
    cs = make_changeset()
    fs = [finding(5), finding(4), finding(8), finding(1), finding(5, path="other.py")]
    classify_scope(fs, cs)
    assert [f.scope for f in fs] == [
        "changed-line",  # the edited line itself
        "changed-function",  # same function, line not edited
        "other",  # untouched function
        "changed-line",  # module-level edit
        "other",  # different file
    ]
    assert fs[0].unit_key == "m.py::a" and fs[1].unit_key == "m.py::a"


def test_evidence_excerpt_and_does_not_overwrite():
    cs = make_changeset()
    fs = [finding(5), finding(4, evidence="traceback here"), finding(99)]
    attach_evidence(fs, cs.parsed)
    assert fs[0].evidence == "4:     x = 1\n5:     return y\n6: "
    assert fs[1].evidence == "traceback here"
    assert fs[2].evidence == ""


def test_relevant_filters_and_orders():
    fs = [
        finding(1, sev="low", scope="changed-line"),
        finding(2, sev="high", conf=0.5, scope="changed-line"),
        finding(3, sev="high", conf=0.9, scope="changed-function"),
        finding(4, sev="high", scope="other"),
    ]
    assert [f.line for f in relevant(fs)] == [3, 2, 1]
