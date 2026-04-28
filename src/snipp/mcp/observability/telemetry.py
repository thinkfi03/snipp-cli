"""Local JSONL telemetry sink with size-based rotation + opt-in remote shipper.

One JSON line per request. Default path:
    $CC_COMPRESS_CACHE/telemetry.jsonl  (or ~/.cache/snipp/telemetry.jsonl)

Schema:
    {ts, request_id, tool, original_tokens, compressed_tokens,
     reduction_pct, fidelity, wall_ms, exit_code, plugin, error}

Rotation: when the live file exceeds 100MB, it's renamed to .1, .2, … up to 5
generations. The oldest is dropped.

Remote shipping (off by default):
    ~/.config/snipp/telemetry.yaml has `opt_in: true` to enable.
    Aggregates are computed locally; raw bodies never leave the machine.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional

DEFAULT_MAX_BYTES = 100 * 1024 * 1024  # 100MB
DEFAULT_MAX_GENERATIONS = 5

_lock = threading.Lock()


def _telemetry_root() -> Path:
    base = os.environ.get("CC_COMPRESS_CACHE")
    if base:
        return Path(base).expanduser()
    return Path.home() / ".cache" / "snipp"


def telemetry_path() -> Path:
    return _telemetry_root() / "telemetry.jsonl"


def _opt_in_path() -> Path:
    return Path.home() / ".config" / "snipp" / "telemetry.yaml"


def is_opt_in() -> bool:
    p = _opt_in_path()
    if not p.exists():
        return False
    try:
        import yaml
        cfg = yaml.safe_load(p.read_text()) or {}
        return bool(cfg.get("opt_in", False))
    except Exception:
        return False


def set_opt_in(enabled: bool) -> Path:
    p = _opt_in_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    import yaml
    p.write_text(yaml.safe_dump({"opt_in": enabled}))
    return p


def record(event: Dict[str, Any], *, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
    """Append one event as a JSON line. Rotates if file is larger than max_bytes."""
    path = telemetry_path()
    line = json.dumps(_clean(event), default=str) + "\n"
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size + len(line.encode()) > max_bytes:
            _rotate(path)
        with path.open("a", encoding="utf-8") as f:
            f.write(line)


def _rotate(path: Path, generations: int = DEFAULT_MAX_GENERATIONS) -> None:
    """Rename live → .1, .1 → .2, …; drop the oldest."""
    for i in range(generations, 0, -1):
        src = path.with_suffix(path.suffix + f".{i}")
        if i == generations and src.exists():
            try:
                src.unlink()
            except OSError:
                pass
            continue
        if src.exists():
            dst = path.with_suffix(path.suffix + f".{i + 1}")
            try:
                src.rename(dst)
            except OSError:
                pass
    try:
        path.rename(path.with_suffix(path.suffix + ".1"))
    except OSError:
        pass


def _clean(event: Dict[str, Any]) -> Dict[str, Any]:
    """Remove fields that should never be persisted (raw output bodies, etc.)."""
    redact_keys = {"output", "compressed", "stdin", "stdout", "stderr", "argv", "env"}
    return {k: v for k, v in event.items() if k not in redact_keys}


def iter_events(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Read all events from the live file (and rotated generations)."""
    p = path or telemetry_path()
    files: List[Path] = []
    if p.exists():
        files.append(p)
    for i in range(1, DEFAULT_MAX_GENERATIONS + 1):
        rot = p.with_suffix(p.suffix + f".{i}")
        if rot.exists():
            files.append(rot)
    out: List[Dict[str, Any]] = []
    for f in files:
        try:
            with f.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        continue
        except OSError:
            continue
    return out


def summarize(path: Optional[Path] = None) -> Dict[str, Any]:
    """Compute headline stats from the JSONL: totals, p50/p95, top tools."""
    events = iter_events(path)
    if not events:
        return {
            "total_requests": 0,
            "total_tokens_saved": 0,
            "avg_reduction_pct": 0.0,
            "p50_wall_ms": 0.0,
            "p95_wall_ms": 0.0,
            "top_tools": [],
        }

    total_tokens_saved = 0
    reductions: List[float] = []
    wall_times: List[float] = []
    by_tool: Dict[str, Dict[str, Any]] = {}

    for ev in events:
        original = ev.get("original_tokens", 0) or 0
        compressed = ev.get("compressed_tokens", 0) or 0
        saved = max(0, original - compressed)
        total_tokens_saved += saved
        if "reduction_pct" in ev:
            reductions.append(float(ev["reduction_pct"]))
        if "wall_ms" in ev:
            wall_times.append(float(ev["wall_ms"]))

        tool = ev.get("tool") or "unknown"
        bucket = by_tool.setdefault(tool, {"count": 0, "tokens_saved": 0})
        bucket["count"] += 1
        bucket["tokens_saved"] += saved

    wall_sorted = sorted(wall_times) if wall_times else [0.0]
    p50 = wall_sorted[len(wall_sorted) // 2] if wall_sorted else 0.0
    p95_idx = max(0, int(len(wall_sorted) * 0.95) - 1)
    p95 = wall_sorted[p95_idx] if wall_sorted else 0.0

    top_tools = sorted(
        ({"tool": k, **v} for k, v in by_tool.items()),
        key=lambda r: r["count"],
        reverse=True,
    )[:5]

    return {
        "total_requests": len(events),
        "total_tokens_saved": total_tokens_saved,
        "avg_reduction_pct": (sum(reductions) / len(reductions)) if reductions else 0.0,
        "median_reduction_pct": median(reductions) if reductions else 0.0,
        "p50_wall_ms": round(p50, 2),
        "p95_wall_ms": round(p95, 2),
        "top_tools": top_tools,
    }
