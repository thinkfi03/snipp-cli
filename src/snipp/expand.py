"""Round-trip expansion sidecar for lossy compression.

Every elision becomes a stable handle of the form `[<<elide:HHHHHHHH:NL>>]`
where HHHHHHHH is the first 8 hex chars of a SHA-256 over the elided content,
and NL is the line count (informational).

Original content is persisted under ~/.cache/snipp/<session>/<handle>.txt
so an agent can call `snipp expand <handle>` later.

Storage is content-addressed and idempotent. Sessions are isolated to avoid
collisions across concurrent agents.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

HANDLE_RE = re.compile(r"\[<<elide:([0-9a-f]{8,16}):(\d+)L>>\]")
DEFAULT_TTL_SECONDS = 7 * 24 * 3600


def cache_root() -> Path:
    base = os.environ.get("CC_COMPRESS_CACHE")
    if base:
        return Path(base).expanduser()
    return Path.home() / ".cache" / "snipp"


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


@dataclass
class ExpansionStore:
    """Filesystem-backed content-addressed store for elided content."""

    session: str
    root: Path = field(default_factory=cache_root)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def session_dir(self) -> Path:
        return self.root / self.session

    def _ensure_dir(self) -> None:
        self.session_dir.mkdir(parents=True, exist_ok=True)

    def store(self, content: str, *, source: Optional[str] = None) -> str:
        """Persist content and return a handle string `[<<elide:H:NL>>]`."""
        if not content:
            return ""
        h = _digest(content)
        nl = content.count("\n") + 1
        with self._lock:
            self._ensure_dir()
            path = self.session_dir / f"{h}.txt"
            if not path.exists():
                tmp = path.with_suffix(".txt.tmp")
                tmp.write_text(content, encoding="utf-8")
                tmp.replace(path)
            meta_path = self.session_dir / f"{h}.json"
            if not meta_path.exists():
                meta = {
                    "handle": h,
                    "lines": nl,
                    "bytes": len(content.encode("utf-8")),
                    "source": source,
                    "created": time.time(),
                }
                meta_path.write_text(json.dumps(meta), encoding="utf-8")
        return f"[<<elide:{h}:{nl}L>>]"

    def expand(self, handle: str) -> Optional[str]:
        """Return original content for `handle` (raw or wrapped form).

        Only looks inside this store's session directory for isolation.
        """
        m = HANDLE_RE.search(handle)
        h = m.group(1) if m else handle.strip()
        if not re.fullmatch(r"[0-9a-f]{8,16}", h):
            return None
        path = self.session_dir / f"{h}.txt"
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def gc(self, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> int:
        """Delete entries older than ttl. Returns number removed."""
        if not self.root.exists():
            return 0
        cutoff = time.time() - ttl_seconds
        removed = 0
        for sess in self.root.glob("*"):
            if not sess.is_dir():
                continue
            for f in sess.iterdir():
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        removed += 1
                except OSError:
                    pass
            try:
                next(sess.iterdir())
            except StopIteration:
                sess.rmdir()
        return removed


_default_session: Optional[ExpansionStore] = None
_default_lock = threading.Lock()


def default_store() -> ExpansionStore:
    global _default_session
    with _default_lock:
        if _default_session is None:
            sess = os.environ.get("CC_COMPRESS_SESSION") or f"sess-{os.getpid()}"
            _default_session = ExpansionStore(session=sess)
        return _default_session


def find_handles(text: str) -> Dict[str, int]:
    """Return {handle_id: line_count} for every elision token in text."""
    return {m.group(1): int(m.group(2)) for m in HANDLE_RE.finditer(text)}
