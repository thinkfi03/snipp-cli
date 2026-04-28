"""Prometheus-compatible metrics exposition.

Uses `prometheus_client` if installed; otherwise an in-memory shim that
exposes the same surface and serializes to text/plain Prometheus format.

Metrics defined per roadmap §8.2:
  - snipp_requests_total{tool, status}
  - snipp_request_duration_seconds{tool}
  - snipp_tokens_saved_total{tool}
  - snipp_handles_active
  - snipp_plugin_errors_total{plugin}
  - snipp_subprocess_kills_total{reason}
  - snipp_session_count
  - snipp_cache_bytes

Public API:
    Metrics.requests.labels(tool=..., status=...).inc()
    Metrics.duration.labels(tool=...).observe(seconds)
    Metrics.tokens_saved.labels(tool=...).inc(amount)
    Metrics.handles_active.set(n)
    Metrics.plugin_errors.labels(plugin=...).inc()
    Metrics.subprocess_kills.labels(reason=...).inc()
    Metrics.session_count.set(n)
    Metrics.cache_bytes.set(n)

    start_http_server(port: int) -> None    # backed by prometheus_client if present
    render_text() -> str                     # Prometheus text format for the in-memory shim
"""

from __future__ import annotations

import threading
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

try:  # pragma: no cover - import shim
    from prometheus_client import (
        Counter as _PCounter,
        Gauge as _PGauge,
        Histogram as _PHistogram,
        REGISTRY as _PROM_REGISTRY,
        start_http_server as _prom_start_http_server,
        generate_latest as _prom_generate_latest,
    )
    _PROM_AVAILABLE = True
except Exception:  # pragma: no cover
    _PROM_AVAILABLE = False


# ---------------------------------------------------------------------------
# In-memory shim — used when prometheus_client isn't installed
# ---------------------------------------------------------------------------

class _ShimMetric:
    """Base for the in-memory metric shim."""

    def __init__(self, name: str, doc: str, label_names: Tuple[str, ...] = ()):
        self.name = name
        self.doc = doc
        self.label_names = label_names
        self._lock = threading.Lock()
        self._values: Dict[Tuple[str, ...], float] = defaultdict(float)
        # For histograms only
        self._buckets: Dict[Tuple[str, ...], List[float]] = defaultdict(list)

    def labels(self, **labels) -> "_ShimMetric":
        # Bind labels to a child view that shares state
        key = tuple(labels.get(n, "") for n in self.label_names)
        return _ShimMetricView(self, key)


class _ShimMetricView:
    def __init__(self, parent: _ShimMetric, key: Tuple[str, ...]):
        self._parent = parent
        self._key = key

    def inc(self, amount: float = 1.0) -> None:
        with self._parent._lock:
            self._parent._values[self._key] += amount

    def observe(self, seconds: float) -> None:
        with self._parent._lock:
            self._parent._buckets[self._key].append(seconds)

    def set(self, value: float) -> None:
        with self._parent._lock:
            self._parent._values[self._key] = value


class _ShimUnlabeledView:
    """Shim view for metrics that have no labels (gauges)."""

    def __init__(self, parent: _ShimMetric):
        self._parent = parent
        self._key: Tuple[str, ...] = ()

    def inc(self, amount: float = 1.0) -> None:
        with self._parent._lock:
            self._parent._values[self._key] += amount

    def set(self, value: float) -> None:
        with self._parent._lock:
            self._parent._values[self._key] = value


_HIST_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class _ShimRegistry:
    def __init__(self):
        self._metrics: List[Tuple[str, _ShimMetric, str]] = []

    def register(self, kind: str, m: _ShimMetric) -> None:
        self._metrics.append((kind, m, m.name))

    def render(self) -> str:
        out: List[str] = []
        for kind, m, name in self._metrics:
            out.append(f"# HELP {name} {m.doc}")
            out.append(f"# TYPE {name} {kind}")
            with m._lock:
                if kind in ("counter", "gauge"):
                    for key, val in m._values.items():
                        if m.label_names:
                            label_str = ",".join(f'{ln}="{v}"' for ln, v in zip(m.label_names, key))
                            out.append(f"{name}{{{label_str}}} {val}")
                        else:
                            out.append(f"{name} {val}")
                elif kind == "histogram":
                    for key, samples in m._buckets.items():
                        label_prefix = ""
                        if m.label_names:
                            label_prefix = ",".join(f'{ln}="{v}"' for ln, v in zip(m.label_names, key))
                        for b in _HIST_BUCKETS:
                            count = sum(1 for s in samples if s <= b)
                            inner = f'le="{b}"' + (f",{label_prefix}" if label_prefix else "")
                            out.append(f"{name}_bucket{{{inner}}} {count}")
                        inf_inner = f'le="+Inf"' + (f",{label_prefix}" if label_prefix else "")
                        out.append(f"{name}_bucket{{{inf_inner}}} {len(samples)}")
                        sum_label = "{" + label_prefix + "}" if label_prefix else ""
                        out.append(f"{name}_sum{sum_label} {sum(samples)}")
                        out.append(f"{name}_count{sum_label} {len(samples)}")
        return "\n".join(out) + "\n"


_shim_registry = _ShimRegistry()


def _shim_counter(name: str, doc: str, label_names: Tuple[str, ...] = ()) -> _ShimMetric:
    m = _ShimMetric(name, doc, label_names)
    _shim_registry.register("counter", m)
    return m


