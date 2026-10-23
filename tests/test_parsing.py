from app.parsing import language_for, parse_file, parse_python

CODE = """\
import os


def top(a):
    helper(a)
    return os.path.join("x", a)


class Cart:
    tax = 0.1

    def __init__(self):
        self.items = []

    @property
    def total(self):
        return self._sum() * (1 + self.tax)

    def _sum(self):
        def inner(i):
            return i.price
        return sum(inner(i) for i in self.items)
"""


def names(parsed):
    return {u.qualified_name: u for u in parsed.units}


def test_extracts_units_with_kinds():
    units = names(parse_python("m.py", CODE))
    assert units["top"].kind == "function"
    assert units["Cart"].kind == "class"
    assert units["Cart.__init__"].kind == "method"
    assert units["Cart.total"].kind == "method"
    assert units["Cart._sum.inner"].kind == "function"


def test_spans_are_one_based_and_include_decorators():
    units = names(parse_python("m.py", CODE))
    assert (units["top"].start_line, units["top"].end_line) == (4, 6)
    # @property is on line 15, def on 16
    assert units["Cart.total"].start_line == 15
    assert units["Cart.total"].end_line == 17


def test_calls_are_collected():
    units = names(parse_python("m.py", CODE))
    assert set(units["top"].calls) == {"helper", "join"}
    assert set(units["Cart.total"].calls) == {"_sum"}
    assert "inner" in units["Cart._sum"].calls and "sum" in units["Cart._sum"].calls


def test_unit_at_returns_innermost_function():
    parsed = parse_python("m.py", CODE)
    assert parsed.unit_at(5).qualified_name == "top"
    assert parsed.unit_at(21).qualified_name == "Cart._sum.inner"
    assert parsed.unit_at(22).qualified_name == "Cart._sum"
    # class attribute line is only inside the class
    assert parsed.unit_at(10).qualified_name == "Cart"
    assert parsed.unit_at(1) is None


def test_syntax_errors_flagged():
    assert parse_python("bad.py", "def f(:\n  pass\n").has_syntax_errors
    assert not parse_python("ok.py", "x = 1\n").has_syntax_errors


def test_language_detection():
    assert language_for("a/b.py") == "python"
    assert language_for("a/b.js") is None
    assert parse_file("a.txt", "hi") is None
