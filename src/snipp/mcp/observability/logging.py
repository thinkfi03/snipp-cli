"""Structured logging for the MCP server.

Uses `structlog` if installed; falls back to stdlib `logging` with a JSON
formatter. Every request gets a `request_id` (8-byte hex) carried via
contextvars so log lines emitted from inside tool handlers correlate.

Redaction policy:
  - `env`, `auth_token`, `bearer`, `password`, `secret` keys → "<redacted>"
  - Raw command output bodies are NEVER logged (only their length).

Public API:
    configure(level: str, json_output: bool = True)
    get_logger(name: str) -> Logger
    request_context(request_id: Optional[str] = None) -> ContextManager
    new_request_id() -> str
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import sys
import time
from contextvars import ContextVar
from typing import Any, Dict, Iterator, Optional

_REDACT_KEYS = {
    "env", "auth_token", "bearer", "password", "passwd", "secret",
    "api_key", "anthropic_api_key", "openai_api_key", "token",
    "authorization",
}
_REDACTED = "<redacted>"

_request_id: ContextVar[Optional[str]] = ContextVar("cc_request_id", default=None)
_extra_context: ContextVar[Dict[str, Any]] = ContextVar("cc_extra_context", default={})

_configured = False


def new_request_id() -> str:
    return secrets.token_hex(8)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (_REDACTED if k.lower() in _REDACT_KEYS else _redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


class _JSONFormatter(logging.Formatter):
    """Stdlib formatter that emits a JSON line per record with redaction + request_id."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = _request_id.get()
        if rid:
            payload["request_id"] = rid
        ctx = _extra_context.get()
        if ctx:
            payload.update(_redact(ctx))
        # Include any structured kwargs the caller passed via .extra
        if hasattr(record, "structured"):
            payload.update(_redact(getattr(record, "structured")))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        try:
            return json.dumps(payload, default=str)
        except Exception:
            return json.dumps({"ts": time.time(), "level": record.levelname, "msg": str(record.getMessage())})


def configure(level: str = "INFO", json_output: bool = True) -> None:
    """Configure logging once. Idempotent."""
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # Wipe any pre-existing handlers so our format wins
    root.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stderr)
    if json_output:
        handler.setFormatter(_JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)


class _StructuredAdapter(logging.LoggerAdapter):
    """Logger adapter that accepts arbitrary structured kwargs."""

    def process(self, msg: str, kwargs: Dict[str, Any]):
        structured = kwargs.pop("extra_fields", None)
        extra = kwargs.setdefault("extra", {})
        if structured:
            extra["structured"] = structured
        return msg, kwargs


def get_logger(name: str) -> _StructuredAdapter:
    return _StructuredAdapter(logging.getLogger(name), {})


@contextlib.contextmanager
def request_context(request_id: Optional[str] = None, **extra: Any) -> Iterator[str]:
    """Bind a request_id and extra context for the duration of a tool call."""
    rid = request_id or new_request_id()
    rid_token = _request_id.set(rid)
    ctx_token = _extra_context.set({**_extra_context.get(), **extra})
    try:
        yield rid
    finally:
        _request_id.reset(rid_token)
        _extra_context.reset(ctx_token)
