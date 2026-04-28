"""Regression tests for the kimik2.6-review bug fixes.

Each test pins a previously-failing scenario from the brutal review.
"""

from __future__ import annotations

from snipp.compressors.eslint import EslintCompressor
from snipp.compressors.npm import NpmCompressor
from snipp.compressors.npm_test import NpmTestCompressor
from snipp.detector import detect_tool, ToolType


# ---------------------------------------------------------------------------
# Bug #1: trace-end heuristic (single blank should NOT end a failure trace)
# ---------------------------------------------------------------------------
class TestTraceEnd:
    def test_single_blank_does_not_end_trace(self):
        out = """FAIL src/a.test.js
  ✕ should fail
    Error: bad

    at deeper frame
    at even deeper

Test Suites: 1 failed, 0 passed, 1 total
Tests: 1 failed, 0 passed, 1 total
"""
        r = NpmTestCompressor().compress(out)
        assert "Error: bad" in r.compressed
        assert "deeper frame" in r.compressed
        assert r.fidelity["tests_failed"] == 1

    def test_two_blanks_end_trace(self):
        out = """FAIL src/a.test.js
  ✕ should fail
    Error: bad


unrelated noise that should not be part of the trace
"""
        r = NpmTestCompressor().compress(out)
        assert "Error: bad" in r.compressed
        assert "unrelated noise" not in r.compressed


# ---------------------------------------------------------------------------
# Bug #2: framework lock — Vitest ✓ lines must not be counted as Mocha
# ---------------------------------------------------------------------------
class TestFrameworkLock:
    def test_vitest_check_marks_not_mocha_passes(self):
        out = """ DEV  v1.0.0 /project

 PASS  src/math.test.ts > add
   ✓ adds two numbers
   ✓ adds three numbers

Test Files  1 passed (1)
     Tests  2 passed (2)
"""
        r = NpmTestCompressor().compress(out)
        assert r.fidelity["framework"] == "vitest"


# ---------------------------------------------------------------------------
# Bug #3: vulnerability counting (sum severities; honor authoritative line)
# ---------------------------------------------------------------------------
class TestVulnSum:
    def test_severity_sum_when_no_authoritative_line(self):
        out = """1 low
2 moderate
3 high
"""
        r = NpmCompressor().compress(out)
        assert r.fidelity["vulnerabilities"] == 6
        assert r.fidelity["vulnerabilities_source"] == "severity_sum"

    def test_authoritative_line_overrides(self):
        out = """1 low
2 moderate
3 high

found 6 vulnerabilities (1 low, 2 moderate, 3 high)
"""
        r = NpmCompressor().compress(out)
        assert r.fidelity["vulnerabilities"] == 6
        assert r.fidelity["vulnerabilities_source"] == "authoritative_summary"


# ---------------------------------------------------------------------------
# Bug #4: anchored error regex (Error: in source paths must NOT be flagged)
# ---------------------------------------------------------------------------
class TestAnchoredErrors:
    def test_error_in_module_path_is_not_an_error(self):
        out = """> webpack 5
[webpack-cli] Compilation finished
asset main.js 12.3 kB
./src/components/ErrorBoundary.tsx
./src/utils/Error.ts
done in 2.3s
"""
        r = NpmCompressor().compress(out)
        assert r.fidelity["errors"] == 0

    def test_real_npm_err_is_an_error(self):
        out = """npm ERR! code E404
npm ERR! 404 Not Found
"""
        r = NpmCompressor().compress(out)
        assert r.fidelity["errors"] == 2


# ---------------------------------------------------------------------------
# Bug #5: tsc on .d.ts / .mts / .cts files
# ---------------------------------------------------------------------------
class TestTscDts:
    def test_d_ts_error(self):
        out = """src/types/api.d.ts(15,3): error TS2300: Duplicate identifier 'Foo'.
src/utils.mts(2,1): error TS1005: ',' expected.
src/legacy.cts(8,5): error TS2304: Cannot find name 'bar'.
"""
        r = EslintCompressor().compress(out)
        assert r.fidelity["errors"] == 3
        assert "api.d.ts:15" in r.compressed
        assert "utils.mts:2" in r.compressed
        assert "legacy.cts:8" in r.compressed


