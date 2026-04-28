"""Tool detection from command + output with confidence scores."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class ToolType(Enum):
    GREP = "grep"
    GIT_LOG = "git_log"
    GIT_DIFF = "git_diff"
    GIT_STATUS = "git_status"
    CAT = "cat"
    HEAD = "head"
    TAIL = "tail"
    LS = "ls"
    FIND = "find"
    PYTEST = "pytest"
    PYTHON_TEST = "python_test"
    MAKE = "make"
    NPM = "npm"
    NPM_TEST = "npm_test"
    ESLINT = "eslint"
    DOCKER = "docker"
    GENERIC = "generic"


@dataclass(frozen=True)
class Detection:
    tool: ToolType
    confidence: float
    source: str


_GREP_TOOLS = {"grep", "egrep", "fgrep", "rg", "ripgrep", "ag", "ack"}
_LS_TOOLS = {"ls", "exa", "eza", "tree"}
_FIND_TOOLS = {"find", "fd", "fdfind"}
_CAT_TOOLS = {"cat", "bat"}


def _argv0(command: str) -> Optional[str]:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    for p in parts:
        if "=" in p and p.split("=", 1)[0].isupper():
            continue
        return p.split("/")[-1]
    return None


def detect_tool(command: Optional[str], output: str) -> ToolType:
    return detect_tool_with_confidence(command, output).tool


def detect_tool_with_confidence(command: Optional[str], output: str) -> Detection:
    if command:
        cmd = command.strip()
        argv0 = _argv0(cmd) or ""
        argv0_lower = argv0.lower()
        rest = cmd[len(argv0):].lower() if argv0 else cmd.lower()

        if argv0_lower in _GREP_TOOLS:
            return Detection(ToolType.GREP, 0.99, f"argv0={argv0_lower}")
        if argv0_lower == "git":
            if re.search(r"\blog\b", rest):
                return Detection(ToolType.GIT_LOG, 0.99, "git log")
            if re.search(r"\bdiff\b", rest):
                return Detection(ToolType.GIT_DIFF, 0.99, "git diff")
            if re.search(r"\bstatus\b", rest):
                return Detection(ToolType.GIT_STATUS, 0.99, "git status")
            if re.search(r"\bshow\b", rest):
                return Detection(ToolType.GIT_DIFF, 0.9, "git show")
            if re.search(r"\bblame\b", rest):
                return Detection(ToolType.GIT_LOG, 0.8, "git blame")
            return Detection(ToolType.GIT_LOG, 0.6, "git fallback")
        if argv0_lower in _CAT_TOOLS:
            return Detection(ToolType.CAT, 0.99, f"argv0={argv0_lower}")
        if argv0_lower == "head":
            return Detection(ToolType.HEAD, 0.99, "argv0=head")
        if argv0_lower == "tail":
            return Detection(ToolType.TAIL, 0.99, "argv0=tail")
        if argv0_lower in _LS_TOOLS:
            return Detection(ToolType.LS, 0.99, f"argv0={argv0_lower}")
        if argv0_lower in _FIND_TOOLS:
            return Detection(ToolType.FIND, 0.99, f"argv0={argv0_lower}")
        if argv0_lower in {"pytest", "py.test"}:
            return Detection(ToolType.PYTEST, 0.99, "argv0=pytest")
        if argv0_lower.startswith("python"):
            if re.search(r"-m\s+pytest\b", rest):
                return Detection(ToolType.PYTEST, 0.99, "python -m pytest")
            if re.search(r"-m\s+unittest\b", rest):
                return Detection(ToolType.PYTHON_TEST, 0.95, "python -m unittest")
        if argv0_lower in {"make", "cmake", "ninja"}:
            return Detection(ToolType.MAKE, 0.95, f"argv0={argv0_lower}")
        if argv0_lower in {"npm", "yarn", "pnpm", "bun"}:
            if re.search(r"\b(test|jest|vitest|mocha|tap)\b", rest):
                return Detection(ToolType.NPM_TEST, 0.95, f"argv0={argv0_lower} test")
            if re.search(r"\b(lint|eslint|tsc|prettier|stylelint)\b", rest):
                return Detection(ToolType.ESLINT, 0.95, f"argv0={argv0_lower} lint")
            return Detection(ToolType.NPM, 0.95, f"argv0={argv0_lower}")
        if argv0_lower in {"jest", "vitest", "mocha", "ava", "tap"}:
            return Detection(ToolType.NPM_TEST, 0.95, f"argv0={argv0_lower}")
        if argv0_lower == "node":
            # kimik2.6 review bug #7: only treat node as a test runner when it
            # invokes the built-in --test flag or runs a known test runner module.
            if re.search(r"(?:^|\s)--test\b", rest):
                return Detection(ToolType.NPM_TEST, 0.95, "node --test")
            if re.search(
                r"(?:^|\s|/)(jest|vitest|mocha|ava|tap)(?:\.js|\.mjs|\.cjs|/bin/\S+)?\b",
                rest,
            ):
                return Detection(ToolType.NPM_TEST, 0.9, "node + test runner module")
            # Plain `node script.js` or `node ./scripts/test-fixtures.js` — generic.
            pass
        if argv0_lower in {"eslint", "tsc", "prettier", "stylelint"}:
            return Detection(ToolType.ESLINT, 0.95, f"argv0={argv0_lower}")
        if argv0_lower == "npx":
            if re.search(r"\b(jest|vitest|mocha|ava|tap)\b", rest):
                return Detection(ToolType.NPM_TEST, 0.95, "npx test runner")
            if re.search(r"\b(eslint|tsc|prettier|stylelint)\b", rest):
                return Detection(ToolType.ESLINT, 0.95, "npx lint")
            return Detection(ToolType.NPM, 0.8, "npx generic")
        if argv0_lower in {"docker", "docker-compose", "podman"}:
            return Detection(ToolType.DOCKER, 0.95, f"argv0={argv0_lower}")

    return _detect_from_output(output)


_GREP_LINE_RE = re.compile(r"^[^:\n]+:\d+:.+$")
_COMMIT_RE = re.compile(r"^commit [a-f0-9]{7,40}\b")
_PYTEST_HEADER_RE = re.compile(r"=+\s*test session starts\s*=+", re.IGNORECASE)
_LS_LONG_RE = re.compile(r"^[dl\-][rwxstST\-]{9}[\s\+]")
_DOCKER_PS_RE = re.compile(r"^CONTAINER\s+ID\s+IMAGE")
_TSC_ERROR = re.compile(r"\.tsx?\(\d+,\d+\):\s+(error|warning)")
_ESLINT_ERROR = re.compile(r"^\s*\d+:\d+\s+(error|warning)\s+")
_TAP_PLAN = re.compile(r"^1\.\.\d+")
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _detect_from_output(output: str) -> Detection:
    if not output:
        return Detection(ToolType.GENERIC, 0.3, "empty")

    lines = output.split("\n")[:50]

    grep_hits = sum(1 for ln in lines if _GREP_LINE_RE.match(ln))
    if grep_hits >= 3:
        return Detection(ToolType.GREP, 0.85, f"file:line: x{grep_hits}")

    if _PYTEST_HEADER_RE.search("\n".join(lines[:5])):
        return Detection(ToolType.PYTEST, 0.95, "test session starts")
    if any("PASSED" in ln or "FAILED" in ln for ln in lines[:20]) and any(
        "::" in ln for ln in lines[:20]
    ):
        return Detection(ToolType.PYTEST, 0.85, "PASSED/FAILED + ::")

    # JS test runner heuristics
    if any(k in output for k in ("PASS ", "FAIL ", "✓ ", "✕ ", "✔ ", "✗ ")) and any(
        ln.strip().endswith((".test.", ".spec.")) or ".test." in ln or ".spec." in ln
        for ln in lines[:30]
    ):
        return Detection(ToolType.NPM_TEST, 0.85, "PASS/FAIL + .test/.spec files")
    if any(k in output for k in ("Test Suites:", "Tests:", "Snapshots:")) and any(
        k in output for k in ("passed", "failed", "skipped")
    ):
        return Detection(ToolType.NPM_TEST, 0.8, "Jest-style summary")
    if "vitest" in output.lower() or "vitest" in "".join(lines[:3]).lower():
        return Detection(ToolType.NPM_TEST, 0.9, "vitest header")
    if "mocha" in output.lower() or any("passing" in ln.lower() for ln in lines[:10]):
        if any("failing" in ln.lower() for ln in lines[:10]):
            return Detection(ToolType.NPM_TEST, 0.8, "mocha passing/failing")
    if output.startswith("TAP") or _TAP_PLAN.match(lines[0] if lines else ""):
        return Detection(ToolType.NPM_TEST, 0.9, "TAP output")

    # Lint heuristics
    if any(k in output for k in ("eslint", "ESLint", "prettier", "stylelint")):
        return Detection(ToolType.ESLINT, 0.85, "lint tool name in output")
    if any(_TSC_ERROR.match(ln) for ln in lines[:20]):
        return Detection(ToolType.ESLINT, 0.9, "tsc error format")
    if sum(1 for ln in lines[:30] if _ESLINT_ERROR.match(_ANSI_RE.sub("", ln))) >= 2:
        return Detection(ToolType.ESLINT, 0.85, "eslint error lines")
    if "error TS" in output and ".ts" in output:
        return Detection(ToolType.ESLINT, 0.8, "tsc TS errors")

    commit_hits = sum(1 for ln in lines if _COMMIT_RE.match(ln))
    if commit_hits >= 2:
        return Detection(ToolType.GIT_LOG, 0.9, f"commit lines x{commit_hits}")

    ls_hits = sum(1 for ln in lines if _LS_LONG_RE.match(ln))
    if ls_hits >= 3:
        return Detection(ToolType.LS, 0.85, f"perm lines x{ls_hits}")

    if any(_DOCKER_PS_RE.match(ln) for ln in lines[:5]):
        return Detection(ToolType.DOCKER, 0.95, "docker ps header")

    if "Step " in output and any("FROM " in ln for ln in lines[:30]):
        return Detection(ToolType.DOCKER, 0.8, "docker build")

    return Detection(ToolType.GENERIC, 0.4, "fallback")
