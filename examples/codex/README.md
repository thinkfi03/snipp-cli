# Codex CLI Integration

Codex doesn't yet expose a stable hook spec, so use the proxy mode:

```bash
snipp proxy --model codex -- codex --task "fix flaky test"
```

This wraps the Codex child process and compresses chunks of stdout that
exceed `--threshold-bytes` (default 4096). Smaller outputs pass through
verbatim so quick interactions aren't perturbed.

When Codex's middleware spec stabilizes, `snipp install codex`
will write the hook config directly.
