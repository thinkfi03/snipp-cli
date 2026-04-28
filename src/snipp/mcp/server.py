"""MCP server entry point for snipp.

Built on FastMCP from the official `mcp` Python SDK.
Primary transport: stdio. HTTP is disabled until Phase 7 (auth + per-connection
isolation) can be properly implemented.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Literal, Optional

from mcp.server.fastmcp import FastMCP, Context

from snipp import __version__ as cc_version
from snipp.core import (
    compress_output,
    register_plugin_compressor,
    unregister_plugin_compressor,
)
from snipp.config_loader import load_config
from snipp.detector import detect_tool_with_confidence
from snipp.expand import find_handles
from snipp.tokenizer import list_backends, count_tokens
from snipp.context_repo import explore_repo, show_symbol

from snipp.mcp.schemas import (
    CompressOutput,
    RunOutput,
    ExpandOutput,
    DetectOutput,
    ListHandlesOutput,
    HandleInfo,
    HealthOutput,
    RegisterPluginOutput,
    ExploreRepoOutput,
    ShowSymbolOutput,
)
from snipp.mcp.services.session_registry import SessionRegistry
from snipp.mcp.security.argv_validator import ArgvValidator
from snipp.mcp.security.path_guard import PathGuard, validate_handle
from snipp.mcp.security.subprocess_sandbox import sandboxed_popen, safe_wait
from snipp.mcp.observability import (
    configure_logging,
    get_logger,
    get_metrics,
    init_tracing,
    new_request_id,
    record_telemetry,
    request_context,
    span,
    start_metrics_server,
)

logger = get_logger(__name__)
_metrics = get_metrics()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_OUTPUT_LENGTH = 50_000_000      # 50MB
MAX_COMMAND_LENGTH = 4096
MAX_QUERY_LENGTH = 4096
MAX_STDIN_LENGTH = 10_000_000       # 10MB
MAX_STDERR_RETURN = 65_536          # 64KB

# ---------------------------------------------------------------------------
# Global state (one process = one user/session namespace per spec)
# ---------------------------------------------------------------------------

_server_start_time = time.time()
_sessions = SessionRegistry()
_path_guard = PathGuard(_sessions.cache_root)
_last_error: Optional[str] = None
_plugin_registry: Dict[str, Dict[str, Any]] = {}
_default_session_id: Optional[str] = None


def _get_default_session() -> str:
    """Return the single default session for this process, creating it once."""
    global _default_session_id
    if _default_session_id is None:
        _default_session_id = _sessions.create()
        logger.info("Default session created: %s", _default_session_id)
    return _default_session_id


def _resolve_session(session_id: Optional[str]) -> str:
    """Resolve a session id. None → default session (not a new one)."""
    return session_id or _get_default_session()


def _record_error(exc: Exception) -> None:
    global _last_error
    msg = str(exc)[:100]
    _last_error = msg
    logger.error("Tool error: %s", msg, exc_info=True)


def _check_input_size(label: str, data: Optional[str], max_len: int) -> None:
    if data and len(data) > max_len:
        raise ValueError(f"{label} exceeds maximum size ({max_len} chars)")


def _observe(tool: str, **extra):
    """Context manager bundle: request_id + structured log + metric + span + telemetry."""
    rid = new_request_id()
    return _ObserveCtx(tool=tool, request_id=rid, extra=extra)


class _ObserveCtx:
    def __init__(self, tool: str, request_id: str, extra: Dict[str, Any]):
        self.tool = tool
        self.request_id = request_id
        self.extra = extra
        self._start = 0.0
        self._req_ctx = None
        self._span_ctx = None
        self._span = None
        self.status = "ok"
        self.fields: Dict[str, Any] = {}

    def __enter__(self):
        self._start = time.perf_counter()
        self._req_ctx = request_context(self.request_id, tool=self.tool, **self.extra)
        self._req_ctx.__enter__()
        self._span_ctx = span(f"mcp.tool.{self.tool}", tool=self.tool, request_id=self.request_id)
        self._span = self._span_ctx.__enter__()
        logger.info("tool_call_start", extra_fields={"tool": self.tool, **self.extra})
        return self

    def set(self, **fields: Any) -> None:
        self.fields.update(fields)

    def __exit__(self, exc_type, exc_val, exc_tb):
        elapsed = time.perf_counter() - self._start
        wall_ms = elapsed * 1000
        if exc_val is not None:
            self.status = "error"
            self.fields["error"] = str(exc_val)[:200]
            logger.error(
                "tool_call_error",
                extra_fields={"tool": self.tool, "wall_ms": wall_ms, "error_type": exc_type.__name__ if exc_type else None},
            )
        else:
            logger.info(
                "tool_call_done",
                extra_fields={"tool": self.tool, "wall_ms": wall_ms, **self.fields},
            )

        try:
            _metrics.requests.labels(tool=self.tool, status=self.status).inc()
            _metrics.duration.labels(tool=self.tool).observe(elapsed)
            saved = self.fields.get("tokens_saved")
            if saved and saved > 0:
                _metrics.tokens_saved.labels(tool=self.tool).inc(saved)
            if self.fields.get("subprocess_killed"):
                _metrics.subprocess_kills.labels(reason=self.fields.get("kill_reason", "unknown")).inc()
            if self.fields.get("plugin_error"):
                _metrics.plugin_errors.labels(plugin=self.fields.get("plugin", "unknown")).inc()
        except Exception:
            pass

        try:
            record_telemetry({
                "ts": time.time(),
                "request_id": self.request_id,
                "tool": self.tool,
                "wall_ms": round(wall_ms, 2),
                "status": self.status,
                **{k: v for k, v in self.fields.items() if k not in ("error",)},
            })
        except Exception:
            pass

        if self._span_ctx is not None:
            try:
                self._span_ctx.__exit__(exc_type, exc_val, exc_tb)
            except Exception:
                pass
        if self._req_ctx is not None:
            self._req_ctx.__exit__(exc_type, exc_val, exc_tb)
        return False  # do not suppress


def _filter_inherited_env() -> Dict[str, str]:
    """Return os.environ filtered to allowed keys only."""
    validator = ArgvValidator()
    return validator.filter_env(dict(os.environ))


def _build_subprocess_env(user_env: Optional[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """Build child env: filtered inherited env merged with filtered user env."""
    base = _filter_inherited_env()
    if user_env:
        validator = ArgvValidator()
        filtered_user = validator.filter_env(user_env)
        base.update(filtered_user)
    return base


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown / periodic GC)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _app_lifespan(server: FastMCP) -> AsyncIterator[Dict[str, Any]]:
    """FastMCP lifespan: startup reaper + persistent-plugin loader + periodic GC + metrics refresh."""
    # Initialize OpenTelemetry tracing if configured
    tracing_active = init_tracing()
    if tracing_active:
        logger.info("OpenTelemetry tracing active")

    # Startup: reap stale sessions
    reaped = _sessions.gc(protect={_default_session_id} if _default_session_id else None)
    if reaped:
        logger.info("startup_gc_reaped", extra_fields={"sessions": reaped})

    # Load persistent plugins from ~/.config/snipp/plugins.yaml
    loaded = _load_persistent_plugins()
    if loaded:
        logger.info("persistent_plugins_loaded", extra_fields={"count": loaded})

    async def _gc_loop() -> None:
        while True:
            await asyncio.sleep(300)  # 5 minutes
            try:
                # Always protect the active default session from periodic GC
                protect = {_default_session_id} if _default_session_id else None
                n = _sessions.gc(protect=protect)
                if n:
                    logger.info("periodic_gc_reaped", extra_fields={"sessions": n})
                # Refresh observability gauges
                _metrics.session_count.set(len(_sessions.list_sessions()))
                _metrics.handles_active.set(_sessions.total_handles())
                _metrics.cache_bytes.set(_sessions.cache_size_bytes())
            except Exception:
                logger.error("periodic_gc_failed", exc_info=True)

    gc_task = asyncio.create_task(_gc_loop(), name="mcp-gc")

    try:
        yield {"sessions": _sessions}
    finally:
        if gc_task is not None:
            gc_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await gc_task
        # Unregister all plugin compressors so a subsequent server start
        # doesn't see stale plugin entries from a previous run in the same
        # Python process (matters for tests).
        for tool_name in list(_plugin_registry.keys()):
            unregister_plugin_compressor(tool_name)
        # Best-effort cleanup of non-persistent default session
        if _default_session_id:
            _sessions._stores.pop(_default_session_id, None)


# ---------------------------------------------------------------------------
# Server factory
# ---------------------------------------------------------------------------

def create_server(name: str = "snipp") -> FastMCP:
    """Create and configure the FastMCP server with all tools and resources."""

    mcp = FastMCP(
        name=name,
        instructions=(
            "snipp MCP server — intelligent tool-output compression for coding agents.\n"
            "Tools: compress, run, run_streaming, expand, detect, list_handles, register_plugin, health.\n"
            "Resources: snipp://session/current, snipp://session/{id}/{handle}, "
            "snipp://config/current"
        ),
        lifespan=_app_lifespan,
    )

    # =====================================================================
    # TOOLS
    # =====================================================================

    @mcp.tool()
    async def snipp_compress(
        output: str,
        command: Optional[str] = None,
        query: Optional[str] = None,
        stage: Optional[Literal["explore", "edit", "debug", "verify"]] = None,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
        session_id: Optional[str] = None,
        ctx: Context = None,
    ) -> CompressOutput:
        """Compress tool output the agent already has. Preserves failures, collapses noise."""
        _check_input_size("output", output, MAX_OUTPUT_LENGTH)
        _check_input_size("command", command, MAX_COMMAND_LENGTH)
        _check_input_size("query", query, MAX_QUERY_LENGTH)

        start = time.perf_counter()
        with _observe("compress", input_bytes=len(output)) as obs:
            try:
                sid = _resolve_session(session_id)
                _sessions.check_quota(sid)

                result = compress_output(
                    output=output,
                    command=command,
                    query=query,
                    model=model,
                    stage=stage,
                )

                handles = find_handles(result.compressed)
                handle_list = list(handles.keys())
                elapsed = (time.perf_counter() - start) * 1000
                reduction = max(0.0, result.reduction_pct)
                tokens_saved = max(0, result.original_tokens - result.compressed_tokens)

                obs.set(
                    tool=result.tool_type,
                    original_tokens=result.original_tokens,
                    compressed_tokens=result.compressed_tokens,
                    reduction_pct=reduction,
                    tokens_saved=tokens_saved,
                    handles=len(handle_list),
                )

                return CompressOutput(
                    compressed=result.compressed,
                    original_tokens=result.original_tokens,
                    compressed_tokens=result.compressed_tokens,
                    reduction_pct=reduction,
                    tool_type=result.tool_type,
                    strategy=result.strategy,
                    tokenizer=result.tokenizer,
                    exact_tokens=result.exact_tokens,
                    fidelity=result.fidelity or {},
                    handles=handle_list,
                    elapsed_ms=elapsed,
                )
            except Exception as exc:
                _record_error(exc)
                raise

    @mcp.tool()
    async def snipp_run(
        argv: List[str],
        query: Optional[str] = None,
        stage: Optional[Literal["explore", "edit", "debug", "verify"]] = None,
        model: Optional[str] = None,
        timeout_seconds: int = 300,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        stdin: Optional[str] = None,
        session_id: Optional[str] = None,
        ctx: Context = None,
    ) -> RunOutput:
        """Execute a command, capture stdout, compress it, and return.

