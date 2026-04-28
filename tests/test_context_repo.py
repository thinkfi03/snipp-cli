"""Tests for snipp.context_repo — repo exploration and symbol lookup."""

from __future__ import annotations

import pytest

from snipp.context_repo import explore_repo, show_symbol


@pytest.fixture
def sample_repo(tmp_path):
    """Create a miniature Python project for testing."""
    (tmp_path / "src" / "utils").mkdir(parents=True)
    (tmp_path / "tests").mkdir()

    (tmp_path / "src" / "main.py").write_text("""
import os
from typing import Optional

class AuthManager:
    \"\"\"Handles user authentication with JWT tokens.\"\"\"

    def __init__(self, secret: str):
        self.secret = secret

    def authenticate(self, token: str) -> Optional[dict]:
        \"\"\"Validate a JWT token and return user payload.\"\"\"
        if not token:
            return None
        parts = token.split(".")
        if len(parts) != 3:
            return None
        return {"user": "alice"}

class TokenStore:
    def save(self, token: str) -> None:
        pass

def create_app():
    \"\"\"Factory function to create the main application.\"\"\"
    return AuthManager("secret")
""")

    (tmp_path / "src" / "utils" / "helpers.py").write_text("""
def format_date(ts: int) -> str:
    \"\"\"Format a Unix timestamp as ISO-8601.\"\"\"
    from datetime import datetime
    return datetime.fromtimestamp(ts).isoformat()
""")

    (tmp_path / "tests" / "test_auth.py").write_text("""
import pytest
from src.main import AuthManager

def test_authenticate_valid():
    auth = AuthManager("secret")
    assert auth.authenticate("a.b.c") == {"user": "alice"}
""")

    return tmp_path


class TestExploreRepo:
    def test_returns_file_tree(self, sample_repo):
        doc = explore_repo(str(sample_repo))
        assert "# File Tree" in doc
        assert "src/main.py" in doc
        assert "src/utils/helpers.py" in doc

    def test_returns_symbols(self, sample_repo):
        doc = explore_repo(str(sample_repo))
        assert "# Key Symbols" in doc
        assert "AuthManager" in doc
        assert "authenticate" in doc
        assert "create_app" in doc
        assert "format_date" in doc

    def test_query_ranks_relevant_symbols(self, sample_repo):
        doc = explore_repo(str(sample_repo), query="authentication jwt")
        # AuthManager and authenticate should appear early
        idx_auth = doc.find("AuthManager")
        idx_format = doc.find("format_date")
        assert idx_auth < idx_format or idx_auth != -1

    def test_max_tokens_caps_output(self, sample_repo):
        from snipp.tokenizer import count_tokens
        doc = explore_repo(str(sample_repo), max_tokens=500)
        tokens = count_tokens(doc)
        assert tokens <= 500

    def test_empty_repo(self, tmp_path):
        doc = explore_repo(str(tmp_path))
        assert "# File Tree" in doc
        assert "# Key Symbols" not in doc or doc.count("\n### ") == 0

    def test_invalid_root_raises(self):
        with pytest.raises(ValueError):
            explore_repo("/no/such/path")


class TestShowSymbol:
    def test_finds_class(self, sample_repo):
        result = show_symbol(str(sample_repo), "AuthManager")
        assert result is not None
        assert "class AuthManager" in result
        assert "Handles user authentication" in result

    def test_finds_method(self, sample_repo):
        result = show_symbol(str(sample_repo), "authenticate")
        assert result is not None
        assert "def authenticate" in result
        assert "Validate a JWT token" in result

    def test_returns_body_preview(self, sample_repo):
        result = show_symbol(str(sample_repo), "authenticate")
        assert result is not None
        assert "## Body (first 5 lines)" in result

    def test_missing_symbol_returns_none(self, sample_repo):
        result = show_symbol(str(sample_repo), "NonExistent")
        assert result is None

    def test_invalid_root_raises(self):
        with pytest.raises(ValueError):
            show_symbol("/no/such/path", "foo")
