import io
import tarfile

from app.index import RepoIndex, extract_python_files, is_test_path
from tests.helpers import FAKE_REPO, make_tarball


def test_extract_strips_prefix_and_keeps_only_python():
    files = extract_python_files(make_tarball({**FAKE_REPO, "README.md": "hi"}))
    assert "shop/cart.py" in files and "tests/test_cart.py" in files
    assert "README.md" not in files


def test_extract_skips_vendored_traversal_and_oversized():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in {
            "p/ok.py": "x = 1\n",
            "p/.venv/lib/dep.py": "y = 2\n",
            "p/../evil.py": "z = 3\n",
            "p/big.py": "a" * 100,
        }.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    files = extract_python_files(buf.getvalue(), max_file_bytes=50)
    assert set(files) == {"ok.py"}


def test_is_test_path():
    assert is_test_path("tests/test_cart.py")
    assert is_test_path("pkg/tests/helpers.py")
    assert is_test_path("pkg/cart_test.py")
    assert is_test_path("conftest.py")
    assert not is_test_path("shop/cart.py")
    assert not is_test_path("shop/latest.py")


def test_index_definitions_and_callers():
    index = RepoIndex(FAKE_REPO)
    assert [d.key for d in index.definitions("apply_discount")] == [
        "shop/pricing.py::apply_discount"
    ]
    callers = {c.key for c in index.callers("total")}
    assert "shop/checkout.py::checkout" in callers
    assert "tests/test_cart.py::test_total_empty" in callers


def test_call_sites_carry_line_numbers():
    index = RepoIndex(FAKE_REPO)
    checkout = index.definitions("checkout")[0].unit
    assert ("total", 4) in checkout.call_sites
    assert ("charge", 5) in checkout.call_sites
