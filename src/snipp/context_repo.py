"""Repo exploration: structured codebase context for agents.

Two public functions:
  - explore_repo(root, query, max_tokens) → ranked file tree + key symbols
  - show_symbol(root, symbol_name) → signature + 5-line body context

Reuses structure.py for AST extraction and ranking.py for BM25 relevance.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import List, Optional, Tuple

from snipp.structure import detect_language, extract, Structure
from snipp.ranking import rank_chunks
from snipp.tokenizer import count_tokens

# Extensions we can parse
_SOURCE_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs",
    ".ts", ".tsx", ".go", ".rs", ".java",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx",
}

# Directories to skip
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__",
    ".venv", "venv", ".env", "env", "dist", "build",
    ".tox", ".pytest_cache", ".mypy_cache", ".egg-info",
    "target", ".gitignore", ".DS_Store",
}


def _should_skip_dir(name: str) -> bool:
    return name in _SKIP_DIRS or name.startswith(".")


def _should_skip_file(name: str) -> bool:
    return name.startswith(".") or Path(name).suffix not in _SOURCE_EXTS


def _walk_repo(root: Path) -> List[Path]:
    """Yield source file paths under root, skipping irrelevant dirs."""
    files: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Modify dirnames in-place to prune
        dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
        for fname in filenames:
            if not _should_skip_file(fname):
                files.append(Path(dirpath) / fname)
    files.sort()
    return files


def _extract_symbols(root: Path, file_path: Path) -> List[Tuple[Path, str, str, str]]:
    """Extract symbols from a single file.

    Returns list of (relative_path, kind, name, chunk_text).
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []
    if len(content) > 500_000:  # Skip huge files
        return []

    lang = detect_language(content, hint=file_path.name)
    struct = extract(content, lang)
    rel = file_path.relative_to(root)
    results: List[Tuple[Path, str, str, str]] = []
    for sig in struct.signatures:
        chunk = f"{rel} {sig.kind} {sig.name}\n{sig.render()}"
        results.append((rel, sig.kind, sig.name, chunk))
    return results


def explore_repo(
    root: str,
    query: Optional[str] = None,
    max_tokens: int = 2000,
) -> str:
    """Return a ranked file tree + key symbols for a codebase, capped at max_tokens.

    Args:
        root: Path to the codebase root.
        query: Optional natural-language query to rank symbols by relevance.
        max_tokens: Hard token budget for the returned document.
    """
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise ValueError(f"Not a directory: {root}")

    files = _walk_repo(root_path)

    # Build file tree (compact)
    tree_lines = ["# File Tree", ""]
    for f in files:
        tree_lines.append(str(f.relative_to(root_path)))
    tree_text = "\n".join(tree_lines)

    # Extract symbols from all files
    all_symbols: List[Tuple[Path, str, str, str]] = []
    for f in files:
        all_symbols.extend(_extract_symbols(root_path, f))

    # Rank by query if provided
    if query and all_symbols:
        chunks = [chunk for _, _, _, chunk in all_symbols]
        ranked = rank_chunks(chunks, query)
        # Reorder symbols by rank
        ordered = []
        for idx, _ in ranked:
            ordered.append(all_symbols[idx])
        all_symbols = ordered

    # Build document incrementally with signatures, capping at max_tokens
    doc_lines = list(tree_lines)
    if all_symbols:
        doc_lines.extend(["", "# Key Symbols", ""])
        for rel, kind, name, chunk in all_symbols:
            candidate = doc_lines + [f"### {name}  ({kind} in {rel})", chunk, ""]
            text = "\n".join(candidate)
            if count_tokens(text) > max_tokens:
                break
            doc_lines = candidate

    return "\n".join(doc_lines)





def show_symbol(root: str, symbol_name: str) -> Optional[str]:
    """Find a named symbol in the repo and return its signature + 5-line context.

    Args:
        root: Path to the codebase root.
        symbol_name: Name of the class/function/method to find.

    Returns:
        Formatted string with signature and first 5 lines of body, or None.
    """
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise ValueError(f"Not a directory: {root}")

    target = symbol_name.strip()
    for f in _walk_repo(root_path):
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue
        if len(content) > 500_000:
            continue

        lang = detect_language(content, hint=f.name)
        struct = extract(content, lang)
        for sig in struct.signatures:
            if sig.name == target:
                rel = f.relative_to(root_path)
                lines = [f"# {sig.name}  ({sig.kind} in {rel})", ""]
                lines.append(sig.render())
                lines.append("")
                # 5-line body context
                body = sig.body_text.strip()
                if body:
                    body_lines = body.split("\n")[:5]
                    lines.append("## Body (first 5 lines)")
                    lines.extend(body_lines)
                return "\n".join(lines)
    return None
