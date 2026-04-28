"""Bearer token auth for HTTP transport."""

from __future__ import annotations

import secrets
from typing import Optional, Set


class BearerAuth:
    """Constant-time bearer token validator."""

    def __init__(self, tokens: Optional[Set[str]] = None):
        self._tokens: Set[str] = set(tokens or ())

    def add_token(self, token: str) -> None:
        self._tokens.add(token)

    def is_valid(self, token: str) -> bool:
        """Check token against all stored tokens using constant-time comparison.

        Compares against EVERY stored token (no short-circuit) so wall time
        does not leak which token (if any) matched. With one token this is a
        single constant-time compare; with N tokens it's N constant-time
        compares OR'd together.
        """
        if not self._tokens:
            return False
        # Compare all; do not short-circuit.
        match = False
        for t in self._tokens:
            if secrets.compare_digest(token, t):
                match = True  # do not return early
        return match

    @property
    def enabled(self) -> bool:
        return bool(self._tokens)
