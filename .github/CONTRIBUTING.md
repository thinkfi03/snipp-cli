# Contributing to snipp

snipp is open-source, MIT-licensed, and built in public. Contributions of every size are welcome — from typo fixes to whole new compressors.

This guide gets you from `git clone` to merged PR in under 30 minutes.

---

## TL;DR — the fastest contribution path

The most valuable contribution is **a new compressor for a tool snipp doesn't yet handle**. Examples: `kubectl`, `terraform`, `cargo`, `mvn`, `gradle`, `psql`, `jq`, `aws-cli`, `gh`. Each is ~100 lines of Python, well-scoped, easy to test, and immediately useful to thousands of users.

If you want to start there, open an issue using the **"Add a compressor"** template and we'll help you scope it. Otherwise, read on.

---

## Quick start

```bash
git clone https://github.com/thinkfi03/snipp-cli.git
cd snipp-cli
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,all]"
pytest                    # 232 tests, ~10 seconds
snipp demo                # see it work
snipp bench               # see the headline numbers
```

That's it. You're set up.

---

## How to contribute

### 1. Find or open an issue first

Don't start coding without an issue — it avoids wasted work if your idea overlaps with someone else's, or if we'd rather solve it differently.

- **Bug?** → [Bug report](https://github.com/thinkfi03/snipp-cli/issues/new?template=bug_report.yml)
- **Feature idea?** → [Feature request](https://github.com/thinkfi03/snipp-cli/issues/new?template=feature_request.yml)
- **Want to add a compressor?** → [Add a compressor](https://github.com/thinkfi03/snipp-cli/issues/new?template=new_compressor.yml)
- **Question?** → Open a [Discussion](https://github.com/thinkfi03/snipp-cli/discussions), not an issue.

Issues labeled `good first issue` are scoped specifically for new contributors. Issues labeled `help wanted` are larger but well-defined.

### 2. Branch + commit

```bash
git checkout -b fix/short-description       # or feat/, docs/, test/
# make changes
pytest -q                                   # must stay green
git commit -m "Add kubectl compressor"      # imperative mood, no period
git push origin fix/short-description
```

Open a PR using the template. Link the issue number with `Closes #123`.

### 3. What we look for in a PR

| Required | What it means |
|---|---|
| Tests | New code has tests; existing tests still pass |
| One thing per PR | Don't bundle a refactor with a feature with a bugfix |
| Small enough to review | Aim for <400 lines diff. Larger is fine if the change is genuinely indivisible |
| Clear commit message | Subject line under 72 chars; body explains *why* not *what* |
| Doesn't break the public API | Imports, function signatures, and CLI flags are stable in 0.x within a minor version |

We aim to review within 48h. If we don't, ping the PR with a comment — we may have missed it.

---

## Writing a new compressor

The [`new_compressor.yml`](https://github.com/thinkfi03/snipp-cli/issues/new?template=new_compressor.yml) issue template walks you through scoping it. Here's the code path:

1. **Add a file** — `src/snipp/compressors/<your_tool>.py`
2. **Subclass `BaseCompressor`** — see `src/snipp/compressors/base.py` for the contract
3. **Implement `compress(output, query=None) -> CompressResult`** — return compressed text + fidelity metrics + handles
4. **Register** — add an entry to `src/snipp/compressors/__init__.py`'s `_REGISTRY` if it maps to a new `ToolType`, or use `register_plugin_compressor()` if it's a third-party tool
5. **Add detector hint** — `src/snipp/detector.py` should recognize the tool from argv[0] and/or output heuristics
6. **Add tests** — `tests/test_<your_tool>_compressor.py` with at least:
   - Round-trip on a real output sample (you can find examples in `bench/fixtures/` or generate one)
   - Fidelity metric is populated
   - Reduction > 50% on a realistic input
   - Edge cases: empty input, malformed output, very small input

**Example skeleton:**

```python
# src/snipp/compressors/kubectl.py
from snipp.compressors.base import BaseCompressor, CompressResult

class KubectlCompressor(BaseCompressor):
    def compress(self, output, query=None):
        # parse kubectl output, drop healthy pods, keep failures + summary
        # ...
        return CompressResult(
            compressed=...,
            original_tokens=...,
            compressed_tokens=...,
            tool_type="kubectl",
            strategy="failures_only",
            tokenizer=self.tokenizer.name,
            exact_tokens=self.tokenizer.exact,
            fidelity={"healthy_pods_dropped": 198, "failures_kept": 2},
        )
```

That's roughly 100 lines including parsing, ranking, and tests. Reference: `src/snipp/compressors/pytest.py` is a good shape to copy.

---

## Code style

- **Format:** `ruff format` (auto-applied by pre-commit when set up)
- **Lint:** `ruff check`
- **Types:** type annotations on public functions; we don't run `mypy --strict` yet but won't reject PRs that add types
- **Imports:** stdlib first, third-party second, local last; `ruff` enforces this
- **Docstrings:** required on public classes and functions; one-line summary minimum; no need for full Sphinx style

Run `ruff check .` and `pytest -q` before pushing. CI runs both on every PR.

---

## Testing

- Tests live in `tests/`, mirroring `src/snipp/`
- Use `pytest`, not `unittest`
- New test files: `tests/test_<module>.py`
- Test isolation: write to `tmp_path`, not `~/.cache/snipp/` — the conftest fixture handles cache redirection automatically

A good test:
- Uses a real-shaped fixture (e.g. real grep output, not synthetic)
- Asserts both reduction (token count) and fidelity (matches preserved)
- Has a regression name if it pins a specific bug fix

---

## Releasing (maintainers only)

```bash
# Bump version in pyproject.toml + src/snipp/__init__.py
# Update CHANGELOG.md
git tag -a v0.X.Y -m "v0.X.Y — what changed"
git push origin main --tags
# CI publishes to PyPI via Trusted Publishing
```

---

## Code of Conduct

We use the [Contributor Covenant 2.1](./CODE_OF_CONDUCT.md). Be kind, be specific, be technical. We moderate hard against personal attacks and dismissive language. Disagreement on technical decisions is welcome and encouraged.

---

## License

By contributing, you agree your contributions will be licensed under the project's [MIT License](../LICENSE).

---

## Questions?

Open a [Discussion](https://github.com/thinkfi03/snipp-cli/discussions). For private matters (security, conduct), email the maintainer (see [SECURITY.md](./SECURITY.md)).

Thanks for helping. Even tiny PRs add up.
