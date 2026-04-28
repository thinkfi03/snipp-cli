"""Streaming line-buffered compression for grep/find/ls/generic.

For tools whose output is a sequence of independent lines, we can avoid
holding the full buffer in memory by streaming lines through a small state
machine and flushing every N lines / B bytes.

Public API:
    compress_stream(stdin_iter, *, tool, ...) -> Iterator[str]

The stream yields compressed text fragments. Caller writes them to a sink.
"""

from __future__ import annotations

import re
import sys
from typing import Callable, Iterable, Iterator, Optional

from snipp.compressors.base import BaseCompressor
from snipp.config import Config
from snipp.core import compress_output
from snipp.detector import ToolType


_FLUSH_LINES = 5000
_FLUSH_BYTES = 1 << 20  # 1 MiB


def stream_lines(
    source: Iterable[str],
    *,
    command: str = "",
    config: Optional[Config] = None,
    flush_lines: int = _FLUSH_LINES,
    flush_bytes: int = _FLUSH_BYTES,
    on_chunk: Optional[Callable[[str], None]] = None,
) -> Iterator[str]:
    """Compress a line-iterable in chunks and yield compressed fragments.

    Each chunk runs through the standard dispatcher with `command` so the
    same tool-specific compressor is selected. Chunk boundaries follow line
    counts or accumulated bytes, whichever comes first.
    """
    cfg = config or Config.default()
    buf: list = []
    bytes_buf = 0
    for line in source:
        if not line.endswith("\n"):
            line = line + "\n"
        buf.append(line)
        bytes_buf += len(line)
        if len(buf) >= flush_lines or bytes_buf >= flush_bytes:
            chunk = "".join(buf)
            result = compress_output(chunk, command=command, config=cfg)
            if on_chunk:
                on_chunk(chunk)
            yield result.compressed + "\n"
            buf, bytes_buf = [], 0
    if buf:
        chunk = "".join(buf)
        result = compress_output(chunk, command=command, config=cfg)
        if on_chunk:
            on_chunk(chunk)
        yield result.compressed + "\n"


def stream_stdin(command: str = "", config: Optional[Config] = None) -> Iterator[str]:
    """Convenience: stream sys.stdin line-by-line."""
    return stream_lines(sys.stdin, command=command, config=config)
