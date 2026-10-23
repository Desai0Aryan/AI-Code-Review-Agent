"""Run every corpus case through the real pipeline (against an in-memory GitHub) and score it.

Scoring, per case:
  buggy case  caught      a confirmed/likely finding sits inside the function the bug was seeded in,
                          or is a related test that fails with the PR (and passes on the base)
              missed      otherwise
              flagged     (extra) the bug was only raised as "needs a human check"
  any case    false alarm every confirmed/likely finding that is NOT the seeded bug
Aggregates:
  recall      caught / buggy cases
  precision   true findings / (true findings + false alarms), over all confirmed+likely findings
  clean-PR false-alarm rate   clean PRs with at least one confirmed/likely finding / clean PRs
"""

import json
import tempfile
import time
from dataclasses import asdict, dataclass, field

from app.config import Settings
from app.devtools.fake_github import FakeGitHub
from app.llm import get_llm
from app.parsing import parse_python
from app.sandbox import DockerSandbox, LocalSandbox
from app.service import ReviewService
from benchmark.corpus import CASES, Case

REPORTED = ("confirmed", "likely")


@dataclass
class CaseResult:
    id: str
    how: str
    buggy: bool
    caught: bool = False
    flagged_uncertain: bool = False
    true_findings: int = 0
    false_alarms: list[str] = field(default_factory=list)
    fix_verified: bool = False
    rejected_claims: int = 0
    seconds: float = 0.0
    status: str = ""
    error: str | None = None


def _bug_span(case: Case) -> tuple[int, int] | None:
    lines = case.bug_lines()
    if lines is None:
        return None
    parsed = parse_python("pkg/mod.py", case.head_files()["pkg/mod.py"])
    span = [lines[0], lines[1]]
    for ln in range(lines[0], lines[1] + 1):
        unit = parsed.unit_at(ln)
        if unit is not None:
            span = [min(span[0], unit.start_line), max(span[1], unit.end_line)]
    return span[0], span[1]


def run_case(case: Case, settings: Settings, sandbox, llm) -> CaseResult:
    res = CaseResult(case.id, case.how, case.buggy)
    fake = FakeGitHub(case.base_files(), case.head_files())
    with tempfile.TemporaryDirectory() as tmp:
        s = settings.model_copy(update={"data_dir": tmp})
        service = ReviewService(s, github_factory=fake.factory(), llm=llm, sandbox=sandbox)
        t0 = time.monotonic()
        out = service.run(fake.event(), publish=False)
        res.seconds = round(time.monotonic() - t0, 1)
    res.status, res.error = out.status, out.error
    res.rejected_claims = len(out.review.rejected)
    span = _bug_span(case)

    for f in out.review.findings:
        in_bug = span is not None and (
            (f.path == "pkg/mod.py" and span[0] <= f.line <= span[1])
            # A test that passes on the base commit and fails with the PR is the bug showing
            # itself, even though the failing line is in the test file.
            or (
                f.scope == "related-test"
                and any(t.startswith("pytest:") for t in f.corroborated_by)
            )
        )
        if f.status in REPORTED:
            if in_bug:
                res.true_findings += 1
                res.caught = True
                fix = out.fixes.fixes.get(f.id)
                res.fix_verified = res.fix_verified or (
                    fix is not None and fix.status == "verified"
                )
            else:
                res.false_alarms.append(f"{f.path}:{f.line} {f.title}")
        elif in_bug:
            res.flagged_uncertain = True
    return res


