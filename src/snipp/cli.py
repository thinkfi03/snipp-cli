"""CLI for snipp.

Subcommands:
  snipp              compress stdin (default)
  snipp run          run a child command and compress its stdout
  snipp hook         Claude Code / Codex hook adapter
  snipp proxy        wrap an upstream agent process
  snipp install      write hook config into Claude Code / Codex settings
  snipp expand       resolve an elision handle back to original content
  snipp bench        run benchmarks on bundled fixtures
  snipp demo         run a tiny demo

Stdin path is the default. The `run` subcommand is the only one that
executes a child command; argv is split safely (no shell).
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import List

import click
from rich.console import Console
from rich.table import Table

from snipp.config import Config
from snipp.config_loader import load_config, write_default_config
from snipp.core import compress_output
from snipp.expand import default_store, ExpansionStore

console = Console()


def _stats_table(result) -> Table:
    table = Table(title="Compression Stats", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Tool", result.tool_type)
    table.add_row("Strategy", result.strategy)
    table.add_row(
        "Tokenizer",
        f"{result.tokenizer} ({'exact' if result.exact_tokens else 'estimate'})",
    )
    table.add_row("Original", str(result.original_tokens))
    table.add_row("Compressed", str(result.compressed_tokens))
    table.add_row("Saved", str(result.savings))
    table.add_row("Reduction", f"{result.reduction_pct:.1f}%")
    if result.fidelity:
        table.add_row("Fidelity", json.dumps(result.fidelity))
    if result.elision_handles:
        table.add_row("Handles", f"{len(result.elision_handles)} elided sections")
    return table


def _load_cfg(ctx) -> Config:
    """Load config from --config flag or search paths."""
    config_path = ctx.obj.get("config_path") if ctx.obj else None
    return load_config(config_path)


@click.group(invoke_without_command=True)
@click.option("--tool", "-t")
@click.option("--query", "-q")
@click.option("--max-tokens", "-m", type=int, default=4000)
@click.option("--model")
@click.option("--stage", type=click.Choice(["explore", "edit", "debug", "verify"]))
@click.option("--config", "-c", help="Path to YAML config file")
@click.option("--verbose", "-v", is_flag=True)
@click.option("--stats-only", "-s", is_flag=True)
@click.option("--output", "-o", type=click.File("w"), default="-")
@click.pass_context
def main(ctx, tool, query, max_tokens, model, stage, config, verbose, stats_only, output):
    """Intent-aware tool output compressor for coding agents."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    if ctx.invoked_subcommand is not None:
        return
    if sys.stdin.isatty():
        click.echo(ctx.get_help())
        sys.exit(0)
    raw = sys.stdin.read()
    if not raw.strip():
        sys.exit(0)
    cfg = _load_cfg(ctx)
    cfg.default_max_tokens = max_tokens
    cfg.model = model
    cfg.stage = stage
    result = compress_output(
        output=raw,
        command=tool or "",
        query=query,
        config=cfg,
        model=model,
        stage=stage,
    )
    if stats_only:
        console.print(_stats_table(result))
    else:
        output.write(result.compressed)
        output.write("\n")
    if verbose:
        console.print(_stats_table(result))


@main.command()
@click.argument("argv", nargs=-1, required=True)
@click.option("--query", "-q")
@click.option("--max-tokens", "-m", type=int, default=4000)
@click.option("--model")
@click.option("--stage", type=click.Choice(["explore", "edit", "debug", "verify"]))
@click.option("--verbose", "-v", is_flag=True)
@click.pass_context
def run(ctx, argv, query, max_tokens, model, stage, verbose):
    """Run a command and compress its stdout."""
    proc = subprocess.run(list(argv), capture_output=True, text=True, shell=False)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    cfg = _load_cfg(ctx)
    cfg.default_max_tokens = max_tokens
    cfg.model = model
    cfg.stage = stage
    result = compress_output(
        output=proc.stdout,
        command=" ".join(shlex.quote(a) for a in argv),
        query=query,
        config=cfg,
        model=model,
        stage=stage,
    )
    sys.stdout.write(result.compressed)
    sys.stdout.write("\n")
    if verbose:
        console.print(_stats_table(result))
    sys.exit(proc.returncode)


