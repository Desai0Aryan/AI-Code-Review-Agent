"""Shared fixtures: a tiny fake repo and a helper to pack it like a GitHub tarball."""

import io
import tarfile

FAKE_REPO = {
    "shop/__init__.py": "",
    "shop/cart.py": (
        "from shop.pricing import apply_discount\n"
        "\n"
        "class Cart:\n"
        "    def __init__(self):\n"
        "        self.items = []\n"
        "\n"
        "    def total(self, discount=0):\n"
        "        raw = sum(i.price for i in self.items)\n"
        "        return apply_discount(raw, discount)\n"
    ),
    "shop/pricing.py": "def apply_discount(amount, discount):\n    return amount - discount\n",
    "shop/checkout.py": (
        "from shop.cart import Cart\n"
        "\n"
        "def checkout(cart):\n"
        "    t = cart.total()\n"
        "    return charge(t)\n"
        "\n"
        "def charge(amount):\n"
        "    return amount\n"
    ),
    "tests/test_cart.py": (
        "from shop.cart import Cart\n"
        "\n"
        "def test_total_empty():\n"
        "    assert Cart().total() == 0\n"
        "\n"
        "def test_other():\n"
        "    assert Cart() is not None\n"
    ),
    "tests/test_pricing.py": (
        "from shop.pricing import apply_discount\n"
        "\n"
        "def test_apply_discount():\n"
        "    assert apply_discount(10, 3) == 7\n"
    ),
}


def make_tarball(files: dict[str, str], prefix: str = "octo-demo-abc123") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"{prefix}/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()
