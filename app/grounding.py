"""Grounding: a model claim is only accepted if the code it cites really says so.

This is the main defence against invented bugs. A finding must
  * cite a file and line the model was actually shown,
  * quote text that appears in the real source at (or, for a wrong line number, uniquely near)
    the cited line, and
  * keep only trace steps that point at lines the model was shown.
"""

import re
from dataclasses import dataclass, field

from app.changes import ChangedUnit
from app.parsing import ParsedFile
from app.prompts import Prompt
from app.review_models import LLMFinding, TraceStep

MIN_QUOTE_CHARS = 4


@dataclass
class Grounded:
    finding: LLMFinding
    line: int  # possibly corrected from the model's citation
    end_line: int | None
    scope: str  # changed-line | changed-function | context
    relocated_from: int | None = None
    trace: list[TraceStep] = field(default_factory=list)
    dropped_trace_steps: int = 0


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _scope(path: str, line: int, cu: ChangedUnit) -> str:
    if path == cu.path:
        if line in cu.changed_lines:
            return "changed-line"
        if cu.unit.contains(line):
            return "changed-function"
    return "context"


def _window(lines: list[str], start: int, count: int) -> str:
    return _norm(" ".join(lines[start - 1 : start - 1 + count]))


def ground_finding(
    f: LLMFinding,
    prompt: Prompt,
    files: dict[str, ParsedFile],
    cu: ChangedUnit,
) -> tuple[Grounded | None, str | None]:
    """Returns (grounded, None) when accepted, or (None, reason) when the claim is blocked."""
    quote = _norm(f.evidence_quote)
    if len(quote) < MIN_QUOTE_CHARS:
        return None, "evidence quote is too short to verify"
    if f.path not in files or f.path not in prompt.shown:
        return None, f"cites {f.path}, which was not provided"

    lines = files[f.path].source.splitlines()
    quote_lines = max(1, len([ln for ln in f.evidence_quote.splitlines() if ln.strip()]))
    span = max(quote_lines, (f.end_line or f.line) - f.line + 1)

    line = f.line
    relocated_from = None
    if not prompt.covers(f.path, f.line):
        return None, f"cites {f.path}:{f.line}, which was not shown"
    if quote not in _window(lines, f.line, span):
        # Models sometimes miscount lines. Accept a correction only if the quote matches
        # exactly one place among the lines they were shown.
        candidates = [
            n
            for lo, hi in prompt.shown[f.path]
            for n in range(lo, hi + 1)
            if prompt.covers(f.path, n + quote_lines - 1)
            and quote in _window(lines, n, quote_lines)
        ]
        if len(candidates) != 1:
            reason = "quoted text does not appear in the cited code"
            if len(candidates) > 1:
                reason = "quoted text matches several places; the cited line cannot be verified"
            return None, reason
        relocated_from, line = f.line, candidates[0]

    steps, dropped = [], 0
    for step in f.trace:
        if prompt.covers(step.path, step.line):
            steps.append(TraceStep(step.path, step.line, step.note))
        else:
            dropped += 1

    end_line = f.end_line if relocated_from is None else None
    return (
        Grounded(
            finding=f,
            line=line,
            end_line=end_line,
            scope=_scope(f.path, line, cu),
            relocated_from=relocated_from,
            trace=steps,
            dropped_trace_steps=dropped,
        ),
        None,
    )
