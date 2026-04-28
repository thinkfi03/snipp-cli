"""Tests for Node.js compressors: npm_test, npm, eslint."""

import pytest

from snipp.compressors.npm_test import NpmTestCompressor
from snipp.compressors.npm import NpmCompressor
from snipp.compressors.eslint import EslintCompressor


class TestNpmTestCompressor:
    def test_jest_output(self):
        output = """PASS src/utils.test.js
  utils
    ✓ should format date (5 ms)
    ✓ should parse JSON (2 ms)

FAIL src/auth.test.js
  auth
    ✓ should login (10 ms)
    ✕ should logout

      Error: expect(received).toBe(expected)
      Expected: true
      Received: false
        at Object.<anonymous> (src/auth.test.js:42:23)

Test Suites: 1 failed, 1 passed, 2 total
Tests:       1 failed, 3 passed, 4 total
Snapshots:   0 total
Time:        1.234 s
"""
        comp = NpmTestCompressor()
        result = comp.compress(output)
        assert result.tool_type == "npm_test"
        assert "1 failed" in result.compressed
        assert "Tests:" in result.compressed
        assert "Error: expect" in result.compressed
        # Pass lines should be collapsed
        assert "should format date" not in result.compressed

    def test_vitest_output(self):
        output = """ DEV  v1.0.0 /project

 FAIL  src/math.test.ts > add
     Error: expected 5 to be 4
     ❯ src/math.test.ts:5:17

Test Files  1 failed | 3 passed (4)
     Tests  1 failed | 12 passed (13)
      Time  1.23s
"""
        comp = NpmTestCompressor()
        result = comp.compress(output)
        assert result.tool_type == "npm_test"
        assert "Error: expected 5 to be 4" in result.compressed
        assert "Tests:" in result.compressed or "Summary" in result.compressed

    def test_mocha_output(self):
        output = """  math
    ✓ adds numbers
    ✓ subtracts numbers
    1) divides by zero

  2 passing (12ms)
  1 failing

  1) math
       divides by zero:
     Error: Division by zero
      at Context.<anonymous> (test/math.js:15:10)
"""
        comp = NpmTestCompressor()
        result = comp.compress(output)
        assert "2 passing" in result.compressed or "passed" in result.compressed
        assert "1 failing" in result.compressed or "failed" in result.compressed
        assert "Error: Division by zero" in result.compressed

    def test_tap_output(self):
        output = """TAP version 13
ok 1 - should add
ok 2 - should subtract
not ok 3 - should divide
  ---
  message: Division by zero
  ...
1..3
# tests 3
# pass 2
# fail 1
"""
        comp = NpmTestCompressor()
        result = comp.compress(output)
        assert "Tests:" in result.compressed
        assert "1 failed" in result.compressed or "fail 1" in result.compressed

    def test_empty_output(self):
        comp = NpmTestCompressor()
        result = comp.compress("")
        assert "# Test Results" in result.compressed
        assert "Suites: 0" in result.compressed

    def test_watch_mode_noise_stripped(self):
        output = """Watch Usage
 > Press p to filter by filename
 > Press q to quit

PASS src/a.test.js
  ✓ test
"""
        comp = NpmTestCompressor()
        result = comp.compress(output)
        assert "Watch Usage" not in result.compressed
        assert "Press p" not in result.compressed


