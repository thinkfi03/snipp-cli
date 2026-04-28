"""Compressor for npm/yarn/pnpm output: install, build, run.

Handles:
  - npm install / yarn / pnpm install
  - npm run build / next build / vite build
  - npm ci / npm audit
  - webpack / rollup / esbuild / tsc output

Strategy:
  - Keep errors and warnings (anchored regexes; not greedy substring)
  - Collapse dependency resolution to counts
  - Strip progress bars, spinners, download metadata
  - Keep final bundle/asset stats
  - Keep build summary (time, chunks, sizes)

Bug-fix history (kimik2.6 review):
  - Vulnerability counting: prefer authoritative `found N vulnerabilities`
    line; otherwise SUM per-severity counts instead of taking the max.
  - Error/build regexes anchored to start-of-line / word boundaries so a
    string literal containing "Error:" doesn't get mis-classified.
"""

from __future__ import annotations

import re
from typing import Optional, List

from snipp.compressors.base import BaseCompressor, CompressResult


_SPINNER_RE = re.compile(r"^\s*[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏▰▱⣾⣽⣻⢿⡿⣟⣯⣷]\s")
_DOWNLOAD_RE = re.compile(r"^\s*[-\\|/]\s")

# npm/yarn/pnpm package counts — anchored
_NPM_ADDED = re.compile(r"^\s*added\s+(\d+)", re.IGNORECASE)
_NPM_REMOVED = re.compile(r"^\s*removed\s+(\d+)", re.IGNORECASE)
_NPM_CHANGED = re.compile(r"^\s*changed\s+(\d+)", re.IGNORECASE)
_NPM_FUNDING = re.compile(r"^\s*\d+\s+packages?\s+are\s+looking\s+for\s+funding", re.IGNORECASE)

# Build tools — anchored at line start (or as the first non-space token)
_WEBPACK_DONE = re.compile(r"^\s*(webpack\s+compiled|compiled\s+successfully|compiled\s+with)", re.IGNORECASE)
_VITE_BUILD = re.compile(r"^\s*(vite\s+v\d|building\s+for\s+production|✓\s+\d+\s+modules\s+transformed)", re.IGNORECASE)
_NEXT_BUILD = re.compile(r"^\s*(▲\s*Next\.js\s+v\d|Creating an optimized production build|✓\s+Build completed|✓\s+Compiled successfully|✓\s+Linting|✓\s+Collecting|✓\s+Generating)", re.IGNORECASE)
_TSC_DONE = re.compile(r"^\s*(Found\s+\d+\s+errors?|error\s+TS\d+)", re.IGNORECASE)
_ESBUILD_DONE = re.compile(r"^\s*(\d+\s+errors?|\d+\s+warnings?)\s*$", re.IGNORECASE)

# Bundle stats
_BUNDLE_STAT = re.compile(
    r"^\s*(?:[┌├└│─\s○●◆]+)?(?:dist/|build/|\.next/|\.vite/|\S+\.(?:js|css|html|svg|png|jpg))",
    re.IGNORECASE,
)
_KB_LINE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:kB|MB|B)\b")
_BUILD_TIME = re.compile(r"^\s*(?:✓\s+)?(?:done in|built in|completed in|time:)\s*[\d\.]+\s*(?:s|ms|sec)", re.IGNORECASE)
_ROUTE_TABLE_RE = re.compile(r"^[┌├└│─\s○●◆]+(?:/\S*)?\s+\d+(?:\.\d+)?\s+(?:kB|MB|B)\b", re.IGNORECASE)

# Errors / warnings — anchored to line start (no substring matching)
_ERROR_LINE = re.compile(
    r"^\s*(?:npm ERR!|yarn error|pnpm ERR!|✖|✕|×|❌|ERR!|ERROR\b|error\s+TS\d+|Error:|TypeError:|ReferenceError:|SyntaxError:|FATAL:|fatal:)",
)
_WARN_LINE = re.compile(
    r"^\s*(?:npm WARN|yarn warning|pnpm WARN|warning\b|⚠|DeprecationWarning:)",
)

# Vulnerability lines
_VULN_FOUND = re.compile(r"^\s*found\s+(\d+)\s+vulnerabilit", re.IGNORECASE)
_VULN_SEVERITY = re.compile(r"^\s*(\d+)\s+(low|moderate|high|critical)\b", re.IGNORECASE)


