# snipp

> **The tl;dr for your terminal.**
> Cut the noise. Keep the signal. Save 80–95% of the tokens your AI coding agent burns on tool output.

[![PyPI](https://img.shields.io/pypi/v/snipp-cli.svg)](https://pypi.org/project/snipp-cli/)
[![Python](https://img.shields.io/pypi/pyversions/snipp-cli.svg)](https://pypi.org/project/snipp-cli/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Tests](https://img.shields.io/badge/tests-232%20passing-brightgreen.svg)](#testing)
[![MCP](https://img.shields.io/badge/MCP-1.20%2B-blue.svg)](https://modelcontextprotocol.io)

---

Coding agents like Claude Code, Cursor, and Codex burn 50–95% of every tool call's tokens on output the agent never reads — and you pay for all of it.

**snipp sits between your agent and the tools it runs.** It compresses `grep` / `pytest` / `git` / `npm` / `kubectl` output before the LLM sees it — preserving every failure, every match, every signature, dropping every noise line. Lossy where it doesn't matter, recoverable where it does.

```bash
pip install snipp-cli[mcp]
snipp install claude-code --mcp
# restart Claude Code — done. No workflow change.
```

---

## What you save (real numbers, real fixtures)

| Tool | Original | Compressed | Reduction | p95 latency |
|---|---:|---:|---:|---:|
| `grep -rn TODO .` (Django monorepo)  | 14,800 tok | 1,150 tok | **92.2%** | 18 ms |
| `pytest -v` (47 tests, 3 fail)        |  5,200 tok |   380 tok | **92.7%** |  4 ms |
| `cat src/api.py` (820 lines)          |  6,400 tok |   480 tok | **92.5%** |  9 ms |
| `git log --oneline -50`               |  8,000 tok |   620 tok | **92.3%** |  6 ms |
| `npm install` (Next.js app)           |  4,200 tok |   180 tok | **95.7%** |  3 ms |
| `git diff` (2 files, 18+/4-)          |  2,800 tok |   220 tok | **92.1%** |  5 ms |

Run `snipp bench` after install to reproduce.

---

## Why snipp exists

Coding agents waste tokens on:

- `grep -rn "TODO" .` → 12,000 tokens of matches the agent will read 5 of
- `pytest -v` → 5,000 tokens of `PASSED` lines surrounding 2 actual failures
- `git log -50` → 8,000 tokens of commit bodies for the 3 commits that matter
- `cat large_module.py` → 3,000 tokens of implementation when the agent needs signatures
- `npm install` → dependency-tree noise around one error line
- `kubectl get pods -A` → 200 healthy pods around the 2 unhealthy ones

The pain isn't dollars — it's **session continuity**. 4-hour debugging sessions hit context-full at 95% and crash. snipp keeps you under 30%.

---

## Install

```bash
# Core CLI (no extras)
pip install snipp-cli

# With MCP server — the recommended path for Claude Code, Cursor, Zed, Cline, Continue
pip install snipp-cli[mcp]

# Everything (tokenizers, MCP, tree-sitter, observability)
pip install snipp-cli[all]
```

> **Note:** package name is `snipp-cli` on PyPI; the binary, import name, and MCP server name are all `snipp`. Same pattern as `python-dateutil` → `dateutil`.

Requires Python 3.10+. MIT licensed. Zero outbound network calls in the default path.

---

## Three ways to use it

### 1. MCP server (the recommended path)

snipp ships an [MCP](https://modelcontextprotocol.io) server. Any MCP-aware agent calls it transparently when about to dump large tool output.

```bash
snipp install claude-code --mcp
snipp install cursor --mcp
snipp install zed --mcp
snipp install all --mcp        # write configs everywhere they're found
snipp install --uninstall      # clean removal
```

This writes one block to your agent's settings file:

```json
{
  "mcpServers": {
    "snipp": {
      "command": "snipp-mcp",
      "args": []
    }
  }
}
```

Restart your agent. From this point on, **every tool call is automatically compressed** before the LLM sees it. You change nothing about how you work.

### 2. Pipe through the CLI

For agents that shell out (Aider, OpenHands, browser chat, in-house agents):

```bash
grep -rn "TODO" . | snipp --tool grep --verbose
pytest -v        | snipp --tool pytest
git log -50      | snipp --tool git
cat large.py     | snipp --tool cat --query "auth flow"
```

### 3. Run a command and compress its output

```bash
snipp run --model claude --stage debug -- pytest -v
snipp run --model gpt-4o -- grep -rn "import" .
```

`--model` picks the exact tokenizer (tiktoken / Anthropic / HuggingFace). `--stage` (`explore` / `edit` / `debug` / `verify`) tunes how aggressive the compression is.

---

## MCP tools reference

Once `snipp install <agent> --mcp` is in place, your agent has these 10 tools available. **You don't call these directly** — the agent invokes them as needed. This is the surface for capability-aware agents (Claude Code, Cursor, Zed, Cline, Continue):

| Tool | What it does | Typical caller |
|---|---|---|
| `snipp_compress` | Compress an output the agent already has in hand | Agent post-processing any tool output |
| `snipp_run` | Execute a command in a sandbox (ulimits + process-group), capture stdout, compress it | Agent that wants snipp to also run the command |
| `snipp_run_streaming` | Same as `run` but emits MCP progress notifications during long-running commands | Long pytest / npm install / build runs |
| `snipp_expand` | Recover the original content behind an elision handle | Agent realizes it needs the elided detail |
| `snipp_detect` | Identify which tool produced a given output (returns tool + confidence) | Routing logic; rarely called directly |
| `snipp_list_handles` | List every elision handle in the current session, sorted by age | Agent wants to know what's recoverable |
| `snipp_register_plugin` | Register a third-party compressor executable at runtime | Power users; persistent plugins reload on server start |
| `snipp_health` | Server version, uptime, tokenizer backends, plugin count, cache size | Liveness / debug checks |
| **`snipp_explore_repo`** *(new in 0.4)* | Walk a codebase root, extract AST signatures, return a token-capped, BM25-ranked context document | Agent starting work on an unfamiliar repo |
| **`snipp_show_symbol`** *(new in 0.4)* | Find a named symbol (function/class) in a codebase and return its signature + body preview | Agent following a specific function across files |

### The two new tools — repo intelligence

`snipp_explore_repo` and `snipp_show_symbol` extend snipp from "compress what the agent fetched" to "help the agent decide what to fetch in the first place."

**`snipp_explore_repo`** — for "what does this codebase look like?"

```jsonc
// Agent sends:
{
  "name": "snipp_explore_repo",
  "arguments": {
    "root": "/path/to/repo",
    "query": "authentication flow",
    "max_tokens": 2000
  }
}

// snipp returns:
{
  "context": "# Project: my-app\n## Files (42 scanned)\n## Symbols ranked by relevance to 'authentication flow'\n### auth/login.py\n  def authenticate(user, password) -> Token: ...\n  def hash_password(plain: str) -> str: ...\n  [<<elide:a3f2:18L>>]\n### middleware/jwt.py\n  class JWTMiddleware: ...\n  ...",
  "files_scanned": 42,
  "symbols_found": 187,
  "tokens": 1840,
  "elapsed_ms": 89.2
}
```

The agent gets a navigable map in ~2,000 tokens instead of reading every file. Implementations are elided to handles; the agent can `snipp_expand` any one to drill in.

**`snipp_show_symbol`** — for "show me this exact function"

```jsonc
// Agent sends:
{
  "name": "snipp_show_symbol",
  "arguments": {
    "root": "/path/to/repo",
    "symbol_name": "authenticate"
  }
}

// snipp returns:
{
  "symbol": "authenticate",
  "kind": "function",
  "file": "auth/login.py",
  "signature": "def authenticate(user: str, password: str) -> Token:",
  "body_preview": "    if not user or not password:\n        raise ValueError(\"missing creds\")\n    hashed = hash_password(password)\n    ...",
  "found": true,
  "elapsed_ms": 12.4
}
```

Goes from "agent greps, reads three files, finally finds the signature" to "agent gets the answer in one tool call."

### MCP resources

Three read-only resources are also exposed for introspection (no side effects):

| URI | Returns |
|---|---|
| `snipp://session/current` | Active sessions, total handles, cache size |
| `snipp://session/{session_id}/{handle}` | Original content for a specific handle |
| `snipp://config/current` | Resolved config snapshot (after YAML + flag merge) |

### How the agent actually uses these (typical Claude Code session)

```
1. User: "Find duplicate refund email bug"
2. Claude → snipp_run({"argv": ["grep", "-rn", "refund.*email", "."]})
3. Claude reads compressed grep result (1,150 tokens vs 14,800 raw)
4. Claude → snipp_show_symbol({"root": ".", "symbol_name": "_on_refund_finalized"})
5. Claude reads the function signature + body preview
6. Claude → snipp_expand("a3f7d2e1") to see full implementation
7. Claude proposes fix, runs snipp_run again on pytest, ships the diff
```

No prompt engineering, no manual paste. The agent decides when to compress, when to expand, when to explore. snipp is the toolkit; the agent is the chooser.

---

## What gets compressed

| Tool | Strategy | Typical reduction |
|---|---|---:|
| `grep` / `rg` / `ag` | Match dedup, file grouping, BM25 query ranking, binary noise removal | 85–95% |
| `git log` | Compact commit format, configurable depth, body summary | 80–90% |
| `git diff` | Stats + first-file sample + full-diff handle | 90–95% |
| `git status` | Grouped staged / unstaged / untracked | 50–70% |
| `cat` / `head` / `tail` (code) | AST-aware (Python via stdlib `ast`; JS/TS/Go/Rust/Java/C/C++ via tree-sitter): keep signatures, elide bodies | 60–85% |
| `cat` (data) | JSON/YAML structural truncation | 60–80% |
| `ls` / `tree` | Group by type, sort by relevance | 60–80% |
| `find` / `fd` | Directory grouping + dedup | 70–85% |
| `pytest` | Full failure traces, collapse passes | 70–95% |
| `jest` / `vitest` / `mocha` / `tap` | Same shape as pytest, framework-aware | 75–95% |
| `npm` / `yarn` / `pnpm` install / build | Errors + bundle stats; strip progress noise | 90–98% |
| `eslint` / `tsc` / `prettier` | Dedup by `(file, line, msg)`, summary preserved | 80–95% |
| `docker` build / ps / logs | Steps + errors; tail logs with handle for head | 60–85% |
| Generic | Head/tail with elision sidecar | 30–60% |

Don't see your tool? **Write a plugin** (see [Plugins](#plugins)).

---

## The expand round-trip — why lossy is safe

Every elision becomes a stable handle:

```
[<<elide:a3f7d2e1:42L>>]
```

- `a3f7d2e1` = first 8 hex chars of SHA-256 over the elided content
- `42L` = original line count (informational)

When the agent realizes it needs the dropped content, it calls one tool — `snipp expand a3f7d2e1` — and gets the original 42 lines back, byte-for-byte.

**This is what makes lossy compression safe for AI agents.** Nothing is ever permanently lost. The sidecar lives at `~/.cache/snipp/<session>/` and is content-addressed (idempotent, no collisions).

```bash
snipp expand a3f7d2e1                       # CLI
# or via MCP: agent calls snipp_expand     # automatic, no user action
```

---

## See your savings

snipp records local telemetry (JSONL, no remote calls) so you can see cumulative savings:

```bash
$ snipp stats
                    snipp telemetry summary
┏━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Metric         ┃ Value                                            ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Total requests │ 84                                               │
│ Tokens saved   │ 312,400                                          │
│ Avg reduction  │ 89.4%                                            │
│ p50 wall time  │ 14.2 ms                                          │
│ p95 wall time  │ 96.8 ms                                          │
│ Remote opt-in  │ no (local-only)                                  │
└────────────────┴──────────────────────────────────────────────────┘
```

For Prometheus scraping, run the MCP server with `--metrics-port 9100`.

---

## Stage-aware compression

The agent's task changes how aggressive compression should be. snipp ships four stages:

| Stage | Budget × | Full traces | Sigs only | Pass tests | grep ctx | git diff |
|---|---:|:-:|:-:|:-:|---:|:-:|
| `explore` |  0.6 | ❌ | ✅ | ❌ | 0 | ❌ |
| `edit`    |  1.0 | ❌ | ❌ | ❌ | 2 | ❌ |
| `debug`   |  1.5 | ✅ | ❌ | ❌ | 4 | ✅ |
| `verify`  |  1.2 | ✅ | ❌ | ✅ | 2 | ❌ |

```bash
snipp run --stage debug -- pytest tests/payment/   # keep full failure traces
snipp run --stage explore -- cat src/big_module.py # signatures only, terse
```

---

## Plugins

Have a tool snipp doesn't ship for? Write a 100-line executable that speaks JSON over stdin/stdout, register it once:

```bash
snipp register-plugin \
  --tool-name kubectl \
  --argv0 kubectl \
  --executable /usr/local/bin/snipp-kubectl \
  --persistent
```

Plugin contract (read JSON from stdin, write JSON to stdout):

```json
// stdin
{"output": "...", "query": null, "max_tokens": 2000}

// stdout
{"compressed": "...", "original_tokens": 1200, "compressed_tokens": 90, "fidelity": {}}
```

Plugins run in the same sandbox as `snipp run` — ulimits + process-group isolation + timeout. A crashing plugin falls back to the generic compressor; it never takes down the request.

Persistent plugins reload on server start from `~/.config/snipp/plugins.yaml`.

---

## Configuration

Project-local: `.snipp.yaml`. User-global: `~/.config/snipp/config.yaml`.

```yaml
default_max_tokens: 4000
model: claude-sonnet-4-6      # or gpt-4o, o3, kimi, qwen, deepseek...
stage: edit

compressors:
  grep:
    max_output_tokens: 2000
    context_lines: 2
    max_files: 50
  pytest:
    max_output_tokens: 2000
    show_passed: false
    max_failure_trace: 80
  cat:
    show_signatures: true
    keep_signatures_only: false
```

Generate a fully-commented default with `snipp init`. Unknown keys produce a warning with a "did you mean…?" suggestion.

---

## How it works

```
┌──────────────┐     stdio JSON-RPC       ┌──────────────────────┐
│  MCP client  │─────────────────────────▶│  snipp-mcp server    │
│ (Claude Code,│                          │                      │
│  Cursor,…)   │◀─────────────────────────│  ┌────────────────┐  │
└──────────────┘   compressed envelope    │  │  Detect tool   │  │
                                          │  │   ↓            │  │
                                          │  │  Route to      │  │
                                          │  │  compressor    │  │
                                          │  │   ↓            │  │
                                          │  │  Sandbox + run │  │
                                          │  │  (if `run`)    │  │
                                          │  │   ↓            │  │
                                          │  │  Compress +    │  │
                                          │  │  emit handles  │  │
                                          │  └────────────────┘  │
                                          │  ~/.cache/snipp/     │
                                          │   └ session/         │
                                          │     └ <handle>.txt   │
                                          └──────────────────────┘
```

1. **Detect** — argv0 + output heuristics with confidence scores.
2. **Compress** — tool-specific compressor (e.g. `pytest` parses the output, separates failures from passes).
3. **Elide** — anything dropped goes to a content-addressed sidecar; a stable handle replaces it inline.
4. **Rank** — when a query is provided, BM25 re-orders chunks so the relevant ones survive the budget.
5. **Return** — compressed text + fidelity metrics + handles.

All paths through the system have a fidelity number alongside the reduction percentage. Reduction without fidelity is meaningless ("compress to nothing → 100% reduction") — snipp never reports one without the other.

---

## Security

The MCP server runs subprocesses with a real sandbox:

- **Argv allowlist** for `snipp run`. ~50 commands by default; extend via config.
- **No shell.** Always `subprocess.Popen(..., shell=False)`.
- **Resource limits** (Linux/macOS): `RLIMIT_AS` (2GB virtual memory), `RLIMIT_FSIZE` (100MB per-file write), `RLIMIT_CPU` (600s).
- **Process-group kill** on timeout. When `pytest` or `npm` spawns children, all of them die together.
- **Path-traversal protection** on every handle/session path.
- **Inherited env filtered** through an allowlist before subprocess execution — secrets like `ANTHROPIC_API_KEY` don't leak into untrusted children.

HTTP transport is **disabled** in the current release. MCP is stdio-only until full Phase 7 auth (bearer + OAuth) lands.

---

## Multi-agent support

Verified working with:

| Agent | Mode | Setup |
|---|---|---|
| Claude Code | MCP | `snipp install claude-code --mcp` |
| Cursor | MCP | `snipp install cursor --mcp` |
| Zed | MCP | `snipp install zed --mcp` |
| Cline (VS Code) | MCP | manual settings edit |
| Continue.dev | MCP | manual settings edit |
| Aider | shell aliases | see `examples/aider/` |
| OpenHands | event listener | see `examples/openhands/` |
| Codex CLI | proxy | see `examples/codex/` |
| SWE-agent | post-command filter | see `examples/swe_agent/` |
| Browser chat | manual paste | use the CLI to compress, paste compressed output |

---

## Python API

```python
from snipp import compress_output, default_store

result = compress_output(
    output=raw_grep_output,
    command="grep -rn TODO .",
    query="payment flow",       # BM25 ranks results by relevance
    model="claude-sonnet-4-6",  # exact tokenizer
    stage="debug",
)

print(result.compressed)
print(f"Saved {result.savings:,} tokens ({result.reduction_pct:.1f}%)")
print(f"Fidelity: {result.fidelity}")

# Recover any elided section on demand:
for handle in result.elision_handles:
    original = default_store().expand(handle)
```

---

## Observability

Built-in observability for production use:

- **Structured JSON logs** with per-request ULID correlation; secrets redacted automatically
- **Prometheus metrics** at `--metrics-port 9100` (8 metrics: requests, duration histogram, tokens saved, handles active, plugin errors, subprocess kills, session count, cache bytes)
- **OpenTelemetry traces** when `OTEL_EXPORTER_OTLP_ENDPOINT` is set
- **Local JSONL telemetry** with size-based rotation; opt-in remote aggregation

```bash
snipp-mcp --log-format json --metrics-port 9100
snipp metrics                 # render current Prometheus exposition
snipp stats                   # local telemetry summary
snipp telemetry --enable      # opt in to anonymous aggregate stats (off by default)
```

No outbound network calls in the default path. Tokenization is local-only unless you opt into Anthropic's free `count_tokens` endpoint with `--model claude` and an API key.

---

## Status

- **Version:** 0.4.0 (alpha)
- **Tests:** 232 passing
- **Transport:** stdio MCP (HTTP coming in 0.5)
- **Audience:** single-tenant local development. Not yet for shared multi-user deployments.
- **License:** MIT

What's solid: tool-output compression, plugin sandbox, expand round-trip, observability, all 13 compressors, all major MCP-aware agents.

What's still cooking (0.5):
- HTTP transport with real bearer + OAuth
- SWE-bench A/B headline numbers
- Homebrew + Docker + single-binary release
- Rate limiting

---

## Contributing

Issues and PRs welcome. The smallest contribution path is **a new compressor** — pick a tool, write ~100 lines, add fidelity tests. The base class is documented in `src/snipp/compressors/base.py`.

To run the test suite:

```bash
git clone https://github.com/devlabs0x/snipp-cli.git
cd snipp
pip install -e ".[dev,all]"
pytest                      # 232 tests, ~10 seconds
snipp bench                 # reproduce the headline numbers
```

---

## License

[MIT](LICENSE) © 2026 snipp contributors

---

## Acknowledgements

snipp was inspired by every developer who has watched their AI agent dump 12,000 tokens of `grep` output and silently sworn at their cost dashboard. The expand-handle pattern owes a debt to git's content-addressing; the BM25 ranker is the same algorithm Lucene has used for 20 years. Tree-sitter does the heavy lifting for non-Python languages. We stand on the shoulders of unix.

> **Just snipp it.**
