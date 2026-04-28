"""Path traversal protection for handles, sessions, and filesystem access."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_HANDLE_RE = re.compile(r"^[0-9a-f]{8,16}$")
_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class PathGuard:
    """Validates that filesystem paths stay within allowed roots."""

    def __init__(self, cache_root: Path):
        self.cache_root = cache_root.expanduser().resolve()

    def validate_handle(self, handle: str) -> str:
        """Extract and validate a raw handle hex string."""
        # Handle may be wrapped: [<<elide:HHHHHHHH:NL>>]
        from snipp.expand import HANDLE_RE
        m = HANDLE_RE.search(handle)
        h = m.group(1) if m else handle.strip()
        if not _HANDLE_RE.match(h):
            raise ValueError(f"Invalid handle format: {handle!r}")
        return h

    def validate_session_id(self, session_id: str) -> str:
        if not _SESSION_ID_RE.match(session_id):
            raise ValueError(f"Invalid session_id: {session_id!r}")
        return session_id

    def resolve_handle_path(self, session_id: str, handle: str, suffix: str = ".txt") -> Path:
        """Return the canonical path for a handle, ensuring it stays under cache_root."""
        sid = self.validate_session_id(session_id)
        h = self.validate_handle(handle)
        target = (self.cache_root / sid / f"{h}{suffix}").resolve()
        # Ensure no traversal
        if not str(target).startswith(str(self.cache_root)):
            raise ValueError(f"Path traversal detected: {target}")
        return target

    def resolve_session_dir(self, session_id: str) -> Path:
        sid = self.validate_session_id(session_id)
        target = (self.cache_root / sid).resolve()
        if not str(target).startswith(str(self.cache_root)):
            raise ValueError(f"Path traversal detected: {target}")
        return target


def validate_handle(handle: str) -> str:
    """Standalone handle validation."""
    from snipp.expand import HANDLE_RE
    m = HANDLE_RE.search(handle)
    h = m.group(1) if m else handle.strip()
    if not _HANDLE_RE.match(h):
        raise ValueError(f"Invalid handle format: {handle!r}")
    return h


def validate_session_id(session_id: str) -> str:
    if not _SESSION_ID_RE.match(session_id):
        raise ValueError(f"Invalid session_id: {session_id!r}")
    return session_id
