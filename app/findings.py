"""Normalized findings: every tool's output is converted into one shape.

Scope matters as much as content: a lint warning in code the PR did not touch is
pre-existing noise, while the same warning on a changed line is worth reporting.
"""

from dataclasses import dataclass

from app.changes import ChangeSet
from app.parsing import ParsedFile

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


@dataclass
class Finding:
    tool: str  # ruff | bandit | mypy | pytest
    rule: str  # e.g. F821, B602, name-defined, test-failure
    severity: str  # high | medium | low
    confidence: float  # 0..1, how much this tool's signal can be trusted
    category: str  # bug | security | type | test-failure
    path: str
    line: int
    message: str
    end_line: int | None = None
    scope: str = "other"  # changed-line | changed-function | related-test | other
    unit_key: str | None = None
    evidence: str = ""  # source excerpt or traceback tail

    @property
    def location(self) -> str:
        return f"{self.path}:{self.line}"


def classify_scope(findings: list[Finding], changeset: ChangeSet) -> None:
    """Mark each finding as on a changed line, inside a changed function, or elsewhere."""
    for f in findings:
        best: tuple[str, str | None] = ("other", None)
        for cu in changeset.changed_units:
            if cu.path != f.path:
                continue
            if f.line in cu.changed_lines:
                best = ("changed-line", cu.key)
                break
            if cu.unit.contains(f.line) and best[0] == "other":
                best = ("changed-function", cu.key)
        if best[0] == "other" and f.line in changeset.module_level.get(f.path, []):
            best = ("changed-line", None)
        f.scope, f.unit_key = best


def attach_evidence(findings: list[Finding], parsed: dict[str, ParsedFile]) -> None:
    """Fill in a 3-line source excerpt (with line numbers) for findings lacking evidence."""
    for f in findings:
        if f.evidence or f.path not in parsed:
            continue
        lines = parsed[f.path].source.splitlines()
        if not 1 <= f.line <= len(lines):
            continue
        lo, hi = max(1, f.line - 1), min(len(lines), f.line + 1)
        f.evidence = "\n".join(f"{n}: {lines[n - 1]}" for n in range(lo, hi + 1))


def relevant(findings: list[Finding]) -> list[Finding]:
    """Findings about code this PR touched, most severe and most trustworthy first."""
    kept = [f for f in findings if f.scope != "other"]
    return sorted(
        kept, key=lambda f: (SEVERITY_ORDER.get(f.severity, 9), -f.confidence, f.path, f.line)
    )
