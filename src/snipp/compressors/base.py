"""Base compressor interface with real tokenizer + fidelity metrics."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, TYPE_CHECKING
from abc import ABC, abstractmethod

from snipp.tokenizer import Tokenizer, get_tokenizer

if TYPE_CHECKING:
    from snipp.expand import ExpansionStore


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def strip_ansi(text: str) -> str:
    """Remove ANSI color/control escapes from a string."""
    return _ANSI_RE.sub("", text)


@dataclass
class CompressResult:
    """Result of compression with reduction + fidelity metrics."""

    compressed: str
    original_tokens: int
    compressed_tokens: int
    tool_type: str
    strategy: str
    tokenizer: str = "heuristic:chars/4"
    exact_tokens: bool = False
    fidelity: Dict[str, Any] = field(default_factory=dict)
    elision_handles: list = field(default_factory=list)

    @property
    def reduction_pct(self) -> float:
        if self.original_tokens == 0:
            return 0.0
        return (1 - self.compressed_tokens / self.original_tokens) * 100

    @property
    def savings(self) -> int:
        return self.original_tokens - self.compressed_tokens


def estimate_tokens(text: str, tokenizer: Optional[Tokenizer] = None) -> int:
    """Token count using configured tokenizer (free, local by default)."""
    tk = tokenizer or get_tokenizer()
    return tk.count(text).tokens


class BaseCompressor(ABC):
    """Base class for all compressors.

    Subclasses implement compress(); base provides tokenizer access,
    truncation helpers, and elision handle creation.
    """

    def __init__(
        self,
        max_tokens: int = 4000,
        tokenizer: Optional[Tokenizer] = None,
        store: Optional["ExpansionStore"] = None,
    ):
        self.max_tokens = max_tokens
        self.tokenizer = tokenizer or get_tokenizer()
        self._store = store

    @property
    def store(self) -> "ExpansionStore":
        if self._store is None:
            from snipp.expand import default_store
            self._store = default_store()
        return self._store

    def count(self, text: str) -> int:
        return self.tokenizer.count(text).tokens

    @staticmethod
    def strip_ansi(text: str) -> str:
        return _ANSI_RE.sub("", text)

    @abstractmethod
    def compress(self, output: str, query: Optional[str] = None) -> CompressResult:
        ...

    def _elide(self, content: str, *, source: Optional[str] = None) -> str:
        """Persist content and return a handle string."""
        if not content.strip():
            return ""
        try:
            return self.store.store(content, source=source)
        except Exception:
            nl = content.count("\n") + 1
            return f"[... {nl} lines elided ...]"

    def _truncate_to_tokens(self, text: str, max_tokens: Optional[int] = None) -> str:
        """Truncate text to token limit, eliding remainder via the store."""
        limit = max_tokens or self.max_tokens
        if self.count(text) <= limit:
            return text
        lines = text.split("\n")
        lo, hi = 0, len(lines)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.count("\n".join(lines[:mid])) <= limit:
                lo = mid
            else:
                hi = mid - 1
        kept = "\n".join(lines[:lo])
        elided = "\n".join(lines[lo:])
        handle = self._elide(elided, source="truncation")
        suffix = f"\n{handle}" if handle else ""
        return kept + suffix
