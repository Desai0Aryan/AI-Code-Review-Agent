"""The reviewer: ask the model, ground its claims, decide status from evidence, merge tool results.

Key rule: the model never decides how certain a finding is on its own. `status` is computed
here from independent evidence (a failing test, a deterministic tool finding) plus the model's
stated confidence. A claim with no independent evidence can reach "likely" at best.
"""

import logging

from pydantic import ValidationError

from app.analysis import AnalysisReport
from app.changes import ChangedUnit, ChangeSet
from app.context import UnitContext
from app.findings import SEVERITY_ORDER, Finding
from app.grounding import Grounded, ground_finding
from app.llm import LLMClient, LLMError
from app.parsing import ParsedFile
from app.prompts import REPORT_SCHEMA, REPORT_TOOL, SYSTEM_PROMPT, build_unit_prompt
from app.review_models import (
    STATUS_ORDER,
    LLMFinding,
    RejectedFinding,
    ReviewFinding,
    ReviewResult,
    make_id,
)

logger = logging.getLogger("code_review_agent")

DROP_BELOW = 0.25  # model confidence under this is noise, not "uncertain"
CONTEXT_MIN_CONFIDENCE = 0.75  # claims about code the PR did not touch need more conviction
MAX_REPORTED = 15

BUG_LIKE = {"bug", "logic", "error-handling", "api-misuse", "concurrency"}
TOOL_CATEGORY_MATCH = {
    "bug": BUG_LIKE,
    "type": {"bug", "logic", "api-misuse"},
    "security": {"security"},
    "test-failure": BUG_LIKE,
}


def corroborating(g: Grounded, unit_key: str, tool_findings: list[Finding]) -> list[Finding]:
    """Independent tool results that back up a model claim.

    A result only counts if it points at the lines the claim cites. The one exception is a
    failing test in the same unit that the model explicitly cites in `based_on`: an assertion
    on a wrong value has no traceback frame in the buggy code, so it cannot be located by line.
    """
    f = g.finding
    last = g.end_line or g.line
    cites_a_test = "test" in " ".join(f.based_on).lower()
    out = []
    for t in tool_findings:
        if f.category not in TOOL_CATEGORY_MATCH.get(t.category, set()):
            continue
        on_cited_lines = t.path == f.path and g.line <= t.line <= last
        if t.tool == "pytest":
            if t.pre_existing:
                continue  # fails on the base commit too, so it proves nothing about this change
            if g.scope != "context" and (
                on_cited_lines or (t.unit_key == unit_key and cites_a_test)
            ):
                out.append(t)
        elif on_cited_lines:
            out.append(t)
    return out


def decide_status(g: Grounded, corroboration: list[Finding], min_confidence: float) -> str:
    f = g.finding
    if any(t.tool == "pytest" or t.confidence >= 0.9 for t in corroboration):
        return "confirmed"
    if f.certainty == "uncertain" or f.confidence < min_confidence:
        return "uncertain"
    if g.scope == "context" and f.confidence < CONTEXT_MIN_CONFIDENCE:
        return "uncertain"
    return "likely"  # the model saying "confirmed" without evidence is capped here


def _tags(findings: list[Finding]) -> list[str]:
    return sorted({f"{t.tool}:{t.rule}" for t in findings})


def tool_finding_to_review(t: Finding) -> ReviewFinding | None:
    """Tool findings worth showing on their own; weak signals (style-ish, low confidence)
    stay as hints to the model but never become standalone findings."""
    standalone = (
        t.tool == "pytest"
        or t.severity == "high"
        or (t.confidence >= 0.9 and t.severity == "medium")
    )
    if not standalone:
        return None
    category = "security" if t.category == "security" else "bug"
    return ReviewFinding(
        id=make_id(t.path, t.line, category),
        source="tool",
        title=f"{t.tool} {t.rule}: {t.message}"[:200],
        category=category,
        severity=t.severity,
        path=t.path,
        line=t.line,
        end_line=t.end_line,
        evidence=t.evidence,
        explanation=(
            "A related test fails; see the traceback."
            if t.tool == "pytest"
            else f"Reported by {t.tool} ({t.rule})."
        ),
        confidence=t.confidence,
        status=(
            "uncertain"
            if t.pre_existing
            else "confirmed"
            if (t.tool == "pytest" or t.confidence >= 0.9)
            else "likely"
        ),
        scope=t.scope,
        unit_key=t.unit_key,
        uncertainty_reason=(
            "This test also fails on the base commit, so this pull request may not have caused it."
            if t.pre_existing
            else None
        ),
        corroborated_by=[f"{t.tool}:{t.rule}"],
    )


def _unit_signals(cu: ChangedUnit, findings: list[Finding]) -> list[Finding]:
    return [
        f
        for f in findings
        if f.unit_key == cu.key or (f.path == cu.path and cu.unit.contains(f.line))
    ]


