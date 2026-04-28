"""Tests for the AST/tree-sitter structure extractor."""

from snipp.structure import detect_language, extract


def test_detect_python():
    assert detect_language("def f(): pass\nimport os\n") == "python"


def test_detect_typescript():
    assert detect_language("interface X { a: number }\nconst y = 1;\n") in (
        "typescript", "javascript"
    )


def test_python_ast_extraction():
    src = '''import os
from typing import List

@decorator
def hello(x: int) -> int:
    """Doc."""
    return x

class Foo:
    def bar(self):
        return 1
'''
    s = extract(src, "python")
    assert s.language == "python"
    assert any("import os" in i for i in s.imports)
    names = [sig.name for sig in s.signatures]
    assert "hello" in names
    assert "Foo" in names
    assert "bar" in names
    hello_sig = next(sig for sig in s.signatures if sig.name == "hello")
    assert any("@decorator" in d for d in hello_sig.decorators)


def test_python_syntax_error_falls_back():
    src = "def broken(:\n    pass"
    s = extract(src, "python")
    assert s.language == "python"


def test_regex_fallback_handles_single_line_docstring():
    src = '''def f():
    """one liner"""
    return 1

def g():
    return 2
'''
    s = extract(src, "python")
    names = [sig.name for sig in s.signatures]
    assert "f" in names and "g" in names