@main.command()
@click.option(
    "--event",
    type=click.Choice(["PreToolUse", "PostToolUse", "auto"]),
    default="auto",
)
@click.option("--max-tokens", "-m", type=int, default=4000)
@click.option("--model")
@click.option("--stage", type=click.Choice(["explore", "edit", "debug", "verify"]))
@click.option("--passthrough/--no-passthrough", default=True)
@click.pass_context
def hook(ctx, event, max_tokens, model, stage, passthrough):
    """Claude Code / Codex hook adapter.

    Reads a JSON envelope from stdin and emits a compressed envelope.
    See examples/claude_code/settings.json for the matching hook config.
    """
    raw = sys.stdin.read()
    try:
        env = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        sys.stderr.write(f"snipp hook: bad JSON: {e}\n")
        sys.stdout.write(raw)
        sys.exit(0)

    if event == "auto":
        if env.get("hook_event_name") != "PostToolUse":
            sys.stdout.write("{}")
            return

    tool_name = env.get("tool_name", "")
    tool_input = env.get("tool_input", {}) or {}
    tool_response = env.get("tool_response", {}) or {}

    if tool_name in ("Bash", "BashOutput"):
        command = tool_input.get("command", "")
    elif tool_name == "Read":
        command = f"cat {tool_input.get('file_path') or tool_input.get('path') or ''}"
    elif tool_name == "Grep":
        command = f"grep {tool_input.get('pattern', '')}"
    else:
        command = ""

    raw_stdout = (
        tool_response.get("stdout")
        or tool_response.get("output")
        or tool_response.get("content")
        or ""
    )
    raw_stderr = tool_response.get("stderr", "")

    if not raw_stdout:
        sys.stdout.write("{}")
        return
    if passthrough and len(raw_stdout) < max_tokens * 2:
        sys.stdout.write("{}")
        return

    cfg = _load_cfg(ctx)
    cfg.default_max_tokens = max_tokens
    cfg.model = model
    cfg.stage = stage

    result = compress_output(
        output=raw_stdout,
        command=command,
        query=env.get("query"),
        config=cfg,
        model=model,
        stage=stage,
    )

    response = {
        "decision": "modify",
        "modified_response": {
            "stdout": result.compressed,
            "stderr": raw_stderr,
        },
        "metadata": {
            "snipp": {
                "tool_type": result.tool_type,
                "strategy": result.strategy,
                "tokenizer": result.tokenizer,
                "exact_tokens": result.exact_tokens,
                "original_tokens": result.original_tokens,
                "compressed_tokens": result.compressed_tokens,
                "reduction_pct": round(result.reduction_pct, 1),
                "fidelity": result.fidelity,
                "handles": result.elision_handles,
            }
        },
    }
    sys.stdout.write(json.dumps(response))


@main.command()
@click.argument("argv", nargs=-1, required=True)
@click.option("--max-tokens", "-m", type=int, default=4000)
@click.option("--model")
@click.option("--stage", type=click.Choice(["explore", "edit", "debug", "verify"]))
@click.option("--threshold-bytes", type=int, default=4096)
@click.pass_context
def proxy(ctx, argv, max_tokens, model, stage, threshold_bytes):
    """Wrap a child process and compress its stdout in chunks."""
    cfg = _load_cfg(ctx)
    cfg.default_max_tokens = max_tokens
    cfg.model = model
    cfg.stage = stage

    proc = subprocess.Popen(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=1,
        text=True,
    )
    assert proc.stdout is not None
    assert proc.stderr is not None

    buf: List[str] = []
    buf_bytes = 0
    for line in proc.stdout:
        buf.append(line)
        buf_bytes += len(line)
        if line.strip() == "" and buf_bytes >= threshold_bytes:
            chunk = "".join(buf)
            r = compress_output(chunk, command="", config=cfg, model=model, stage=stage)
            sys.stdout.write(r.compressed)
            sys.stdout.write("\n")
            buf, buf_bytes = [], 0
    if buf:
        chunk = "".join(buf)
        if buf_bytes >= threshold_bytes:
            r = compress_output(chunk, command="", config=cfg, model=model, stage=stage)
            sys.stdout.write(r.compressed)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(chunk)
    proc.wait()
    for line in proc.stderr:
        sys.stderr.write(line)
    sys.exit(proc.returncode)