# ---------------------------------------------------------------------------
# Bug #6: ✖-prefixed ESLint summary line is preserved
# ---------------------------------------------------------------------------
class TestEslintSummary:
    def test_x_prefix_summary_preserved(self):
        out = """/project/src/App.js
  10:5  error  'foo' is not defined  no-undef

✖ 1 problem (1 error, 0 warnings)
"""
        r = EslintCompressor().compress(out)
        assert "1 problem" in r.compressed


# ---------------------------------------------------------------------------
# Bug #7: detector — `node script.js` (no test flag) is generic, not test runner
# ---------------------------------------------------------------------------
class TestNodeDetection:
    def test_plain_node_script_is_not_test_runner(self):
        # `node ./scripts/run-tests.js` should NOT be classified as npm_test
        # just because the filename contains "test".
        assert detect_tool("node ./scripts/run-tests.js", "") != ToolType.NPM_TEST

    def test_node_dash_dash_test_is_test_runner(self):
        assert detect_tool("node --test", "") == ToolType.NPM_TEST

    def test_node_invoking_jest_module(self):
        assert detect_tool("node ./node_modules/.bin/jest", "") == ToolType.NPM_TEST


# ---------------------------------------------------------------------------
# Bug #9: unknown config keys produce a warning
# ---------------------------------------------------------------------------
class TestConfigWarnings:
    def test_typo_in_extras_warns(self, tmp_path, capsys):
        p = tmp_path / "cfg.yaml"
        p.write_text(
            "compressors:\n"
            "  grep:\n"
            "    max_outputs_token: 5000\n"  # typo: extra 's'
        )
        from snipp.config_loader import load_config
        load_config(str(p))
        captured = capsys.readouterr()
        assert "max_outputs_token" in captured.err
        assert "did you mean" in captured.err

    def test_unknown_top_level_key_warns(self, tmp_path, capsys):
        p = tmp_path / "cfg.yaml"
        p.write_text("default_max_token: 4000\n")  # typo
        from snipp.config_loader import load_config
        load_config(str(p))
        captured = capsys.readouterr()
        assert "default_max_token" in captured.err


# ---------------------------------------------------------------------------
# Bug #10: default config model list is generated, not stale
# ---------------------------------------------------------------------------
class TestModelListGeneration:
    def test_default_config_includes_all_model_keys(self, tmp_path):
        from snipp.config_loader import write_default_config
        from snipp.tokenizer import MODEL_TOKENIZER_MAP

        target = tmp_path / "cfg.yaml"
        write_default_config(str(target))
        text = target.read_text()
        # A representative key from each major family must appear.
        for key in ("claude-opus", "gpt-5", "gpt-4o", "o3", "kimi", "qwen", "deepseek"):
            assert key in text, f"missing {key} in default config text"
        # The list shouldn't reference models that aren't actually mapped.
        for missing in ("gpt-7", "claude-zeta"):
            assert missing not in text


# ---------------------------------------------------------------------------
# Bug #8: malformed YAML produces a stderr warning, not a silent fallback
# ---------------------------------------------------------------------------
class TestYamlParseError:
    def test_bad_yaml_warns(self, tmp_path, capsys):
        p = tmp_path / "broken.yaml"
        p.write_text("compressors:\n  grep:\n    - this is a list under a mapping\n  invalid: : :\n")
        from snipp.config_loader import load_config
        load_config(str(p))
        captured = capsys.readouterr()
        assert "broken.yaml" in captured.err or "failed to parse" in captured.err


# ---------------------------------------------------------------------------
# Architecture: per-failure handles in npm_test
# ---------------------------------------------------------------------------
class TestNpmTestHandles:
    def test_long_failure_trace_emits_handle(self):
        long_trace_lines = "\n".join(f"    at frame {i}" for i in range(50))
        out = f"""FAIL src/a.test.js
  ✕ should fail
    Error: bad
{long_trace_lines}

Test Suites: 1 failed, 0 passed, 1 total
Tests: 1 failed, 0 passed, 1 total
"""
        r = NpmTestCompressor().compress(out)
        assert len(r.elision_handles) >= 1
        # The first ~20 trace lines should still be inline.
        assert "at frame 0" in r.compressed


# ---------------------------------------------------------------------------
# ANSI handling lifted into BaseCompressor
# ---------------------------------------------------------------------------
class TestAnsiInBase:
    def test_strip_ansi_static(self):
        from snipp.compressors.base import BaseCompressor, strip_ansi
        assert strip_ansi("\x1b[31mhello\x1b[0m world") == "hello world"
        assert BaseCompressor.strip_ansi("\x1b[1;32mok\x1b[0m") == "ok"
