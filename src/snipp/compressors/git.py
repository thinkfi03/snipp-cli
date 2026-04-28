"""Git log/diff/status compressors with proper parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from snipp.compressors.base import BaseCompressor, CompressResult


_COMMIT_HEAD_RE = re.compile(r"^commit\s+([a-f0-9]{7,40})(?:\s+\(.*\))?$")
_AUTHOR_RE = re.compile(r"^Author:\s+(.+)$")
_DATE_RE = re.compile(r"^Date:\s+(.+)$")


@dataclass
class _Commit:
    hash: str
    author: str = ""
    date: str = ""
    subject: str = ""
    body: List[str] = field(default_factory=list)


class GitLogCompressor(BaseCompressor):
    def __init__(
        self,
        max_tokens: int = 1500,
        max_commits: int = 20,
        show_diffs: bool = False,
        **kwargs,
    ):
        super().__init__(max_tokens=max_tokens, **kwargs)
        self.max_commits = max_commits
        self.show_diffs = show_diffs

    def compress(self, output: str, query: Optional[str] = None) -> CompressResult:
        original_tokens = self.count(output)
        commits = self._parse(output)

        parts = ["# git log summary", f"Total commits: {len(commits)}"]
        if query:
            parts.append(f"Query: {query!r}")
        parts.append("")

        for c in commits[: self.max_commits]:
            parts.append(f"{c.hash[:12]}  {c.date:<24}  {c.author}")
            if c.subject:
                parts.append(f"  {c.subject}")
            if c.body:
                first = c.body[0].strip()
                if first:
                    parts.append(f"  > {first[:120]}")
            parts.append("")

        elision_handles: List[str] = []
        if len(commits) > self.max_commits:
            rest = commits[self.max_commits:]
            text = "\n".join(
                f"{c.hash[:12]} {c.date} {c.author} {c.subject}" for c in rest
            )
            handle = self._elide(text, source="git_log:older_commits")
            if handle:
                elision_handles.append(handle)
                parts.append(f"... {len(rest)} older commits: {handle}")

        compressed = "\n".join(parts)
        compressed = self._truncate_to_tokens(compressed)

        fidelity = {
            "commits_total": len(commits),
            "commits_preserved": min(len(commits), self.max_commits),
        }
        return CompressResult(
            compressed=compressed,
            original_tokens=original_tokens,
            compressed_tokens=self.count(compressed),
            tool_type="git_log",
            strategy="commit_compact+limit",
            tokenizer=self.tokenizer.name,
            exact_tokens=self.tokenizer.exact,
            fidelity=fidelity,
            elision_handles=elision_handles,
        )

    def _parse(self, output: str) -> List[_Commit]:
        commits: List[_Commit] = []
        current: Optional[_Commit] = None
        for raw in output.split("\n"):
            m = _COMMIT_HEAD_RE.match(raw)
            if m:
                if current:
                    commits.append(current)
                current = _Commit(hash=m.group(1))
                continue
            if not current:
                continue
            am = _AUTHOR_RE.match(raw)
            if am:
                current.author = am.group(1)[:40]
                continue
            dm = _DATE_RE.match(raw)
            if dm:
                current.date = dm.group(1)[:24]
                continue
            if raw.startswith("    "):
                if not current.subject:
                    current.subject = raw.strip()[:100]
                else:
                    current.body.append(raw[4:])
                continue
        if current:
            commits.append(current)
        return commits


class GitDiffCompressor(BaseCompressor):
    def __init__(self, max_tokens: int = 1500, max_files: int = 20, **kwargs):
        super().__init__(max_tokens=max_tokens, **kwargs)
        self.max_files = max_files

    def compress(self, output: str, query: Optional[str] = None) -> CompressResult:
        original_tokens = self.count(output)
        files = re.findall(r"^diff --git a/(.+?) b/", output, re.MULTILINE)
        additions = sum(
            1 for ln in output.split("\n") if ln.startswith("+") and not ln.startswith("+++")
        )
        deletions = sum(
            1 for ln in output.split("\n") if ln.startswith("-") and not ln.startswith("---")
        )

        parts = [
            "# git diff summary",
            f"Files: {len(files)}, +{additions} / -{deletions}",
        ]
        if query:
            parts.append(f"Query: {query!r}")
        parts.append("")

        for f in files[: self.max_files]:
            parts.append(f"  {f}")
        if len(files) > self.max_files:
            parts.append(f"  ... and {len(files) - self.max_files} more files")

        elision_handles: List[str] = []
        if files:
            first = files[0]
            pat = re.compile(
                r"^diff --git a/" + re.escape(first) + r".*?(?=^diff --git|\Z)",
                re.MULTILINE | re.DOTALL,
            )
            m = pat.search(output)
            if m:
                sample = m.group(0)
                budget = max(self.max_tokens // 3, 200)
                trimmed = self._truncate_to_tokens(sample, max_tokens=budget)
                parts.append(f"\n## Sample diff: {first}")
                parts.append(trimmed)

        full_handle = self._elide(output, source="git_diff:full")
        if full_handle:
            elision_handles.append(full_handle)
            parts.append(f"\n# Full diff available via: {full_handle}")

        compressed = "\n".join(parts)
        compressed = self._truncate_to_tokens(compressed)

        fidelity = {
            "files_total": len(files),
            "files_preserved": min(len(files), self.max_files),
            "additions": additions,
            "deletions": deletions,
        }
        return CompressResult(
            compressed=compressed,
            original_tokens=original_tokens,
            compressed_tokens=self.count(compressed),
            tool_type="git_diff",
            strategy="stats+sample_diff+full_handle",
            tokenizer=self.tokenizer.name,
            exact_tokens=self.tokenizer.exact,
            fidelity=fidelity,
            elision_handles=elision_handles,
        )


class GitStatusCompressor(BaseCompressor):
    def __init__(self, max_tokens: int = 800, **kwargs):
        super().__init__(max_tokens=max_tokens, **kwargs)

    def compress(self, output: str, query: Optional[str] = None) -> CompressResult:
        original_tokens = self.count(output)
        staged: List[str] = []
        unstaged: List[str] = []
        untracked: List[str] = []
        section: Optional[str] = None
        for line in output.split("\n"):
            stripped = line.strip()
            if "Changes to be committed" in line:
                section = "staged"
                continue
            if "Changes not staged" in line:
                section = "unstaged"
                continue
            if "Untracked files" in line:
                section = "untracked"
                continue
            if not stripped or stripped.startswith("(") or stripped.startswith("On branch"):
                continue
            entry = stripped
            if section == "staged" and entry.startswith(
                ("modified:", "new file:", "deleted:", "renamed:")
            ):
                staged.append(entry)
            elif section == "unstaged" and entry.startswith(
                ("modified:", "deleted:", "renamed:")
            ):
                unstaged.append(entry)
            elif section == "untracked":
                untracked.append(entry)

        parts = ["# git status"]
        if staged:
            parts.append(f"\nStaged ({len(staged)}):")
            parts.extend(f"  + {s}" for s in staged)
        if unstaged:
            parts.append(f"\nUnstaged ({len(unstaged)}):")
            parts.extend(f"  ~ {s}" for s in unstaged)
        if untracked:
            parts.append(f"\nUntracked ({len(untracked)}):")
            parts.extend(f"  ? {u}" for u in untracked[:20])
            if len(untracked) > 20:
                parts.append(f"  ... and {len(untracked) - 20} more")
        compressed = "\n".join(parts)

        fidelity = {
            "staged": len(staged),
            "unstaged": len(unstaged),
            "untracked": len(untracked),
        }
        return CompressResult(
            compressed=compressed,
            original_tokens=original_tokens,
            compressed_tokens=self.count(compressed),
            tool_type="git_status",
            strategy="section_grouping",
            tokenizer=self.tokenizer.name,
            exact_tokens=self.tokenizer.exact,
            fidelity=fidelity,
        )