Security: argv[0] is allowlisted; no shell; cwd is validated; env is filtered.
Subprocess runs with ulimits (2GB AS, 100MB file, 600s CPU).
Timeout triggers process-group kill (leader + children)."""
        _check_input_size("query", query, MAX_QUERY_LENGTH)
        _check_input_size("stdin", stdin, MAX_STDIN_LENGTH)

        start = time.perf_counter()
        try:
            sid = _resolve_session(session_id)
            _sessions.check_quota(sid)

            # Security validation
            argv_copy = list(argv)
            validator = ArgvValidator()
            validator.validate(argv_copy, cwd=cwd)

            # Build filtered env (inherited + user-supplied, both filtered)
            merged_env = _build_subprocess_env(env)

            # Launch with sandboxing (ulimits + process group)
            proc = sandboxed_popen(
                argv_copy,
                cwd=cwd,
                env=merged_env,
                stdin_data=stdin,
                timeout_seconds=timeout_seconds,
            )

            stdout, stderr, timed_out = safe_wait(
                proc,
                timeout=timeout_seconds,
                stdin_data=stdin.encode("utf-8") if stdin else None,
            )

            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")[:MAX_STDERR_RETURN]

            command_str = " ".join(argv_copy)
            result = compress_output(
                output=stdout_text,
                command=command_str,
                query=query,
                model=model,
                stage=stage,
            )

            handles = find_handles(result.compressed)
            elapsed = (time.perf_counter() - start) * 1000
            reduction = max(0.0, result.reduction_pct)

            # Honest OOM / signal detection
            oom_killed = False
            killed_by_signal = False
            if timed_out:
                exit_code = -9
            else:
                exit_code = proc.returncode or 0
                if exit_code == -9:
                    oom_killed = True  # SIGKILL — likely OOM or our timeout kill
                elif exit_code < 0:
                    killed_by_signal = True

            return RunOutput(
                compressed=result.compressed,
                original_tokens=result.original_tokens,
                compressed_tokens=result.compressed_tokens,
                reduction_pct=reduction,
                tool_type=result.tool_type,
                strategy=result.strategy,
                tokenizer=result.tokenizer,
                exact_tokens=result.exact_tokens,
                fidelity=result.fidelity or {},
                handles=list(handles.keys()),
                elapsed_ms=elapsed,
                exit_code=exit_code,
                stderr=stderr_text,
                timeout=timed_out,
                oom_killed=oom_killed,
            )
        except Exception as exc:
            _record_error(exc)
            raise

    @mcp.tool()
    async def snipp_run_streaming(
        argv: List[str],
        query: Optional[str] = None,
        stage: Optional[Literal["explore", "edit", "debug", "verify"]] = None,
        model: Optional[str] = None,
        timeout_seconds: int = 300,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        stdin: Optional[str] = None,
        session_id: Optional[str] = None,
        ctx: Context = None,
    ) -> RunOutput:
        """Execute a command and return compressed output.

