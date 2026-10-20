"""Run pytest in the sandbox and turn failures into line-specific findings."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from defusedxml import DefusedXmlException, ElementTree

from app.analyzers import relative_path
from app.findings import Finding
from app.index import is_test_path
from app.sandbox import Sandbox

MAX_JUNIT_BYTES = 5_000_000
FRAME_RE = re.compile(r"^(?P<path>[^\s:]+\.py):(?P<line>\d+): ", re.MULTILINE)


@dataclass
class TestFailure:
    __test__ = False  # not a pytest test class

    nodeid: str
    kind: str  # failure | error
    message: str
    text: str  # full traceback text from the junit report


@dataclass
class TestRun:
    __test__ = False

    ran: list[str] = field(default_factory=list)
    tests: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration: float = 0.0
    timed_out: bool = False
    crashed: bool = False  # pytest produced no usable report (collection crash, OOM, ...)
    failures: list[TestFailure] = field(default_factory=list)
    output_tail: str = ""

    @property
    def ok(self) -> bool:
        return not (self.timed_out or self.crashed or self.failed or self.errors)


def parse_junit(xml_text: str) -> TestRun:
    root = ElementTree.fromstring(xml_text)
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    run = TestRun()
    for suite in suites:
        run.tests += int(suite.get("tests", 0))
        run.failed += int(suite.get("failures", 0))
        run.errors += int(suite.get("errors", 0))
        run.skipped += int(suite.get("skipped", 0))
        run.duration += float(suite.get("time", 0))
        for case in suite.iter("testcase"):
            failure, error = case.find("failure"), case.find("error")
            node = failure if failure is not None else error
            if node is None:
                continue
            run.failures.append(
                TestFailure(
                    nodeid=f"{case.get('classname', '')}::{case.get('name', '')}",
                    kind="failure" if failure is not None else "error",
                    message=(node.get("message") or "").strip(),
                    text=node.text or "",
                )
            )
    run.passed = max(0, run.tests - run.failed - run.errors - run.skipped)
    return run


def run_tests(
    sandbox: Sandbox, work: Path, deps: Path, out: Path, test_paths: list[str], timeout: int
) -> TestRun:
    paths = [p for p in test_paths if (work / p).is_file()]
    if not paths:
        return TestRun()

    cmd = [
        "python", "-m", "pytest", "-p", "no:cacheprovider", "-q", "--tb=short",
        "-o", "addopts=", "--rootdir={work}", "--junitxml={out}/junit.xml", *paths,
    ]  # fmt: skip
    res = sandbox.run(
        cmd, work=work, deps=deps, out=out, timeout=timeout,
        env={"PYTHONPATH": "{deps}:{work}"},
    )  # fmt: skip
    tail = (res.stdout + "\n" + res.stderr).strip()[-1500:]
    xml_path = out / "junit.xml"

    if res.timed_out:
        return TestRun(ran=paths, timed_out=True, output_tail=tail)
    if not xml_path.is_file() or xml_path.stat().st_size > MAX_JUNIT_BYTES:
        return TestRun(ran=paths, crashed=True, output_tail=tail)
    try:
        run = parse_junit(xml_path.read_text(encoding="utf-8", errors="replace"))
    except (ElementTree.ParseError, DefusedXmlException, ValueError, TypeError):
        # malformed or hostile XML: treat as a crash, never trust it
        return TestRun(ran=paths, crashed=True, output_tail=tail)
    run.ran = paths
    run.output_tail = tail
    return run


def failures_to_findings(run: TestRun, roots: tuple[str, ...]) -> list[Finding]:
    """One finding per failing test, located at the innermost frame in non-test code."""
    findings = []
    for fail in run.failures:
        frames = [
            (relative_path(m["path"], roots), int(m["line"])) for m in FRAME_RE.finditer(fail.text)
        ]
        source_frames = [f for f in frames if not is_test_path(f[0])]
        path, line = (source_frames or frames or [("", 0)])[-1]
        excerpt = "\n".join(fail.text.strip().splitlines()[-12:])
        findings.append(
            Finding(
                tool="pytest",
                rule="test-error" if fail.kind == "error" else "test-failure",
                severity="high",
                confidence=0.95,
                category="test-failure",
                path=path,
                line=line,
                message=f"{fail.nodeid}: {fail.message.splitlines()[0] if fail.message else fail.kind}",
                evidence=excerpt,
            )
        )
    return findings
