"""End-to-end test of every MCP tool on the server.

Runs each tool through the FastMCP server and asserts output schema / semantics.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from snipp.mcp.server import create_server


@pytest.fixture
def server():
    return create_server()


@pytest.fixture
def sample_repo(tmp_path):
    """Mini Python project for repo exploration tests."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("""
class AuthManager:
    \"\"\"Handles JWT auth.\"\"\"
    def authenticate(self, token: str) -> dict:
        return {"user": "alice"}

def create_app():
    return AuthManager("secret")
""")
    (tmp_path / "README.md").write_text("# Demo\n")
    return tmp_path


# ---------------------------------------------------------------------------
# Tool listing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_tools_registered(server):
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
        "snipp_explore_repo",
        "snipp_show_symbol",
    }
    assert expected <= names, f"Missing: {expected - names}"


# ---------------------------------------------------------------------------
# snipp_compress
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compress_pytest_output(server):
    result = await server.call_tool("snipp_compress", {
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
    content, data = result
    assert data["tool_type"] == "pytest"
    assert data["original_tokens"] > 0
    assert data["compressed_tokens"] > 0
    assert 0 <= data["reduction_pct"] <= 100
    assert data["elapsed_ms"] >= 0


@pytest.mark.asyncio
async def test_compress_rejects_oversized_input(server):
    with pytest.raises(Exception) as exc_info:
        await server.call_tool("snipp_compress", {
            "output": "x" * 50_000_001,
        })
    assert "exceeds" in str(exc_info.value).lower() or "maximum" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# snipp_run
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_allowed_command(server):
    result = await server.call_tool("snipp_run", {
        "argv": ["ls", "-la"],
    })
    content, data = result
    assert data["exit_code"] == 0
    assert data["tool_type"] == "ls"
    assert data["timeout"] is False


@pytest.mark.asyncio
async def test_run_rejects_disallowed_command(server):
    with pytest.raises(Exception) as exc_info:
        await server.call_tool("snipp_run", {
            "argv": ["bash", "-c", "echo hello"],
        })
    assert "allowlist" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_run_timeout_kills_process(server):
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


# ---------------------------------------------------------------------------
# snipp_run_streaming
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_streaming_behaves_like_run(server):
    result = await server.call_tool("snipp_run_streaming", {
        "argv": ["ls", "-la"],
    })
    content, data = result
    assert data["exit_code"] == 0
    assert data["tool_type"] == "ls"


# ---------------------------------------------------------------------------
# snipp_expand
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expand_round_trip(server):
    from snipp.mcp.server import _sessions
    sid = _sessions.create()
    store = _sessions.get(sid)
    handle = store.store("Original hidden content\nline two", source="test")

    result = await server.call_tool("snipp_expand", {
        "handle": handle,
        "session_id": sid,
    })
    content, data = result
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
# snipp_detect
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_detect_pytest(server):
    result = await server.call_tool("snipp_detect", {
        "output": "============================= test session starts ==============================",
        "command": "pytest -v",
    })
    content, data = result
    assert data["tool"] == "pytest"
    assert data["confidence"] >= 0.9


@pytest.mark.asyncio
async def test_detect_git_diff(server):
    result = await server.call_tool("snipp_detect", {
        "output": "diff --git a/src/main.py b/src/main.py\nindex abc123..def456 100644",
        "command": "git diff",
    })
    content, data = result
    assert data["tool"] == "git_diff"


# ---------------------------------------------------------------------------
# snipp_list_handles
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_handles_empty(server):
    result = await server.call_tool("snipp_list_handles", {})
    content, data = result
    assert data["total"] == 0


# ---------------------------------------------------------------------------
# snipp_health
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health(server):
    result = await server.call_tool("snipp_health", {})
    content, data = result
    assert data["version"] == "0.2.0"
    assert data["uptime_seconds"] >= 0.0
    assert "tiktoken" in data["tokenizer_backends"]


# ---------------------------------------------------------------------------
# snipp_explore_repo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explore_repo_returns_context(server, sample_repo):
    result = await server.call_tool("snipp_explore_repo", {
        "root": str(sample_repo),
        "query": "authentication",
        "max_tokens": 1000,
    })
    content, data = result
    assert "# File Tree" in data["context"]
    assert "src/main.py" in data["context"]
    assert "AuthManager" in data["context"]
    assert data["files_scanned"] >= 1
    assert data["symbols_found"] >= 2
    assert data["tokens"] <= 1000
    assert data["elapsed_ms"] >= 0.0


@pytest.mark.asyncio
async def test_explore_repo_no_query(server, sample_repo):
    result = await server.call_tool("snipp_explore_repo", {
        "root": str(sample_repo),
        "max_tokens": 500,
    })
    content, data = result
    assert data["tokens"] <= 500
    assert data["symbols_found"] >= 2


@pytest.mark.asyncio
async def test_explore_repo_invalid_root(server):
    with pytest.raises(Exception) as exc_info:
        await server.call_tool("snipp_explore_repo", {
            "root": "/no/such/path",
        })
    assert "not a directory" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# snipp_show_symbol
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_show_symbol_found(server, sample_repo):
    result = await server.call_tool("snipp_show_symbol", {
        "root": str(sample_repo),
        "symbol_name": "AuthManager",
    })
    content, data = result
    assert data["found"] is True
    assert data["symbol"] == "AuthManager"
    assert data["kind"] == "class"
    assert "class AuthManager" in (data["signature"] or "")
    assert data["file"] == "src/main.py"
    assert data["body_preview"] is not None
    assert data["elapsed_ms"] >= 0.0


@pytest.mark.asyncio
async def test_show_symbol_found_method(server, sample_repo):
    result = await server.call_tool("snipp_show_symbol", {
        "root": str(sample_repo),
        "symbol_name": "authenticate",
    })
    content, data = result
    assert data["found"] is True
    assert data["symbol"] == "authenticate"
    assert "def authenticate" in (data["signature"] or "")


@pytest.mark.asyncio
async def test_show_symbol_not_found(server, sample_repo):
    result = await server.call_tool("snipp_show_symbol", {
        "root": str(sample_repo),
        "symbol_name": "NonExistent",
    })
    content, data = result
    assert data["found"] is False
    assert data["symbol"] is None


# ---------------------------------------------------------------------------
# snipp_register_plugin
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_plugin_executable_not_found(server):
    with pytest.raises(Exception) as exc_info:
        await server.call_tool("snipp_register_plugin", {
            "tool_name": "mytool",
            "argv0": "mytool",
            "executable": "/no/such/executable",
        })
    assert "not found" in str(exc_info.value).lower()


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