Note: True streaming (per-chunk progress) is not yet implemented.
This tool behaves identically to snipp.run but reports coarse
progress (0% → 100%) if the client supports it."""
        # Report progress safely (some clients don't send progressToken)
        with contextlib.suppress(RuntimeError, ValueError):
            await ctx.report_progress(0, 100)

        result = await snipp_run(
            argv=argv,
            query=query,
            stage=stage,
            model=model,
            timeout_seconds=timeout_seconds,
            cwd=cwd,
            env=env,
            stdin=stdin,
            session_id=session_id,
            ctx=ctx,
        )

        with contextlib.suppress(RuntimeError, ValueError):
            await ctx.report_progress(100, 100)

        return result

    @mcp.tool()
    async def snipp_expand(
        handle: str,
        session_id: Optional[str] = None,
        ctx: Context = None,
    ) -> ExpandOutput:
        """Expand an elision handle back to its original content."""
        try:
            # Validate BEFORE touching filesystem
            validated_hash = validate_handle(handle)
            sid = _resolve_session(session_id)
            store = _sessions.get(sid)
            # Pass validated hash to avoid re-parsing inside store.expand
            content = store.expand(validated_hash)
            if content is None:
                raise ValueError(f"Handle not found: {handle!r}")

            meta_path = store.session_dir / f"{validated_hash}.json"
            meta: Dict[str, Any] = {}
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))

            age = time.time() - meta.get("created", time.time())
            return ExpandOutput(
                content=content,
                source=meta.get("source"),
                lines=meta.get("lines", content.count("\n") + 1),
                bytes=meta.get("bytes", len(content.encode("utf-8"))),
                age_seconds=age,
            )
        except Exception as exc:
            _record_error(exc)
            raise

    @mcp.tool()
    async def snipp_detect(
        output: str,
        command: Optional[str] = None,
        ctx: Context = None,
    ) -> DetectOutput:
        """Run tool detection heuristics and return the detected type + confidence."""
        _check_input_size("output", output, 1_000_000)
        _check_input_size("command", command, MAX_COMMAND_LENGTH)
        try:
            detection = detect_tool_with_confidence(command, output)
            return DetectOutput(
                tool=detection.tool.value,
                confidence=detection.confidence,
                source=detection.source,
            )
        except Exception as exc:
            _record_error(exc)
            raise

    @mcp.tool()
    async def snipp_list_handles(
        session_id: Optional[str] = None,
        ctx: Context = None,
    ) -> ListHandlesOutput:
        """List all elision handles for the current session, sorted by age."""
        try:
            sid = _resolve_session(session_id)
            handles = _sessions.handles_for(sid)
            infos = [
                HandleInfo(
                    handle=h["handle"],
                    source=h.get("source"),
                    lines=h.get("lines", 0),
                    bytes=h.get("bytes", 0),
                    age_seconds=h.get("age_seconds", 0.0),
                )
                for h in handles
            ]
            return ListHandlesOutput(
                handles=infos,
                total=len(infos),
                session_id=sid,
            )
        except Exception as exc:
            _record_error(exc)
            raise

    @mcp.tool()
    async def snipp_register_plugin(
        tool_name: str,
        argv0: str,
        executable: str,
        timeout_seconds: int = 10,
        persistent: bool = False,
        ctx: Context = None,
    ) -> RegisterPluginOutput:
        """Register an external compressor plugin executable.

