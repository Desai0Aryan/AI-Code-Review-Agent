"""Safely unpack a repository tarball into a scratch directory for sandboxed runs."""

import io
import os
import tarfile
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "node_modules"}


def extract_workspace(
    archive: bytes,
    dest: Path,
    max_files: int = 5000,
    max_file_bytes: int = 5_000_000,
    max_total_bytes: int = 100_000_000,
) -> int:
    """Write regular files from the archive into `dest`. Returns the file count.

    Symlinks, hard links, devices and anything that would land outside `dest` are
    dropped, so a hostile archive cannot reach the host filesystem. Raises ValueError
    when the repo is bigger than the limits.
    """
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o755)  # the unprivileged sandbox user must be able to read it
    root = dest.resolve()
    count = total = 0

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
        for member in tar:
            if not member.isfile():
                continue
            parts = member.name.split("/")[1:]  # drop "<owner>-<repo>-<sha>/"
            if not parts or any(p in ("", ".", "..") for p in parts):
                continue
            if any(p in SKIP_DIRS for p in parts[:-1]):
                continue
            if member.size > max_file_bytes:
                continue
            count += 1
            total += member.size
            if count > max_files or total > max_total_bytes:
                raise ValueError("repository exceeds workspace size limits")

            target = (root / "/".join(parts)).resolve()
            if not target.is_relative_to(root):
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(handle.read())
            os.chmod(target, 0o644)

    for directory in [root, *(p for p in root.rglob("*") if p.is_dir())]:
        os.chmod(directory, 0o755)
    return count
