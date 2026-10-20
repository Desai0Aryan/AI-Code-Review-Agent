"""Syntax-tree parsing with tree-sitter.

Extracts functions, methods and classes with exact line spans, plus the names each
function calls. This is what lets the reviewer reason about code structure instead
of raw text.
"""

from dataclasses import dataclass, field

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

PY_LANGUAGE = Language(tspython.language())
_parser = Parser(PY_LANGUAGE)

SUPPORTED_SUFFIXES = {".py": "python"}


def language_for(path: str) -> str | None:
    for suffix, lang in SUPPORTED_SUFFIXES.items():
        if path.endswith(suffix):
            return lang
    return None


@dataclass
class CodeUnit:
    """A function, method or class definition."""

    kind: str  # "function" | "method" | "class"
    name: str
    qualified_name: str  # e.g. "Cart.total"
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    source: str
    calls: list[str] = field(default_factory=list)
    call_sites: list[tuple[str, int]] = field(default_factory=list)  # (callee name, 1-based line)
    parent: str | None = None  # qualified name of enclosing class/function

    def contains(self, line: int) -> bool:
        return self.start_line <= line <= self.end_line

    @property
    def span(self) -> int:
        return self.end_line - self.start_line


@dataclass
class ParsedFile:
    path: str
    source: str
    units: list[CodeUnit]
    has_syntax_errors: bool

    def unit_at(self, line: int) -> CodeUnit | None:
        """Innermost function/method containing `line` (classes only if no function does)."""
        matches = [u for u in self.units if u.contains(line)]
        if not matches:
            return None
        funcs = [u for u in matches if u.kind != "class"]
        pool = funcs or matches
        return min(pool, key=lambda u: u.span)


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _call_name(call: Node, src: bytes) -> str | None:
    fn = call.child_by_field_name("function")
    if fn is None:
        return None
    if fn.type == "identifier":
        return _text(fn, src)
    if fn.type == "attribute":
        attr = fn.child_by_field_name("attribute")
        return _text(attr, src) if attr is not None else None
    return None


def _collect_calls(
    node: Node, src: bytes, out: list[str], sites: list[tuple[str, int]]
) -> None:
    if node.type == "call":
        name = _call_name(node, src)
        if name:
            if name not in out:
                out.append(name)
            sites.append((name, node.start_point[0] + 1))
    for child in node.children:
        _collect_calls(child, src, out, sites)


def _walk(node: Node, src: bytes, scope: list[tuple[str, str]], units: list[CodeUnit]) -> None:
    for child in node.children:
        target = child
        # Decorated definitions wrap the real def; span should include decorators.
        if child.type == "decorated_definition":
            inner = child.child_by_field_name("definition")
            if inner is None:
                continue
            target = inner

        if target.type in ("function_definition", "class_definition"):
            name_node = target.child_by_field_name("name")
            name = _text(name_node, src) if name_node is not None else "<anonymous>"
            in_class = bool(scope) and scope[-1][0] == "class"
            if target.type == "class_definition":
                kind = "class"
            else:
                kind = "method" if in_class else "function"

            qualified = ".".join([s[1] for s in scope] + [name])
            unit = CodeUnit(
                kind=kind,
                name=name,
                qualified_name=qualified,
                start_line=child.start_point[0] + 1,
                end_line=child.end_point[0] + 1,
                source=_text(child, src),
                parent=".".join(s[1] for s in scope) or None,
            )
            if kind != "class":
                body = target.child_by_field_name("body")
                if body is not None:
                    _collect_calls(body, src, unit.calls, unit.call_sites)
            units.append(unit)

            body = target.child_by_field_name("body")
            if body is not None:
                _walk(body, src, scope + [(kind if kind == "class" else "function", name)], units)
        else:
            _walk(child, src, scope, units)


def parse_python(path: str, source: str) -> ParsedFile:
    src = source.encode("utf-8")
    tree = _parser.parse(src)
    units: list[CodeUnit] = []
    _walk(tree.root_node, src, [], units)
    units.sort(key=lambda u: (u.start_line, u.end_line))
    return ParsedFile(path=path, source=source, units=units, has_syntax_errors=tree.root_node.has_error)


def parse_file(path: str, source: str) -> ParsedFile | None:
    """Parse a file if its language is supported, else None."""
    lang = language_for(path)
    if lang == "python":
        return parse_python(path, source)
    return None