@main.command("init")
@click.option("--path", help="Config file path (default: ~/.config/snipp/config.yaml)")
def init(path):
    """Write a default config file."""
    target = write_default_config(path)
    console.print(f"[green]Created default config at {target}[/green]")


@main.command()
@click.argument("handle")
@click.option("--session", default=None)
def expand(handle, session):
    """Resolve an elision handle back to original content."""
    store = ExpansionStore(session=session) if session else default_store()
    content = store.expand(handle)
    if content is None:
        sys.stderr.write(f"No content found for handle: {handle}\n")
        sys.exit(1)
    sys.stdout.write(content)


@main.command()
@click.argument("agent", type=click.Choice(["claude-code", "codex", "cursor", "all"]))
@click.option("--scope", type=click.Choice(["user", "project"]), default="user")
@click.option("--dry-run", is_flag=True)
@click.option("--mcp", is_flag=True, help="Install as MCP server instead of legacy hook")
@click.option("--uninstall", is_flag=True, help="Remove all integrations")
def install(agent, scope, dry_run, mcp, uninstall):
    """Install snipp as a hook or MCP server for the given agent."""
    if uninstall:
        # Best-effort removal
        removed = []
        for p in [
            Path.home() / ".claude" / "settings.json",
            Path.cwd() / ".claude" / "settings.json",
            Path.home() / ".codex" / "config.json",
            Path.cwd() / ".codex" / "config.json",
            Path.home() / ".cursor" / "mcp.json",
            Path.cwd() / ".cursor" / "mcp.json",
        ]:
            if p.exists():
                text = p.read_text(encoding="utf-8")
                if "snipp" in text or "snipp" in text:
                    # Naive removal — in production, parse and surgically remove
                    p.rename(p.with_suffix(p.suffix + ".bak"))
                    removed.append(str(p))
        if removed:
            console.print(f"[yellow]Removed integrations from:[/yellow] {', '.join(removed)}")
        else:
            console.print("[yellow]No integrations found to remove.[/yellow]")
        return

    targets = []
    if agent in ("claude-code", "all"):
        targets.append(("claude-code", scope))
    if agent in ("codex", "all"):
        targets.append(("codex", scope))
    if agent in ("cursor", "all"):
        targets.append(("cursor", scope))

    for ag, sc in targets:
        _install_agent(ag, sc, dry_run, mcp)


def _install_agent(agent: str, scope: str, dry_run: bool, mcp: bool) -> None:
    if agent == "claude-code":
        path = (
            Path.home() / ".claude" / "settings.json"
            if scope == "user"
            else Path.cwd() / ".claude" / "settings.json"
        )
        if mcp:
            block = {
                "mcpServers": {
                    "snipp": {
                        "command": "snipp-mcp",
                        "args": [],
                    }
                }
            }
        else:
            block = {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "snipp hook --event PostToolUse",
                                }
                            ],
                        }
                    ]
                }
            }
    elif agent == "codex":
        path = (
            Path.home() / ".codex" / "config.json"
            if scope == "user"
            else Path.cwd() / ".codex" / "config.json"
        )
        if mcp:
            block = {
                "mcpServers": {
                    "snipp": {
                        "command": "snipp-mcp",
                        "args": [],
                    }
                }
            }
        else:
            block = {"middleware": {"tool_output": "snipp hook"}}
    elif agent == "cursor":
        path = (
            Path.home() / ".cursor" / "mcp.json"
            if scope == "user"
            else Path.cwd() / ".cursor" / "mcp.json"
        )
        block = {
            "mcpServers": {
                "snipp": {
                    "command": "snipp-mcp",
                    "args": [],
                }
            }
        }
    else:
        console.print(f"[red]Unknown agent: {agent}[/red]")
        return

    if dry_run:
        console.print(f"[bold]Would write to:[/bold] {path}")
        console.print(json.dumps(block, indent=2))
        return
    _merge_settings(path, block)
    console.print(f"[green]Installed snipp {'MCP server' if mcp else 'hook'} into {path}[/green]")