def _shim_gauge(name: str, doc: str, label_names: Tuple[str, ...] = ()) -> _ShimMetric:
    m = _ShimMetric(name, doc, label_names)
    _shim_registry.register("gauge", m)
    return m


def _shim_histogram(name: str, doc: str, label_names: Tuple[str, ...] = ()) -> _ShimMetric:
    m = _ShimMetric(name, doc, label_names)
    _shim_registry.register("histogram", m)
    return m


# ---------------------------------------------------------------------------
# Public Metrics namespace
# ---------------------------------------------------------------------------

class _Gauge:
    """Wrapper that exposes inc/set even for label-less gauges."""

    def __init__(self, metric, view):
        self._metric = metric
        self._view = view

    def inc(self, amount: float = 1.0) -> None:
        self._view.inc(amount)

    def set(self, value: float) -> None:
        self._view.set(value)


def _make_metrics():
    if _PROM_AVAILABLE:
        try:
            requests = _PCounter("snipp_requests_total", "Total tool requests", ("tool", "status"))
            duration = _PHistogram("snipp_request_duration_seconds", "Tool request duration", ("tool",))
            tokens_saved = _PCounter("snipp_tokens_saved_total", "Total tokens saved", ("tool",))
            handles_active = _PGauge("snipp_handles_active", "Active elision handles")
            plugin_errors = _PCounter("snipp_plugin_errors_total", "Plugin failures", ("plugin",))
            subprocess_kills = _PCounter("snipp_subprocess_kills_total", "Subprocesses killed", ("reason",))
            session_count = _PGauge("snipp_session_count", "Active sessions")
            cache_bytes = _PGauge("snipp_cache_bytes", "Total cache size in bytes")
            return _PromBundle(
                requests, duration, tokens_saved, handles_active,
                plugin_errors, subprocess_kills, session_count, cache_bytes,
            )
        except ValueError:
            # prometheus_client raises if a metric is re-registered (e.g. test reload).
            # Fall back to shim in that case.
            pass

    requests = _shim_counter("snipp_requests_total", "Total tool requests", ("tool", "status"))
    duration = _shim_histogram("snipp_request_duration_seconds", "Tool request duration", ("tool",))
    tokens_saved = _shim_counter("snipp_tokens_saved_total", "Total tokens saved", ("tool",))
    handles_active = _shim_gauge("snipp_handles_active", "Active elision handles")
    plugin_errors = _shim_counter("snipp_plugin_errors_total", "Plugin failures", ("plugin",))
    subprocess_kills = _shim_counter("snipp_subprocess_kills_total", "Subprocesses killed", ("reason",))
    session_count = _shim_gauge("snipp_session_count", "Active sessions")
    cache_bytes = _shim_gauge("snipp_cache_bytes", "Total cache size in bytes")

    return _ShimBundle(
        requests, duration, tokens_saved, handles_active,
        plugin_errors, subprocess_kills, session_count, cache_bytes,
    )


class _PromBundle:
    backend = "prometheus_client"

    def __init__(self, requests, duration, tokens_saved, handles_active,
                 plugin_errors, subprocess_kills, session_count, cache_bytes):
        self.requests = requests
        self.duration = duration
        self.tokens_saved = tokens_saved
        self.handles_active = handles_active
        self.plugin_errors = plugin_errors
        self.subprocess_kills = subprocess_kills
        self.session_count = session_count
        self.cache_bytes = cache_bytes


class _ShimBundle:
    backend = "in-memory"

    def __init__(self, requests, duration, tokens_saved, handles_active,
                 plugin_errors, subprocess_kills, session_count, cache_bytes):
        self.requests = requests
        self.duration = duration
        self.tokens_saved = tokens_saved
        # Wrap gauges so they can be used directly with .inc()/.set()
        self.handles_active = _Gauge(handles_active, _ShimUnlabeledView(handles_active))
        self.plugin_errors = plugin_errors
        self.subprocess_kills = subprocess_kills
        self.session_count = _Gauge(session_count, _ShimUnlabeledView(session_count))
        self.cache_bytes = _Gauge(cache_bytes, _ShimUnlabeledView(cache_bytes))


# Module-level singleton, initialized on first access
_metrics_instance: Optional[object] = None
_metrics_lock = threading.Lock()


def get_metrics():
    """Return the metrics bundle, creating it once."""
    global _metrics_instance
    if _metrics_instance is None:
        with _metrics_lock:
            if _metrics_instance is None:
                _metrics_instance = _make_metrics()
    return _metrics_instance


def reset_for_tests() -> None:
    """Reset module state — only for tests that need a clean registry."""
    global _metrics_instance
    with _metrics_lock:
        _metrics_instance = None
        _shim_registry._metrics.clear()


def start_http_server(port: int, addr: str = "127.0.0.1") -> bool:
    """Start a Prometheus-compatible /metrics HTTP endpoint.

    Returns True if the prometheus_client backend started a real server,
    False if we're using the in-memory shim (no HTTP).
    """
    if not _PROM_AVAILABLE:
        return False
    _prom_start_http_server(port, addr=addr)
    return True


def render_text() -> str:
    """Render current metrics as Prometheus text exposition format."""
    if _PROM_AVAILABLE:
        return _prom_generate_latest(_PROM_REGISTRY).decode("utf-8")
    return _shim_registry.render()
