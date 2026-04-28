"""BM25-based intent ranking for query-aware chunk selection.

Pure-Python implementation (no external dep). Tokenization is deliberately
simple: lowercase, alphanumeric+underscore tokens of length >=2. Good enough
for code/log content and avoids a heavy NLP dependency.

Public API:
  - rank_chunks(chunks, query, top_k=None, min_score=None)
  - score_chunk(chunk, query)
  - tokenize(text)

All functions are deterministic and side-effect free.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+|\d+")
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "that", "this", "from", "into", "your",
        "are", "was", "were", "but", "not", "all", "any", "you", "have",
        "has", "had", "will", "can", "may", "use", "using", "uses",
    }
)


def tokenize(text: str) -> List[str]:
    return [
        t.lower()
        for t in _TOKEN_RE.findall(text)
        if len(t) >= 2 and t.lower() not in _STOPWORDS
    ]


@dataclass
class _Doc:
    idx: int
    tokens: List[str]
    length: int
    tf: dict


class BM25:
    """BM25 Okapi with k1=1.5, b=0.75."""

    def __init__(self, docs: Sequence[str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs: List[_Doc] = []
        df: dict = {}
        for i, raw in enumerate(docs):
            toks = tokenize(raw)
            tf: dict = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            for t in tf:
                df[t] = df.get(t, 0) + 1
            self.docs.append(_Doc(idx=i, tokens=toks, length=len(toks), tf=tf))
        n = max(1, len(self.docs))
        self.avgdl = (sum(d.length for d in self.docs) / n) if self.docs else 0.0
        self.idf = {
            t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()
        }
        self.n = n

    def score(self, query: str) -> List[Tuple[int, float]]:
        q_tokens = tokenize(query)
        if not q_tokens:
            return [(d.idx, 0.0) for d in self.docs]
        out: List[Tuple[int, float]] = []
        for d in self.docs:
            if d.length == 0:
                out.append((d.idx, 0.0))
                continue
            score = 0.0
            for qt in q_tokens:
                if qt not in d.tf:
                    continue
                idf = self.idf.get(qt, 0.0)
                tf = d.tf[qt]
                denom = tf + self.k1 * (1 - self.b + self.b * d.length / max(self.avgdl, 1.0))
                score += idf * (tf * (self.k1 + 1)) / denom
            out.append((d.idx, score))
        return out


def rank_chunks(
    chunks: Sequence[str],
    query: str,
    top_k: Optional[int] = None,
    min_score: Optional[float] = None,
) -> List[Tuple[int, float]]:
    """Rank chunks by BM25 relevance to query.

    Returns list of (chunk_index, score) sorted descending. Ties broken by index.
    Empty query returns chunks in original order with score 0.
    """
    if not chunks:
        return []
    bm25 = BM25(chunks)
    scored = bm25.score(query)
    scored.sort(key=lambda x: (-x[1], x[0]))
    if min_score is not None:
        scored = [s for s in scored if s[1] >= min_score]
    if top_k is not None:
        scored = scored[:top_k]
    return scored


def score_chunk(chunk: str, query: str) -> float:
    """Convenience: BM25 score of a single chunk against a query."""
    if not query.strip():
        return 0.0
    bm25 = BM25([chunk])
    return bm25.score(query)[0][1]


def select_relevant(
    chunks: Iterable[str],
    query: Optional[str],
    *,
    keep_count: int,
) -> List[str]:
    """Return up to keep_count chunks most relevant to query, preserving original order."""
    chunk_list = list(chunks)
    if not query or not chunk_list:
        return chunk_list[:keep_count]
    ranked = rank_chunks(chunk_list, query, top_k=keep_count)
    keep_idx = sorted(i for i, _ in ranked)
    return [chunk_list[i] for i in keep_idx]
