"""ls / find compressors."""

from __future__ import annotations

import re
from typing import List, Optional

from snipp.compressors.base import BaseCompressor, CompressResult


_LS_LONG_RE = re.compile(
    r"^(?P<type>[dl\-])(?P<perms>[rwxstST\-]{9})[\s\+]+\S+\s+\S+\s+\S+\s+(?P<size>\d+)\s+\S+\s+\S+\s+\S+\s+(?P<name>.+)$"
)


class LsCompressor(BaseCompressor):
    def __init__(self, max_tokens: int = 1000, max_entries: int = 80, **kwargs):
        super().__init__(max_tokens=max_tokens, **kwargs)
        self.max_entries = max_entries

    def compress(self, output: str, query: Optional[str] = None) -> CompressResult:
        original_tokens = self.count(output)
        dirs: List[str] = []
        files: List[tuple] = []
        symlinks: List[str] = []
        total_line = ""
        for raw in output.split("\n"):
            stripped = raw.strip()
            if not stripped:
                continue
            if stripped.startswith("total "):
                total_line = stripped
                continue
            m = _LS_LONG_RE.match(raw)
            if m:
                t = m.group("type")
                name = m.group("name")
                if t == "d":
                    dirs.append(name)
                elif t == "l":
                    symlinks.append(name)
                else:
                    files.append((name, int(m.group("size"))))
            else:
                if stripped.endswith("/"):
                    dirs.append(stripped)
                else:
                    files.append((stripped, 0))

        parts = ["# Directory listing"]
        if total_line:
            parts.append(total_line)
        parts.append(f"Dirs: {len(dirs)}, Files: {len(files)}, Symlinks: {len(symlinks)}")
        if query:
            parts.append(f"Query: {query!r}")

        elision_handles: List[str] = []
        budget = self.max_entries

        if dirs:
            parts.append("\n## Directories")
            keep = dirs[:budget]
            parts.extend(f"  {d}/" for d in keep)
            if len(dirs) > budget:
                handle = self._elide("\n".join(dirs[budget:]), source="ls:dirs")
                if handle:
                    elision_handles.append(handle)
                    parts.append(f"  ... {len(dirs) - budget} more dirs: {handle}")

        if files:
            parts.append("\n## Files")
            keep_f = files[:budget]
            for name, size in keep_f:
                parts.append(f"  {name}  ({size}b)" if size else f"  {name}")
            if len(files) > budget:
                handle = self._elide(
                    "\n".join(f"{n} ({s}b)" for n, s in files[budget:]),
                    source="ls:files",
                )
                if handle:
                    elision_handles.append(handle)
                    parts.append(f"  ... {len(files) - budget} more files: {handle}")

        if symlinks:
            parts.append("\n## Symlinks")
            parts.extend(f"  {s}" for s in symlinks[:20])

        compressed = "\n".join(parts)
        compressed = self._truncate_to_tokens(compressed)

        fidelity = {
            "dirs_total": len(dirs),
            "dirs_preserved": min(len(dirs), budget),
            "files_total": len(files),
            "files_preserved": min(len(files), budget),
            "symlinks_total": len(symlinks),
        }
        return CompressResult(
            compressed=compressed,
            original_tokens=original_tokens,
            compressed_tokens=self.count(compressed),
            tool_type="ls",
            strategy="group_by_type+elide_overflow",
            tokenizer=self.tokenizer.name,
            exact_tokens=self.tokenizer.exact,
            fidelity=fidelity,
            elision_handles=elision_handles,
        )


class FindCompressor(BaseCompressor):
    def __init__(self, max_tokens: int = 1500, max_dirs: int = 30, **kwargs):
        super().__init__(max_tokens=max_tokens, **kwargs)
        self.max_dirs = max_dirs

    def compress(self, output: str, query: Optional[str] = None) -> CompressResult:
        original_tokens = self.count(output)
        lines = [ln.strip() for ln in output.split("\n") if ln.strip()]
        dirs: dict = {}
        for ln in lines:
            d = ln.rsplit("/", 1)[0] if "/" in ln else "."
            dirs.setdefault(d, []).append(ln)

        parts = [
            "# find results",
            f"Total: {len(lines)} entries across {len(dirs)} dirs",
        ]
        if query:
            parts.append(f"Query: {query!r}")
        parts.append("")

        elision_handles: List[str] = []
        for d, items in sorted(dirs.items())[: self.max_dirs]:
            parts.append(f"## {d}/  ({len(items)})")
            for it in items[:10]:
                parts.append(f"  {it}")
            if len(items) > 10:
                handle = self._elide("\n".join(items[10:]), source=f"find:{d}")
                if handle:
                    elision_handles.append(handle)
                    parts.append(f"  ... {len(items) - 10} more: {handle}")

        if len(dirs) > self.max_dirs:
            parts.append(f"\n... {len(dirs) - self.max_dirs} more directories")

        compressed = "\n".join(parts)
        compressed = self._truncate_to_tokens(compressed)

        fidelity = {
            "entries_total": len(lines),
            "dirs_total": len(dirs),
            "dirs_preserved": min(len(dirs), self.max_dirs),
        }
        return CompressResult(
            compressed=compressed,
            original_tokens=original_tokens,
            compressed_tokens=self.count(compressed),
            tool_type="find",
            strategy="dir_grouping+elide_overflow",
            tokenizer=self.tokenizer.name,
            exact_tokens=self.tokenizer.exact,
            fidelity=fidelity,
            elision_handles=elision_handles,
        )
