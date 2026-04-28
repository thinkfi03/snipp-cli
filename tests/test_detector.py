"""Tests for tool detector with confidence + word-boundary discipline."""

from snipp.detector import detect_tool, detect_tool_with_confidence, ToolType


class TestDetector:
    def test_grep_variants(self):
        assert detect_tool("grep -r TODO .", "") == ToolType.GREP
        assert detect_tool("rg TODO", "") == ToolType.GREP
        assert detect_tool("ag --hidden TODO", "") == ToolType.GREP

    def test_git(self):
        assert detect_tool("git log --oneline", "") == ToolType.GIT_LOG
        assert detect_tool("git diff HEAD~1", "") == ToolType.GIT_DIFF
        assert detect_tool("git status", "") == ToolType.GIT_STATUS

    def test_cat_head_tail(self):
        assert detect_tool("cat file.py", "") == ToolType.CAT
        assert detect_tool("bat file.py", "") == ToolType.CAT
        assert detect_tool("head -50 file.py", "") == ToolType.HEAD
        assert detect_tool("tail -f log", "") == ToolType.TAIL

    def test_ls_word_boundary(self):
        """Regression: lsof / lsblk must not match ls."""
        assert detect_tool("ls -la", "") == ToolType.LS
        assert detect_tool("lsof -i :8080", "") != ToolType.LS
        assert detect_tool("lsblk", "") != ToolType.LS

    def test_pytest(self):
        assert detect_tool("pytest -v", "") == ToolType.PYTEST
        assert detect_tool("python -m pytest tests/", "") == ToolType.PYTEST
        assert detect_tool("py.test", "") == ToolType.PYTEST

    def test_docker(self):
        assert detect_tool("docker ps", "") == ToolType.DOCKER
        assert detect_tool("docker-compose up", "") == ToolType.DOCKER

    def test_output_heuristic_with_high_confidence(self):
        out = "file.py:10:def x():\nfile.py:11:    pass\nfile.py:20:def y():\n"
        det = detect_tool_with_confidence(None, out)
        assert det.tool == ToolType.GREP
        assert det.confidence > 0.7

    def test_generic_fallback(self):
        assert detect_tool("some_random_command", "output here") == ToolType.GENERIC

    def test_env_prefix_stripped(self):
        assert detect_tool("PATH=/usr/bin grep -r TODO", "") == ToolType.GREP