Plugins must speak JSON over stdin/stdout and implement the compressor contract."""
        try:
            exe_path = Path(executable)
            if not exe_path.exists():
                raise ValueError(f"Executable not found: {executable}")
            if not os.access(exe_path, os.X_OK):
                raise ValueError(f"File is not executable: {executable}")

            _plugin_registry[tool_name] = {
                "argv0": argv0,
                "executable": str(exe_path),
                "timeout_seconds": timeout_seconds,
            }

            # Wire into compressor registry
            _wire_plugin(tool_name, _plugin_registry[tool_name])

            if persistent:
                plugins_file = Path.home() / ".config" / "snipp" / "plugins.yaml"
                plugins_file.parent.mkdir(parents=True, exist_ok=True)
                existing: Dict[str, Any] = {}
                if plugins_file.exists():
                    import yaml
                    existing = yaml.safe_load(plugins_file.read_text()) or {}
                existing[tool_name] = _plugin_registry[tool_name]
                plugins_file.write_text(yaml.safe_dump(existing), encoding="utf-8")

            return RegisterPluginOutput(
                tool_name=tool_name,
                registered=True,
                persistent=persistent,
                message=f"Plugin {tool_name!r} registered from {exe_path}",
            )
        except Exception as exc:
            _record_error(exc)
            raise

    @mcp.tool()
    async def snipp_health(ctx: Context = None) -> HealthOutput:
        """Return server health, version, and capability information."""
        try:
            uptime = time.time() - _server_start_time
            backends = list_backends()
            return HealthOutput(
                version=cc_version,
                uptime_seconds=uptime,
                tokenizer_backends=backends,
                plugins_loaded=list(_plugin_registry.keys()),
                sessions_active=len(_sessions.list_sessions()),
                handles_total=_sessions.total_handles(),
                cache_size_bytes=_sessions.cache_size_bytes(),
                last_error=_last_error,
            )
        except Exception as exc:
            _record_error(exc)
            raise

    @mcp.tool()
    async def snipp_explore_repo(
        root: str,
        query: Optional[str] = None,
        max_tokens: int = 2000,
        ctx: Context = None,
    ) -> ExploreRepoOutput:
        """Explore a codebase root and return a structured, token-capped context document.

