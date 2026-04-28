"""Cat/head/tail compressor backed by AST/tree-sitter structure extraction."""

from __future__ import annotations

from typing import Optional, List

from snipp.compressors.base import BaseCompressor, CompressResult
from snipp.structure import detect_language, extract, Signature
from snipp.ranking import rank_chunks


CODE_LANGS = {"python", "javascript", "typescript", "go", "rust", "java", "c", "cpp"}


class CatCompressor(BaseCompressor):
    """Compress file contents using structure extraction.

    For code: keep imports + signatures, elide bodies via expansion handles.
    For data: keep top-level structure, elide arrays/objects.
    For markdown: keep headings + first line of each section.
    """

    def __init__(
        self,
        max_tokens: int = 3000,
        show_signatures: bool = True,
        keep_signatures_only: bool = False,
        **kwargs,
    ):
        super().__init__(max_tokens=max_tokens, **kwargs)
        self.show_signatures = show_signatures
        self.keep_signatures_only = keep_signatures_only

    def compress(self, output: str, query: Optional[str] = None) -> CompressResult:
        original_tokens = self.count(output)
        language = detect_language(output)

        if language in CODE_LANGS:
            compressed, fidelity, handles = self._compress_code(output, language, query)
            strategy = f"{language}_structure_extraction"
        elif language in ("json", "yaml"):
            compressed, fidelity, handles = self._compress_data(output, language)
            strategy = f"{language}_structural_truncation"
        elif language == "markdown":
            compressed, fidelity, handles = self._compress_markdown(output)
            strategy = "markdown_outline"
        else:
            compressed, fidelity, handles = self._compress_generic(output)
            strategy = "head_tail_truncation"

        compressed = self._truncate_to_tokens(compressed)
        return CompressResult(
            compressed=compressed,
            original_tokens=original_tokens,
            compressed_tokens=self.count(compressed),
            tool_type="cat",
            strategy=strategy,
            tokenizer=self.tokenizer.name,
            exact_tokens=self.tokenizer.exact,
            fidelity=fidelity,
            elision_handles=handles,
        )

    def _compress_code(self, content: str, language: str, query: Optional[str]):
        struct = extract(content, language)
        handles: List[str] = []
        parts: List[str] = [f"# File ({language}) — {struct.total_lines} lines"]

        if query:
            parts.append(f"# Query: {query!r}")

        if struct.imports:
            parts.append(f"\n## Imports ({len(struct.imports)})")
            for imp in struct.imports[:30]:
                parts.append(imp)
            if len(struct.imports) > 30:
                handle = self._elide(
                    "\n".join(struct.imports[30:]), source="imports_overflow"
                )
                if handle:
                    handles.append(handle)
                    parts.append(handle)

        ranked: List[Signature] = struct.signatures
        relevant_idx: set = set()
        if query and struct.signatures:
            scored = rank_chunks(
                [s.text + "\n" + s.body_text for s in struct.signatures],
                query,
            )
            relevant_idx = {i for i, score in scored if score > 0}

        if struct.signatures:
            parts.append(f"\n## Structure ({len(struct.signatures)} definitions)")
            for i, sig in enumerate(struct.signatures):
                parts.append(sig.render())
                show_body = (
                    not self.keep_signatures_only
                    and sig.body_text
                    and (not relevant_idx or i in relevant_idx)
                )
                if sig.body_text and not self.keep_signatures_only:
                    handle = self._elide(
                        sig.body_text, source=f"{sig.kind}:{sig.name}"
                    )
                    if handle:
                        handles.append(handle)
                        marker = handle if show_body else f"  {handle}"
                        parts.append(f"  {handle}")

        fidelity = {
            "imports_preserved": min(len(struct.imports), 30),
            "imports_total": len(struct.imports),
            "signatures_preserved": len(struct.signatures),
            "signatures_total": len(struct.signatures),
            "relevant_signatures": len(relevant_idx),
            "language": language,
        }
        return "\n".join(parts), fidelity, handles

    def _compress_data(self, content: str, lang: str):
        lines = content.split("\n")
        parts: List[str] = [f"# {lang.upper()} — {len(lines)} lines"]
        keep = lines[: min(len(lines), max(40, self.max_tokens // 8))]
        parts.extend(keep)
        handles: List[str] = []
        if len(lines) > len(keep):
            handle = self._elide("\n".join(lines[len(keep):]), source=f"{lang}_tail")
            if handle:
                handles.append(handle)
                parts.append(handle)
        fidelity = {"top_lines_kept": len(keep), "total_lines": len(lines)}
        return "\n".join(parts), fidelity, handles

    def _compress_markdown(self, content: str):
        lines = content.split("\n")
        parts: List[str] = []
        last_was_heading = False
        kept_paragraphs = 0
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                parts.append(line)
                last_was_heading = True
            elif last_was_heading and stripped:
                parts.append(line)
                kept_paragraphs += 1
                last_was_heading = False
        fidelity = {
            "headings_preserved": sum(1 for p in parts if p.lstrip().startswith("#")),
            "first_lines_preserved": kept_paragraphs,
            "total_lines": len(lines),
        }
        return "\n".join(parts), fidelity, []

    def _compress_generic(self, content: str):
        lines = content.split("\n")
        if len(lines) < 50:
            return content, {"total_lines": len(lines), "kept_lines": len(lines)}, []
        head = lines[:30]
        tail = lines[-10:]
        handle = self._elide("\n".join(lines[30:-10]), source="generic_middle")
        handles = [handle] if handle else []
        parts = [f"# File (generic) — {len(lines)} lines"]
        parts.extend(head)
        if handle:
            parts.append(handle)
        parts.extend(tail)
        fidelity = {
            "total_lines": len(lines),
            "kept_lines": len(head) + len(tail),
            "elided_lines": len(lines) - len(head) - len(tail),
        }
        return "\n".join(parts), fidelity, handles
