"""Generic fallback compressor."""

from __future__ import annotations

from typing import List, Optional

from snipp.compressors.base import BaseCompressor, CompressResult


class GenericCompressor(BaseCompressor):
    def compress(self, output: str, query: Optional[str] = None) -> CompressResult:
        original_tokens = self.count(output)
        lines = output.split("\n")
        elision_handles: List[str] = []
        if len(lines) < 30:
            compressed = output
            fidelity = {"total_lines": len(lines), "kept_lines": len(lines)}
        else:
            head = lines[:20]
            tail = lines[-10:]
            handle = self._elide("\n".join(lines[20:-10]), source="generic:middle")
            if handle:
                elision_handles.append(handle)
            parts: List[str] = ["# Compressed output"]
            if query:
                parts.append(f"Query: {query!r}")
            parts.append(f"Original: {len(lines)} lines, ~{original_tokens} tokens")
            parts.append("")
            parts.extend(head)
            if handle:
                parts.append(handle)
            parts.extend(tail)
            compressed = "\n".join(parts)
            fidelity = {
                "total_lines": len(lines),
                "kept_lines": len(head) + len(tail),
                "elided_lines": max(0, len(lines) - len(head) - len(tail)),
            }

        compressed = self._truncate_to_tokens(compressed)
        return CompressResult(
            compressed=compressed,
            original_tokens=original_tokens,
            compressed_tokens=self.count(compressed),
            tool_type="generic",
            strategy="head_tail_truncation",
            tokenizer=self.tokenizer.name,
            exact_tokens=self.tokenizer.exact,
            fidelity=fidelity,
            elision_handles=elision_handles,
        )
