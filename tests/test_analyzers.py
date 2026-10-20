import json

import pytest

from app.analyzers import (
    parse_bandit,
    parse_mypy,
    parse_ruff,
    relative_path,
    run_bandit,
    run_mypy,
    run_ruff,
)
from app.sandbox import LocalSandbox

ROOTS = ("/work/",)

PROJECT = {
    "shop/__init__.py": "",
    "shop/cart.py": (
        "from shop.pricing import apply_discount\n"
        "\n"
        "def total(items, discount=0):\n"
        "    raw = sum(items)\n"
        "    return aply_discount(raw, discount)\n"  # undefined name (typo)
    ),
    "shop/pricing.py": (
        "def apply_discount(amount: int, discount: int) -> int:\n"
        "    return amount - discount\n"
        "\n"
        "def label() -> int:\n"
        "    return 'not an int'\n"  # type error
    ),
    "shop/util.py": (
        "import subprocess\n"
        "\n"
        "def run(cmd):\n"
        "    return subprocess.call(cmd, shell=True)\n"  # command injection risk
    ),
}


@pytest.fixture
def project(tmp_path):
    for rel, text in PROJECT.items():
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    return tmp_path


def test_relative_path():
    assert relative_path("/work/shop/a.py", ROOTS) == "shop/a.py"
    assert relative_path("./shop/a.py", ROOTS) == "shop/a.py"
    assert relative_path("shop/a.py", ROOTS) == "shop/a.py"


def test_parse_ruff():
    out = json.dumps(
        [
            {"code": "F821", "message": "Undefined name `x`", "filename": "/work/a.py",
             "location": {"row": 5, "column": 1}, "end_location": {"row": 5, "column": 2}},
            {"code": "F401", "message": "unused import", "filename": "/work/a.py",
             "location": {"row": 1, "column": 1}, "end_location": {"row": 1, "column": 9}},
            {"code": "B006", "message": "mutable default", "filename": "/work/a.py",
             "location": {"row": 3, "column": 1}, "end_location": {"row": 3, "column": 9}},
            {"code": None, "message": "SyntaxError: bad", "filename": "/work/a.py",
             "location": {"row": 9, "column": 1}, "end_location": None},
        ]
    )  # fmt: skip
    f = parse_ruff(out, ROOTS)
    assert [(x.rule, x.severity, x.path, x.line) for x in f] == [
        ("F821", "high", "a.py", 5),
        ("F401", "low", "a.py", 1),
        ("B006", "medium", "a.py", 3),
        ("syntax-error", "high", "a.py", 9),
    ]
    assert parse_ruff("", ROOTS) == []


def test_parse_bandit():
    out = json.dumps(
        {
            "results": [
                {"test_id": "B602", "issue_severity": "HIGH", "issue_confidence": "HIGH",
                 "issue_text": "shell=True", "filename": "./shop/util.py", "line_number": 4,
                 "line_range": [4]},
                {"test_id": "B311", "issue_severity": "LOW", "issue_confidence": "MEDIUM",
                 "issue_text": "random", "filename": "./a.py", "line_number": 2},
            ]
        }
    )  # fmt: skip
    f = parse_bandit(out, ROOTS)
    assert (f[0].rule, f[0].severity, f[0].confidence, f[0].category) == ("B602", "high", 0.9, "security")
    assert (f[1].severity, f[1].confidence, f[1].path) == ("low", 0.6, "a.py")


def test_parse_mypy_ignores_notes_and_handles_missing_code():
    out = (
        "shop/pricing.py:5: error: Incompatible return value type  [return-value]\n"
        "shop/pricing.py:5: note: something helpful\n"
        "shop/cart.py:3:9: error: Name 'zz' is not defined  [name-defined]\n"
        "shop/x.py:1: error: weird message without a code\n"
        "Found 2 errors in 2 files\n"
    )
    f = parse_mypy(out, ROOTS)
    assert [(x.rule, x.severity, x.line) for x in f] == [
        ("return-value", "medium", 5),
        ("name-defined", "high", 3),
        ("mypy", "medium", 1),
    ]


def test_ruff_finds_undefined_name_for_real(project):
    res = run_ruff(LocalSandbox(), project, ["shop/cart.py"], 60, (f"{project}/",))
    assert res.error is None
    found = {(f.rule, f.path, f.line) for f in res.findings}
    assert ("F821", "shop/cart.py", 5) in found  # the typo'd name
    assert ("F401", "shop/cart.py", 1) in found  # ...and the import it should have used


def test_bandit_finds_shell_injection_for_real(project):
    res = run_bandit(LocalSandbox(), project, ["shop/util.py"], 60, (f"{project}/",))
    assert res.error is None
    found = {(f.rule, f.severity, f.line) for f in res.findings}
    assert ("B602", "high", 4) in found


def test_mypy_finds_type_error_across_files_for_real(project):
    res = run_mypy(LocalSandbox(), project, ["shop/cart.py", "shop/pricing.py"], 120, (f"{project}/",))
    assert res.error is None, res.error
    rules = {(f.path, f.rule, f.line) for f in res.findings}
    assert ("shop/pricing.py", "return-value", 5) in rules
    assert ("shop/cart.py", "name-defined", 5) in rules


def test_tool_failure_is_reported_not_raised(project):
    res = run_ruff(LocalSandbox(), project, ["--definitely-not-a-flag"], 60, ROOTS)
    assert res.findings == [] and res.error and "ruff" in res.error
