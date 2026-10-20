"""Unified diff parsing.

Turns the text GitHub returns for a pull request into per-file hunks with exact
old/new line numbers, so findings can later point at real lines.
"""

import re
from dataclasses import dataclass, field

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    added: list[tuple[int, str]] = field(default_factory=list)  # (new line no, text)
    removed: list[tuple[int, str]] = field(default_factory=list)  # (old line no, text)


@dataclass
class FileDiff:
    old_path: str | None  # None for new files
    new_path: str | None  # None for deleted files
    hunks: list[Hunk] = field(default_factory=list)
    is_binary: bool = False

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""

    @property
    def is_new(self) -> bool:
        return self.old_path is None

    @property
    def is_deleted(self) -> bool:
        return self.new_path is None

    @property
    def added_lines(self) -> list[int]:
        return [n for h in self.hunks for n, _ in h.added]

    @property
    def removed_lines(self) -> list[int]:
        return [n for h in self.hunks for n, _ in h.removed]


def _strip_prefix(path: str) -> str | None:
    path = path.strip()
    if path == "/dev/null":
        return None
    if path.startswith(("a/", "b/")):
        return path[2:]
    return path


def parse_unified_diff(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    old_no = new_no = 0

    for line in text.splitlines():
        if line.startswith("diff --git "):
            current = FileDiff(old_path=None, new_path=None)
            # Provisional paths from the header; refined by ---/+++ lines.
            parts = line.split(" ")
            if len(parts) >= 4:
                current.old_path = _strip_prefix(parts[2])
                current.new_path = _strip_prefix(parts[3])
            files.append(current)
            hunk = None
            continue

        if current is None:
            continue

        if hunk is None:
            if line.startswith("--- "):
                current.old_path = _strip_prefix(line[4:].split("\t")[0])
                continue
            if line.startswith("+++ "):
                current.new_path = _strip_prefix(line[4:].split("\t")[0])
                continue
            if line.startswith(("Binary files ", "GIT binary patch")):
                current.is_binary = True
                continue
            if line.startswith("new file mode"):
                current.old_path = None
                continue
            if line.startswith("deleted file mode"):
                current.new_path = None
                continue

        m = HUNK_RE.match(line)
        if m:
            hunk = Hunk(
                old_start=int(m.group(1)),
                old_count=int(m.group(2) if m.group(2) is not None else 1),
                new_start=int(m.group(3)),
                new_count=int(m.group(4) if m.group(4) is not None else 1),
            )
            current.hunks.append(hunk)
            old_no, new_no = hunk.old_start, hunk.new_start
            continue

        if hunk is None:
            continue

        if line.startswith("+"):
            hunk.added.append((new_no, line[1:]))
            new_no += 1
        elif line.startswith("-"):
            hunk.removed.append((old_no, line[1:]))
            old_no += 1
        elif line.startswith("\\"):  # "\ No newline at end of file"
            continue
        else:  # context line
            old_no += 1
            new_no += 1

    return files