class NpmCompressor(BaseCompressor):
    def __init__(self, max_tokens: int = 2000, **kwargs):
        super().__init__(max_tokens=max_tokens, **kwargs)

    def compress(self, output: str, query: Optional[str] = None) -> CompressResult:
        original_tokens = self.count(output)
        lines = output.split("\n")

        errors: List[str] = []
        warnings: List[str] = []
        bundle_stats: List[str] = []
        build_summary: List[str] = []

        added = removed = changed = 0
        vuln_authoritative: Optional[int] = None
        vuln_severity_sum = 0

        in_dep_tree = False
        dep_buffer_count = 0

        for raw in lines:
            clean = self.strip_ansi(raw).rstrip()
            if not clean:
                continue

            if _SPINNER_RE.match(clean) or _DOWNLOAD_RE.match(clean):
                continue
            if clean.lstrip().startswith("http") and (".tgz" in clean or "tarball" in clean):
                continue
            if "resolving" in clean.lower() and "dependencies" in clean.lower():
                in_dep_tree = True
                continue
            if in_dep_tree and (
                clean.startswith(" ")
                or clean.startswith(("├", "└", "│"))
            ):
                dep_buffer_count += 1
                continue
            in_dep_tree = False

            m = _NPM_ADDED.match(clean)
            if m:
                added = max(added, int(m.group(1)))
                continue
            m = _NPM_REMOVED.match(clean)
            if m:
                removed = max(removed, int(m.group(1)))
                continue
            m = _NPM_CHANGED.match(clean)
            if m:
                changed = max(changed, int(m.group(1)))
                continue

            m = _VULN_FOUND.match(clean)
            if m:
                vuln_authoritative = int(m.group(1))
                warnings.append(raw)
                continue
            m = _VULN_SEVERITY.match(clean)
            if m:
                vuln_severity_sum += int(m.group(1))
                warnings.append(raw)
                continue
            if _NPM_FUNDING.match(clean):
                continue

            if _ERROR_LINE.match(clean):
                errors.append(raw)
                continue
            if _WARN_LINE.match(clean):
                if raw not in warnings:
                    warnings.append(raw)
                continue

            if (
                _WEBPACK_DONE.match(clean)
                or _VITE_BUILD.match(clean)
                or _NEXT_BUILD.match(clean)
                or _TSC_DONE.match(clean)
                or _ESBUILD_DONE.match(clean)
                or _BUILD_TIME.match(clean)
            ):
                build_summary.append(raw)
                continue

            if _ROUTE_TABLE_RE.match(clean):
                bundle_stats.append(raw)
                continue
            if _BUNDLE_STAT.match(clean) and _KB_LINE.search(clean):
                bundle_stats.append(raw)
                continue

            stripped = clean.lstrip()
            if (
                stripped.startswith(("✓ ", "✗ ", "✕ "))
                and any(k in stripped for k in ("Compiled", "Build", "Linting", "Collecting", "Generating"))
            ):
                build_summary.append(raw)
                continue

        # Compute final vulnerability count.
        vulnerabilities = (
            vuln_authoritative
            if vuln_authoritative is not None
            else vuln_severity_sum
        )

        parts: List[str] = ["# npm/yarn/pnpm Output"]
        if query:
            parts.append(f"Query: {query!r}")

        if added or removed or changed or dep_buffer_count:
            parts.append("\n## Packages")
            if added:
                parts.append(f"  + {added} added")
            if removed:
                parts.append(f"  - {removed} removed")
            if changed:
                parts.append(f"  ~ {changed} changed")
            if dep_buffer_count:
                parts.append(f"  ({dep_buffer_count} dependency tree lines elided)")

        if vulnerabilities:
            parts.append(f"\n## Audit: {vulnerabilities} vulnerabilities found")

        elision_handles: List[str] = []

        if errors:
            parts.append(f"\n## Errors ({len(errors)})")
            parts.extend(errors[:30])
            if len(errors) > 30:
                handle = self._elide("\n".join(errors[30:]), source="npm:errors")
                if handle:
                    elision_handles.append(handle)
                    parts.append(handle)

        if warnings:
            parts.append(f"\n## Warnings ({len(warnings)})")
            parts.extend(warnings[:20])
            if len(warnings) > 20:
                handle = self._elide("\n".join(warnings[20:]), source="npm:warnings")
                if handle:
                    elision_handles.append(handle)
                    parts.append(handle)

        if build_summary:
            parts.append("\n## Build Summary")
            parts.extend(build_summary)

        if bundle_stats:
            parts.append(f"\n## Bundle Stats ({len(bundle_stats)} assets)")
            parts.extend(bundle_stats[:15])
            if len(bundle_stats) > 15:
                handle = self._elide(
                    "\n".join(bundle_stats[15:]), source="npm:bundle"
                )
                if handle:
                    elision_handles.append(handle)
                    parts.append(handle)

        compressed = "\n".join(parts)
        compressed = self._truncate_to_tokens(compressed)

        fidelity = {
            "errors": len(errors),
            "warnings": len(warnings),
            "packages_added": added,
            "packages_removed": removed,
            "packages_changed": changed,
            "vulnerabilities": vulnerabilities,
            "vulnerabilities_source": (
                "authoritative_summary" if vuln_authoritative is not None else "severity_sum"
            ),
            "bundle_stats": len(bundle_stats),
            "build_summary_lines": len(build_summary),
        }

        return CompressResult(
            compressed=compressed,
            original_tokens=original_tokens,
            compressed_tokens=self.count(compressed),
            tool_type="npm",
            strategy="anchored_regex+errors+stats",
            tokenizer=self.tokenizer.name,
            exact_tokens=self.tokenizer.exact,
            fidelity=fidelity,
            elision_handles=elision_handles,
        )
