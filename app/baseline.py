"""Was it already broken? Re-run the failing tests on the base commit.

A test that fails both before and after the pull request cannot be blamed on the pull request.
"""

import os
import tempfile
from pathlib import Path

from app.analysis import AnalysisReport, install_dependencies
from app.sandbox import Sandbox
from app.testrun import run_tests
from app.workspace import extract_workspace


def mark_pre_existing(
    report: AnalysisReport, base_archive: bytes, sandbox: Sandbox, timeout: int = 180
) -> int:
    """Flag pytest findings whose test also fails on the base commit. Returns how many."""
    failing = [f for f in report.findings if f.tool == "pytest" and f.nodeid]
    if not failing or report.tests is None or not report.tests.ran:
        return 0

    with tempfile.TemporaryDirectory(prefix="cra-base-") as tmp:
        base = Path(tmp)
        work, deps, out = base / "work", base / "deps", base / "out"
        for d in (deps, out):
            d.mkdir()
            os.chmod(d, 0o777)
        try:
            extract_workspace(base_archive, work)
        except ValueError:
            report.notes.append("base-commit check skipped: repository too large")
            return 0
        install_dependencies(sandbox, work, deps, timeout)
        run = run_tests(sandbox, work, deps, out, report.tests.ran, timeout)

    if run.timed_out or run.crashed:
        report.notes.append("base-commit check was inconclusive (tests crashed or timed out there)")
        return 0
    failing_at_base = {f.nodeid for f in run.failures}
    marked = 0
    for f in failing:
        if f.nodeid in failing_at_base:
            f.pre_existing = True
            marked += 1
    return marked