def _llm_to_review(
    g: Grounded, status: str, corroboration: list[Finding], unit_key: str, source_lines: list[str]
) -> ReviewFinding:
    f = g.finding
    first = g.line
    last = g.end_line or g.line
    evidence = "\n".join(
        f"{n}: {source_lines[n - 1]}" for n in range(first, min(last, len(source_lines)) + 1)
    )
    return ReviewFinding(
        id=make_id(f.path, g.line, f.category),
        source="llm+tool" if corroboration else "llm",
        title=f.title,
        category=f.category,
        severity=f.severity,
        path=f.path,
        line=g.line,
        end_line=g.end_line,
        evidence=evidence,
        explanation=f.explanation,
        confidence=f.confidence,
        status=status,
        scope=g.scope,
        unit_key=unit_key,
        trace=g.trace,
        suggested_fix=f.suggested_fix,
        uncertainty_reason=f.uncertainty_reason if status == "uncertain" else None,
        corroborated_by=_tags(corroboration),
        relocated_from=g.relocated_from,
    )


def _review_unit(
    cu: ChangedUnit,
    ctx: UnitContext | None,
    files: dict[str, ParsedFile],
    report: AnalysisReport,
    llm: LLMClient,
    result: ReviewResult,
    min_confidence: float,
    max_tokens: int,
) -> tuple[list[ReviewFinding], set[int]]:
    """Returns accepted LLM findings and the ids of tool findings that corroborated them."""
    signals = _unit_signals(cu, report.relevant)
    prompt = build_unit_prompt(cu, ctx, files, signals, report.tests)
    try:
        resp = llm.complete_json(
            system=SYSTEM_PROMPT,
            user=prompt.text,
            tool_name=REPORT_TOOL,
            tool_schema=REPORT_SCHEMA,
            max_tokens=max_tokens,
        )
    except LLMError as exc:
        result.notes.append(f"LLM review of {cu.key} failed: {exc}")
        return [], set()

    result.units_reviewed += 1
    result.input_tokens += resp.input_tokens
    result.output_tokens += resp.output_tokens
    if isinstance(resp.data.get("summary"), str):
        result.summaries[cu.key] = resp.data["summary"]

    accepted: list[ReviewFinding] = []
    used: set[int] = set()
    for raw in resp.data.get("findings") or []:
        try:
            claim = LLMFinding.model_validate(raw)
        except ValidationError:
            result.notes.append(f"dropped a malformed finding for {cu.key}")
            continue

        def reject(reason: str, claim=claim) -> None:
            result.rejected.append(
                RejectedFinding(cu.key, claim.title, claim.path, claim.line, reason)
            )

        if claim.confidence < DROP_BELOW:
            reject(f"confidence {claim.confidence:.2f} is below {DROP_BELOW}")
            continue
        grounded, reason = ground_finding(claim, prompt, files, cu)
        if grounded is None:
            reject(reason or "ungrounded")
            continue

        support = corroborating(grounded, cu.key, report.relevant)
        used.update(id(t) for t in support)
        status = decide_status(grounded, support, min_confidence)
        accepted.append(
            _llm_to_review(grounded, status, support, cu.key, files[claim.path].source.splitlines())
        )
    return accepted, used


def _rank(f: ReviewFinding) -> tuple:
    return (
        STATUS_ORDER[f.status],
        SEVERITY_ORDER.get(f.severity, 9),
        -f.confidence,
        f.path,
        f.line,
    )


def review_changes(
    changeset: ChangeSet,
    contexts: dict[str, UnitContext],
    report: AnalysisReport,
    files: dict[str, ParsedFile],
    llm: LLMClient | None,
    *,
    min_confidence: float = 0.6,
    max_units: int = 10,
    max_tokens: int = 4096,
) -> ReviewResult:
    result = ReviewResult()
    accepted: list[ReviewFinding] = []
    used_tool_ids: set[int] = set()

    if llm is None:
        result.notes.append(
            "LLM review skipped (no API key configured); showing tool findings only"
        )
    else:
        units = [cu for cu in changeset.changed_units if cu.path in files]
        if len(units) > max_units:
            result.notes.append(
                f"only the first {max_units} of {len(units)} changed functions were reviewed"
            )
        for cu in units[:max_units]:
            found, used = _review_unit(
                cu, contexts.get(cu.key), files, report, llm, result, min_confidence, max_tokens
            )
            accepted += found
            used_tool_ids |= used
        if changeset.module_level:
            result.notes.append("module-level changes are not reviewed by the model yet")

    # Tool findings that no model claim accounted for, one per location.
    by_location: dict[tuple[str, int], list[Finding]] = {}
    for t in report.relevant:
        if id(t) not in used_tool_ids:
            by_location.setdefault((t.path, t.line), []).append(t)
    for group in by_location.values():
        if any(not t.pre_existing for t in group):
            group = [
                t for t in group if not t.pre_existing
            ]  # a test failing before the PR proves nothing
        group.sort(key=lambda t: (t.pre_existing, -t.confidence))
        review = tool_finding_to_review(group[0])
        if review is not None:
            review.corroborated_by = _tags(group)
            accepted.append(review)

    # One finding per id: keep the strongest.
    best: dict[str, ReviewFinding] = {}
    for f in sorted(accepted, key=_rank):
        best.setdefault(f.id, f)
    result.findings = sorted(best.values(), key=_rank)
    if len(result.findings) > MAX_REPORTED:
        result.notes.append(f"{len(result.findings) - MAX_REPORTED} lower-ranked findings omitted")
        result.findings = result.findings[:MAX_REPORTED]
    for note in report.notes:
        result.notes.append(f"analysis: {note}")
    return result
