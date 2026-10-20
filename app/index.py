"""Repository index: parse every Python file once, then answer
"who calls X?" and "what does X call?" across files.

Resolution is by name (Python is dynamic, so this is a heuristic). Callers and callees
that could refer to several definitions are flagged as ambiguous instead of guessed.
"""

import io
import posixpath
import tarfile
from dataclasses import dataclass

from app.parsing import CodeUnit, ParsedFile, parse_file

MAX_FILES = 2000
MAX_FILE_BYTES = 300_000
SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "site-packages",
    "build", "dist", "__pycache__", ".tox", ".mypy_cache",
}  # fmt: skip


def extract_python_files(
    archive: bytes, max_files: int = MAX_FILES, max_file_bytes: int = MAX_FILE_BYTES
) -> dict[str, str]:
    """Read .py files out of a GitHub tarball entirely in memory (nothing touches disk)."""
    files: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
        for member in tar:
            if not member.isfile():
                continue
            parts = member.name.split("/")[1:]  # drop the "<owner>-<repo>-<sha>/" prefix
            if not parts or ".." in parts or parts[0] == "":
                continue
            if any(p in SKIP_DIRS for p in parts[:-1]):
                continue
            path = "/".join(parts)
            if not path.endswith(".py") or member.size > max_file_bytes:
                continue
            if len(files) >= max_files:
                break
            handle = tar.extractfile(member)
            if handle is None:
                continue
            files[path] = handle.read().decode("utf-8", errors="replace")
    return files


def is_test_path(path: str) -> bool:
    p = path.lower()
    name = posixpath.basename(p)
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
        or "/tests/" in f"/{p}"
        or "/test/" in f"/{p}"
    )


@dataclass(frozen=True)
class IndexedUnit:
    path: str
    unit: CodeUnit

    @property
    def key(self) -> str:
        return f"{self.path}::{self.unit.qualified_name}"


class RepoIndex:
    def __init__(self, sources: dict[str, str]):
        self.files: dict[str, ParsedFile] = {}
        self._defs: dict[str, list[IndexedUnit]] = {}
        self._callers: dict[str, list[IndexedUnit]] = {}

        for path, text in sources.items():
            parsed = parse_file(path, text)
            if parsed is None:
                continue
            self.files[path] = parsed
            for unit in parsed.units:
                indexed = IndexedUnit(path, unit)
                self._defs.setdefault(unit.name, []).append(indexed)
                for name in unit.calls:
                    self._callers.setdefault(name, []).append(indexed)

    def definitions(self, name: str) -> list[IndexedUnit]:
        return self._defs.get(name, [])

    def callers(self, name: str) -> list[IndexedUnit]:
        return self._callers.get(name, [])
