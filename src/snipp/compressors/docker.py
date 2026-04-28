"""Docker compressor with real truncation and section parsing."""

from __future__ import annotations

import re
from typing import List, Optional

from snipp.compressors.base import BaseCompressor, CompressResult


_STEP_RE = re.compile(r"^(?:Step\s+\d+/\d+\s+:|#\d+\s+\[)\s*(.*)$")
_ERROR_RE = re.compile(r"\b(ERROR|error:|failed|Cannot|fatal:)\b", re.IGNORECASE)


class DockerCompressor(BaseCompressor):
    def __init__(self, max_tokens: int = 2000, max_containers: int = 30, **kwargs):
        super().__init__(max_tokens=max_tokens, **kwargs)
        self.max_containers = max_containers

    def compress(self, output: str, query: Optional[str] = None) -> CompressResult:
        original_tokens = self.count(output)
        lines = output.split("\n")
        head = "\n".join(lines[:5])
        if "CONTAINER ID" in head and "IMAGE" in head:
            return self._compress_ps(output, query, original_tokens)
        if any(_STEP_RE.match(ln) for ln in lines[:50]) or "Building" in head:
            return self._compress_build(output, query, original_tokens)
        return self._compress_logs(output, query, original_tokens)

    def _compress_ps(self, output: str, query: Optional[str], original_tokens: int) -> CompressResult:
        lines = output.split("\n")
        header = lines[0] if lines else ""
        rows = [ln for ln in lines[1:] if ln.strip()]
        parts = ["# Docker containers", header]
        keep = rows[: self.max_containers]
        parts.extend(keep)
        elision_handles: List[str] = []
        if len(rows) > self.max_containers:
            handle = self._elide("\n".join(rows[self.max_containers:]), source="docker:ps")
            if handle:
                elision_handles.append(handle)
                parts.append(f"... {len(rows) - self.max_containers} more containers: {handle}")
        compressed = "\n".join(parts)
        compressed = self._truncate_to_tokens(compressed)
        fidelity = {"containers_total": len(rows), "containers_preserved": len(keep)}
        return CompressResult(
            compressed=compressed,
            original_tokens=original_tokens,
            compressed_tokens=self.count(compressed),
            tool_type="docker",
            strategy="ps_truncate",
            tokenizer=self.tokenizer.name,
            exact_tokens=self.tokenizer.exact,
            fidelity=fidelity,
            elision_handles=elision_handles,
        )

    def _compress_build(self, output: str, query: Optional[str], original_tokens: int) -> CompressResult:
        lines = output.split("\n")
        steps: List[str] = []
        errors: List[str] = []
        for ln in lines:
            m = _STEP_RE.match(ln)
            if m:
                steps.append(ln.rstrip())
            elif _ERROR_RE.search(ln):
                errors.append(ln.rstrip())
        parts = ["# Docker build", f"Total steps: {len(steps)}, errors: {len(errors)}"]
        if query:
            parts.append(f"Query: {query!r}")
        if steps:
            parts.append("\n## Last 10 steps")
            parts.extend(steps[-10:])
        if errors:
            parts.append(f"\n## Errors ({len(errors)})")
            parts.extend(errors[:30])
        else:
            parts.append("\nNo errors detected.")
        elision_handles: List[str] = []
        full_handle = self._elide(output, source="docker:build_full")
        if full_handle:
            elision_handles.append(full_handle)
            parts.append(f"\n# Full build log: {full_handle}")
        compressed = "\n".join(parts)
        compressed = self._truncate_to_tokens(compressed)
        fidelity = {"steps": len(steps), "errors": len(errors)}
        return CompressResult(
            compressed=compressed,
            original_tokens=original_tokens,
            compressed_tokens=self.count(compressed),
            tool_type="docker",
            strategy="build_steps+errors",
            tokenizer=self.tokenizer.name,
            exact_tokens=self.tokenizer.exact,
            fidelity=fidelity,
            elision_handles=elision_handles,
        )

    def _compress_logs(self, output: str, query: Optional[str], original_tokens: int) -> CompressResult:
        lines = output.split("\n")
        parts = ["# Docker logs", f"Lines: {len(lines)}"]
        if query:
            parts.append(f"Query: {query!r}")
        keep = lines[-100:]
        parts.append("\n## Tail (last 100 lines)")
        parts.extend(keep)
        elision_handles: List[str] = []
        if len(lines) > 100:
            handle = self._elide("\n".join(lines[:-100]), source="docker:logs_head")
            if handle:
                elision_handles.append(handle)
                parts.append(f"\n# Earlier logs: {handle}")
        compressed = "\n".join(parts)
        compressed = self._truncate_to_tokens(compressed)
        fidelity = {"lines_total": len(lines), "lines_preserved": min(100, len(lines))}
        return CompressResult(
            compressed=compressed,
            original_tokens=original_tokens,
            compressed_tokens=self.count(compressed),
            tool_type="docker",
            strategy="tail_with_handle",
            tokenizer=self.tokenizer.name,
            exact_tokens=self.tokenizer.exact,
            fidelity=fidelity,
            elision_handles=elision_handles,
        )
