"""Grep-style output compressor (works for grep, rg, ag, ack)."""

from __future__ import annotations

import re
from collections import OrderedDict
from typing import List, Optional

from snipp.compressors.base import BaseCompressor, CompressResult
from snipp.ranking import rank_chunks


_MATCH_RE = re.compile(r"^(?P<file>[^:\n]+):(?P<line>\d+):(?P<text>.*)$")
_BINARY_RE = re.compile(r"^Binary file.*matches\s*$")


class GrepCompressor(BaseCompressor):
    def __init__(
        self,
        max_tokens: int = 2000,
        context_lines: int = 2,
        max_files: int = 50,
        max_matches_per_file: int = 20,
        **kwargs,
    ):
        super().__init__(max_tokens=max_tokens, **kwargs)
        self.context_lines = context_lines
        self.max_files = max_files
        self.max_matches_per_file = max_matches_per_file

    def compress(self, output: str, query: Optional[str] = None) -> CompressResult:
        original_tokens = self.count(output)

        per_file: "OrderedDict[str, List[tuple]]" = OrderedDict()
        binary_count = 0
        total_matches = 0

        for line in output.split("\n"):
            if not line:
                continue
            if _BINARY_RE.match(line):
                binary_count += 1
                continue
            m = _MATCH_RE.match(line)
            if m:
                f = m.group("file")
                per_file.setdefault(f, []).append((int(m.group("line")), m.group("text")))
                total_matches += 1

        files = list(per_file.keys())
        if query and len(files) > 1:
            chunks = ["\n".join(f"{ln}: {tx}" for ln, tx in per_file[f]) for f in files]
            ranked = rank_chunks(chunks, query)
            order = [files[i] for i, _ in ranked if per_file[files[i]]]
            order = order or files
        else:
            order = files

        parts: List[str] = []
        parts.append(f"# grep results ({total_matches} matches in {len(per_file)} files)")
        if query:
            parts.append(f"# Query: {query!r}")
        if binary_count:
            parts.append(f"# Skipped: {binary_count} binary file(s)")
        parts.append("")

        shown_matches = 0
        shown_files = 0
        elision_handles: List[str] = []
        for f in order[: self.max_files]:
            matches = per_file[f]
            parts.append(f"## {f}  ({len(matches)} matches)")
            for line_no, text in matches[: self.max_matches_per_file]:
                parts.append(f"{line_no}: {text}")
                shown_matches += 1
            if len(matches) > self.max_matches_per_file:
                rest = matches[self.max_matches_per_file:]
                handle = self._elide(
                    "\n".join(f"{ln}: {tx}" for ln, tx in rest),
                    source=f"grep:{f}",
                )
                if handle:
                    elision_handles.append(handle)
                    parts.append(handle)
            parts.append("")
            shown_files += 1

        if len(per_file) > self.max_files:
            rest_files = order[self.max_files:]
            rest_text = "\n".join(
                f"{f}\n" + "\n".join(f"{ln}: {tx}" for ln, tx in per_file[f])
                for f in rest_files
            )
            handle = self._elide(rest_text, source="grep:overflow_files")
            if handle:
                elision_handles.append(handle)
                parts.append(f"... {len(rest_files)} more files: {handle}")

        compressed = "\n".join(parts)
        compressed = self._truncate_to_tokens(compressed)

        fidelity = {
            "matches_total": total_matches,
            "matches_preserved": shown_matches,
            "files_total": len(per_file),
            "files_preserved": shown_files,
            "binary_files_skipped": binary_count,
        }

        return CompressResult(
            compressed=compressed,
            original_tokens=original_tokens,
            compressed_tokens=self.count(compressed),
            tool_type="grep",
            strategy="match_dedup+file_grouping+bm25",
            tokenizer=self.tokenizer.name,
            exact_tokens=self.tokenizer.exact,
            fidelity=fidelity,
            elision_handles=elision_handles,
        )
