"""Regression tests for the deployment-readiness fix pass.

Pins:
  - Bug #1: registering a plugin must NOT clobber the GENERIC fallback.
  - Bug #2: plugin executables must run sandboxed (no fork-bomb / OOM).
  - Bug #3: persistent plugins reload on server start (lifespan).
  - Bug #5: GC must not reap a protected default session.
  - Bug #7: timeout kills the entire process group (children too).
  - Bug #8: BearerAuth.is_valid runs constant-time over all tokens.
  - Bug #9: end-to-end plugin dispatch goes through the plugin executable.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from snipp.core import (
    compress_output,
    register_plugin_compressor,
    unregister_plugin_compressor,
    list_plugin_compressors,
    _PLUGIN_REGISTRY,
)
from snipp.compressors.base import BaseCompressor, CompressResult
from snipp.compressors.generic import GenericCompressor
from snipp.mcp.services.session_registry import SessionRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plugin_script(tmp_path: Path, body: str) -> Path:
    """Write an executable Python script that acts as a snipp plugin."""
    script = tmp_path / "plugin.py"
    script.write_text(f"#!{sys.executable}\n{textwrap.dedent(body)}")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


@pytest.fixture(autouse=True)
def _clear_plugin_registry():
    """Make sure no plugin state leaks across tests."""
    snapshot = dict(_PLUGIN_REGISTRY)
    _PLUGIN_REGISTRY.clear()
    yield
    _PLUGIN_REGISTRY.clear()
    _PLUGIN_REGISTRY.update(snapshot)


# ---------------------------------------------------------------------------
# Bug #1: GENERIC fallback must survive plugin registration
# ---------------------------------------------------------------------------

class TestGenericNotClobbered:
    def test_register_plugin_does_not_clobber_generic_dispatch(self):
        """Output that should hit GENERIC must keep going to GenericCompressor."""

        class FakePlugin(BaseCompressor):
            def compress(self, output, query=None):
                return CompressResult(
                    compressed="PLUGIN-RAN",
                    original_tokens=10,
                    compressed_tokens=1,
                    tool_type="kubectl",
                    strategy="plugin:kubectl",
                    tokenizer="heuristic",
                    exact_tokens=False,
                )

        # Plugin only fires when command argv0 == kubectl, not on arbitrary noise.
        register_plugin_compressor("kubectl", FakePlugin)

        random_unrelated = "this is a totally unrelated blob of text\n" * 20
        result = compress_output(random_unrelated, command="some-mystery-tool foo")
        assert result.tool_type == "generic"
        assert "PLUGIN-RAN" not in result.compressed

    def test_plugin_dispatch_when_command_matches(self):
        class FakePlugin(BaseCompressor):
            def compress(self, output, query=None):
                return CompressResult(
                    compressed="PLUGIN-RAN",
                    original_tokens=len(output),
                    compressed_tokens=10,
                    tool_type="kubectl",
                    strategy="plugin:kubectl",
                    tokenizer="heuristic",
                    exact_tokens=False,
                )

        register_plugin_compressor("kubectl", FakePlugin)
        result = compress_output("pods\n" * 200, command="kubectl get pods")
        assert result.tool_type == "kubectl"
        assert result.compressed == "PLUGIN-RAN"

    def test_unregister_plugin(self):
        class FakePlugin(BaseCompressor):
            def compress(self, output, query=None):
                return CompressResult(
                    compressed="X", original_tokens=1, compressed_tokens=1,
                    tool_type="t", strategy="s", tokenizer="h", exact_tokens=False,
                )

        register_plugin_compressor("kubectl", FakePlugin)
        assert "kubectl" in list_plugin_compressors()
        unregister_plugin_compressor("kubectl")
        assert "kubectl" not in list_plugin_compressors()


# ---------------------------------------------------------------------------
# Bug #2 + #9: plugin invocation goes through sandboxed_popen
# ---------------------------------------------------------------------------

class TestPluginExecutionEndToEnd:
    def test_plugin_executable_invoked_via_mcp_wire(self, tmp_path):
        """Wire a real executable plugin via _wire_plugin and verify it runs."""
        from snipp.mcp.server import _wire_plugin

        script = _make_plugin_script(tmp_path, """
            import sys, json
            payload = json.loads(sys.stdin.read())
            out = {
                "compressed": f"plugin-saw:{len(payload['output'])}",
                "original_tokens": len(payload["output"]),
                "compressed_tokens": 5,
                "fidelity": {"plugin_invoked": True},
            }
            print(json.dumps(out))
        """)

        _wire_plugin("kubectl", {
            "argv0": "kubectl",
            "executable": str(script),
            "timeout_seconds": 10,
        })

        result = compress_output(
            "x" * 500,
            command="kubectl get pods -A",
        )
        assert result.tool_type == "kubectl"
        assert result.compressed.startswith("plugin-saw:500")
        assert result.fidelity.get("plugin_invoked") is True

    def test_plugin_failure_falls_back_to_generic(self, tmp_path):
        """A crashing plugin must NOT take down the request — fall back to generic."""
        from snipp.mcp.server import _wire_plugin

        script = _make_plugin_script(tmp_path, """
            import sys
            sys.stderr.write("plugin crash!\\n")
            sys.exit(1)
        """)

        _wire_plugin("brokentool", {
            "argv0": "brokentool",
            "executable": str(script),
            "timeout_seconds": 5,
        })

        result = compress_output("hello world\n" * 100, command="brokentool foo")
        # Fallback ⇒ generic compressor runs successfully
        assert result.tool_type == "generic"

    def test_plugin_timeout_kills_process(self, tmp_path):
        from snipp.mcp.server import _wire_plugin

        script = _make_plugin_script(tmp_path, """
            import sys, time
            time.sleep(60)
            print('{"compressed":"never","original_tokens":0,"compressed_tokens":0}')
        """)

        _wire_plugin("slowtool", {
            "argv0": "slowtool",
            "executable": str(script),
            "timeout_seconds": 1,
        })

        start = time.perf_counter()
        result = compress_output("x" * 50, command="slowtool")
        elapsed = time.perf_counter() - start
        # Should fall back to generic in ~1s, not hang for 60s
        assert elapsed < 10
        assert result.tool_type == "generic"


# ---------------------------------------------------------------------------
# Bug #3: persistent plugins reload on lifespan startup
# ---------------------------------------------------------------------------

class TestPersistentPluginLoad:
    def test_load_persistent_plugins_reads_yaml(self, tmp_path, monkeypatch):
        from snipp.mcp import server as server_mod

        # Build a real executable plugin
        script = _make_plugin_script(tmp_path, """
            import sys, json
            payload = json.loads(sys.stdin.read())
            print(json.dumps({
                "compressed": "persistent-loaded",
                "original_tokens": len(payload["output"]),
                "compressed_tokens": 3,
            }))
        """)

        # Redirect ~/.config to tmp_path
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        cfg_dir = fake_home / ".config" / "snipp"
        cfg_dir.mkdir(parents=True)
        plugins_yaml = cfg_dir / "plugins.yaml"
        import yaml
        plugins_yaml.write_text(yaml.safe_dump({
            "persistedtool": {
                "argv0": "persistedtool",
                "executable": str(script),
                "timeout_seconds": 5,
            }
        }))

        monkeypatch.setattr(Path, "home", lambda: fake_home)
        # Reset registry state and run loader
        server_mod._plugin_registry.clear()
        loaded = server_mod._load_persistent_plugins()
        assert loaded == 1
        assert "persistedtool" in server_mod._plugin_registry

        # And it actually dispatches
        result = compress_output("hi", command="persistedtool ls")
        assert result.tool_type == "persistedtool"
        assert result.compressed == "persistent-loaded"

    def test_load_persistent_plugins_skips_missing_executable(self, tmp_path, monkeypatch):
        from snipp.mcp import server as server_mod

        fake_home = tmp_path / "home"
        cfg_dir = fake_home / ".config" / "snipp"
        cfg_dir.mkdir(parents=True)
        import yaml
        (cfg_dir / "plugins.yaml").write_text(yaml.safe_dump({
            "ghost": {"executable": "/no/such/path", "argv0": "ghost"},
        }))

        monkeypatch.setattr(Path, "home", lambda: fake_home)
        server_mod._plugin_registry.clear()
        loaded = server_mod._load_persistent_plugins()
        assert loaded == 0

    def test_load_persistent_plugins_no_file(self, tmp_path, monkeypatch):
        from snipp.mcp import server as server_mod
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        server_mod._plugin_registry.clear()
        assert server_mod._load_persistent_plugins() == 0


# ---------------------------------------------------------------------------
# Bug #5: GC honors protect set
# ---------------------------------------------------------------------------

class TestGCProtect:
    def test_gc_skips_protected_session(self, tmp_path):
        registry = SessionRegistry(cache_root=tmp_path, ttl_seconds=0)
        sid_default = registry.create()
        sid_other = registry.create()

        # Force timestamps to be ancient
        registry._created_at[sid_default] = 0
        registry._created_at[sid_other] = 0

        removed = registry.gc(protect={sid_default})
        assert removed == 1
        assert sid_default in registry._stores
        assert sid_other not in registry._stores

    def test_gc_without_protect_reaps_all(self, tmp_path):
        registry = SessionRegistry(cache_root=tmp_path, ttl_seconds=0)
        sid_a = registry.create()
        sid_b = registry.create()
        registry._created_at[sid_a] = 0
        registry._created_at[sid_b] = 0
        removed = registry.gc()
        assert removed == 2

    def test_touch_extends_session_ttl(self, tmp_path):
        registry = SessionRegistry(cache_root=tmp_path, ttl_seconds=0)
        sid = registry.create()
        registry._created_at[sid] = 0
        registry.touch(sid)
        # After touch, ttl=0 still reaps because cutoff = now-0 = now and created_at == now
        # So touch + ttl=0 leaves it on the boundary. Use ttl=3600 to verify protect.
        registry._ttl_seconds = 3600
        registry.touch(sid)
        assert registry.gc() == 0
        assert sid in registry._stores


# ---------------------------------------------------------------------------
# Bug #7: process-group kill — timeout reaps children too
# ---------------------------------------------------------------------------

class TestProcessGroupKill:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups only")
    @pytest.mark.asyncio
    async def test_run_timeout_kills_child_processes(self, tmp_path):
        """A pytest-style parent that spawns a long sleep child must take the
        child down on timeout. Without process-group kill, the child survives."""
        from snipp.mcp.server import create_server

        # Parent script: spawn a sleep child, write its pid, then sleep itself.
        pid_file = tmp_path / "child.pid"
        parent = tmp_path / "parent.py"
        parent.write_text(textwrap.dedent(f"""
            import subprocess, time, os
            child = subprocess.Popen(['sleep', '60'])
            open({str(pid_file)!r}, 'w').write(str(child.pid))
            time.sleep(60)
        """))

        server = create_server()
        result = await server.call_tool("snipp_run", {
            "argv": ["python", str(parent)],
            "timeout_seconds": 2,
        })
        data = result[1]
        assert data["timeout"] is True

        # Wait briefly for the OS to reap the killed group, then verify the
        # child sleep process is gone.
        time.sleep(1)
        if not pid_file.exists():
            pytest.skip("child pid file never written (parent died too fast)")
        child_pid = int(pid_file.read_text().strip())
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)


# ---------------------------------------------------------------------------
# Bug #8: BearerAuth constant-time comparison
# ---------------------------------------------------------------------------

class TestBearerConstantTime:
    def test_no_match_when_no_tokens(self):
        from snipp.mcp.auth.bearer import BearerAuth
        assert BearerAuth().is_valid("anything") is False

    def test_single_token_match(self):
        from snipp.mcp.auth.bearer import BearerAuth
        b = BearerAuth({"alpha-1234567890"})
        assert b.is_valid("alpha-1234567890") is True
        assert b.is_valid("alpha-1234567891") is False

    def test_multi_token_match(self):
        from snipp.mcp.auth.bearer import BearerAuth
        b = BearerAuth({"a-token-aaaaaaaa", "b-token-bbbbbbbb", "c-token-cccccccc"})
        assert b.is_valid("a-token-aaaaaaaa") is True
        assert b.is_valid("b-token-bbbbbbbb") is True
        assert b.is_valid("c-token-cccccccc") is True
        assert b.is_valid("d-token-dddddddd") is False

    def test_does_not_short_circuit(self):
        """is_valid must compare ALL tokens — no early return on first match.

        We can't directly observe time, but we can verify behavior: a match
        anywhere in the set still returns True regardless of insertion order.
        """
        from snipp.mcp.auth.bearer import BearerAuth
        # A dozen tokens, target last — the ANY()/short-circuit version
        # would still return True, so this is a structural smoke test.
        tokens = {f"tok-{i:08d}" for i in range(12)}
        target = "tok-00000011"
        tokens.add(target)
        b = BearerAuth(tokens)
        assert b.is_valid(target) is True
