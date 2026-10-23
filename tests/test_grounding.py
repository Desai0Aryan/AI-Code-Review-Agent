from app.changes import ChangedUnit
from app.grounding import ground_finding
from app.index import RepoIndex
from app.prompts import build_unit_prompt
from app.review_models import LLMFinding, LLMTraceStep
from tests.test_analysis import REPO, changeset_for_total


def setup(repo=None):
    repo = repo or REPO
    cs, contexts = changeset_for_total(repo)
    cu = cs.changed_units[0]
    files = RepoIndex(repo).files
    return cu, files, build_unit_prompt(cu, contexts[cu.key], files, [], nonce="n")


def claim(**kw) -> LLMFinding:
    base = {
        "title": "t",
        "path": "shop/cart.py",
        "line": 9,
        "evidence_quote": "return aply_discount(raw, discount)",
        "explanation": "e",
        "confidence": 0.9,
        "certainty": "likely",
    }
    return LLMFinding(**{**base, **kw})


def run(c, setup_=None):
    cu, files, prompt = setup_ or setup()
    return ground_finding(c, prompt, files, cu)


def test_accepts_a_claim_whose_quote_is_on_the_cited_line():
    g, reason = run(claim())
    assert reason is None
    assert (g.line, g.scope, g.relocated_from) == (9, "changed-line", None)


def test_quote_matching_ignores_whitespace_differences():
    g, _ = run(claim(evidence_quote="return   aply_discount(raw,  discount)"))
    assert g is not None


def test_rejects_a_quote_that_is_not_in_the_code():
    g, reason = run(claim(evidence_quote="return apply_discount(raw, discount) / 0"))
    assert g is None and "does not appear" in reason


def test_corrects_a_miscounted_line_only_when_the_quote_is_unique():
    g, _ = run(claim(line=8))  # quote is really line 9
    assert (g.line, g.relocated_from) == (9, 8)

    index = RepoIndex({"a.py": "def f(x):\n    y = g(x)\n    y = g(x)\n    return y\n"})
    cu = ChangedUnit("a.py", index.files["a.py"].units[0], [2, 3])
    prompt = build_unit_prompt(cu, None, index.files, [], nonce="n")
    g, reason = ground_finding(
        claim(path="a.py", line=4, evidence_quote="y = g(x)"), prompt, index.files, cu
    )
    assert g is None and "matches several places" in reason


def test_rejects_lines_and_files_the_model_never_saw():
    g, reason = run(claim(line=12, evidence_quote="return len(self.items)"))
    assert g is None and "was not shown" in reason
    g, reason = run(claim(path="other.py"))
    assert g is None and "was not provided" in reason
    g, reason = run(claim(evidence_quote="x"))
    assert g is None and "too short" in reason


def test_scope_is_computed_from_the_diff_not_from_the_model():
    g, _ = run(claim(line=7, evidence_quote="def total(self, discount=0):"))
    assert g.scope == "changed-function"  # inside the changed function, line itself untouched
    resolved = {
        **REPO,
        "shop/cart.py": REPO["shop/cart.py"].replace("aply_discount", "apply_discount"),
    }
    c = claim(path="shop/pricing.py", line=2, evidence_quote="return amount - discount")
    g, _ = run(c, setup(resolved))
    assert g.scope == "context"  # callee code the PR did not change


def test_trace_steps_outside_what_was_shown_are_dropped():
    steps = [
        LLMTraceStep(path="shop/cart.py", line=8, note="raw computed"),
        LLMTraceStep(path="shop/cart.py", line=12, note="never shown"),
        LLMTraceStep(path="x.py", line=1, note="invented file"),
    ]
    g, _ = run(claim(trace=steps))
    assert [(s.path, s.line) for s in g.trace] == [("shop/cart.py", 8)]
    assert g.dropped_trace_steps == 2


def test_model_output_is_normalized():
    c = claim(category="made-up", severity="catastrophic", certainty="definitely", confidence=7)
    assert (c.category, c.severity, c.certainty, c.confidence) == (
        "other",
        "medium",
        "uncertain",
        1.0,
    )
    assert claim(line=9, end_line=3).end_line is None  # end before start is discarded
