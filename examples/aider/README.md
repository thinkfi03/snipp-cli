# Aider Integration

Aider exposes a `--shell-command` hook for pre/post processing. Wrap your
common shell helpers with snipp:

```bash
# .aider.conf.yml
shell-commands:
  - "alias g='grep -rn'"
  - "alias gcc='grep -rn \"$@\" | snipp --tool grep --model gpt-4o'"
  - "alias pcc='pytest -v 2>&1 | snipp --tool pytest --model gpt-4o'"
```

Or use the proxy:

```bash
snipp proxy --model gpt-4o -- aider --model gpt-4o
```
