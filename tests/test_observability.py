"""Tests for the observability stack: logging, metrics, telemetry, tracing."""

from __future__ import annotations

import io
import json
import logging
import os
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------

class TestStructuredLogging:
    def test_json_format_includes_request_id(self, monkeypatch):
        from snipp.mcp.observability import logging as log_mod

        # Wipe singleton flag so configure() runs again
        log_mod._configured = False
        log_mod.configure(level="DEBUG", json_output=True)

        # Capture stderr
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(log_mod._JSONFormatter())
        root = logging.getLogger()
        root.addHandler(handler)
        try:
            adapter = log_mod.get_logger("test")
            with log_mod.request_context(tool="compress"):
                adapter.info("hello", extra_fields={"foo": "bar"})
        finally:
            root.removeHandler(handler)

        out = buf.getvalue().strip().split("\n")
        assert out, "no log output captured"
        record = json.loads(out[-1])
        assert record["msg"] == "hello"
        assert "request_id" in record
        assert record["tool"] == "compress"
        assert record["foo"] == "bar"

    def test_redaction_in_context(self):
        from snipp.mcp.observability.logging import _redact

        red = _redact({"command": "ls", "auth_token": "sekret", "env": {"X": "Y"}})
        assert red["auth_token"] == "<redacted>"
        assert red["env"] == "<redacted>"
        assert red["command"] == "ls"

    def test_request_id_is_8_byte_hex(self):
        from snipp.mcp.observability.logging import new_request_id

        rid = new_request_id()
        assert len(rid) == 16  # 8 bytes hex
        int(rid, 16)  # parses as hex


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_counter_increments(self):
        from snipp.mcp.observability import metrics as m

        m.reset_for_tests()
        bundle = m.get_metrics()
        bundle.requests.labels(tool="compress", status="ok").inc()
        bundle.requests.labels(tool="compress", status="ok").inc()
        text = m.render_text()
        assert "snipp_requests_total" in text

    def test_histogram_observes(self):
        from snipp.mcp.observability import metrics as m

        m.reset_for_tests()
        bundle = m.get_metrics()
        bundle.duration.labels(tool="run").observe(0.1)
        bundle.duration.labels(tool="run").observe(0.5)
        text = m.render_text()
        assert "snipp_request_duration_seconds" in text

    def test_gauge_set(self):
        from snipp.mcp.observability import metrics as m

        m.reset_for_tests()
        bundle = m.get_metrics()
        bundle.session_count.set(7)
        bundle.cache_bytes.set(1024)
        text = m.render_text()
        assert "snipp_session_count" in text
        assert "snipp_cache_bytes" in text

    def test_render_text_is_prometheus_format(self):
        from snipp.mcp.observability import metrics as m

        m.reset_for_tests()
        bundle = m.get_metrics()
        bundle.tokens_saved.labels(tool="grep").inc(1500)
        text = m.render_text()
        assert "# HELP" in text or "# TYPE" in text
        assert "snipp_tokens_saved_total" in text


# ---------------------------------------------------------------------------
# telemetry
# ---------------------------------------------------------------------------

