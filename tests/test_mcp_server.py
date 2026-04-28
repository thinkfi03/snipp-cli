"""Tests for the snipp MCP server.

Covers:
  - Tool registration and schema generation
  - Core tools: compress, expand, detect, list_handles, health
  - Subprocess tools: run (with security)
  - Resources
  - Session isolation
  - Security: argv allowlist, path traversal rejection
"""

from __future__ import annotations

import asyncio
import json
import os
import pytest

from snipp.mcp.server import create_server
from snipp.mcp.services.session_registry import SessionRegistry


@pytest.fixture
def server():
    return create_server()


@pytest.fixture
def session_registry(tmp_path):
    return SessionRegistry(cache_root=tmp_path)


# ---------------------------------------------------------------------------
# Tool listing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_tools(server):
    tools = await server.list_tools()
    names = {t.name for t in tools}
    expected = {
        "snipp_compress",
        "snipp_run",
        "snipp_run_streaming",
        "snipp_expand",
        "snipp_detect",
        "snipp_list_handles",
        "snipp_register_plugin",
        "snipp_health",
    }
    assert expected <= names


# ---------------------------------------------------------------------------
# compress
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compress_pytest_output(server):
    result_tuple = await server.call_tool("snipp_compress", {
        "output": (
            "============================= test session starts ==============================\n"
            "platform darwin -- Python 3.13.1, pytest-9.0.3\n"
            "collected 4 items\n\n"
            "tests/test_demo.py::test_add PASSED\n"
            "tests/test_demo.py::test_sub PASSED\n"
            "tests/test_demo.py::test_div FAILED\n"
            "tests/test_demo.py::test_mul PASSED\n\n"
            "=========================== short test summary info ===========================\n"
            "FAILED tests/test_demo.py::test_div - AssertionError: expected 2, got 0\n"
            "========================= 1 failed, 3 passed in 0.01s ==========================\n"
        ),
        "command": "pytest",
    })
    content, data = result_tuple
    assert data["tool_type"] == "pytest"
    assert "failed" in data["compressed"].lower() or "1 failed" in data["compressed"]
    assert data["original_tokens"] > 0
    assert data["elapsed_ms"] >= 0


@pytest.mark.asyncio
async def test_compress_npm_test_output(server):
    result_tuple = await server.call_tool("snipp_compress", {
        "output": (
            "PASS src/utils.test.js\n"
            "  utils\n"
            "    ✓ should format date\n\n"
            "FAIL src/auth.test.js\n"
            "  auth\n"
            "    ✕ should logout\n"
            "      Error: expect(received).toBe(expected)\n\n"
            "Test Suites: 1 failed, 1 passed, 2 total\n"
            "Tests:       1 failed, 3 passed, 4 total\n"
        ),
        "command": "npm test",
    })
    content, data = result_tuple
    assert data["tool_type"] == "npm_test"
    assert "1 failed" in data["compressed"]


@pytest.mark.asyncio
async def test_compress_small_output_no_negative_reduction(server):
    """Very small inputs can have compressed > original; reduction must be >= 0."""
    result_tuple = await server.call_tool("snipp_compress", {
        "output": "ok",
        "command": "echo",
    })
    content, data = result_tuple
    assert data["reduction_pct"] >= 0.0


# ---------------------------------------------------------------------------
# detect
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_pytest(server):
    result_tuple = await server.call_tool("snipp_detect", {
        "output": "============================= test session starts ==============================",
        "command": "pytest -v",
    })
    content, data = result_tuple
    assert data["tool"] == "pytest"
    assert data["confidence"] >= 0.9


@pytest.mark.asyncio
async def test_detect_git_diff(server):
    result_tuple = await server.call_tool("snipp_detect", {
        "output": "diff --git a/src/main.py b/src/main.py\nindex abc123..def456 100644",
        "command": "git diff",
    })
    content, data = result_tuple
    assert data["tool"] == "git_diff"


# ---------------------------------------------------------------------------
# run (subprocess)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_allowed_command(server):
    result_tuple = await server.call_tool("snipp_run", {
        "argv": ["ls", "-la"],
    })
    content, data = result_tuple
    assert data["exit_code"] == 0
    assert data["tool_type"] == "ls"
    assert "# Directory listing" in data["compressed"] or "listing" in data["compressed"].lower()


@pytest.mark.asyncio
async def test_run_rejects_disallowed_command(server):
    with pytest.raises(Exception) as exc_info:
        await server.call_tool("snipp_run", {
            "argv": ["bash", "-c", "echo hello"],
        })
    assert "allowlist" in str(exc_info.value).lower() or "not in allowlist" in str(exc_info.value)


