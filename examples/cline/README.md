# Cline Integration

Cline runs in VS Code and shells out for tool calls. The simplest path is
to install snipp shell aliases and tell Cline to use them:

```bash
# ~/.zshrc / ~/.bashrc
alias gcc='snipp run --model claude -- grep -rn'
alias pcc='snipp run --model claude -- pytest -v'
alias lcc='snipp run --model claude -- ls -la'
```

Then in Cline's system prompt:

> When running shell commands, prefer `gcc`, `pcc`, `lcc` aliases.
> They produce token-efficient summaries of grep/pytest/ls.
