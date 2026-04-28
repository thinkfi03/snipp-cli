# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in snipp, **please do not open a public issue.** Vulnerabilities reported privately are fixed faster and prevent in-the-wild exploitation.

### How to report

Open a [private security advisory](https://github.com/thinkfi03/snipp-cli/security/advisories/new) on GitHub. We monitor these and respond within 72 hours.

If GitHub Advisories isn't an option for you, email the maintainer with subject line `[snipp security]`.

Please include:

- A description of the vulnerability
- Steps to reproduce (proof-of-concept code is welcome)
- The version of snipp affected
- Your assessment of impact (what an attacker could achieve)

### What to expect

| Step | Timeline |
|---|---|
| Acknowledgment of report | Within 72 hours |
| Initial triage + severity assessment | Within 7 days |
| Patch developed + tested | Severity-dependent (critical: <72h, high: <7 days, medium/low: next release) |
| Coordinated disclosure | After patch is released; we credit you in the changelog unless you prefer anonymity |

## Supported Versions

| Version | Supported |
|---|---|
| 0.4.x | ✅ |
| 0.3.x | ❌ (please upgrade) |
| < 0.3 | ❌ |

We support the current minor version and the immediately previous one. Older versions get security fixes only at the maintainer's discretion.

## Security Model

snipp's threat model is documented at a high level here. The exhaustive version is in `docs/security/threat_model.md`.

### What snipp protects against

- **Subprocess command injection** — argv allowlist + no shell + word-boundary argv0 matching
- **Resource exhaustion** — RLIMIT_AS (2GB), RLIMIT_FSIZE (100MB per file), RLIMIT_CPU (600s) on every spawned subprocess
- **Process tree leaks** — process-group kill on timeout reaps children, not just the leader
- **Path traversal** — handle and session-id regexes, plus Path.resolve() containment check on every filesystem operation
- **Plugin sandbox escapes** — plugins run with the same ulimits and process-group isolation as `snipp_run`; failures fall back to the generic compressor instead of taking down the request
- **Env secret leakage** — inherited environment is filtered through an allowlist before subprocess execution; secrets like `ANTHROPIC_API_KEY` are stripped by default
- **Telemetry leakage** — local-only JSONL by default; remote shipping is opt-in and only ships aggregates, never command bodies or compressed content

### What snipp does NOT protect against

- **Malicious plugins authored by the user** — if you `register-plugin` an executable, it runs on your machine. Don't register plugins you didn't audit
- **Network-side attacks** — snipp makes zero outbound calls in the default path; HTTP transport is disabled
- **Compromised tokenizer downloads** — HuggingFace model downloads happen via the `transformers` library at first use; trust their supply chain
- **Compromised PyPI install** — verify our wheel is signed (we use PyPI Trusted Publishing) and check `pip install --no-deps` if you want minimal trust

### Disclosure history

No vulnerabilities reported yet.

---

Thank you for helping keep snipp and its users safe.