@pytest.mark.asyncio
async def test_run_rejects_shell_metachar(server):
    with pytest.raises(Exception) as exc_info:
        await server.call_tool("snipp_run", {
            "argv": ["ls", ";", "rm", "-rf", "/"],
        })
    assert "shell" in str(exc_info.value).lower() or "metacharacter" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# expand
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expand_round_trip(server):
    # Use the server's global session registry
    from snipp.mcp.server import _sessions
    sid = _sessions.create()
    store = _sessions.get(sid)
    handle = store.store("Original hidden content\nline two", source="test")

    result_tuple = await server.call_tool("snipp_expand", {
        "handle": handle,
        "session_id": sid,
    })
    content, data = result_tuple
    assert data["content"] == "Original hidden content\nline two"
    assert data["lines"] == 2


@pytest.mark.asyncio
async def test_expand_missing_handle(server):
    with pytest.raises(Exception) as exc_info:
        await server.call_tool("snipp_expand", {
            "handle": "deadbeef",
        })
    assert "not found" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# list_handles
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_handles_empty(server):
    result_tuple = await server.call_tool("snipp_list_handles", {})
    content, data = result_tuple
    assert data["total"] == 0


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health(server):
    result_tuple = await server.call_tool("snipp_health", {})
    content, data = result_tuple
    assert data["version"] == "0.2.0"
    assert data["uptime_seconds"] >= 0.0
    assert "tiktoken" in data["tokenizer_backends"]


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resource_session_current(server):
    resources = await server.list_resources()
    uris = [str(r.uri) for r in resources]
    assert "snipp://session/current" in uris


@pytest.mark.asyncio
async def test_resource_config_current(server):
    resources = await server.list_resources()
    uris = [str(r.uri) for r in resources]
    assert "snipp://config/current" in uris


# ---------------------------------------------------------------------------
# Session isolation
# ---------------------------------------------------------------------------

def test_session_isolation(session_registry):
    sid_a = session_registry.create()
    sid_b = session_registry.create()
    store_a = session_registry.get(sid_a)
    store_b = session_registry.get(sid_b)

    handle_a = store_a.store("content A")
    assert store_a.expand(handle_a) == "content A"
    # Cross-session isolation: store_b must NOT find handle_a
    assert store_b.expand(handle_a) is None


def test_session_quota_exact_limit(session_registry):
    """Exact quota: max_handles=2 allows 2 stores; the next check_quota fails."""
    session_registry._max_handles = 2
    sid = session_registry.create()
    store = session_registry.get(sid)

    store.store("one")
    session_registry.check_quota(sid)  # 1 < 2 → ok, room for one more

    store.store("two")
    # 2 >= 2 → limit reached; any further operation should be blocked
    with pytest.raises(RuntimeError, match="exceeded max_handles"):
        session_registry.check_quota(sid)


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------

def test_argv_validator_allows_ls():
    from snipp.mcp.security.argv_validator import ArgvValidator
    v = ArgvValidator()
    argv = ["ls", "-la"]
    v.validate(argv)
    assert argv[0].endswith("ls")


def test_argv_validator_rejects_bash():
    from snipp.mcp.security.argv_validator import ArgvValidator
    v = ArgvValidator()
    with pytest.raises(ValueError):
        v.validate(["bash", "-c", "echo hi"])


def test_argv_validator_rejects_shell_injection():
    from snipp.mcp.security.argv_validator import ArgvValidator
    v = ArgvValidator()
    with pytest.raises(ValueError):
        v.validate(["ls", ";", "rm", "-rf", "/"])


def test_path_guard_rejects_traversal():
    from snipp.mcp.security.path_guard import PathGuard
    from pathlib import Path
    pg = PathGuard(Path("/tmp/cc-cache"))
    with pytest.raises(ValueError):
        pg.resolve_handle_path("sess-abc", "../../etc/passwd")


def test_path_guard_accepts_valid_handle():
    from snipp.mcp.security.path_guard import PathGuard
    from pathlib import Path
    pg = PathGuard(Path("/tmp/cc-cache"))
    path = pg.resolve_handle_path("sess-abc", "a3f7d2e1")
    assert path.name == "a3f7d2e1.txt"


@pytest.mark.asyncio
async def test_compress_rejects_oversized_input(server):
    with pytest.raises(Exception) as exc_info:
        await server.call_tool("snipp_compress", {
            "output": "x" * 50_000_001,
        })
    assert "exceeds" in str(exc_info.value).lower() or "maximum" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_run_timeout_kills_process(server):
    import tempfile, os
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("import time, sys\nprint('start')\nsys.stdout.flush()\ntime.sleep(60)\n")
        path = f.name
    try:
        result = await server.call_tool("snipp_run", {
            "argv": ["python", path],
            "timeout_seconds": 1,
        })
        data = result[1]
        assert data["timeout"] is True
        assert data["exit_code"] == -9
    finally:
        os.unlink(path)


# HTTP transport is disabled until Phase 7 (authenticated per-connection
# isolation) is fully implemented. Stdio is the primary production transport.