def _merge_settings(path: Path, block: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except json.JSONDecodeError:
            backup = path.with_suffix(path.suffix + ".bak")
            path.rename(backup)
            console.print(f"[yellow]Backed up unparseable settings to {backup}[/yellow]")
            existing = {}
    merged = _deep_merge(existing, block)
    path.write_text(json.dumps(merged, indent=2))


def _deep_merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        elif k in out and isinstance(out[k], list) and isinstance(v, list):
            out[k] = out[k] + v
        else:
            out[k] = v
    return out


@main.command()
@click.option("--fixtures", type=click.Path(exists=True, file_okay=False))
@click.option("--model")
@click.pass_context
def bench(ctx, fixtures, model):
    """Run benchmarks on a directory of fixtures."""
    cfg = _load_cfg(ctx)
    chosen_model = model or cfg.model
    from snipp.bench import run_bench
    run_bench(Path(fixtures) if fixtures else None, model=chosen_model)


@main.command()
@click.pass_context
def demo(ctx):
    """Run a tiny demo."""
    grep_output = (
        "file1.py:10:# TODO: refactor\n"
        "file1.py:25:# TODO: error handling\n"
        "Binary file lockfile matches\n"
        "src/utils.py:100:# TODO: doc\n"
    ) * 30
    cfg = _load_cfg(ctx)
    result = compress_output(grep_output, command="grep -r TODO .", query="TODO", config=cfg)
    console.print(_stats_table(result))


@main.command()
@click.option("--json", "as_json", is_flag=True, help="Emit raw JSON instead of a table")
def stats(as_json):
    """Summarize local telemetry: total tokens saved, p50/p95, top tools."""
    from snipp.mcp.observability import (
        summarize_telemetry, telemetry_path, telemetry_opt_in,
    )
    summary = summarize_telemetry()
    summary["telemetry_path"] = str(telemetry_path())
    summary["remote_opt_in"] = telemetry_opt_in()
    if as_json:
        console.print_json(data=summary)
        return
    table = Table(title="snipp telemetry summary", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Total requests", str(summary["total_requests"]))
    table.add_row("Tokens saved", f"{summary['total_tokens_saved']:,}")
    table.add_row("Avg reduction", f"{summary['avg_reduction_pct']:.1f}%")
    table.add_row("p50 wall time", f"{summary['p50_wall_ms']:.1f}ms")
    table.add_row("p95 wall time", f"{summary['p95_wall_ms']:.1f}ms")
    table.add_row("Telemetry path", summary["telemetry_path"])
    table.add_row("Remote opt-in", "yes" if summary["remote_opt_in"] else "no (local-only)")
    console.print(table)
    if summary["top_tools"]:
        tt = Table(title="Top tools", show_header=True)
        tt.add_column("Tool")
        tt.add_column("Calls")
        tt.add_column("Tokens saved")
        for row in summary["top_tools"]:
            tt.add_row(
                row["tool"], str(row["count"]), f"{row['tokens_saved']:,}"
            )
        console.print(tt)


@main.command()
@click.option("--enable/--disable", default=True, help="Opt in or out of remote aggregate telemetry")
def telemetry(enable):
    """Manage opt-in remote aggregate telemetry."""
    from snipp.mcp.observability import set_telemetry_opt_in
    p = set_telemetry_opt_in(enable)
    state = "ENABLED" if enable else "DISABLED"
    console.print(f"[green]Remote telemetry {state}[/green]  (config: {p})")
    console.print("Local JSONL telemetry is always on by default; only aggregate sharing is opt-in.")


@main.command()
def metrics():
    """Render current Prometheus metrics text from this process.

    Note: useful for inspecting an in-process registry. To scrape live metrics,
    pass --metrics-port to `snipp-mcp`.
    """
    from snipp.mcp.observability import render_metrics
    console.print(render_metrics())


if __name__ == "__main__":
    main()
