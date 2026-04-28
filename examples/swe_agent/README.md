# SWE-agent Integration

SWE-agent's `_communicate` step shells out and pipes output back to the
LLM. The cleanest integration is to wrap commands with snipp at the
config level:

```yaml
# config/snipp.yaml
post_command_filter: "snipp --model gpt-4o --max-tokens 4000"
```

Or override per command:

```yaml
commands:
  - name: search
    code: "grep -rn '$ARG1' . | snipp --tool grep --query '$ARG1' --model gpt-4o"
```
