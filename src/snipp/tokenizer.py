"""Real tokenizer adapters with graceful fallback.

Cost model:
  - tiktoken: free, fully local
  - transformers (HF): free, local; first run downloads public weights
  - anthropic count_tokens: free endpoint, requires ANTHROPIC_API_KEY
  - heuristic: free fallback when nothing is installed

No tokenizer here makes a paid API call. The Anthropic adapter uses the
billing-free `count_tokens` endpoint but is opt-in via env var.

Model coverage (as of April 2026):
  Anthropic Claude — all models via anthropic count_tokens API
    Opus 4.7, Opus 4.6, Opus 4.5, Sonnet 4.6, Sonnet 4.5, Haiku 4.5,
    Claude 4, Claude 3.7 Sonnet, Claude 3.5 Sonnet, Mythos Preview, etc.

  OpenAI — via tiktoken
    GPT-5 family (o200k_base), GPT-4.1 family (o200k_base),
    GPT-4o family (o200k_base), o3/o4-mini/o3-pro (o200k_base),
    GPT-4 / GPT-4-turbo / GPT-4.5 (cl100k_base), Codex (o200k_base)

  Open-weight — via HuggingFace
    Kimi K2, Qwen 2.5, DeepSeek V3, Llama 3.1, GPT-OSS
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Optional


_lock = threading.Lock()


@dataclass(frozen=True)
class TokenCount:
    tokens: int
    tokenizer: str
    exact: bool

    def __int__(self) -> int:
        return self.tokens


# ---------------------------------------------------------------------------
# Model → tokenizer backend mapping
# ---------------------------------------------------------------------------
# All keys are lower-cased for lookup. Prefix matching is used so that
# "claude-sonnet-4-6-20260217" still resolves to "anthropic".

MODEL_TOKENIZER_MAP = {
    # -- Anthropic Claude (all models share the same tokenizer) ----------
    "claude": "anthropic",
    "claude-opus": "anthropic",
    "claude-sonnet": "anthropic",
    "claude-haiku": "anthropic",
    "claude-instant": "anthropic",
    "claude-mythos": "anthropic",

    # -- OpenAI GPT-5 family (o200k_base) -------------------------------
    "gpt-5": "tiktoken:o200k_base",
    "gpt-5.1": "tiktoken:o200k_base",
    "gpt-5.2": "tiktoken:o200k_base",
    "gpt-5.3": "tiktoken:o200k_base",
    "gpt-5-mini": "tiktoken:o200k_base",
    "gpt-5-nano": "tiktoken:o200k_base",
    "gpt-5-lite": "tiktoken:o200k_base",

    # -- OpenAI GPT-4.1 family (o200k_base) -----------------------------
    "gpt-4.1": "tiktoken:o200k_base",
    "gpt-4.1-mini": "tiktoken:o200k_base",
    "gpt-4.1-nano": "tiktoken:o200k_base",

    # -- OpenAI GPT-4o family (o200k_base) ------------------------------
    "gpt-4o": "tiktoken:o200k_base",
    "gpt-4o-mini": "tiktoken:o200k_base",
    "gpt-4o-lite": "tiktoken:o200k_base",
    "chatgpt-4o": "tiktoken:o200k_base",

    # -- OpenAI reasoning models (o200k_base) ---------------------------
    "o1": "tiktoken:o200k_base",
    "o1-mini": "tiktoken:o200k_base",
    "o1-pro": "tiktoken:o200k_base",
    "o3": "tiktoken:o200k_base",
    "o3-mini": "tiktoken:o200k_base",
    "o3-pro": "tiktoken:o200k_base",
    "o4-mini": "tiktoken:o200k_base",

    # -- OpenAI Codex / agentic coding (o200k_base) ---------------------
    "codex": "tiktoken:o200k_base",
    "gpt-5.3-codex": "tiktoken:o200k_base",

    # -- OpenAI GPT-4 legacy (cl100k_base) ------------------------------
    "gpt-4": "tiktoken:cl100k_base",
    "gpt-4-turbo": "tiktoken:cl100k_base",
    "gpt-4.5": "tiktoken:cl100k_base",
    "gpt-4.5-preview": "tiktoken:cl100k_base",
    "gpt-4.5-mini": "tiktoken:cl100k_base",
    "gpt-4.5-nano": "tiktoken:cl100k_base",

    # -- OpenAI open-weight models (cl100k_base approximation) ----------
    "gpt-oss": "tiktoken:cl100k_base",

    # -- Moonshot Kimi --------------------------------------------------
    "kimi": "hf:moonshotai/Kimi-K2-Instruct",
    "kimi-k2": "hf:moonshotai/Kimi-K2-Instruct",

    # -- Alibaba Qwen ---------------------------------------------------
    "qwen": "hf:Qwen/Qwen2.5-Coder-32B-Instruct",
    "qwen2.5": "hf:Qwen/Qwen2.5-Coder-32B-Instruct",

    # -- DeepSeek -------------------------------------------------------
    "deepseek": "hf:deepseek-ai/DeepSeek-V3",
    "deepseek-v3": "hf:deepseek-ai/DeepSeek-V3",

    # -- Meta Llama -----------------------------------------------------
    "llama": "hf:meta-llama/Llama-3.1-8B",
    "llama-3": "hf:meta-llama/Llama-3.1-8B",
    "llama-3.1": "hf:meta-llama/Llama-3.1-8B",
}

# Map of model-name prefixes → canonical model names for the Anthropic
# count_tokens endpoint. The API accepts any valid model identifier, but
# using the latest stable name for each family avoids deprecation warnings.
_ANTHROPIC_MODEL_ALIASES = {
    "claude-opus": "claude-opus-4-7",
    "claude-sonnet": "claude-sonnet-4-6",
    "claude-haiku": "claude-haiku-4-5",
    "claude-mythos": "claude-mythos-preview",
    "claude-instant": "claude-instant-1.2",
}


def _anthropic_model_name(model_key: str) -> str:
    """Return the best model name to pass to Anthropic count_tokens."""
    key = model_key.lower()
    # Exact alias match first
    if key in _ANTHROPIC_MODEL_ALIASES:
        return _ANTHROPIC_MODEL_ALIASES[key]
    # If it looks like a dated API model ID (e.g. claude-opus-4-7-20260401),
    # use it verbatim — these are exact identifiers, not prefixes.
    if "-20" in key:
        return key
    # Prefix match against known aliases (longest prefix wins)
    for prefix, canonical in sorted(_ANTHROPIC_MODEL_ALIASES.items(), key=lambda x: -len(x[0])):
        if key.startswith(prefix):
            return canonical
    # Bare "claude-" IDs that aren't aliases and aren't dated
    if key.startswith("claude-"):
        return key
    return "claude-sonnet-4-6"


class Tokenizer:
    def __init__(self, name: str, encode_fn: Callable[[str], int], exact: bool):
        self.name = name
        self._encode = encode_fn
        self.exact = exact

    def count(self, text: str) -> TokenCount:
        if not text:
            return TokenCount(0, self.name, self.exact)
        return TokenCount(self._encode(text), self.name, self.exact)


@lru_cache(maxsize=8)
def _load_tiktoken(encoding: str) -> Optional[Tokenizer]:
    try:
        import tiktoken
    except ImportError:
        return None
    try:
        enc = tiktoken.get_encoding(encoding)
    except Exception:
        return None
    return Tokenizer(
        name=f"tiktoken:{encoding}",
        encode_fn=lambda s: len(enc.encode(s, disallowed_special=())),
        exact=True,
    )


@lru_cache(maxsize=8)
def _load_hf(model_id: str) -> Optional[Tokenizer]:
    try:
        from transformers import AutoTokenizer  # type: ignore
    except ImportError:
        return None
    try:
        tk = AutoTokenizer.from_pretrained(model_id, trust_remote_code=False)
    except Exception:
        return None
    return Tokenizer(
        name=f"hf:{model_id}",
        encode_fn=lambda s: len(tk.encode(s, add_special_tokens=False)),
        exact=True,
    )


@lru_cache(maxsize=8)
def _load_anthropic(model_key: str = "claude-sonnet-4-6") -> Optional[Tokenizer]:
    """Load Anthropic count_tokens tokenizer.

    Args:
        model_key: The model name / identifier used for lookup. We map this
            to the best canonical name for the count_tokens API.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic  # type: ignore
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic()
    except Exception:
        return None

    api_model = _anthropic_model_name(model_key)

    def _count(text: str) -> int:
        try:
            r = client.messages.count_tokens(
                model=api_model,
                messages=[{"role": "user", "content": text}],
            )
            return int(r.input_tokens)
        except Exception:
            return max(1, len(text) // 4)

    return Tokenizer(
        name=f"anthropic:count_tokens ({api_model})",
        encode_fn=_count,
        exact=True,
    )


def _heuristic() -> Tokenizer:
    return Tokenizer(
        name="heuristic:chars/4",
        encode_fn=lambda s: max(1, len(s) // 4),
        exact=False,
    )


def get_tokenizer(model: Optional[str] = None) -> Tokenizer:
    """Get the best available tokenizer for the given model.

    Args:
        model: Model name or identifier, e.g. "claude-sonnet-4-6",
            "gpt-5", "gpt-4.1-nano", "o3-mini", "codex", etc.
            If None, tries tiktoken cl100k_base, then heuristic.

    Returns:
        Tokenizer instance ready to count tokens.
    """
    with _lock:
        if model:
            key = model.lower().strip()
            # 1. Exact lookup
            spec = MODEL_TOKENIZER_MAP.get(key)
            # 2. Prefix lookup (longest prefix wins)
            if spec is None:
                matches = [
                    (prefix, mapped)
                    for prefix, mapped in MODEL_TOKENIZER_MAP.items()
                    if key.startswith(prefix)
                ]
                if matches:
                    # Sort by prefix length descending → longest match wins
                    matches.sort(key=lambda x: -len(x[0]))
                    spec = matches[0][1]

            if spec:
                if spec == "anthropic":
                    tk = _load_anthropic(model_key=key)
                    if tk:
                        return tk
                elif spec.startswith("tiktoken:"):
                    tk = _load_tiktoken(spec.split(":", 1)[1])
                    if tk:
                        return tk
                elif spec.startswith("hf:"):
                    tk = _load_hf(spec.split(":", 1)[1])
                    if tk:
                        return tk

        # Fallback chain
        tk = _load_tiktoken("o200k_base")
        if tk:
            return tk
        tk = _load_tiktoken("cl100k_base")
        if tk:
            return tk
        return _heuristic()


def list_backends() -> dict[str, bool]:
    """Return availability map for each tokenizer backend."""
    tiktoken_ok = False
    try:
        import tiktoken
        tiktoken_ok = True
    except ImportError:
        pass

    anthropic_ok = False
    try:
        import anthropic
        anthropic_ok = bool(os.environ.get("ANTHROPIC_API_KEY"))
    except ImportError:
        pass

    hf_ok = False
    try:
        from transformers import AutoTokenizer
        hf_ok = True
    except ImportError:
        pass

    return {
        "tiktoken": tiktoken_ok,
        "anthropic": anthropic_ok,
        "hf": hf_ok,
    }


def count_tokens(text: str, model: Optional[str] = None) -> int:
    """Count tokens in text for the given model.

    Args:
        text: Text to tokenize.
        model: Model identifier (optional).

    Returns:
        Number of tokens.
    """
    return get_tokenizer(model).count(text).tokens
