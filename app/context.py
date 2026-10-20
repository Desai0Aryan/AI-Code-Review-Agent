"""Per-function context: callers, callees, related tests and recent history.

This is the evidence the reviewer (Part 5) gets so it can trace a bug across files
instead of judging a diff in isolation.
"""

import posixpath
from dataclasses import dataclass, field

from app.changes import ChangedUnit
from app.github_client import CommitInfo
from app.index import IndexedUnit, RepoIndex, is_test_path

MAX_CALLERS = 10
MAX_CALLEES = 10
MAX_TESTS = 8


@dataclass
class Reference:
    path: str
    qualified_name: str
    start_line: int
    end_line: int
    lines: list[int] = field(default_factory=list)  # call-site lines (callers only)
    ambiguous: bool = False

    @property
    def key(self) -> str:
        return f"{self.path}::{self.qualified_name}"


@dataclass
class RelatedTest:
    path: str
    name: str
    start_line: int
    reason: str


@dataclass
class UnitContext:
    key: str
    callers: list[Reference] = field(default_factory=list)
    callees: list[Reference] = field(default_factory=list)
    tests: list[RelatedTest] = field(default_factory=list)
    history: list[CommitInfo] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # limits and caveats the reviewer must respect

    def to_text(self) -> str:
        """Compact rendering, intended for the LLM prompt in a later part."""
        out = [f"## Context for {self.key}"]
        out.append("Callers:")
        out += [
            f"- {r.key} (lines {', '.join(map(str, r.lines))})" + (" [ambiguous]" if r.ambiguous else "")
            for r in self.callers
        ] or ["- none found"]
        out.append("Calls into:")
        out += [
            f"- {r.key} (defined at line {r.start_line})" + (" [ambiguous]" if r.ambiguous else "")
            for r in self.callees
        ] or ["- none resolved in repo"]
        out.append("Related tests:")
        out += [f"- {t.path}::{t.name} ({t.reason})" for t in self.tests] or ["- none"]
        out.append("Recent commits touching this file:")
        out += [f"- {c.sha[:7]} {c.message} ({c.author}, {c.date[:10]})" for c in self.history] or [
            "- unavailable"
        ]
        if self.notes:
            out.append("Caveats:")
            out += [f"- {n}" for n in self.notes]
        return "\n".join(out)


def _proximity(a: str, b: str) -> int:
    if a == b:
        return 0
    return 1 if posixpath.dirname(a) == posixpath.dirname(b) else 2


def _lookup_name(unit_name: str, parent: str | None) -> str | None:
    """Name other code uses to reach this unit. Constructors are reached via the class name."""
    if unit_name == "__init__" and parent:
        return parent.split(".")[-1]
    if unit_name.startswith("__") and unit_name.endswith("__"):
        return None  # other dunders are invoked implicitly; name search would be noise
    return unit_name


def _find_callers(index: RepoIndex, changed: ChangedUnit, name: str, notes: list[str]):
    ambiguous = len(index.definitions(name)) > 1
    if ambiguous:
        notes.append(
            f"'{name}' is defined {len(index.definitions(name))} times in the repo; "
            "caller matches are by name only and may belong to a different definition."
        )
    found: list[Reference] = []
    for c in index.callers(name):
        if is_test_path(c.path):
            continue
        if c.path == changed.path and c.unit.qualified_name == changed.unit.qualified_name:
            continue  # recursion
        found.append(
            Reference(
                path=c.path,
                qualified_name=c.unit.qualified_name,
                start_line=c.unit.start_line,
                end_line=c.unit.end_line,
                lines=[ln for n, ln in c.unit.call_sites if n == name],
                ambiguous=ambiguous,
            )
        )
    found.sort(key=lambda r: (_proximity(r.path, changed.path), r.path, r.start_line))
    if len(found) > MAX_CALLERS:
        notes.append(f"{len(found) - MAX_CALLERS} more callers omitted.")
    return found[:MAX_CALLERS]


def _find_callees(index: RepoIndex, changed: ChangedUnit) -> list[Reference]:
    found: list[Reference] = []
    seen: set[str] = set()
    for name in changed.unit.calls:
        if name == changed.unit.name:
            continue
        defs = index.definitions(name)
        if not defs:
            continue
        defs = sorted(defs, key=lambda d: (_proximity(d.path, changed.path), d.path))
        same_file = [d for d in defs if d.path == changed.path]
        ambiguous = len(defs) > 1 and len(same_file) != 1
        best = same_file[0] if len(same_file) == 1 else defs[0]
        if best.key in seen:
            continue
        seen.add(best.key)
        found.append(
            Reference(
                path=best.path,
                qualified_name=best.unit.qualified_name,
                start_line=best.unit.start_line,
                end_line=best.unit.end_line,
                ambiguous=ambiguous,
            )
        )
    return found[:MAX_CALLEES]


def _is_test_function(iu: IndexedUnit) -> bool:
    return iu.unit.kind != "class" and iu.unit.name.startswith("test")


def _find_tests(index: RepoIndex, changed: ChangedUnit, name: str | None) -> list[RelatedTest]:
    tests: dict[str, RelatedTest] = {}

    if name:
        for c in index.callers(name):
            if is_test_path(c.path) and _is_test_function(c):
                tests[c.key] = RelatedTest(c.path, c.unit.qualified_name, c.unit.start_line,
                                           f"calls {name}()")  # fmt: skip

    # Naming convention: shop/cart.py <-> tests/test_cart.py or tests/cart_test.py
    stem = posixpath.splitext(posixpath.basename(changed.path))[0]
    wanted = {f"test_{stem}.py", f"{stem}_test.py"}
    for path, parsed in index.files.items():
        if posixpath.basename(path) in wanted and is_test_path(path):
            for u in parsed.units:
                if u.kind != "class" and u.name.startswith("test"):
                    key = f"{path}::{u.qualified_name}"
                    tests.setdefault(
                        key,
                        RelatedTest(path, u.qualified_name, u.start_line,
                                    f"test module for {posixpath.basename(changed.path)}"),
                    )  # fmt: skip

    ordered = sorted(tests.values(), key=lambda t: (not t.reason.startswith("calls"), t.path, t.start_line))
    return ordered[:MAX_TESTS]


def build_unit_context(
    index: RepoIndex, changed: ChangedUnit, history: list[CommitInfo] | None = None
) -> UnitContext:
    ctx = UnitContext(key=changed.key, history=history or [])
    unit = changed.unit
    name = _lookup_name(unit.name, unit.parent)

    if name is None:
        ctx.notes.append(f"'{unit.name}' is invoked implicitly; callers cannot be found by name.")
    else:
        ctx.callers = _find_callers(index, changed, name, ctx.notes)
        if not ctx.callers:
            ctx.notes.append(
                "No callers found in the repo. This may be an entry point, a public API, "
                "or be called dynamically."
            )

    ctx.callees = _find_callees(index, changed)
    ctx.tests = _find_tests(index, changed, name)
    if not ctx.tests:
        ctx.notes.append(f"No tests reference '{unit.name}'; behaviour is unverified by the suite.")

    parsed = index.files.get(changed.path)
    if parsed is not None and parsed.has_syntax_errors:
        ctx.notes.append("The file has syntax errors; structure-based results may be incomplete.")
    return ctx