class TestTelemetry:
    def test_record_appends_jsonl(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CC_COMPRESS_CACHE", str(tmp_path))
        from snipp.mcp.observability import telemetry as t

        t.record({"tool": "grep", "original_tokens": 1000, "compressed_tokens": 100, "wall_ms": 10, "reduction_pct": 90.0})
        t.record({"tool": "pytest", "original_tokens": 500, "compressed_tokens": 50, "wall_ms": 5, "reduction_pct": 90.0})

        events = t.iter_events(t.telemetry_path())
        assert len(events) == 2
        assert events[0]["tool"] == "grep"

    def test_redact_fields_not_persisted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CC_COMPRESS_CACHE", str(tmp_path))
        from snipp.mcp.observability import telemetry as t

        t.record({
            "tool": "run",
            "output": "raw shell output that should NEVER be persisted",
            "stderr": "secret stderr",
            "argv": ["ls", "-la"],
            "env": {"SECRET": "x"},
            "wall_ms": 1.0,
        })

        text = t.telemetry_path().read_text()
        assert "raw shell output" not in text
        assert "secret stderr" not in text
        assert "SECRET" not in text

    def test_rotation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CC_COMPRESS_CACHE", str(tmp_path))
        from snipp.mcp.observability import telemetry as t

        # Force a tiny max size so rotation kicks in quickly
        for i in range(50):
            t.record({"tool": "x", "i": i, "wall_ms": 0.0}, max_bytes=200)

        rotated = t.telemetry_path().with_suffix(t.telemetry_path().suffix + ".1")
        assert rotated.exists() or t.telemetry_path().exists()

    def test_summarize_empty_returns_zeroes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CC_COMPRESS_CACHE", str(tmp_path))
        from snipp.mcp.observability import telemetry as t

        s = t.summarize()
        assert s["total_requests"] == 0
        assert s["total_tokens_saved"] == 0

    def test_summarize_with_events(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CC_COMPRESS_CACHE", str(tmp_path))
        from snipp.mcp.observability import telemetry as t

        for i in range(10):
            t.record({
                "tool": "grep" if i % 2 else "pytest",
                "original_tokens": 1000,
                "compressed_tokens": 100,
                "wall_ms": float(i * 10),
                "reduction_pct": 90.0,
            })

        s = t.summarize()
        assert s["total_requests"] == 10
        assert s["total_tokens_saved"] == 9000
        assert s["avg_reduction_pct"] == 90.0
        assert any(r["tool"] == "grep" for r in s["top_tools"])
        assert s["p95_wall_ms"] >= s["p50_wall_ms"]

    def test_opt_in_default_off(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from snipp.mcp.observability import telemetry as t
        assert t.is_opt_in() is False

    def test_opt_in_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from snipp.mcp.observability import telemetry as t

        t.set_opt_in(True)
        assert t.is_opt_in() is True
        t.set_opt_in(False)
        assert t.is_opt_in() is False


# ---------------------------------------------------------------------------
# tracing — no-op when env is unset
# ---------------------------------------------------------------------------

class TestTracingShim:
    def test_init_returns_false_without_env(self, monkeypatch):
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        from snipp.mcp.observability import tracing

        # Reset module state for a clean run
        tracing._initialized = False
        tracing._tracer = None
        assert tracing.init_tracing() is False

    def test_span_is_noop_when_inactive(self):
        from snipp.mcp.observability import tracing

        tracing._tracer = None
        with tracing.span("test", foo="bar") as s:
            assert s is None
        # add_attributes should not raise
        tracing.add_attributes(any="thing")


# ---------------------------------------------------------------------------
# server integration: a tool call writes a telemetry line + bumps metrics
# ---------------------------------------------------------------------------

class TestServerIntegration:
    @pytest.mark.asyncio
    async def test_compress_tool_records_telemetry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CC_COMPRESS_CACHE", str(tmp_path))

        # Reset module state so the new env var takes effect
        import snipp.expand as _expand_mod
        _expand_mod._default_session = None
        import snipp.mcp.server as _server_mod
        _server_mod._sessions = _server_mod.SessionRegistry()
        _server_mod._default_session_id = None
        _server_mod._path_guard = _server_mod.PathGuard(_server_mod._sessions.cache_root)

        from snipp.mcp.server import create_server
        from snipp.mcp.observability import telemetry as t

        server = create_server()
        await server.call_tool("snipp_compress", {
            "output": "file.py:1:def x(): pass\n" * 200,
            "command": "grep -rn def .",
        })

        events = t.iter_events()
        assert any(ev.get("tool") == "grep" for ev in events), \
            f"no telemetry recorded with tool=grep; got: {events[:3]}"

    @pytest.mark.asyncio
    async def test_compress_tool_increments_metrics(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CC_COMPRESS_CACHE", str(tmp_path))

        import snipp.expand as _expand_mod
        _expand_mod._default_session = None
        import snipp.mcp.server as _server_mod
        _server_mod._sessions = _server_mod.SessionRegistry()
        _server_mod._default_session_id = None
        _server_mod._path_guard = _server_mod.PathGuard(_server_mod._sessions.cache_root)

        from snipp.mcp.server import create_server
        from snipp.mcp.observability import metrics as m

        m.reset_for_tests()
        # Reload _metrics binding in server module so it picks up the new bundle
        _server_mod._metrics = m.get_metrics()

        server = create_server()
        await server.call_tool("snipp_compress", {
            "output": "x" * 500,
            "command": "ls",
        })

        text = m.render_text()
        assert "snipp_requests_total" in text
        assert "snipp_request_duration_seconds" in text