def summarize(results: list[CaseResult]) -> dict:
    buggy = [r for r in results if r.buggy]
    clean = [r for r in results if not r.buggy]
    true = sum(r.true_findings for r in results)
    false = sum(len(r.false_alarms) for r in results)
    caught = sum(r.caught for r in buggy)

    def ratio(a: float, b: float) -> float | None:
        return round(a / b, 3) if b else None

    by_how: dict[str, dict] = {}
    for r in buggy:
        d = by_how.setdefault(r.how, {"cases": 0, "caught": 0})
        d["cases"] += 1
        d["caught"] += r.caught
    return {
        "cases": len(results),
        "buggy_cases": len(buggy),
        "clean_cases": len(clean),
        "bugs_caught": caught,
        "recall": ratio(caught, len(buggy)),
        "precision": ratio(true, true + false),
        "false_alarms": false,
        "clean_prs_with_false_alarm": sum(bool(r.false_alarms) for r in clean),
        "clean_pr_false_alarm_rate": ratio(sum(bool(r.false_alarms) for r in clean), len(clean)),
        "bugs_only_flagged_uncertain": sum(r.flagged_uncertain and not r.caught for r in buggy),
        "fixes_verified": sum(r.fix_verified for r in buggy),
        "rejected_model_claims": sum(r.rejected_claims for r in results),
        "errors": sum(r.status == "error" for r in results),
        "by_detectable_how": by_how,
    }


def run_benchmark(
    *,
    use_llm: bool,
    sandbox_kind: str = "local",
    only: list[str] | None = None,
    settings: Settings | None = None,
) -> tuple[list[CaseResult], dict]:
    settings = settings or Settings()
    settings = settings.model_copy(update={"post_clean_summary": False})
    llm = get_llm(settings) if use_llm else None
    if use_llm and llm is None:
        raise SystemExit("--llm needs CRA_ANTHROPIC_API_KEY to be set")
    sandbox = (
        DockerSandbox(image=settings.sandbox_image) if sandbox_kind == "docker" else LocalSandbox()
    )
    cases = [c for c in CASES if not only or c.id in only]
    results = [run_case(c, settings, sandbox, llm) for c in cases]
    summary = summarize(results)
    summary["mode"] = "llm+tools" if use_llm else "tools-only"
    summary["model"] = settings.llm_model if use_llm else None
    return results, summary


def render_report(results: list[CaseResult], summary: dict) -> str:
    pct = lambda v: "n/a" if v is None else f"{v:.0%}"
    lines = [
        f"# Benchmark results ({summary['mode']}"
        + (f", {summary['model']}" if summary["model"] else "")
        + ")",
        "",
        f"- Bugs caught: **{summary['bugs_caught']}/{summary['buggy_cases']}** (recall {pct(summary['recall'])})",
        (
            "- Precision of confirmed/likely findings: "
            f"**{pct(summary['precision'])}** ({summary['false_alarms']} false alarm(s))"
        ),
        f"- Clean PRs with a false alarm: **{summary['clean_prs_with_false_alarm']}/{summary['clean_cases']}**",
        f"- Seeded bugs raised only as 'needs a human check': {summary['bugs_only_flagged_uncertain']}",
        f"- Fixes verified in the sandbox: {summary['fixes_verified']}",
        f"- Model claims discarded by grounding: {summary['rejected_model_claims']}",
        f"- Pipeline errors: {summary['errors']}",
        "",
        "Recall by how the bug can be detected:",
        "",
    ]
    for how, d in sorted(summary["by_detectable_how"].items()):
        lines.append(f"- `{how}`: {d['caught']}/{d['cases']}")
    lines += [
        "",
        "| case | detectable by | result | false alarms | seconds |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        if r.buggy:
            verdict = (
                "caught" if r.caught else ("flagged uncertain" if r.flagged_uncertain else "missed")
            )
        else:
            verdict = "clean (no alarm)" if not r.false_alarms else "FALSE ALARM"
        if r.status == "error":
            verdict = f"ERROR {r.error}"
        fa = "; ".join(r.false_alarms) or "-"
        lines.append(f"| {r.id} | {r.how} | {verdict} | {fa} | {r.seconds} |")
    return "\n".join(lines) + "\n"


def to_json(results: list[CaseResult], summary: dict) -> str:
    return json.dumps({"summary": summary, "cases": [asdict(r) for r in results]}, indent=2)