class TestNpmCompressor:
    def test_npm_install_output(self):
        output = """added 147 packages, and audited 148 packages in 2s

found 0 vulnerabilities
"""
        comp = NpmCompressor()
        result = comp.compress(output)
        assert result.tool_type == "npm"
        assert "147" in result.compressed or "added" in result.compressed

    def test_npm_build_output(self):
        output = """> next build
  ▲ Next.js 14.0.0
   Creating an optimized production build ...
 ✓ Compiled successfully
 ✓ Linting and checking validity of types ...
 ✓ Collecting page data ...
 ✓ Generating static pages (5/5)
 ✓ Collecting build traces ...

Route (app)                              Size     First Load JS
┌ ○ /                                    4.2 kB        85.3 kB
├ ○ /about                               1.1 kB        82.2 kB
└ ○ /contact                             2.3 kB        83.4 kB

✓ Build completed in 3.4s
"""
        comp = NpmCompressor()
        result = comp.compress(output)
        assert "Build Summary" in result.compressed or "Build completed" in result.compressed
        # Should keep bundle stats
        assert "kB" in result.compressed or "Size" in result.compressed

    def test_npm_install_with_errors(self):
        output = """npm ERR! code E404
npm ERR! 404 Not Found - GET https://registry.npmjs.org/nonexistent-pkg
npm ERR! 404
npm ERR! 404  'nonexistent-pkg@latest' is not in this registry.
"""
        comp = NpmCompressor()
        result = comp.compress(output)
        assert "Errors" in result.compressed
        assert "E404" in result.compressed

    def test_vite_build_output(self):
        output = """vite v5.0.0 building for production...
depositFiles()...
✓ 42 modules transformed.
dist/                     0.05 kB │ gzip: 0.07 kB
dist/assets/index.js      45.30 kB │ gzip: 12.40 kB
✓ built in 1.23s.
"""
        comp = NpmCompressor()
        result = comp.compress(output)
        assert "Build Summary" in result.compressed or "built in" in result.compressed
        assert "Bundle Stats" in result.compressed or "kB" in result.compressed

    def test_progress_bars_stripped(self):
        output = """⠋ installing dependencies...
⠙ resolving dependencies...
added 10 packages in 1s
"""
        comp = NpmCompressor()
        result = comp.compress(output)
        assert "⠋" not in result.compressed
        assert "⠙" not in result.compressed
        assert "added" in result.compressed or "Packages" in result.compressed


class TestEslintCompressor:
    def test_eslint_stylish_output(self):
        output = """/project/src/App.js
  10:5  error  'foo' is not defined  no-undef
  15:3  warning  Unexpected console statement  no-console

/project/src/utils.js
  5:1  error  Expected indentation of 2 spaces but found 4  indent

✖ 3 problems (2 errors, 1 warning)
"""
        comp = EslintCompressor()
        result = comp.compress(output)
        assert result.tool_type == "eslint"
        assert "Errors (2)" in result.compressed or "Errors:" in result.compressed
        assert "'foo' is not defined" in result.compressed
        # Rule name included in message
        assert "no-undef" in result.compressed
        # Should show file:line
        assert "App.js:10" in result.compressed
        assert "utils.js:5" in result.compressed

    def test_tsc_output(self):
        output = """src/App.tsx(10,5): error TS2304: Cannot find name 'foo'.
src/utils.ts(5,1): error TS2322: Type 'string' is not assignable to type 'number'.

Found 2 errors in 2 files.
"""
        comp = EslintCompressor()
        result = comp.compress(output)
        assert "Errors (2)" in result.compressed or "Errors:" in result.compressed
        assert "App.tsx:10" in result.compressed
        assert "TS2304" in result.compressed
        assert "Cannot find name 'foo'" in result.compressed

    def test_no_errors(self):
        output = """✔ No linting errors found.
"""
        comp = EslintCompressor()
        result = comp.compress(output)
        assert "Errors: 0" in result.compressed or "0 errors" in result.compressed

    def test_deduplication(self):
        output = """/project/src/App.js
  10:5  error  'foo' is not defined  no-undef
  10:5  error  'foo' is not defined  no-undef

✖ 2 problems (2 errors, 0 warnings)
"""
        comp = EslintCompressor()
        result = comp.compress(output)
        # Should deduplicate identical errors
        assert result.fidelity["errors"] == 1
        assert result.fidelity["unique_error_messages"] == 1

    def test_clean_files_counted(self):
        output = """✔ /project/src/a.js
✔ /project/src/b.js

/project/src/c.js
  1:1  error  Missing semicolon  semi

3 files checked, 1 error
"""
        comp = EslintCompressor()
        result = comp.compress(output)
        assert result.fidelity["clean_files"] == 2
        assert result.fidelity["files_with_issues"] == 1
