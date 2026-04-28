"""Compressor for ESLint, TypeScript compiler (tsc), and similar lint output.

Handles:
  - eslint (stylish, compact, json, unix formatters)
  - tsc --noEmit / tsc --watch
  - prettier --check
  - stylelint, etc.

Strategy:
  - Deduplicate errors by (file, line, message)
  - Keep one representative context line per error
  - Collapse "clean" files to a count
  - Keep summary counts (errors, warnings, files)
"""

from __future__ import annotations

import re
from typing import Optional, List, Dict, Tuple

from snipp.compressors.base import BaseCompressor, CompressResult

# tsc format — check BEFORE eslint file detection since tsc lines also contain file paths.
# Allow .ts, .tsx, .mts, .cts, .d.ts, .d.mts, .d.cts (kimik2.6 review bug #5).
_TSC_ERROR = re.compile(
    r"^(?P<file>.+?\.(?:ts|tsx|mts|cts|d\.ts|d\.mts|d\.cts))"
    r"\((?P<line>\d+),(?P<col>\d+)\):\s+"
    r"(?P<sev>error|warning)\s+TS(?P<code>\d+):\s*(?P<msg>.+)$"
)

# ESLint stylish format
_ESLINT_FILE = re.compile(r"^\s*(/|\.|\w+[/\\]).*\.(js|jsx|ts|tsx|mjs|cjs|vue|svelte|astro)")
_ESLINT_ERROR = re.compile(r"^\s*(\d+):(\d+)\s+(error|warning)\s+(.+?)(?:\s{2,}(\S+))?$")
_ESLINT_SUMMARY = re.compile(r"^\s*(\d+)\s+(error|warning)s?\s*$")
# kimik2.6 review bug #6: ESLint emits `✖ N problems (...)` — accept ✖/✕/× prefix.
_ESLINT_PROBLEM = re.compile(r"^\s*[✖✕×]?\s*\d+\s+problem", re.IGNORECASE)

# prettier
_PRETIER_ERROR = re.compile(r"^\[warn\]\s+(.+?)(\s+Code style issue|\s+is not formatted)")

# Generic lint
_LINT_ERROR = re.compile(r"(error|ERR|✖|✕)\s*[:\-]?\s*(.+)", re.IGNORECASE)
_LINT_WARNING = re.compile(r"(warning|WARN|⚠)\s*[:\-]?\s*(.+)", re.IGNORECASE)

# Clean file notices
_CLEAN_RE = re.compile(r"^\s*✔|clean|passed|ok\s*$", re.IGNORECASE)


class EslintCompressor(BaseCompressor):
    """Compress ESLint / tsc / prettier output."""

    def __init__(self, max_tokens: int = 2000, **kwargs):
        super().__init__(max_tokens=max_tokens, **kwargs)

    def compress(self, output: str, query: Optional[str] = None) -> CompressResult:
        original_tokens = self.count(output)
        lines = output.split("\n")

        errors: Dict[Tuple[str, int, str], List[str]] = {}
        warnings: Dict[Tuple[str, int, str], List[str]] = {}
        clean_files = 0
        current_file = ""
        summary_lines: List[str] = []

        for raw_line in lines:
            clean = self.strip_ansi(raw_line).rstrip()
            if not clean:
                continue

            # Check tsc errors FIRST (before eslint file detection)
            m = _TSC_ERROR.match(clean)
            if m:
                current_file = m.group(1).strip()
                line_no = int(m.group(2))
                severity = m.group(4).lower()
                code = m.group(5)
                message = m.group(6).strip()
                key = (current_file, line_no, f"TS{code}: {message}")
                if severity == "error":
                    errors.setdefault(key, []).append(raw_line)
                else:
                    warnings.setdefault(key, []).append(raw_line)
                continue

            # Detect current file (ESLint stylish)
            if _ESLINT_FILE.match(clean) and not _ESLINT_ERROR.match(clean):
                current_file = clean.strip()
                continue

            # ESLint error/warning line
            m = _ESLINT_ERROR.match(clean)
            if m:
                line_no = int(m.group(1))
                severity = m.group(3).lower()
                message = m.group(4).strip()
                rule = m.group(5) or ""
                full_message = f"{message}  {rule}" if rule else message
                key = (current_file, line_no, full_message)
                if severity == "error":
                    errors.setdefault(key, []).append(raw_line)
                else:
                    warnings.setdefault(key, []).append(raw_line)
                continue

            # Summary lines
            if _ESLINT_SUMMARY.match(clean) or _ESLINT_PROBLEM.match(clean):
                summary_lines.append(raw_line)
                continue
            if "error" in clean.lower() and "warning" in clean.lower() and re.search(r"\d+", clean):
                summary_lines.append(raw_line)
                continue

            # Clean files
            if _CLEAN_RE.match(clean):
                clean_files += 1
                continue

        # Build output
        parts: List[str] = ["# Lint / Type Check Results"]

        if query:
            parts.append(f"Query: {query!r}")

        total_errors = len(errors)
        total_warnings = len(warnings)
        total_files_with_issues = len({k[0] for k in list(errors.keys()) + list(warnings.keys())})

        parts.append(
            f"Files: {total_files_with_issues} with issues, {clean_files} clean"
        )
        parts.append(f"Errors: {total_errors}, Warnings: {total_warnings}")

        # Show errors
        if errors:
            parts.append(f"\n## Errors ({total_errors})")
            shown = 0
            for (file, line, msg), lines_list in sorted(errors.items()):
                parts.append(f"  {file}:{line}")
                parts.append(f"    {msg}")
                shown += 1
                if shown >= 25:
                    parts.append(f"  ... ({total_errors - shown} more errors)")
                    break

        # Show warnings
        if warnings:
            parts.append(f"\n## Warnings ({total_warnings})")
            shown = 0
            for (file, line, msg), lines_list in sorted(warnings.items()):
                parts.append(f"  {file}:{line}")
                parts.append(f"    {msg}")
                shown += 1
                if shown >= 15:
                    parts.append(f"  ... ({total_warnings - shown} more warnings)")
                    break

        # Summary
        if summary_lines:
            parts.append("\n## Summary")
            parts.extend(summary_lines)

        compressed = "\n".join(parts)
        compressed = self._truncate_to_tokens(compressed)

        fidelity = {
            "errors": total_errors,
            "warnings": total_warnings,
            "files_with_issues": total_files_with_issues,
            "clean_files": clean_files,
            "unique_error_messages": len(set(k[2] for k in errors)),
            "unique_warning_messages": len(set(k[2] for k in warnings)),
        }

        return CompressResult(
            compressed=compressed,
            original_tokens=original_tokens,
            compressed_tokens=self.count(compressed),
            tool_type="eslint",
            strategy="dedup_by_location+summary",
            tokenizer=self.tokenizer.name,
            exact_tokens=self.tokenizer.exact,
            fidelity=fidelity,
        )
