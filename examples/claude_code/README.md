# Claude Code Integration

Drop the `settings.json` block into `~/.claude/settings.json` (user scope) or
`./.claude/settings.json` (project scope), or run:

```bash
snipp install claude-code           # user scope
snipp install claude-code --scope project
snipp install claude-code --dry-run # preview only
```

## What happens

For every `PostToolUse` event matching `Bash`, `Read`, or `Grep`, Claude Code
pipes the JSON envelope into `snipp hook`, which:

1. Inspects `tool_input.command` to detect the tool (grep / pytest / git / ...)
2. Routes to the matching compressor with `--model claude` for exact tokens
3. Emits a `decision: modify` envelope with compressed `stdout`
4. Stores elided sections in `~/.cache/snipp/<session>/` so the agent
   can call `snipp expand <handle>` later.

## Tuning

| Tool   | Suggested `--max-tokens` | Why                                  |
|--------|--------------------------|--------------------------------------|
| Bash   | 4000                     | typical pytest/grep/ls output        |
| Read   | 6000                     | code files need more headroom        |
| Grep   | 3000                     | match lists compress aggressively    |

Set `--stage debug` while triaging failures to keep full tracebacks. Use
`--passthrough` (default) so small outputs skip compression entirely.
