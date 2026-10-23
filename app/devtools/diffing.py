"""Build a git-style unified diff from two snapshots of a repo (used by the fake GitHub,
the offline demo and the benchmark)."""

import difflib


def make_unified_diff(base: dict[str, str], head: dict[str, str]) -> str:
    out: list[str] = []
    for path in sorted(set(base) | set(head)):
        old, new = base.get(path), head.get(path)
        if old == new:
            continue
        out.append(f"diff --git a/{path} b/{path}")
        if old is None:
            out.append("new file mode 100644")
        elif new is None:
            out.append("deleted file mode 100644")
        a = [] if old is None else old.splitlines()
        b = [] if new is None else new.splitlines()
        out.append("--- /dev/null" if old is None else f"--- a/{path}")
        out.append("+++ /dev/null" if new is None else f"+++ b/{path}")
        out += list(difflib.unified_diff(a, b, n=3, lineterm=""))[2:]
    return "\n".join(out) + "\n"
