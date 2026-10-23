"""Orchestrates sandboxed analysis for a PR: lint/security/type checks plus related tests."""

import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.analyzers import run_bandit, run_mypy, run_ruff
from app.changes import ChangeSet
from app.context import UnitContext
from app.findings import Finding, attach_evidence, classify_scope, relevant
from app.index import is_test_path
from app.sandbox import Sandbox
from app.testrun import TestRun, failures_to_findings, run_tests
from app.workspace import extract_workspace

logger = logging.getLogger("code_review_agent")

MAX_TEST_FILES = 20


@dataclass
class AnalysisReport:
    findings: list[Finding] = field(default_factory=list)
    tests: TestRun | None = None
    notes: list[str] = field(default_factory=list)  # tool failures and other caveats

    @property
    def relevant(self) -> list[Finding]:
        return relevant(self.findings)


def select_test_files(changeset: ChangeSet, contexts: dict[str, UnitContext]) -> list[str]:
    paths = {t.path for ctx in contexts.values() for t in ctx.tests}
    paths |= {p for p in changeset.parsed if is_test_path(p)}
    return sorted(paths)[:MAX_TEST_FILES]


def install_dependencies(sandbox: Sandbox, work: Path, deps: Path, timeout: int) -> str | None:
    """Install requirements.txt wheels. Wheels only: no sdist build scripts ever run, and the
    PR's own code is not executed in this network-enabled step."""
    if not (work / "requirements.txt").is_file():
        return None
    res = sandbox.run(
        [
            "python",
            "-m",
            "pip",
            "install",
            "--only-binary=:all:",
            "--no-input",
            "--disable-pip-version-check",
            "--quiet",
            "--target",
            "{deps}",
            "-r",
            "{work}/requirements.txt",
        ],
        work=work,
        deps=deps,
        deps_writable=True,
        network=True,
        timeout=timeout,
    )
    if res.timed_out or res.returncode != 0:
        return f"dependency install failed; test results may be unreliable: {res.stderr.strip()[-200:]}"
    return None


def run_analysis(
    archive: bytes,
    changeset: ChangeSet,
    contexts: dict[str, UnitContext],
    sandbox: Sandbox,
    *,
    analysis_timeout: int = 120,
    test_timeout: int = 180,
) -> AnalysisReport:
    report = AnalysisReport()
    changed_files = sorted(changeset.parsed)
    if not changed_files:
        return report

    with tempfile.TemporaryDirectory(prefix="cra-") as tmp:
        base = Path(tmp)
        work, deps, out = base / "work", base / "deps", base / "out"
        for d in (deps, out):
            d.mkdir()
            os.chmod(d, 0o777)  # the sandbox user is unprivileged and must write here
        try:
            extract_workspace(archive, work)
        except ValueError as exc:
            report.notes.append(f"analysis skipped: {exc}")
            return report

        roots = ("/work/", f"{work.resolve()}/")
        non_test = [p for p in changed_files if not is_test_path(p)]

        for name, runner, files in (
            ("ruff", run_ruff, changed_files),
            ("bandit", run_bandit, non_test),
            ("mypy", run_mypy, changed_files),
        ):
            if not files:
                continue
            result = runner(sandbox, work, files, analysis_timeout, roots)
            report.findings += result.findings
            if result.error:
                report.notes.append(result.error)

        test_files = select_test_files(changeset, contexts)
        if test_files:
            note = install_dependencies(sandbox, work, deps, test_timeout)
            if note:
                report.notes.append(note)
            report.tests = run_tests(sandbox, work, deps, out, test_files, test_timeout)
            report.findings += failures_to_findings(report.tests, roots)
            if report.tests.timed_out:
                report.notes.append(f"tests timed out after {test_timeout}s")
            elif report.tests.crashed:
                report.notes.append("pytest produced no report (collection error or crash)")
        else:
            report.notes.append("no related tests were found to run")

    classify_scope(report.findings, changeset)
    # A failing test chosen *because* it exercises changed code is relevant even when the
    # failing line sits in the (unchanged) test file, e.g. an assertion on a wrong value.
    for f in report.findings:
        if f.tool == "pytest" and f.scope == "other":
            f.scope = "related-test"
            f.unit_key = next(
                (k for k, ctx in contexts.items() if any(t.path == f.path for t in ctx.tests)),
                None,
            )
    attach_evidence(report.findings, changeset.parsed)
    return report
