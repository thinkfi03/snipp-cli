"""Compressor for JavaScript test runners: Jest, Vitest, Mocha, TAP.

Recognizes output from:
  - Jest (default, --verbose, --ci)
  - Vitest (default, --reporter=verbose)
  - Mocha (spec, dot, json reporters)
  - Node built-in test runner / TAP

Strategy:
  - Always preserve failures with full stack traces (per-failure elision handles)
  - Collapse passes to a single count per suite
  - Preserve summary lines (duration, coverage, snapshot)
  - Strip ANSI escape codes, progress bars, watch-mode noise

Bug-fix history (kimik2.6 review):
  - Frame end no longer triggers on a single blank line; needs two blanks,
    a suite header, or a summary marker.
  - Framework is locked once detected (no re-classification mid-stream).
  - Per-failure traces are persisted via the expand sidecar instead of
    being inlined verbatim into the compressed output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict

from snipp.compressors.base import BaseCompressor, CompressResult


_JEST_SUITE_PASS = re.compile(r"^\s*PASS\s+\S")
_JEST_SUITE_FAIL = re.compile(r"^\s*FAIL\s+\S")
_JEST_TEST_PASS = re.compile(r"^\s+✓\s+")
_JEST_TEST_FAIL = re.compile(r"^\s+✕\s+")
_JEST_SNAPSHOT = re.compile(r"^\s*Snapshot\s+Summary")
_JEST_COVERAGE = re.compile(r"^\s*%{9,}|^\s*-{9,}|^\s*All files")

_VITEST_FAIL = re.compile(r"^\s*FAIL\s+\S+\s*>")
_VITEST_PASS = re.compile(r"^\s*PASS\s+\S+\s*>")

_MOCHA_PASSING = re.compile(r"^\s*\d+\s+passing")
_MOCHA_FAILING = re.compile(r"^\s*\d+\s+failing")
_MOCHA_PENDING = re.compile(r"^\s*\d+\s+pending")
_MOCHA_FAIL_HEADER = re.compile(r"^\s+\d+\)\s+\S")

_TAP_OK = re.compile(r"^ok\s+\d+")
_TAP_NOT_OK = re.compile(r"^not ok\s+\d+")
_TAP_PLAN = re.compile(r"^1\.\.\d+")
_TAP_TOTAL = re.compile(r"^#\s+(tests|pass|fail|skip)\s+\d+", re.IGNORECASE)

_SUMMARY_RE = re.compile(
    r"^(Test Suites?|Tests?|Snapshots?|Time|Coverage|Test Files|passed?|failed?|skipped?|todo|total)\b",
    re.IGNORECASE,
)
_WATCH_RE = re.compile(r"^\s*Watch Usage|^\s*Press \w+ to|^\s*>\s+\w+\s|^\s*watch mode")


@dataclass
class _Failure:
    test: str
    trace: List[str] = field(default_factory=list)


def _detect_framework(lines: List[str]) -> str:
    """Detect framework from the first ~30 cleaned lines. Locked after first hit."""
    head = lines[:30]
    head_text = "\n".join(head).lower()
    if "vitest" in head_text or any(
        ln.startswith(" DEV  ") or ln.startswith(" RUN  ") for ln in head
    ):
        return "vitest"
    if "jest" in head_text or any(_JEST_SUITE_PASS.match(ln) or _JEST_SUITE_FAIL.match(ln) for ln in head):
        return "jest"
    if any(_TAP_PLAN.match(ln) for ln in head) or any(ln.startswith("TAP") for ln in head):
        return "tap"
    if "mocha" in head_text or any(_MOCHA_PASSING.match(ln) or _MOCHA_FAILING.match(ln) for ln in head):
        return "mocha"
    return "unknown"


class NpmTestCompressor(BaseCompressor):
    def __init__(self, max_tokens: int = 2000, show_passed_suites: bool = False, **kwargs):
        super().__init__(max_tokens=max_tokens, **kwargs)
        self.show_passed_suites = show_passed_suites

    def compress(self, output: str, query: Optional[str] = None) -> CompressResult:
        original_tokens = self.count(output)
        raw_lines = output.split("\n")
        clean_lines = [self.strip_ansi(ln) for ln in raw_lines]

        framework = _detect_framework(clean_lines)

        failures: List[_Failure] = []
        current_failure: Optional[_Failure] = None
        suite_results: Dict[str, Dict[str, int]] = {}
        current_suite: Optional[str] = None

        summary_lines: List[str] = []
        coverage_lines: List[str] = []
        snapshot_lines: List[str] = []
        in_coverage = False
        in_snapshot = False

        passed_tests = 0
        failed_tests = 0
        skipped_tests = 0
        total_suites = 0
        passed_suites = 0
        failed_suites = 0

        consecutive_blanks = 0

        def end_failure_section() -> None:
            nonlocal current_failure
            if current_failure is not None:
                failures.append(current_failure)
                current_failure = None

        for raw, clean in zip(raw_lines, clean_lines):
            if _WATCH_RE.match(clean):
                consecutive_blanks = 0
                continue

            stripped = clean.strip()

            # --- failure-trace continuation rules (run BEFORE other matchers) ---
            if current_failure is not None:
                # End on summary or new suite header.
                if (
                    _SUMMARY_RE.match(clean)
                    or _JEST_SUITE_PASS.match(clean)
                    or _JEST_SUITE_FAIL.match(clean)
                    or _VITEST_PASS.match(clean)
                    or _MOCHA_PASSING.match(clean)
                    or _MOCHA_FAILING.match(clean)
                ):
                    end_failure_section()
                    consecutive_blanks = 0
                    # fall through so the line itself is processed
                elif stripped == "":
                    consecutive_blanks += 1
                    if consecutive_blanks >= 2:
                        end_failure_section()
                    else:
                        current_failure.trace.append(raw)
                    continue
                else:
                    consecutive_blanks = 0
                    current_failure.trace.append(raw)
                    continue

            consecutive_blanks = 0

            # --- Vitest ---
            if framework == "vitest" and _VITEST_FAIL.match(clean):
                total_suites += 1
                failed_suites += 1
                name = stripped
                suite_results.setdefault(name, {"pass": 0, "fail": 0, "skip": 0})
                current_suite = name
                failed_tests += 1
                current_failure = _Failure(test=name)
                current_failure.trace.append(raw)
                continue
            if framework == "vitest" and _VITEST_PASS.match(clean):
                total_suites += 1
                passed_suites += 1
                current_suite = stripped
                suite_results.setdefault(current_suite, {"pass": 0, "fail": 0, "skip": 0})
                continue

            # --- Jest ---
            if framework in ("jest", "unknown") and _JEST_SUITE_PASS.match(clean):
                total_suites += 1
                passed_suites += 1
                current_suite = clean.split("PASS", 1)[1].strip()
                suite_results.setdefault(current_suite, {"pass": 0, "fail": 0, "skip": 0})
                continue
            if framework in ("jest", "unknown") and _JEST_SUITE_FAIL.match(clean):
                total_suites += 1
                failed_suites += 1
                current_suite = clean.split("FAIL", 1)[1].strip()
                suite_results.setdefault(current_suite, {"pass": 0, "fail": 0, "skip": 0})
                continue

            if _JEST_TEST_PASS.match(clean) or _TAP_OK.match(clean):
                passed_tests += 1
                if current_suite:
                    suite_results[current_suite]["pass"] += 1
                continue

            if _JEST_TEST_FAIL.match(clean) or _TAP_NOT_OK.match(clean):
                failed_tests += 1
                if current_suite:
                    suite_results[current_suite]["fail"] += 1
                test_name = stripped.lstrip("✕✗not ok")[:120].strip() or "<unnamed>"
                current_failure = _Failure(test=test_name)
                current_failure.trace.append(raw)
                continue

            # --- Mocha ---
            if framework == "mocha" and stripped.startswith("✓ "):
                passed_tests += 1
                continue

            if framework == "mocha" and _MOCHA_FAIL_HEADER.match(clean):
                failed_tests += 1
                test_name = stripped[:120]
                current_failure = _Failure(test=test_name)
                current_failure.trace.append(raw)
                continue

            if _MOCHA_PASSING.match(clean):
                summary_lines.append(raw)
                m = re.search(r"\d+", clean)
                if m:
                    passed_tests = max(passed_tests, int(m.group()))
                continue
            if _MOCHA_FAILING.match(clean):
                summary_lines.append(raw)
                m = re.search(r"\d+", clean)
                if m:
                    failed_tests = max(failed_tests, int(m.group()))
                continue
            if _MOCHA_PENDING.match(clean):
                summary_lines.append(raw)
                m = re.search(r"\d+", clean)
                if m:
                    skipped_tests = max(skipped_tests, int(m.group()))
                continue

            # --- TAP totals ---
            if _TAP_TOTAL.match(clean):
                summary_lines.append(raw)
                continue

            # --- Snapshot section ---
            if _JEST_SNAPSHOT.match(clean):
                in_snapshot = True
                snapshot_lines.append(raw)
                continue
            if in_snapshot:
                if stripped == "" or _SUMMARY_RE.match(clean):
                    in_snapshot = False
                else:
                    snapshot_lines.append(raw)
                    continue

            # --- Coverage section ---
            if stripped.startswith("Coverage") or _JEST_COVERAGE.match(clean):
                in_coverage = True
                coverage_lines.append(raw)
                continue
            if in_coverage:
                if stripped == "" or not (
                    stripped.startswith("|") or "%" in stripped or stripped.startswith("-")
                ):
                    in_coverage = False
                else:
                    coverage_lines.append(raw)
                    continue

            # --- Generic summary lines ---
            if _SUMMARY_RE.match(clean):
                summary_lines.append(raw)
                continue

        end_failure_section()

        # Build output
        parts: List[str] = ["# Test Results"]
        if framework != "unknown":
            parts.append(f"Framework: {framework}")
        parts.append(
            f"Suites: {total_suites} total ({passed_suites} passed, {failed_suites} failed)"
        )
        parts.append(
            f"Tests: {passed_tests + failed_tests + skipped_tests} total "
            f"({passed_tests} passed, {failed_tests} failed, {skipped_tests} skipped)"
        )
        if query:
            parts.append(f"Query: {query!r}")

        if self.show_passed_suites and passed_suites > 0:
            parts.append(f"\n## Passed Suites ({passed_suites})")
            for suite, counts in suite_results.items():
                if counts["fail"] == 0:
                    parts.append(f"  ✓ {suite} ({counts['pass']} tests)")

        elision_handles: List[str] = []

        # Per-failure handles: keep first 20 trace lines inline, elide rest.
        if failures:
            parts.append(f"\n## Failures ({failed_tests or len(failures)})")
            inline_budget = 20
            for f in failures:
                parts.append(f"\n### {f.test}")
                head = f.trace[:inline_budget]
                parts.extend(head)
                if len(f.trace) > inline_budget:
                    handle = self._elide(
                        "\n".join(f.trace[inline_budget:]),
                        source=f"npm_test:{f.test}",
                    )
                    if handle:
                        elision_handles.append(handle)
                        parts.append(handle)

        if snapshot_lines:
            parts.append("\n## Snapshots")
            parts.extend(snapshot_lines[:20])
            if len(snapshot_lines) > 20:
                handle = self._elide(
                    "\n".join(snapshot_lines[20:]), source="npm_test:snapshots"
                )
                if handle:
                    elision_handles.append(handle)
                    parts.append(handle)

        if coverage_lines:
            parts.append("\n## Coverage")
            preserved = False
            for ln in coverage_lines:
                cln = self.strip_ansi(ln)
                if "All files" in cln or "Total" in cln:
                    parts.append(ln)
                    preserved = True
                    break
            if not preserved:
                parts.extend(coverage_lines[:5])

        if summary_lines:
            parts.append("\n## Summary")
            parts.extend(summary_lines)

        compressed = "\n".join(parts)
        compressed = self._truncate_to_tokens(compressed)

        fidelity = {
            "framework": framework,
            "suites_total": total_suites,
            "suites_passed": passed_suites,
            "suites_failed": failed_suites,
            "tests_passed": passed_tests,
            "tests_failed": failed_tests,
            "tests_skipped": skipped_tests,
            "failures_preserved": len(failures),
            "snapshot_lines": len(snapshot_lines),
            "coverage_lines": len(coverage_lines),
        }

        return CompressResult(
            compressed=compressed,
            original_tokens=original_tokens,
            compressed_tokens=self.count(compressed),
            tool_type="npm_test",
            strategy="failures_handles+passes_collapsed",
            tokenizer=self.tokenizer.name,
            exact_tokens=self.tokenizer.exact,
            fidelity=fidelity,
            elision_handles=elision_handles,
        )
