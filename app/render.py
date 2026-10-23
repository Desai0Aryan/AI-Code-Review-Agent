"""Human-facing Markdown for a review (Part 6 posts this to the pull request)."""

from app.review_models import ReviewFinding, ReviewResult

FIX_NOTE = {
    "verified": "Fix tested in a sandbox: ",
    "no-regression": "Fix tested in a sandbox (no regressions, but not proven to fix the issue): ",
}

HEADINGS = {
    "confirmed": "Confirmed",
    "likely": "Likely issues",
    "uncertain": "Needs a human check",
}
INTRO = {
    "confirmed": "Backed by a failing test or a deterministic tool, not only by the model.",
    "likely": "Supported by the code shown, but with no independent proof. Please verify.",
    "uncertain": "The model could not verify these from the code it could see. Treat as questions.",
}


def _fence(text: str, lang: str = "") -> str:
    return f"```{lang}\n{text.rstrip()}\n```"


def _render_finding(n: int, f: ReviewFinding, fix=None) -> str:
    loc = f"`{f.path}:{f.line}`" + (f"-{f.end_line}" if f.end_line else "")
    out = [f"**{n}. {f.title}** ({f.severity}, {f.category}) at {loc}"]
    if f.evidence:
        out.append(_fence(f.evidence))
    out.append(f.explanation)
    if f.trace:
        out.append("Trace:\n" + "\n".join(f"- `{s.path}:{s.line}` {s.note}" for s in f.trace))
    if f.uncertainty_reason:
        out.append(f"_Why unsure:_ {f.uncertainty_reason}")
    if f.corroborated_by:
        out.append(f"_Evidence:_ {', '.join(f.corroborated_by)}")
    out.append(_fix_block(f, fix))
    return "\n\n".join(x for x in out if x)


def _fix_block(f: ReviewFinding, fix) -> str:
    if fix is not None and fix.status in FIX_NOTE:
        return FIX_NOTE[fix.status] + fix.reason + "\n" + _fence(fix.new_text, "python")
    if fix is not None and fix.status == "failed":
        return f"_A candidate fix was tried and rejected: {fix.reason}._"
    if f.suggested_fix:
        return "Suggested fix (not tested):\n" + _fence(f.suggested_fix, "python")
    return ""


def render_inline(f: ReviewFinding, fix=None, *, suggestion: bool = False) -> str:
    """Body of one inline review comment. `suggestion` adds a one-click GitHub suggestion."""
    out = [f"**{f.title}** ({f.severity}, {f.status})", f.explanation]
    if f.evidence:
        out.append(_fence(f.evidence))
    if f.uncertainty_reason:
        out.append(f"_Why unsure:_ {f.uncertainty_reason}")
    if f.corroborated_by:
        out.append(f"_Evidence:_ {', '.join(f.corroborated_by)}")
    if suggestion and fix is not None and fix.status in FIX_NOTE:
        out.append(FIX_NOTE[fix.status] + fix.reason + "\n" + _fence(fix.new_text, "suggestion"))
    else:
        out.append(_fix_block(f, fix))
    return "\n\n".join(x for x in out if x)


def render_markdown(result: ReviewResult, fixes: dict | None = None) -> str:
    fixes = fixes or {}
    counts = {s: len(result.with_status(s)) for s in HEADINGS}
    lines = ["## AI code review", ""]
    if result.units_reviewed == 0:
        lines.append(
            f"Static analysis and tests found {counts['confirmed']} confirmed, "
            f"{counts['likely']} likely and {counts['uncertain']} uncertain issue(s)."
            if result.findings
            else "Static analysis and tests found nothing to report."
        )
    elif not result.findings:
        lines.append(
            f"Reviewed {result.units_reviewed} changed function(s) and found nothing "
            "I can support with evidence."
        )
    else:
        lines.append(
            f"Reviewed {result.units_reviewed} changed function(s): "
            f"{counts['confirmed']} confirmed, {counts['likely']} likely, "
            f"{counts['uncertain']} need a human check."
        )
    n = 0
    for status, heading in HEADINGS.items():
        group = result.with_status(status)
        if not group:
            continue
        lines += ["", f"### {heading}", f"_{INTRO[status]}_", ""]
        for f in group:
            n += 1
            lines += [_render_finding(n, f, fixes.get(f.id)), ""]
    if result.rejected:
        lines.append(
            f"_{len(result.rejected)} model claim(s) were discarded because the code they cited "
            "did not support them._"
        )
    for note in result.notes:
        lines.append(f"_Note: {note}_")
    return "\n".join(lines).rstrip() + "\n"
