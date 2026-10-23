"""Offline demo: a pull request with a seeded bug goes through the full pipeline, no GitHub needed."""

from app.config import Settings
from app.devtools.fake_github import FakeGitHub
from app.sandbox import LocalSandbox
from app.service import ReviewOutcome, ReviewService

BASE = {
    "shop/__init__.py": "",
    "shop/pricing.py": "def apply_discount(amount, discount):\n    return amount - discount\n",
    "shop/cart.py": (
        "from shop.pricing import apply_discount\n"
        "\n"
        "\n"
        "class Cart:\n"
        "    def __init__(self):\n"
        "        self.items = []\n"
        "\n"
        "    def total(self, discount=0):\n"
        "        raw = sum(self.items)\n"
        "        return apply_discount(raw, discount)\n"
    ),
    "tests/test_cart.py": (
        "from shop.cart import Cart\n"
        "\n"
        "\n"
        "def test_total_with_discount():\n"
        "    cart = Cart()\n"
        "    cart.items = [10, 5]\n"
        "    assert cart.total(discount=3) == 12\n"
    ),
}
HEAD = {
    **BASE,
    "shop/cart.py": BASE["shop/cart.py"].replace("return apply_discount", "return aply_discount"),
}


def run_demo(settings: Settings | None = None) -> tuple[FakeGitHub, ReviewOutcome]:
    settings = (settings or Settings()).model_copy(update={"data_dir": "data/demo"})
    fake = FakeGitHub(BASE, HEAD)
    service = ReviewService(settings, github_factory=fake.factory(), sandbox=LocalSandbox())
    return fake, service.run(fake.event())
