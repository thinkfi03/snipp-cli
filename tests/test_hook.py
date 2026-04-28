"""Tests for the Claude Code / Codex hook subcommand."""

import json
import subprocess
import sys


def _run_hook(envelope: dict, *args: str) -> tuple[str, str, int]:
    proc = subprocess.run(
        [sys.executable, "-m", "snipp.cli", "hook", *args],
        input=json.dumps(envelope),
        capture_output=True,
        text=True,
    )
    return proc.stdout, proc.stderr, proc.returncode


def test_post_tool_use_compresses_large_output():
    big = "file.py:10:def x(): pass\n" * 5000
    env = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "grep -r def ."},
        "tool_response": {"stdout": big, "stderr": ""},
    }
    out, err, rc = _run_hook(env, "--max-tokens", "500", "--no-passthrough")
    assert rc == 0, err
    payload = json.loads(out)
    assert payload["decision"] == "modify"
    assert payload["metadata"]["snipp"]["tool_type"] == "grep"
    assert payload["metadata"]["snipp"]["compressed_tokens"] < payload["metadata"][
        "snipp"
    ]["original_tokens"]


def test_passthrough_for_small_output():
    env = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "tool_response": {"stdout": "short\n", "stderr": ""},
    }
    out, _, rc = _run_hook(env, "--max-tokens", "4000")
    assert rc == 0
    assert out.strip() == "{}"


def test_non_post_tool_use_event_passes():
    env = {"hook_event_name": "UserPromptSubmit"}
    out, _, rc = _run_hook(env)
    assert rc == 0
    assert out.strip() == "{}"


def test_bad_json_passes_through():
    proc = subprocess.run(
        [sys.executable, "-m", "snipp.cli", "hook"],
        input="not json",
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "not json" in proc.stdout
