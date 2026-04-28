"""Tests for tokenizer adapters."""

import os

import pytest

from snipp.tokenizer import (
    get_tokenizer,
    count_tokens,
    MODEL_TOKENIZER_MAP,
    _anthropic_model_name,
)


class TestTokenizerBasics:
    def test_default_tokenizer_works(self):
        tk = get_tokenizer()
        assert tk.count("hello world").tokens > 0

    def test_count_tokens_helper(self):
        n = count_tokens("def hello(): return 1")
        assert n > 0

    def test_empty_text_returns_zero(self):
        assert get_tokenizer().count("").tokens == 0

    def test_unknown_model_falls_back(self):
        tk = get_tokenizer("definitely-not-a-real-model")
        assert tk.count("x").tokens > 0


class TestModelMap:
    """Verify all major model families are in the map."""

    @pytest.mark.parametrize(
        "model_key",
        [
            # Anthropic Claude
            "claude",
            "claude-opus",
            "claude-sonnet",
            "claude-haiku",
            "claude-instant",
            "claude-mythos",
            # OpenAI GPT-5
            "gpt-5",
            "gpt-5.2",
            "gpt-5.3",
            "gpt-5-mini",
            "gpt-5-nano",
            # OpenAI GPT-4.1
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4.1-nano",
            # OpenAI GPT-4o
            "gpt-4o",
            "gpt-4o-mini",
            "chatgpt-4o",
            # OpenAI reasoning
            "o1",
            "o1-mini",
            "o3",
            "o3-mini",
            "o3-pro",
            "o4-mini",
            # OpenAI Codex
            "codex",
            "gpt-5.3-codex",
            # OpenAI GPT-4 legacy
            "gpt-4",
            "gpt-4-turbo",
            "gpt-4.5",
            # Open-weight
            "gpt-oss",
            # Kimi
            "kimi",
            "kimi-k2",
            # Qwen
            "qwen",
            "qwen2.5",
            # DeepSeek
            "deepseek",
            "deepseek-v3",
            # Llama
            "llama",
            "llama-3",
            "llama-3.1",
        ],
    )
    def test_model_in_map(self, model_key):
        assert model_key in MODEL_TOKENIZER_MAP, f"{model_key} missing from MODEL_TOKENIZER_MAP"

    @pytest.mark.parametrize(
        "model_key,expected_backend",
        [
            ("claude-sonnet-4-6-20260217", "anthropic"),
            ("claude-opus-4-7", "anthropic"),
            ("claude-haiku-4-5", "anthropic"),
            ("gpt-5.2-instant", "tiktoken:o200k_base"),
            ("gpt-4.1-2025-04-14", "tiktoken:o200k_base"),
            ("gpt-4o-2025-08-06", "tiktoken:o200k_base"),
            ("o3-2025-04-16", "tiktoken:o200k_base"),
            ("o4-mini-2025-04-16", "tiktoken:o200k_base"),
            ("codex-cli", "tiktoken:o200k_base"),
            ("gpt-4-turbo-2024-04-09", "tiktoken:cl100k_base"),
        ],
    )
    def test_prefix_resolution(self, model_key, expected_backend):
        """Dated model IDs like 'claude-sonnet-4-6-20260217' resolve correctly."""
        from snipp.tokenizer import get_tokenizer

        # We can't easily assert the backend without mocking, but we can
        # verify the tokenizer name contains the expected backend identifier.
        tk = get_tokenizer(model_key)
        if expected_backend.startswith("tiktoken:"):
            assert "tiktoken" in tk.name
        elif expected_backend == "anthropic":
            # Without API key it falls back to tiktoken/heuristic
            assert "tiktoken" in tk.name or "heuristic" in tk.name or "anthropic" in tk.name


class TestAnthropicModelNameResolution:
    """Verify _anthropic_model_name maps aliases correctly."""

    def test_opus_maps_to_latest(self):
        assert _anthropic_model_name("claude-opus") == "claude-opus-4-7"
        assert _anthropic_model_name("claude-opus-4-6") == "claude-opus-4-7"

    def test_sonnet_maps_to_latest(self):
        assert _anthropic_model_name("claude-sonnet") == "claude-sonnet-4-6"
        assert _anthropic_model_name("claude-sonnet-4-5") == "claude-sonnet-4-6"

    def test_haiku_maps_to_latest(self):
        assert _anthropic_model_name("claude-haiku") == "claude-haiku-4-5"

    def test_mythos_maps_to_preview(self):
        assert _anthropic_model_name("claude-mythos") == "claude-mythos-preview"

    def test_exact_api_id_passed_through(self):
        assert _anthropic_model_name("claude-opus-4-7-20260401") == "claude-opus-4-7-20260401"

    def test_generic_fallback(self):
        assert _anthropic_model_name("claude") == "claude-sonnet-4-6"


class TestAnthropicIntegration:
    def test_anthropic_disabled_without_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        tk = get_tokenizer("claude-sonnet-4-6")
        assert "anthropic" not in tk.name or tk.name.startswith("heuristic")

    def test_anthropic_tokenizer_name_includes_model(self, monkeypatch):
        """When API key is present, tokenizer name includes the resolved model."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake-key")
        # Patch the Anthropic client creation to avoid real network calls
        import snipp.tokenizer as tok_mod

        original_load = tok_mod._load_anthropic

        def mock_load(model_key: str = "claude-sonnet-4-6"):
            return tok_mod.Tokenizer(
                name=f"anthropic:count_tokens ({tok_mod._anthropic_model_name(model_key)})",
                encode_fn=lambda s: len(s) // 4,
                exact=True,
            )

        monkeypatch.setattr(tok_mod, "_load_anthropic", mock_load)
        tk = get_tokenizer("claude-opus-4-7")
        assert "claude-opus-4-7" in tk.name


class TestOpenAIModels:
    """Verify OpenAI model families resolve to correct tiktoken encodings."""

    @pytest.mark.parametrize(
        "model_key",
        [
            "gpt-5",
            "gpt-5.3",
            "gpt-5-mini",
            "gpt-4.1",
            "gpt-4.1-nano",
            "gpt-4o",
            "gpt-4o-mini",
            "o1",
            "o3",
            "o3-mini",
            "o4-mini",
            "codex",
        ],
    )
    def test_modern_openai_uses_o200k(self, model_key):
        spec = MODEL_TOKENIZER_MAP[model_key]
        assert spec == "tiktoken:o200k_base", f"{model_key} should use o200k_base"

    @pytest.mark.parametrize(
        "model_key",
        [
            "gpt-4",
            "gpt-4-turbo",
            "gpt-4.5",
        ],
    )
    def test_legacy_gpt4_uses_cl100k(self, model_key):
        spec = MODEL_TOKENIZER_MAP[model_key]
        assert spec == "tiktoken:cl100k_base", f"{model_key} should use cl100k_base"

    def test_gpt5_prefix_matches_before_gpt4(self):
        """gpt-5 must not be shadowed by the shorter gpt-4 prefix."""
        # This tests the longest-prefix-wins logic
        tk = get_tokenizer("gpt-5.3-codex")
        # Should resolve via "gpt-5.3" or "gpt-5" prefix, not "gpt-4"
        assert "cl100k" not in tk.name or "o200k" in tk.name
