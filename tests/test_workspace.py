import io
import stat
import tarfile

import pytest

from app.workspace import extract_workspace


def tarball(entries):
    """entries: list of (name, bytes | ('symlink', target) | ('hardlink', target))."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, payload in entries:
            info = tarfile.TarInfo(name)
            if isinstance(payload, tuple):
                info.type = tarfile.SYMTYPE if payload[0] == "symlink" else tarfile.LNKTYPE
                info.linkname = payload[1]
                tar.addfile(info)
            else:
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def test_extracts_files_and_strips_prefix(tmp_path):
    n = extract_workspace(
        tarball([("o-r-abc/a.py", b"x=1\n"), ("o-r-abc/pkg/b.txt", b"hi"), ("o-r-abc/.git/HEAD", b"r")]),
        tmp_path / "w",
    )
    assert n == 2
    assert (tmp_path / "w/a.py").read_text() == "x=1\n"
    assert (tmp_path / "w/pkg/b.txt").exists()
    assert not (tmp_path / "w/.git").exists()


def test_symlinks_hardlinks_and_traversal_are_dropped(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("host secret")
    extract_workspace(
        tarball(
            [
                ("p/link.py", ("symlink", str(secret))),
                ("p/hard.py", ("hardlink", "p/ok.py")),
                ("p/../escape.py", b"bad"),
                ("p/a/../../escape2.py", b"bad"),
                ("p/ok.py", b"ok"),
            ]
        ),
        tmp_path / "w",
    )
    assert sorted(p.name for p in (tmp_path / "w").rglob("*") if p.is_file()) == ["ok.py"]
    assert not (tmp_path / "escape.py").exists() and not (tmp_path / "escape2.py").exists()


def test_permissions_are_readable_by_unprivileged_user(tmp_path):
    extract_workspace(tarball([("p/pkg/m.py", b"x")]), tmp_path / "w")
    assert stat.S_IMODE((tmp_path / "w").stat().st_mode) == 0o755
    assert stat.S_IMODE((tmp_path / "w/pkg").stat().st_mode) == 0o755
    assert stat.S_IMODE((tmp_path / "w/pkg/m.py").stat().st_mode) == 0o644


def test_limits_are_enforced(tmp_path):
    big = tarball([(f"p/f{i}.py", b"x") for i in range(10)])
    with pytest.raises(ValueError, match="size limits"):
        extract_workspace(big, tmp_path / "a", max_files=5)
    with pytest.raises(ValueError, match="size limits"):
        extract_workspace(tarball([("p/a.py", b"x" * 100)]), tmp_path / "b", max_total_bytes=50)
    # oversized single files are skipped, not fatal
    assert extract_workspace(tarball([("p/a.py", b"x" * 100)]), tmp_path / "c", max_file_bytes=50) == 0
