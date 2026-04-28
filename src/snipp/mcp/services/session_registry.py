"""Session registry: per-connection ExpansionStore management."""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from snipp.expand import ExpansionStore

logger = logging.getLogger(__name__)

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
DEFAULT_SESSION_TTL_SECONDS = 24 * 3600
DEFAULT_MAX_HANDLES = 1000
DEFAULT_MAX_SESSION_BYTES = 100 * 1024 * 1024


class SessionRegistry:
    """Manages per-session ExpansionStore instances with quota enforcement."""

    def __init__(
        self,
        cache_root: Optional[Path] = None,
        ttl_seconds: int = DEFAULT_SESSION_TTL_SECONDS,
        max_handles: int = DEFAULT_MAX_HANDLES,
        max_bytes: int = DEFAULT_MAX_SESSION_BYTES,
    ):
        self._stores: Dict[str, ExpansionStore] = {}
        self._created_at: Dict[str, float] = {}
        self._cache_root = cache_root or self._default_cache_root()
        self._ttl_seconds = ttl_seconds
        self._max_handles = max_handles
        self._max_bytes = max_bytes

    @property
    def cache_root(self) -> Path:
        return self._cache_root

    @staticmethod
    def _default_cache_root() -> Path:
        base = os.environ.get("CC_COMPRESS_CACHE")
        if base:
            return Path(base).expanduser()
        return Path.home() / ".cache" / "snipp"

    def create(self, session_id: Optional[str] = None) -> str:
        """Create a new session and return its ID."""
        if session_id:
            sid = session_id
            if not _SESSION_ID_RE.match(sid):
                raise ValueError(f"Invalid session_id: {sid!r}")
        else:
            sid = f"sess-{uuid.uuid4().hex[:16]}"

        store = ExpansionStore(session=sid, root=self._cache_root)
        self._stores[sid] = store
        self._created_at[sid] = time.time()
        logger.info("Session created: %s", sid)
        return sid

    def get(self, session_id: str) -> ExpansionStore:
        """Get or create a store for the given session ID."""
        if not _SESSION_ID_RE.match(session_id):
            raise ValueError(f"Invalid session_id: {session_id!r}")

        if session_id not in self._stores:
            self._stores[session_id] = ExpansionStore(session=session_id, root=self._cache_root)
            self._created_at[session_id] = time.time()
        return self._stores[session_id]

    def list_sessions(self) -> List[str]:
        return list(self._stores.keys())

    def handles_for(self, session_id: str) -> List[Dict]:
        store = self.get(session_id)
        results = []
        if not store.session_dir.exists():
            return results
        for meta_path in sorted(store.session_dir.glob("*.json")):
            try:
                import json
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                meta["age_seconds"] = time.time() - meta.get("created", time.time())
                results.append(meta)
            except Exception:
                logger.warning("Failed to read handle meta: %s", meta_path)
        return results

    def total_handles(self) -> int:
        return sum(len(self.handles_for(sid)) for sid in self._stores)

    def cache_size_bytes(self) -> int:
        total = 0
        if not self._cache_root.exists():
            return 0
        for sess_dir in self._cache_root.iterdir():
            if not sess_dir.is_dir():
                continue
            for f in sess_dir.iterdir():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
        return total

    def gc(self, protect: Optional[set] = None) -> int:
        """Remove expired sessions. Returns number of sessions removed.

        Args:
            protect: Session ids that must NOT be reaped regardless of age.
                Use this to keep the active default session alive during long
                server uptimes.
        """
        cutoff = time.time() - self._ttl_seconds
        protect = protect or set()
        removed = 0
        for sid in list(self._stores.keys()):
            if sid in protect:
                continue
            created = self._created_at.get(sid, 0)
            if created < cutoff:
                store = self._stores.pop(sid, None)
                self._created_at.pop(sid, None)
                if store:
                    try:
                        import shutil
                        shutil.rmtree(store.session_dir, ignore_errors=True)
                    except Exception:
                        pass
                removed += 1
                logger.info("GC removed session: %s", sid)
        return removed

    def touch(self, session_id: str) -> None:
        """Refresh the created-at timestamp for a session (extends TTL)."""
        if session_id in self._stores:
            self._created_at[session_id] = time.time()

    def check_quota(self, session_id: str) -> None:
        """Raise if session exceeds handle or byte quota."""
        handles = self.handles_for(session_id)
        if len(handles) >= self._max_handles:
            raise RuntimeError(
                f"Session {session_id!r} exceeded max_handles ({self._max_handles})"
            )
        total_bytes = sum(h.get("bytes", 0) for h in handles)
        if total_bytes >= self._max_bytes:
            raise RuntimeError(
                f"Session {session_id!r} exceeded max_bytes ({self._max_bytes})"
            )
