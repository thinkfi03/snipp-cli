"""Observability package: structured logging, metrics, tracing, telemetry."""

from snipp.mcp.observability.logging import (
    configure as configure_logging,
    get_logger,
    new_request_id,
    request_context,
)
from snipp.mcp.observability.metrics import (
    get_metrics,
    start_http_server as start_metrics_server,
    render_text as render_metrics,
    reset_for_tests as reset_metrics_for_tests,
)
from snipp.mcp.observability.tracing import (
    init_tracing,
    span,
    add_attributes,
    is_active as tracing_active,
)
from snipp.mcp.observability.telemetry import (
    record as record_telemetry,
    summarize as summarize_telemetry,
    iter_events as iter_telemetry_events,
    telemetry_path,
    is_opt_in as telemetry_opt_in,
    set_opt_in as set_telemetry_opt_in,
)

__all__ = [
    "configure_logging",
    "get_logger",
    "new_request_id",
    "request_context",
    "get_metrics",
    "start_metrics_server",
    "render_metrics",
    "reset_metrics_for_tests",
    "init_tracing",
    "span",
    "add_attributes",
    "tracing_active",
    "record_telemetry",
    "summarize_telemetry",
    "iter_telemetry_events",
    "telemetry_path",
    "telemetry_opt_in",
    "set_telemetry_opt_in",
]
