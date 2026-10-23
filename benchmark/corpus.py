"""Seeded-defect corpus.

Every case is a tiny, correct project (with passing tests) plus one pull request that either
introduces a single bug or makes a harmless change. The `how` field records what could
plausibly detect the bug, so tool-only runs are judged fairly:

  tests  a test in the project fails because of the bug
  lint   a deterministic analyzer (ruff / bandit) flags the line
  llm    nothing deterministic catches it (no covering test); only reading the code does
  none   (clean cases) the change is correct; any confident finding is a false positive
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    how: str  # tests | lint | llm | none
    src: str  # contents of pkg/mod.py before the PR
    old: str  # text replaced by the PR (must occur exactly once in src)
    new: str
    tests: str = ""  # contents of tests/test_mod.py ("" = no tests)
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def buggy(self) -> bool:
        return self.how != "none"

    def base_files(self) -> dict[str, str]:
        files = {"pkg/__init__.py": "", "pkg/mod.py": self.src, **self.extra}
        if self.tests:
            files["tests/test_mod.py"] = self.tests
        return files

    def head_files(self) -> dict[str, str]:
        assert self.src.count(self.old) == 1, f"{self.id}: `old` must occur exactly once"
        files = self.base_files()
        files["pkg/mod.py"] = self.src.replace(self.old, self.new)
        return files

    def bug_lines(self) -> tuple[int, int] | None:
        """Line range (in the head file) covered by the replacement; None for clean cases."""
        if not self.buggy:
            return None
        head = self.head_files()["pkg/mod.py"]
        start = head[: head.index(self.new)].count("\n") + 1
        return start, start + self.new.count("\n")


def _t(imports: str, body: str) -> str:
    return f"from pkg.mod import {imports}\n\n\n{body}"


CASES: list[Case] = [
    # ---- bugs a failing test exposes ----------------------------------------------------
    Case(
        "off-by-one-range",
        "range excludes the upper bound",
        "tests",
        "def sum_to(n):\n    return sum(range(1, n + 1))\n",
        "range(1, n + 1)",
        "range(1, n)",
        _t("sum_to", "def test_sum_to():\n    assert sum_to(3) == 6\n"),
    ),
    Case(
        "boundary-comparison",
        "age 18 is not adult",
        "tests",
        "def is_adult(age):\n    return age >= 18\n",
        "age >= 18",
        "age > 18",
        _t(
            "is_adult",
            "def test_boundary():\n    assert is_adult(18)\n\n\ndef test_young():\n    assert not is_adult(5)\n",
        ),
    ),
    Case(
        "missing-return",
        "result computed but never returned",
        "tests",
        "def double(x):\n    result = x * 2\n    return result\n",
        "    return result\n",
        "    result = result\n",
        _t("double", "def test_double():\n    assert double(4) == 8\n"),
    ),
    Case(
        "swapped-operands",
        "ratio divides the wrong way round",
        "tests",
        "def ratio(part, whole):\n    return part / whole\n",
        "part / whole",
        "whole / part",
        _t("ratio", "def test_ratio():\n    assert ratio(1, 4) == 0.25\n"),
    ),
    Case(
        "empty-average",
        "average of an empty list divides by zero",
        "tests",
        "def average(xs):\n    if not xs:\n        return 0\n    return sum(xs) / len(xs)\n",
        "    if not xs:\n        return 0\n",
        "",
        _t(
            "average",
            "def test_average():\n    assert average([2, 4]) == 3\n\n\ndef test_empty():\n    assert average([]) == 0\n",
        ),
    ),
    Case(
        "inverted-condition",
        "is_empty is inverted",
        "tests",
        "def is_empty(items):\n    return len(items) == 0\n",
        "len(items) == 0",
        "len(items) > 0",
        _t(
            "is_empty",
            "def test_empty():\n    assert is_empty([])\n\n\ndef test_full():\n    assert not is_empty([1])\n",
        ),
    ),
    Case(
        "slice-drops-last",
        "first_n returns one item too few",
        "tests",
        "def first_n(items, n):\n    return items[:n]\n",
        "items[:n]",
        "items[: n - 1]",
        _t("first_n", "def test_first_n():\n    assert first_n([1, 2, 3, 4], 2) == [1, 2]\n"),
    ),
    Case(
        "wrong-dict-key",
        "reads a key that is never set",
        "tests",
        "def full_name(user):\n    return f\"{user['first']} {user['last']}\"\n",
        "user['last']",
        "user['surname']",
        _t(
            "full_name",
            "def test_full_name():\n    assert full_name({'first': 'Ada', 'last': 'Lovelace'}) == 'Ada Lovelace'\n",
        ),
    ),
    # ---- bugs a deterministic analyzer flags ----------------------------------------------
    Case(
        "undefined-name",
        "typo in a variable name",
        "lint",
        "def total(prices):\n    subtotal = sum(prices)\n    return subtotal\n",
        "    return subtotal\n",
        "    return subtotl\n",
        _t("total", "def test_total():\n    assert total([1, 2]) == 3\n"),
    ),
    Case(
        "mutable-default",
        "shared mutable default argument",
        "lint",
        "def add(item, bucket=None):\n    bucket = [] if bucket is None else bucket\n    bucket.append(item)\n    return bucket\n",
        "def add(item, bucket=None):\n    bucket = [] if bucket is None else bucket\n",
        "def add(item, bucket=[]):\n",
        _t("add", "def test_add_is_isolated():\n    add(1)\n    assert add(2) == [2]\n"),
    ),
    Case(
        "is-with-literal",
        "compares an int with `is`",
        "lint",
        "def is_ok(code):\n    return code == 200\n",
        "code == 200",
        "code is 200",
        _t("is_ok", "def test_ok():\n    assert is_ok(200)\n"),
    ),
    Case(
        "shell-injection",
        "command run through a shell",
        "lint",
        "import subprocess\n\n\ndef run(name):\n    return subprocess.run(['echo', name], capture_output=True, text=True).stdout\n",
        "subprocess.run(['echo', name], capture_output=True, text=True)",
        "subprocess.run('echo ' + name, shell=True, capture_output=True, text=True)",
        _t("run", "def test_run():\n    assert run('hi').strip() == 'hi'\n"),
    ),
    Case(
        "eval-of-input",
        "eval replaces a safe literal parser",
        "lint",
        "import ast\n\n\ndef parse(text):\n    return ast.literal_eval(text)\n",
        "ast.literal_eval(text)",
        "eval(text)",
        _t("parse", "def test_parse():\n    assert parse('[1, 2]') == [1, 2]\n"),
    ),
    # ---- bugs only a reader can find (no covering test) --------------------------------------
    Case(
        "unchecked-discount",
        "discount applied as a surcharge",
        "llm",
        "def discounted(price, percent):\n    return price * (1 - percent / 100)\n",
        "price * (1 - percent / 100)",
        "price * (1 + percent / 100)",
    ),
    Case(
        "retry-count",
        "retry loop runs one time too few",
        "llm",
        "def attempt(fn, retries):\n    last = None\n    for _ in range(retries + 1):\n        try:\n            return fn()\n        except ValueError as exc:\n            last = exc\n    raise last\n",
        "range(retries + 1)",
        "range(retries)",
    ),
    # ---- clean pull requests: any confident finding is a false positive --------------------
    Case(
        "clean-rename",
        "renames a local variable",
        "none",
        "def total(prices):\n    subtotal = sum(prices)\n    return subtotal\n",
        "subtotal = sum(prices)\n    return subtotal",
        "amount = sum(prices)\n    return amount",
        _t("total", "def test_total():\n    assert total([1, 2]) == 3\n"),
    ),
    Case(
        "clean-docstring",
        "adds a docstring",
        "none",
        "def is_adult(age):\n    return age >= 18\n",
        "def is_adult(age):\n",
        'def is_adult(age):\n    """True from the 18th birthday on."""\n',
        _t("is_adult", "def test_boundary():\n    assert is_adult(18)\n"),
    ),
    Case(
        "clean-new-function",
        "adds a correct, tested function",
        "none",
        "def double(x):\n    return x * 2\n",
        "    return x * 2\n",
        "    return x * 2\n\n\ndef triple(x):\n    return x * 3\n",
        _t("double", "def test_double():\n    assert double(2) == 4\n"),
    ),
    Case(
        "clean-simplify",
        "simplifies a conditional",
        "none",
        "def sign(x):\n    if x > 0:\n        return 1\n    elif x < 0:\n        return -1\n    else:\n        return 0\n",
        "    if x > 0:\n        return 1\n    elif x < 0:\n        return -1\n    else:\n        return 0\n",
        "    if x > 0:\n        return 1\n    if x < 0:\n        return -1\n    return 0\n",
        _t("sign", "def test_sign():\n    assert (sign(5), sign(-2), sign(0)) == (1, -1, 0)\n"),
    ),
    Case(
        "clean-guard",
        "adds an input guard",
        "none",
        "def average(xs):\n    return sum(xs) / len(xs)\n",
        "    return sum(xs) / len(xs)\n",
        "    if not xs:\n        return 0\n    return sum(xs) / len(xs)\n",
        _t(
            "average",
            "def test_average():\n    assert average([2, 4]) == 3\n\n\ndef test_empty():\n    assert average([]) == 0\n",
        ),
    ),
    Case(
        "clean-fstring",
        "switches to an f-string",
        "none",
        "def greet(name):\n    return 'Hello, ' + name + '!'\n",
        "'Hello, ' + name + '!'",
        "f'Hello, {name}!'",
        _t("greet", "def test_greet():\n    assert greet('Ada') == 'Hello, Ada!'\n"),
    ),
]
