"""Tests for compressors with v0.2 contract (fidelity, handles, no-loss invariants)."""

from snipp.compressors.grep import GrepCompressor
from snipp.compressors.git import GitLogCompressor, GitDiffCompressor, GitStatusCompressor
from snipp.compressors.cat import CatCompressor
from snipp.compressors.ls import LsCompressor, FindCompressor
from snipp.compressors.pytest import PytestCompressor
from snipp.compressors.generic import GenericCompressor
from snipp.compressors.docker import DockerCompressor


class TestGrep:
    def test_basic(self):
        out = "file1.py:10:def hello():\nfile1.py:20:def world():\nBinary file matches\n" * 20
        r = GrepCompressor(max_tokens=1000).compress(out, query="find functions")
        assert r.compressed_tokens <= r.original_tokens
        assert "Binary file" not in r.compressed
        assert "file1.py" in r.compressed
        assert r.fidelity["matches_total"] == 40
        assert r.fidelity["binary_files_skipped"] == 20

    def test_empty(self):
        r = GrepCompressor().compress("")
        assert r.fidelity["matches_total"] == 0


class TestGitLog:
    def test_commit_compression(self):
        out = (
            "commit abc123456789\n"
            "Author: Alice <alice@example.com>\n"
            "Date:   Mon Jan 1 10:00:00 2024\n\n"
            "    Fix authentication bug\n"
            "    \n"
            "    This fixes a thing.\n\n"
        ) * 30
        r = GitLogCompressor(max_tokens=1000, max_commits=10).compress(out)
        assert r.fidelity["commits_total"] == 30
        assert r.fidelity["commits_preserved"] == 10
        assert "Total commits: 30" in r.compressed


class TestGitDiff:
    def test_diff(self):
        out = "diff --git a/file.py b/file.py\n@@ -1 +1 @@\n-old\n+new\n" * 10
        r = GitDiffCompressor(max_tokens=2000).compress(out)
        assert r.fidelity["files_total"] == 10
        assert "git diff summary" in r.compressed


class TestGitStatus:
    def test_status(self):
        out = """On branch main
Changes to be committed:
  (use "git restore --staged <file>...")
        modified:   a.py
        new file:   b.py

Changes not staged for commit:
        modified:   c.py

Untracked files:
        temp.txt
"""
        r = GitStatusCompressor().compress(out)
        assert r.fidelity == {"staged": 2, "unstaged": 1, "untracked": 1}


class TestCat:
    def test_python_uses_ast(self):
        out = '''import os
import sys

def hello():
    """One-line docstring with quotes."""
    x = 1
    return x

@staticmethod
def decorated():
    return 42

class C:
    def method(self):
        pass
'''
        r = CatCompressor(max_tokens=1000).compress(out, query="hello function")
        assert "def hello" in r.compressed
        assert "class C" in r.compressed or "class C:" in r.compressed
        assert "@staticmethod" in r.compressed
        assert r.fidelity["language"] == "python"
        assert r.fidelity["signatures_total"] >= 3

    def test_single_line_docstring_does_not_swallow_file(self):
        """Regression: prior toggle-based parser broke after single-line docstring."""
        out = '''def f():
    """one-liner"""
    return 1

def g():
    return 2
'''
        r = CatCompressor().compress(out)
        assert "def f" in r.compressed
        assert "def g" in r.compressed

    def test_decorator_attaches_to_correct_function(self):
        """Regression: decorator must apply to NEXT function, not previous."""
        out = '''def first():
    pass

@my_decorator
def second():
    pass
'''
        r = CatCompressor().compress(out)
        text = r.compressed
        first_idx = text.find("def first")
        dec_idx = text.find("@my_decorator")
        second_idx = text.find("def second")
        assert first_idx >= 0 and dec_idx >= 0 and second_idx >= 0
        assert first_idx < dec_idx < second_idx


class TestLs:
    def test_ls(self):
        out = (
            "drwxr-xr-x 5 user group 4096 Jan 1 10:00 dir\n"
            "-rw-r--r-- 1 user group  123 Jan 1 10:00 file\n"
        ) * 50
        r = LsCompressor().compress(out)
        assert r.fidelity["dirs_total"] == 50
        assert r.fidelity["files_total"] == 50


class TestFind:
    def test_find(self):
        out = "./src/file1.py\n./src/file2.py\n./tests/test1.py\n" * 30
        r = FindCompressor().compress(out)
        assert r.fidelity["entries_total"] == 90


class TestPytest:
    def test_anchored_parser(self):
        out = """============================= test session starts ==============================
tests/a.py::test_one PASSED
tests/a.py::test_two PASSED
tests/b.py::test_three FAILED
    def test_three():
>       assert False
E       AssertionError

========================= 2 passed, 1 failed in 1.0s ==========================
"""
        r = PytestCompressor().compress(out)
        assert r.fidelity["passed_count"] == 2
        assert r.fidelity["failed_count"] == 1
        assert "test_three" in r.compressed

    def test_does_not_match_substring_passed(self):
        """Regression: a string literal containing 'PASSED' must not count as a test result."""
        out = '''tests/a.py::real_test PASSED
tests/b.py::test_x FAILED
    def test_x():
>       assert "All tests PASSED" in actual
E       AssertionError

==== 1 passed, 1 failed in 0.1s ====
'''
        r = PytestCompressor().compress(out)
        assert r.fidelity["passed_count"] == 1
        assert r.fidelity["failed_count"] == 1


class TestDocker:
    def test_docker_ps_truncates(self):
        rows = "container_id image command status ports name\n" * 100
        out = "CONTAINER ID  IMAGE  COMMAND  STATUS  PORTS  NAMES\n" + rows
        r = DockerCompressor(max_containers=10).compress(out)
        assert r.fidelity["containers_total"] == 100
        assert r.fidelity["containers_preserved"] == 10

    def test_docker_build_steps(self):
        out = (
            "Step 1/5 : FROM python:3.11\n"
            "Step 2/5 : COPY . /app\n"
            "Step 3/5 : RUN pip install\n"
            "ERROR: build failed\n"
        )
        r = DockerCompressor().compress(out)
        assert r.fidelity["steps"] == 3
        assert r.fidelity["errors"] >= 1


class TestGeneric:
    def test_large(self):
        r = GenericCompressor(max_tokens=200).compress("line\n" * 1000)
        assert r.compressed_tokens < r.original_tokens

    def test_small(self):
        out = "small output\n"
        r = GenericCompressor().compress(out)
        assert r.compressed.strip() == "small output"
