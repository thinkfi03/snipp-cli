"""Code structure extraction.

Order of preference per language:
  - Python: stdlib `ast` (always available, exact)
  - JS/TS/Go/Rust/Java/C/C++: tree-sitter via tree-sitter-language-pack
    (optional; falls back to legacy tree_sitter_languages if installed)
  - Fallback: corrected regex with proper docstring + decorator handling

Returned object is intentionally simple:

    Structure(
        language: str,
        imports: list[str],
        signatures: list[Signature],
        body_lines: int,
    )

Each Signature carries enough context to render an outline AND to recover
the original implementation via an elision handle (line range).
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class Signature:
    kind: str
    name: str
    decorators: List[str] = field(default_factory=list)
    line: int = 0
    end_line: int = 0
    text: str = ""
    body_text: str = ""

    def render(self) -> str:
        parts: List[str] = []
        for d in self.decorators:
            parts.append(d)
        parts.append(self.text.rstrip())
        return "\n".join(parts)


@dataclass
class Structure:
    language: str
    imports: List[str] = field(default_factory=list)
    signatures: List[Signature] = field(default_factory=list)
    total_lines: int = 0
    body_lines: int = 0

    @property
    def import_count(self) -> int:
        return len(self.imports)

    @property
    def signature_count(self) -> int:
        return len(self.signatures)


def detect_language(content: str, hint: Optional[str] = None) -> str:
    if hint:
        h = hint.lower()
        if h.endswith((".py", ".pyi")) or h == "python":
            return "python"
        if h.endswith((".ts", ".tsx")) or h == "typescript":
            return "typescript"
        if h.endswith((".js", ".jsx", ".mjs", ".cjs")) or h == "javascript":
            return "javascript"
        if h.endswith(".go") or h == "go":
            return "go"
        if h.endswith(".rs") or h == "rust":
            return "rust"
        if h.endswith(".java") or h == "java":
            return "java"
        if h.endswith((".c", ".h")) or h == "c":
            return "c"
        if h.endswith((".cpp", ".hpp", ".cc", ".cxx")) or h == "cpp":
            return "cpp"
        if h.endswith((".json",)) or h == "json":
            return "json"
        if h.endswith((".yml", ".yaml")) or h == "yaml":
            return "yaml"
        if h.endswith(".md") or h == "markdown":
            return "markdown"
    head = content[:2000]
    if re.search(r"(^|\n)\s*(def |class |async def |from \w+ import|import \w+)", head):
        return "python"
    if re.search(r"(^|\n)\s*(interface |type \w+\s*=|enum )", head) and ("=>" in head or ": " in head):
        return "typescript"
    if re.search(r"(^|\n)\s*(function |const |let |var )", head):
        return "javascript"
    if re.search(r"(^|\n)\s*(package |func )", head) and "import (" in head + content:
        return "go"
    if re.search(r"(^|\n)\s*(fn |impl |pub fn |use \w+::)", head):
        return "rust"
    if re.search(r"(^|\n)\s*(public class|private class|package \w+;)", head):
        return "java"
    stripped = content.lstrip()
    if stripped.startswith(("{", "[")):
        return "json"
    if re.search(r"^\w+:\s+\S", content[:200], re.MULTILINE):
        return "yaml"
    if content.lstrip().startswith("#"):
        return "markdown"
    return "generic"


def extract(content: str, language: str) -> Structure:
    if language == "python":
        return _extract_python(content)
    ts = _extract_tree_sitter(content, language)
    if ts is not None:
        return ts
    return _extract_regex(content, language)


def _extract_python(content: str) -> Structure:
    lines = content.split("\n")
    struct = Structure(language="python", total_lines=len(lines))
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return _extract_regex(content, "python")

    body_lines = 0

    def render_signature(node: ast.AST) -> Tuple[str, int, int]:
        start = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", start) or start
        first_line = lines[start - 1] if 0 < start <= len(lines) else ""
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            text = first_line.rstrip()
            if not text.endswith(":"):
                for k in range(start, min(end, start + 5)):
                    text = lines[k - 1].rstrip()
                    if text.endswith(":"):
                        break
        else:
            text = first_line.rstrip()
        return text, start, end

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            line_no = getattr(node, "lineno", 1)
            if 0 < line_no <= len(lines):
                struct.imports.append(lines[line_no - 1].rstrip())
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            text, start, end = render_signature(node)
            decorators = []
            for dec in getattr(node, "decorator_list", []) or []:
                d_line = getattr(dec, "lineno", start)
                if 0 < d_line <= len(lines):
                    raw = lines[d_line - 1].rstrip()
                    if raw.lstrip().startswith("@"):
                        decorators.append(raw)
            kind = "class" if isinstance(node, ast.ClassDef) else "function"
            body_text = "\n".join(lines[start:end]) if end > start else ""
            struct.signatures.append(
                Signature(
                    kind=kind,
                    name=node.name,
                    decorators=decorators,
                    line=start,
                    end_line=end,
                    text=text,
                    body_text=body_text,
                )
            )
            if isinstance(node, ast.ClassDef):
                for inner in node.body:
                    if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        text2, s2, e2 = render_signature(inner)
                        decs = []
                        for dec in getattr(inner, "decorator_list", []) or []:
                            d_line = getattr(dec, "lineno", s2)
                            if 0 < d_line <= len(lines):
                                raw = lines[d_line - 1].rstrip()
                                if raw.lstrip().startswith("@"):
                                    decs.append(raw)
                        struct.signatures.append(
                            Signature(
                                kind=f"method:{node.name}",
                                name=inner.name,
                                decorators=decs,
                                line=s2,
                                end_line=e2,
                                text=text2,
                                body_text="\n".join(lines[s2:e2]) if e2 > s2 else "",
                            )
                        )
            body_lines += max(0, end - start)
    struct.body_lines = body_lines
    return struct


def _get_ts_parser(ts_name: str):
    """Return a tree-sitter parser for `ts_name`, or None if no backend installed.

    Tries packages in order:
      1. tree_sitter_language_pack (maintained, has Python 3.13 wheels)
      2. tree_sitter_languages (legacy, ≤Python 3.12)
    """
    try:
        from tree_sitter_language_pack import get_parser  # type: ignore
        return get_parser(ts_name)
    except Exception:
        pass
    try:
        from tree_sitter_languages import get_parser  # type: ignore
        return get_parser(ts_name)
    except Exception:
        return None


def _extract_tree_sitter(content: str, language: str) -> Optional[Structure]:
    ts_lang_map = {
        "javascript": "javascript",
        "typescript": "typescript",
        "go": "go",
        "rust": "rust",
        "java": "java",
        "c": "c",
        "cpp": "cpp",
    }
    ts_name = ts_lang_map.get(language)
    if not ts_name:
        return None
    parser = _get_ts_parser(ts_name)
    if parser is None:
        return None
    src = content.encode("utf-8")
    try:
        tree = parser.parse(src)
    except Exception:
        return None
    lines = content.split("\n")
    struct = Structure(language=language, total_lines=len(lines))

    SIG_TYPES = {
        "function_declaration", "function_definition", "method_definition",
        "class_declaration", "class_definition", "interface_declaration",
        "type_alias_declaration", "enum_declaration", "struct_item",
        "function_item", "impl_item", "trait_item", "method_declaration",
        "constructor_declaration", "arrow_function",
    }
    IMPORT_TYPES = {
        "import_statement", "import_declaration", "import_spec_list",
        "use_declaration", "preproc_include", "package_clause",
    }

    def name_of(node) -> str:
        for child in node.children:
            if child.type in ("identifier", "type_identifier", "field_identifier", "property_identifier"):
                return src[child.start_byte:child.end_byte].decode("utf-8", "replace")
        return ""

    def walk(node, depth: int = 0):
        if node.type in IMPORT_TYPES:
            line = src[node.start_byte:node.end_byte].decode("utf-8", "replace").split("\n")[0]
            struct.imports.append(line.strip())
            return
        if node.type in SIG_TYPES:
            start = node.start_point[0] + 1
            end = node.end_point[0] + 1
            head = lines[start - 1] if 0 < start <= len(lines) else ""
            kind = node.type.replace("_declaration", "").replace("_definition", "").replace("_item", "")
            struct.signatures.append(
                Signature(
                    kind=kind,
                    name=name_of(node) or "<anon>",
                    line=start,
                    end_line=end,
                    text=head.rstrip(),
                    body_text="\n".join(lines[start:end]) if end > start else "",
                )
            )
        for child in node.children:
            walk(child, depth + 1)

    walk(tree.root_node)
    struct.body_lines = sum(max(0, s.end_line - s.line) for s in struct.signatures)
    return struct


_SIG_RE = re.compile(
    r"^(?P<indent>\s*)(?P<kind>def\s+|class\s+|async\s+def\s+|function\s+|interface\s+|"
    r"type\s+|struct\s+|impl\s+|pub\s+fn\s+|fn\s+|func\s+|public\s+class\s+|"
    r"private\s+class\s+|public\s+\w+\s+\w+\s*\()(?P<rest>.+)$"
)
_IMPORT_RE = re.compile(
    r"^\s*(import\s+|from\s+\S+\s+import|#include\s|using\s|require\(|use\s+\w)"
)


def _extract_regex(content: str, language: str) -> Structure:
    """Fallback extractor with correct docstring + decorator handling."""
    lines = content.split("\n")
    struct = Structure(language=language, total_lines=len(lines))
    pending_decorators: List[str] = []
    in_triple = False
    triple_quote: Optional[str] = None

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if in_triple:
            if triple_quote and triple_quote in line:
                count = line.count(triple_quote)
                if count % 2 == 1:
                    in_triple = False
                    triple_quote = None
            continue
        for q in ('"""', "'''"):
            if q in stripped:
                idx = stripped.find(q)
                rest = stripped[idx + 3:]
                if q in rest:
                    pass
                else:
                    in_triple = True
                    triple_quote = q
                break
        if _IMPORT_RE.match(line):
            struct.imports.append(line.rstrip())
            continue
        if stripped.startswith("@"):
            pending_decorators.append(line.rstrip())
            continue
        m = _SIG_RE.match(line)
        if m:
            name_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*[\(\:\<\{]", m.group("rest"))
            name = name_match.group(1) if name_match else "<anon>"
            kind = m.group("kind").strip().rstrip(":")
            struct.signatures.append(
                Signature(
                    kind=kind,
                    name=name,
                    decorators=pending_decorators,
                    line=i,
                    end_line=i,
                    text=line.rstrip(),
                )
            )
            pending_decorators = []
        elif stripped and not in_triple:
            pending_decorators = []
    struct.body_lines = max(0, len(lines) - len(struct.signatures) - len(struct.imports))
    return struct
