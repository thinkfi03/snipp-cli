"""Pytest config: isolate CC_COMPRESS_CACHE so tests never touch the user's real cache."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolate_snipp_cache(tmp_path_factory: pytest.TempPathFactory):
    """Redirect CC_COMPRESS_CACHE to a session-scoped tmp dir so the
    `default_store()` singleton in expand.py and the MCP `_sessions` registry
    never write to ~/.cache/snipp/.

    Without this, tests writing through the global default session pollute
    the user's real cache directory and create cross-run flakiness.
    """
    cache_dir = tmp_path_factory.mktemp("snipp-cache")
    prev = os.environ.get("CC_COMPRESS_CACHE")
    os.environ["CC_COMPRESS_CACHE"] = str(cache_dir)

    # Reset module-level singletons so they pick up the new env var
    import snipp.expand as _expand_mod
    _expand_mod._default_session = None

    import snipp.mcp.server as _server_mod
    _server_mod._sessions = _server_mod.SessionRegistry()
    _server_mod._default_session_id = None
    _server_mod._path_guard = _server_mod.PathGuard(_server_mod._sessions.cache_root)

    yield cache_dir

    # Restore prior env value
    if prev is None:
        os.environ.pop("CC_COMPRESS_CACHE", None)
    else:
        os.environ["CC_COMPRESS_CACHE"] = prev
    _expand_mod._default_session = None


@pytest.fixture(autouse=True)
def _reset_mcp_global_state():
    """Reset MCP server module globals between tests to avoid order dependence."""
    import snipp.mcp.server as _server
    # Save original
    prev_default = _server._default_session_id
    prev_plugins = dict(_server._plugin_registry)
    prev_sessions = _server._sessions
    yield
    # Restore
    _server._default_session_id = prev_default
    _server._plugin_registry.clear()
    _server._plugin_registry.update(prev_plugins)
    _server._sessions = prev_sessions
