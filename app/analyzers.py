"""Static analyzers (ruff, bandit, mypy) run inside the sandbox and normalized to Findings.

Parsing is separate from running so each parser is testable against canned output.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.findings import Finding
from app.sandbox import Sandbox

RUFF_SELECT = "F,B,E9,PLE"  # real defects only; style rules would drown the signal
MYPY_LINE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?:\d+:)? (?P<level>error|warning|note): "
    r"(?P<msg>.*?)(?:\s+\[(?P<code>[\w-]+)\])?$"
)

RUFF_HIGH = ("F821", "F822", "F823", "E9", "PLE")
RUFF_LOW = ("F401", "F841", "F541")
MYPY_HIGH = {"name-defined", "call-arg"}
BANDIT_SEVERITY = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}
BANDIT_CONFIDENCE = {"HIGH": 0.9, "MEDIUM": 0.6, "LOW": 0.3}


@dataclass
class AnalyzerResult:
    findings: list[Finding] = field(default_factory=list)
    error: str | None = None  # set when the tool itself failed (not when it found issues)


def relative_path(path: str, roots: tuple[str, ...]) -> str:
    for root in roots:
        path = path.removeprefix(root)
    return path.removeprefix("./")


def _ruff_severity(code: str) -> str:
    if code.startswith(RUFF_HIGH):
        return "high"
    if code.startswith(RUFF_LOW):
        return "low"
    return "medium"


def parse_ruff(stdout: str, roots: tuple[str, ...]) -> list[Finding]:
    findings = []
    for item in json.loads(stdout or "[]"):
        code = item.get("code")
        loc, end = item.get("location") or {}, item.get("end_location") or {}
        findings.append(
            Finding(
                tool="ruff",
                rule=code or "syntax-error",
                severity=_ruff_severity(code) if code else "high",
                confidence=0.9,
                category="bug",
                path=relative_path(item.get("filename", ""), roots),
                line=loc.get("row", 0),
                end_line=end.get("row"),
                message=item.get("message", ""),
            )
        )
    return findings


def parse_bandit(stdout: str, roots: tuple[str, ...]) -> list[Finding]:
    findings = []
    for item in json.loads(stdout or "{}").get("results", []):
        findings.append(
            Finding(
                tool="bandit",
                rule=item.get("test_id", "bandit"),
                severity=BANDIT_SEVERITY.get(item.get("issue_severity", "LOW"), "low"),
                confidence=BANDIT_CONFIDENCE.get(item.get("issue_confidence", "LOW"), 0.3),
                category="security",
                path=relative_path(item.get("filename", ""), roots),
                line=item.get("line_number", 0),
                end_line=(item.get("line_range") or [None])[-1],
                message=item.get("issue_text", ""),
            )
        )
    return findings


def parse_mypy(stdout: str, roots: tuple[str, ...]) -> list[Finding]:
    findings = []
    for raw in stdout.splitlines():
        m = MYPY_LINE.match(raw.strip())
        if not m or m["level"] == "note":
            continue
        code = m["code"] or "mypy"
        findings.append(
            Finding(
                tool="mypy",
                rule=code,
                severity="high" if code in MYPY_HIGH else "medium",
                confidence=0.5,
                category="type",
                path=relative_path(m["path"], roots),
                line=int(m["line"]),
                message=m["msg"],
            )
        )
    return findings


def _run(sandbox: Sandbox, tool: str, cmd: list[str], work: Path, timeout: int, parse, roots):
    res = sandbox.run(cmd, work=work, timeout=timeout, env={"MYPYPATH": "{work}"})
    if res.timed_out:
        return AnalyzerResult(error=f"{tool} timed out after {timeout}s")
    if res.returncode not in (0, 1):  # 1 means "issues found"; anything else is a tool failure
        tail = (res.stderr or res.stdout).strip()[-300:]
        return AnalyzerResult(error=f"{tool} exited with {res.returncode}: {tail}")
    try:
        return AnalyzerResult(findings=parse(res.stdout, roots))
    except (ValueError, KeyError, TypeError) as exc:
        tail = (res.stderr or res.stdout).strip()[-300:]
        return AnalyzerResult(error=f"{tool} produced unreadable output ({exc}): {tail}")


def run_ruff(sandbox, work, files, timeout, roots) -> AnalyzerResult:
    cmd = [
        "python", "-m", "ruff", "check", "--isolated", "--no-cache", "--exit-zero",
        "--select", RUFF_SELECT, "--output-format", "json", *files,
    ]  # fmt: skip
    return _run(sandbox, "ruff", cmd, work, timeout, parse_ruff, roots)


def run_bandit(sandbox, work, files, timeout, roots) -> AnalyzerResult:
    cmd = ["python", "-m", "bandit", "-q", "-f", "json", "-s", "B101", *files]
    return _run(sandbox, "bandit", cmd, work, timeout, parse_bandit, roots)


def run_mypy(sandbox, work, files, timeout, roots) -> AnalyzerResult:
    cmd = [
        "python", "-m", "mypy", "--config-file=", "--ignore-missing-imports",
        "--follow-imports=silent", "--check-untyped-defs", "--no-error-summary", "--show-error-codes",
        "--no-pretty", "--cache-dir=/dev/null", "--namespace-packages",
        "--explicit-package-bases", *files,
    ]  # fmt: skip
    result = _run(sandbox, "mypy", cmd, work, timeout, parse_mypy, roots)
    return result
