<!-- Thanks for the PR! Fill in the sections below. Anything not applicable: write "n/a". -->

## What this PR does

<!-- One paragraph: what changed and why. -->

Closes #<!-- issue number -->

## Type of change

<!-- Check all that apply -->

- [ ] 🐛 Bug fix (non-breaking change that fixes an issue)
- [ ] ✨ New feature (non-breaking change that adds functionality)
- [ ] ➕ New compressor (added support for a new tool)
- [ ] 💥 Breaking change (fix or feature that would change existing behavior)
- [ ] 📝 Documentation only
- [ ] 🧪 Tests only (no production code change)
- [ ] 🔧 Internal refactor (no user-visible change)

## Checklist

- [ ] Tests added or updated; `pytest` passes locally (`pytest -q`)
- [ ] If a new compressor: includes a fidelity test (real-shape input, asserts both reduction and fidelity metrics)
- [ ] If a public API change: README and/or `examples/` updated
- [ ] If a new dependency: justified in the PR description (smaller is better; no deps preferred)
- [ ] Commit messages are clear and follow the project's style (imperative subject < 72 chars)
- [ ] No secrets, tokens, or `.env` files committed
- [ ] If user-visible behavior changed: CHANGELOG.md entry added under `[Unreleased]`

## Numbers (if applicable)

<!-- For perf or compression PRs, paste before/after numbers from `snipp bench` or a real fixture. -->

```
Before: original=12,400  compressed=850   reduction=93.1%
After:  original=12,400  compressed=620   reduction=95.0%
```

## Notes for the reviewer

<!-- Anything that would speed up review: known limitations, open questions, files to focus on. -->