Scans source files, extracts AST signatures, and ranks symbols by relevance to
`query`. Returns a compact document with file tree + key signatures that fits
within `max_tokens`."""
        _check_input_size("query", query, 4096)
        start = time.perf_counter()
        try:
            doc = explore_repo(root=root, query=query, max_tokens=max_tokens)
            # Count stats
            files_scanned = doc.count("\n### ")  # rough proxy; we recalculate properly below
            # Re-extract for accurate stats
            from snipp.context_repo import _walk_repo, _extract_symbols
            root_path = Path(root).expanduser().resolve()
            files = _walk_repo(root_path) if root_path.is_dir() else []
            symbols = []
            for f in files:
                symbols.extend(_extract_symbols(root_path, f))
            # If query, compute ranked count
            if query:
                from snipp.ranking import rank_chunks
                chunks = [chunk for _, _, _, chunk in symbols]
                ranked = rank_chunks(chunks, query)
                # Count how many of the ranked symbols made it into the doc
                doc_symbols = doc.count("\n### ")
            else:
                doc_symbols = len(symbols)
            tokens = count_tokens(doc)
            elapsed = (time.perf_counter() - start) * 1000
            return ExploreRepoOutput(
                context=doc,
                files_scanned=len(files),
                symbols_found=len(symbols),
                tokens=tokens,
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            _record_error(exc)
            raise

    @mcp.tool()
    async def snipp_show_symbol(
        root: str,
        symbol_name: str,
        ctx: Context = None,
    ) -> ShowSymbolOutput:
        """Find a named symbol in a codebase and return its signature + body preview."""
        start = time.perf_counter()
        try:
            result = show_symbol(root=root, symbol_name=symbol_name)
            elapsed = (time.perf_counter() - start) * 1000
            if result is None:
                return ShowSymbolOutput(
                    found=False,
                    elapsed_ms=elapsed,
                )
            # Parse the formatted output
            lines = result.split("\n")
            header = lines[0] if lines else ""
            # header format: # name  (kind in path)
            import re
            m = re.search(r"# (.+?)\s+\((.+?) in (.+?)\)", header)
            name = m.group(1) if m else symbol_name
            kind = m.group(2) if m else None
            file = m.group(3) if m else None
            # Find signature block (between header and "## Body")
            sig_lines = []
            body_lines = []
            in_sig = False
            in_body = False
            for line in lines[1:]:
                if line.strip() == "## Body (first 5 lines)":
                    in_sig = False
                    in_body = True
                    continue
                if in_body:
                    body_lines.append(line)
                elif line.strip():
                    in_sig = True
                    sig_lines.append(line)
                # Empty lines before body header are ignored
            signature = "\n".join(sig_lines).strip() or None
            body_preview = "\n".join(body_lines).strip() or None
            return ShowSymbolOutput(
                symbol=name,
                kind=kind,
                file=file,
                signature=signature,
                body_preview=body_preview,
                found=True,
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            _record_error(exc)
            raise

    # =====================================================================
    # RESOURCES
    # =====================================================================

    @mcp.resource("snipp://session/current")
    async def resource_session_current() -> str:
        """Current session metadata."""
        sessions = _sessions.list_sessions()
        return json.dumps(
            {
                "sessions": sessions,
                "total_sessions": len(sessions),
                "total_handles": _sessions.total_handles(),
                "cache_size_bytes": _sessions.cache_size_bytes(),
            },
            indent=2,
        )

    @mcp.resource("snipp://session/{session_id}/{handle}")
    async def resource_session_handle(session_id: str, handle: str) -> str:
        """Read the original content for a specific elision handle."""
        # Validate session_id before use
        sid = _path_guard.validate_session_id(session_id)
        store = _sessions.get(sid)
        content = store.expand(handle)
        if content is None:
            return json.dumps({"error": "Handle not found"})
        return content

    @mcp.resource("snipp://config/current")
    async def resource_config_current() -> str:
        """Resolved configuration snapshot."""
        try:
            cfg = load_config()
            return json.dumps(
                {
                    "default_max_tokens": cfg.default_max_tokens,
                    "model": cfg.model,
                    "stage": cfg.stage,
                    "compressors": {
                        name: {
                            "enabled": c.enabled,
                            "max_output_tokens": c.max_output_tokens,
                            "extras": c.extras,
                        }
                        for name, c in cfg.compressors.items()
                    },
                },
                indent=2,
            )
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    return mcp


def _wire_plugin(tool_name: str, meta: Dict[str, Any]) -> None:
    """Register a plugin-backed compressor under its tool_name.

    The plugin executable is sandboxed (ulimits + process-group kill) and
    routed via the name-keyed plugin registry, NOT via ToolType. This means
    registering a plugin called e.g. "kubectl" never clobbers the GENERIC
    fallback or any ToolType-keyed compressor.
    """
    from snipp.compressors.base import BaseCompressor, CompressResult
    from snipp.compressors.generic import GenericCompressor

    captured_tool_name = tool_name
    captured_meta = dict(meta)
    captured_argv0 = captured_meta.get("argv0", tool_name)

    class PluginCompressor(BaseCompressor):
        def __init__(self, max_tokens: int = 2000, **kwargs):
            super().__init__(max_tokens=max_tokens, **kwargs)
            self._meta = captured_meta

        def compress(self, output: str, query: Optional[str] = None) -> CompressResult:
            payload = {
                "output": output,
                "query": query,
                "max_tokens": self.max_tokens,
            }
            stdin_data = json.dumps(payload)
            try:
                proc = sandboxed_popen(
                    [self._meta["executable"]],
                    cwd=None,
                    env=_filter_inherited_env(),
                    stdin_data=stdin_data,
                    timeout_seconds=self._meta.get("timeout_seconds", 10),
                )
                stdout, stderr, timed_out = safe_wait(
                    proc,
                    timeout=self._meta.get("timeout_seconds", 10),
                    stdin_data=stdin_data.encode("utf-8"),
                )
                if timed_out or proc.returncode != 0:
                    logger.warning(
                        "Plugin %r failed (timeout=%s, exit=%s); falling back to generic",
                        captured_tool_name, timed_out, proc.returncode,
                    )
                    return GenericCompressor(
                        max_tokens=self.max_tokens, tokenizer=self.tokenizer
                    ).compress(output, query=query)
                result = json.loads(stdout.decode("utf-8"))
                return CompressResult(
                    compressed=result["compressed"],
                    original_tokens=result.get("original_tokens", 0),
                    compressed_tokens=result.get("compressed_tokens", 0),
                    tool_type=captured_tool_name,
                    strategy=f"plugin:{captured_tool_name}",
                    tokenizer=self.tokenizer.name,
                    exact_tokens=False,
                    fidelity=result.get("fidelity") or {},
                )
            except Exception as exc:
                logger.warning(
                    "Plugin %r raised %s; falling back to generic",
                    captured_tool_name, exc.__class__.__name__,
                )
                return GenericCompressor(
                    max_tokens=self.max_tokens, tokenizer=self.tokenizer
                ).compress(output, query=query)

    def _predicate(command: Optional[str], output: str) -> bool:
        if not command:
            return False
        # Match argv0 as the first non-env-prefix token in the command
        try:
            import shlex
            parts = shlex.split(command.strip())
        except ValueError:
            parts = command.strip().split()
        for p in parts:
            if "=" in p and p.split("=", 1)[0].isupper():
                continue
            argv0 = p.split("/")[-1].lower()
            return argv0 == captured_argv0.lower() or argv0 == captured_tool_name.lower()
        return False

    register_plugin_compressor(captured_tool_name, PluginCompressor, predicate=_predicate)
    logger.info("Plugin %r wired (argv0=%s)", captured_tool_name, captured_argv0)


def _load_persistent_plugins() -> int:
    """Load plugins from ~/.config/snipp/plugins.yaml on startup."""
    plugins_file = Path.home() / ".config" / "snipp" / "plugins.yaml"
    if not plugins_file.exists():
        return 0
    try:
        import yaml
        existing = yaml.safe_load(plugins_file.read_text()) or {}
    except Exception as exc:
        logger.warning("Failed to load persistent plugins: %s", exc)
        return 0
    loaded = 0
    for tool_name, meta in existing.items():
        if not isinstance(meta, dict) or "executable" not in meta:
            continue
        exe = Path(meta["executable"])
        if not exe.exists() or not os.access(exe, os.X_OK):
            logger.warning("Persistent plugin %r executable missing: %s", tool_name, exe)
            continue
        _plugin_registry[tool_name] = {
            "argv0": meta.get("argv0", tool_name),
            "executable": str(exe),
            "timeout_seconds": int(meta.get("timeout_seconds", 10)),
        }
        _wire_plugin(tool_name, _plugin_registry[tool_name])
        loaded += 1
    return loaded


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the MCP server over stdio (default).

    HTTP transport is disabled until Phase 7 (authenticated per-connection
    isolation) is fully implemented. Use stdio for all production agents.
    """
    import argparse

    parser = argparse.ArgumentParser(description="snipp MCP server")
    parser.add_argument("--transport", choices=["stdio"], default="stdio",
                        help="Transport protocol (stdio only; HTTP disabled pending Phase 7)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-format", choices=["json", "text"], default="json",
                        help="Log line format")
    parser.add_argument("--metrics-port", type=int, default=None,
                        help="Optional Prometheus /metrics HTTP port (requires prometheus_client)")
    args = parser.parse_args()

    configure_logging(level=args.log_level, json_output=(args.log_format == "json"))

    if args.metrics_port:
        ok = start_metrics_server(args.metrics_port)
        if ok:
            logger.info("metrics_server_started", extra_fields={"port": args.metrics_port})
        else:
            logger.warning(
                "metrics_server_unavailable",
                extra_fields={"reason": "prometheus_client not installed; install with snipp[observability]"},
            )

    mcp = create_server()
    asyncio.run(mcp.run_stdio_async())


if __name__ == "__main__":
    main()
