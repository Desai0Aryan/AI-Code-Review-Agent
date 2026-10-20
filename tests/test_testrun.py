import pytest

from app.sandbox import LocalSandbox
from app.testrun import failures_to_findings, parse_junit, run_tests

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="1" failures="1" skipped="1" tests="5" time="0.12">
<testcase classname="tests.test_cart" name="test_ok" time="0.01"/>
<testcase classname="tests.test_cart" name="test_total" time="0.01">
<failure message="NameError: name 'aply_discount' is not defined">tests/test_cart.py:6: in test_total
    assert total([]) == 0
shop/cart.py:5: in total
    return aply_discount(raw, discount)
E   NameError: name 'aply_discount' is not defined</failure></testcase>
<testcase classname="tests.test_cart" name="test_err" time="0.01">
<error message="collection error">/work/tests/test_cart.py:1: in &lt;module&gt;
    import nothing
E   ImportError</error></testcase>
<testcase classname="tests.test_cart" name="test_skip" time="0.0"><skipped message="x"/></testcase>
<testcase classname="tests.test_cart" name="test_ok2" time="0.01"/>
</testsuite></testsuites>"""

BILLION_LAUGHS = """<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>
<testsuite tests="1"><testcase classname="a" name="b">&lol2;</testcase></testsuite>"""


def test_parse_junit_counts_and_failures():
    run = parse_junit(JUNIT)
    assert (run.tests, run.passed, run.failed, run.errors, run.skipped) == (5, 2, 1, 1, 1)
    assert [(f.nodeid, f.kind) for f in run.failures] == [
        ("tests.test_cart::test_total", "failure"),
        ("tests.test_cart::test_err", "error"),
    ]
    assert not run.ok


def test_failures_point_at_innermost_non_test_frame():
    findings = failures_to_findings(parse_junit(JUNIT), ("/work/",))
    total = findings[0]
    assert (total.path, total.line, total.rule) == ("shop/cart.py", 5, "test-failure")
    assert "NameError" in total.message and "NameError" in total.evidence
    # a failure with only test-file frames falls back to the test location
    assert (findings[1].path, findings[1].line, findings[1].rule) == (
        "tests/test_cart.py", 1, "test-error",
    )  # fmt: skip


def test_hostile_xml_entities_are_rejected():
    with pytest.raises(Exception, match="(?i)entit"):
        parse_junit(BILLION_LAUGHS)


def write(tmp_path, files):
    for rel, text in files.items():
        f = tmp_path / "work" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    (tmp_path / "deps").mkdir()
    (tmp_path / "out").mkdir()
    return tmp_path / "work", tmp_path / "deps", tmp_path / "out"


def test_real_pytest_run_reports_pass_and_fail(tmp_path):
    work, deps, out = write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/calc.py": "def add(a, b):\n    return a - b\n",  # the seeded bug
            "tests/test_calc.py": (
                "from pkg.calc import add\n"
                "def test_zero():\n    assert add(0, 0) == 0\n"
                "def test_add():\n    assert add(2, 3) == 5\n"
            ),
        },
    )
    run = run_tests(LocalSandbox(), work, deps, out, ["tests/test_calc.py"], 60)
    assert (run.tests, run.passed, run.failed) == (2, 1, 1)
    finding = failures_to_findings(run, (f"{work}/",))[0]
    # A wrong return value fails at the assertion in the test: no traceback frame in calc.py,
    # so the finding honestly points at the test line rather than guessing the source line.
    assert (finding.path, finding.line) == ("tests/test_calc.py", 5)
    assert "assert -1 == 5" in finding.evidence


def test_collection_error_is_an_error_not_a_crash(tmp_path):
    work, deps, out = write(tmp_path, {"tests/test_x.py": "import does_not_exist\n"})
    run = run_tests(LocalSandbox(), work, deps, out, ["tests/test_x.py"], 60)
    assert run.errors == 1 and not run.ok


def test_timeout_and_missing_files(tmp_path):
    work, deps, out = write(
        tmp_path, {"tests/test_slow.py": "import time\ndef test_slow():\n    time.sleep(30)\n"}
    )
    slow = run_tests(LocalSandbox(), work, deps, out, ["tests/test_slow.py"], 2)
    assert slow.timed_out and not slow.ok
    none = run_tests(LocalSandbox(), work, deps, out, ["tests/test_missing.py"], 5)
    assert none.tests == 0 and none.ran == []


def test_tests_cannot_see_host_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("CRA_GITHUB_PRIVATE_KEY", "super-secret")
    work, deps, out = write(
        tmp_path,
        {
            "tests/test_env.py": (
                "import os\n"
                "def test_no_secret():\n"
                "    assert 'CRA_GITHUB_PRIVATE_KEY' not in os.environ\n"
            )
        },
    )
    run = run_tests(LocalSandbox(), work, deps, out, ["tests/test_env.py"], 60)
    assert run.ok and run.passed == 1
