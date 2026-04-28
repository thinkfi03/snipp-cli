"""Pytest compressor with anchored regex parsing.

Handles three pytest output styles:
  1. Verbose:   `tests/x.py::test_y PASSED`
  2. Short:     `..F.E.s` dot stream + summary
  3. Mixed with section markers `===`

Parser is line-anchored (no substring 'PASSED' matches inside docstrings).
Failures keep their full traceback; passes are collapsed unless show_passed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from snipp.compressors.base import BaseCompressor, CompressResult


_RESULT_RE = re.compile(
    r"^(?P<test>\S+?::\S+?)\s+(?P<status>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)"
    r"(?:\s+\[\s*\d+%\])?\s*$"
)
_SECTION_RE = re.compile(r"^=+\s*(?P<title>.*?)\s*=+$")
_SHORT_SUMMARY_RE = re.compile(r"^=+\s*short test summary info\s*=+$", re.IGNORECASE)
_FINAL_SUMMARY_RE = re.compile(
    r"=+\s*("
    r"(?:\d+\s+(?:passed|failed|error|errors|skipped|xfailed|xpassed|warning|warnings)"
    r"(?:,\s*)?)+"
    r")\s+in\s+\S+\s*=+",
    re.IGNORECASE,
)
_FAILURE_HEADER_RE = re.compile(r"^_+\s+(?P<test>\S+)\s+_+$")
_DOT_STREAM_RE = re.compile(r"^[\.FfEeSsxX]+$")


@dataclass
class _Failure:
    test: str
    trace: List[str] = field(default_factory=list)


class PytestCompressor(BaseCompressor):
    def __init__(
        self,
        max_tokens: int = 2000,
        show_passed: bool = False,
        max_failure_trace: int = 80,
        keep_full_traces: bool = False,
        **kwargs,
    ):
        super().__init__(max_tokens=max_tokens, **kwargs)
        self.show_passed = show_passed
        self.max_failure_trace = max_failure_trace if not keep_full_traces else 10_000

    def compress(self, output: str, query: Optional[str] = None) -> CompressResult:
        original_tokens = self.count(output)
        passed: List[str] = []
        failed: List[_Failure] = []
        errors: List[str] = []
        skipped: List[str] = []
        summary_line = ""
        current: Optional[_Failure] = None
        in_failure_section = False

        lines = output.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.rstrip()

            if _SHORT_SUMMARY_RE.match(stripped):
                if current:
                    failed.append(current)
                    current = None
                in_failure_section = False
                i += 1
                continue

            sec = _SECTION_RE.match(stripped)
            if sec:
                title = sec.group("title").lower()
                if "failures" in title:
                    in_failure_section = True
                    if current:
                        failed.append(current)
                        current = None
                elif "errors" in title:
                    in_failure_section = True
                    if current:
                        failed.append(current)
                        current = None
                elif "test session starts" in title or "summary" in title:
                    if current:
                        failed.append(current)
                        current = None
                    in_failure_section = False
                if _FINAL_SUMMARY_RE.search(line):
                    summary_line = stripped
                i += 1
                continue

            if _FINAL_SUMMARY_RE.search(line):
                summary_line = stripped
                if current:
                    failed.append(current)
                    current = None
                i += 1
                continue

            if in_failure_section:
                fh = _FAILURE_HEADER_RE.match(stripped)
                if fh:
                    if current:
                        failed.append(current)
                    current = _Failure(test=fh.group("test"))
                    i += 1
                    continue
                if current:
                    current.trace.append(line)
                    i += 1
                    continue

            m = _RESULT_RE.match(stripped)
            if m:
                test = m.group("test")
                status = m.group("status")
                if status == "PASSED":
                    passed.append(test)
                elif status == "FAILED":
                    if current:
                        failed.append(current)
                    current = _Failure(test=test)
                elif status == "ERROR":
                    errors.append(test)
                elif status == "SKIPPED":
                    skipped.append(test)
                i += 1
                continue

            if _DOT_STREAM_RE.match(stripped):
                passed.extend(["." for c in stripped if c == "."])
                failed.extend([_Failure(test="<unnamed>") for c in stripped if c in "Ff"])
                errors.extend(["<unnamed>" for c in stripped if c in "Ee"])
                skipped.extend(["<unnamed>" for c in stripped if c in "Ss"])
                i += 1
                continue

            if current and not in_failure_section:
                current.trace.append(line)

            i += 1

        if current:
            failed.append(current)

        parts: List[str] = ["# Test Results"]
        if summary_line:
            parts.append(f"Summary: {summary_line}")
        else:
            parts.append(
                f"Passed: {len(passed)}, Failed: {len(failed)}, "
                f"Errors: {len(errors)}, Skipped: {len(skipped)}"
            )
        if query:
            parts.append(f"Query: {query!r}")

        elision_handles: List[str] = []

        if failed:
            parts.append(f"\n## Failures ({len(failed)})")
            for f in failed:
                parts.append(f"\n### {f.test}")
                trace_lines = f.trace[: self.max_failure_trace]
                parts.extend(trace_lines)
                if len(f.trace) > self.max_failure_trace:
                    handle = self._elide(
                        "\n".join(f.trace[self.max_failure_trace:]),
                        source=f"trace:{f.test}",
                    )
                    if handle:
                        elision_handles.append(handle)
                        parts.append(handle)

        if errors:
            parts.append(f"\n## Errors ({len(errors)})")
            for e in errors[:20]:
                parts.append(f"  ! {e}")
            if len(errors) > 20:
                parts.append(f"  ... and {len(errors) - 20} more errors")

        if self.show_passed and passed:
            parts.append(f"\n## Passed ({len(passed)})")
            for p in passed[:50]:
                parts.append(f"  ✓ {p}")
            if len(passed) > 50:
                handle = self._elide("\n".join(passed[50:]), source="passed_overflow")
                if handle:
                    elision_handles.append(handle)
                    parts.append(handle)
        elif passed:
            parts.append(f"\n## Passed: {len(passed)} tests (details elided)")

        if skipped:
            parts.append(f"\n## Skipped: {len(skipped)} tests")

        compressed = "\n".join(parts)
        compressed = self._truncate_to_tokens(compressed)

        fidelity = {
            "passed_count": len(passed),
            "failed_count": len(failed),
            "error_count": len(errors),
            "skipped_count": len(skipped),
            "failures_preserved": len(failed),
            "trace_lines_preserved": sum(
                min(len(f.trace), self.max_failure_trace) for f in failed
            ),
            "trace_lines_total": sum(len(f.trace) for f in failed),
        }

        return CompressResult(
            compressed=compressed,
            original_tokens=original_tokens,
            compressed_tokens=self.count(compressed),
            tool_type="pytest",
            strategy="failures_detail+passes_summary",
            tokenizer=self.tokenizer.name,
            exact_tokens=self.tokenizer.exact,
            fidelity=fidelity,
            elision_handles=elision_handles,
        )
