"""OpenTelemetry tracing — gated on OTEL_EXPORTER_OTLP_ENDPOINT.

If `opentelemetry-api` + `opentelemetry-sdk` are installed AND the env var
`OTEL_EXPORTER_OTLP_ENDPOINT` is set, spans are emitted via the OTLP HTTP
exporter. Otherwise, a no-op shim returns context managers that do nothing.

Public API:
    init_tracing() -> bool
    span(name: str, **attrs) -> ContextManager
    add_attributes(**attrs) -> None
"""

from __future__ import annotations

import contextlib
import os
from typing import Any, Iterator, Optional

_tracer = None
_initialized = False


def init_tracing(service_name: str = "snipp-mcp") -> bool:
    """Configure OTLP exporter if env + deps are present. Returns True if active."""
    global _tracer, _initialized
    if _initialized:
        return _tracer is not None

    _initialized = True
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except Exception:
        return False

    try:
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        exporter = OTLPSpanExporter(endpoint=endpoint + "/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("snipp.mcp")
        return True
    except Exception:
        return False


@contextlib.contextmanager
def span(name: str, **attrs: Any) -> Iterator[Any]:
    """Open a span if tracing is enabled; otherwise a no-op."""
    if _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(name) as s:
        try:
            for k, v in attrs.items():
                s.set_attribute(k, _safe_attr(v))
        except Exception:
            pass
        yield s


def add_attributes(**attrs: Any) -> None:
    """Add attributes to the current span (if any)."""
    if _tracer is None:
        return
    try:
        from opentelemetry import trace
        current = trace.get_current_span()
        for k, v in attrs.items():
            current.set_attribute(k, _safe_attr(v))
    except Exception:
        pass


def _safe_attr(v: Any) -> Any:
    """OTel only accepts primitives + lists of primitives."""
    if isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (list, tuple)):
        return [_safe_attr(x) for x in v if isinstance(x, (str, int, float, bool))]
    return str(v)[:200]


def is_active() -> bool:
    return _tracer is not None
