"""Subprocess argv validation and sandboxing."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import List, Optional, Set

logger = logging.getLogger(__name__)

# Default conservative allowlist
DEFAULT_ARGV_ALLOWLIST: Set[str] = {
    # grep / search
    "grep", "egrep", "fgrep", "rg", "ripgrep", "ag", "ack",
    # git
    "git",
    # ls / find
    "ls", "exa", "eza", "tree", "find", "fd", "fdfind",
    # cat / head / tail
    "cat", "bat", "head", "tail",
    # python testing
    "pytest", "py.test", "python", "python3",
    # node / js
    "npm", "yarn", "pnpm", "bun", "npx",
    "jest", "vitest", "mocha", "ava", "tap", "node",
    # lint / typecheck
    "eslint", "tsc", "prettier", "stylelint",
    # build
    "make", "cmake", "ninja",
    # docker
    "docker", "docker-compose", "podman",
    # rust / go / java / k8s / terraform
    "cargo", "go", "mvn", "gradle", "kubectl", "terraform", "tf",
    # misc
    "jq", "sed", "awk", "wc", "sort", "uniq", "xargs",
}

# Environment variable allowlist
DEFAULT_ENV_ALLOWLIST: Set[str] = {
    "PATH", "HOME", "USER", "SHELL", "TERM", "TERM_PROGRAM",
    "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "LC_NUMERIC",
    "PWD", "OLDPWD", "TMPDIR", "XDG_*",
    "CC_COMPRESS_*", "SNIP_*",
    # CI env
    "CI", "GITHUB_*", "GITLAB_*", "CIRCLE_*", "TRAVIS", "BUILDKITE",
    # Python
    "PYTHON*", "VIRTUAL_ENV", "PYENV_*",
    # Node
    "NODE_*", "NPM_*", "PNPM_*", "YARN_*",
    # OpenTelemetry
    "OTEL_*",
}


class ArgvValidator:
    """Validates subprocess argv against an allowlist and path constraints."""

    def __init__(
        self,
        allowlist: Optional[Set[str]] = None,
        cwd_allowlist: Optional[List[Path]] = None,
        env_allowlist: Optional[Set[str]] = None,
    ):
        self.allowlist = allowlist or set(DEFAULT_ARGV_ALLOWLIST)
        self.cwd_allowlist = cwd_allowlist or []
        self.env_allowlist = env_allowlist or set(DEFAULT_ENV_ALLOWLIST)

    def validate(self, argv: List[str], cwd: Optional[str] = None) -> None:
        """Validate argv and cwd. Raises ValueError on violation."""
        if not argv:
            raise ValueError("argv must not be empty")

        argv0 = argv[0]
        resolved = self._resolve_argv0(argv0)

        if resolved is None:
            raise ValueError(
                f"Command {argv0!r} not in allowlist. "
                f"Allowed: {sorted(self.allowlist)[:20]}..."
            )

        # Replace argv[0] with resolved path if it was a bare name
        argv[0] = resolved

        if cwd is not None:
            self._validate_cwd(cwd)

        # Check for shell metacharacters in any arg
        for arg in argv[1:]:
            if self._has_shell_metachar(arg):
                raise ValueError(
                    f"Argument contains shell metacharacters: {arg!r}. "
                    "Shell injection is not allowed."
                )

    def filter_env(self, env: Optional[dict]) -> dict:
        """Return env dict filtered to allowed keys."""
        if env is None:
            return {}
        filtered = {}
        for key, value in env.items():
            if self._env_allowed(key):
                filtered[key] = value
            else:
                logger.debug("Dropping env var: %s", key)
        return filtered

    def _resolve_argv0(self, argv0: str) -> Optional[str]:
        """Resolve argv0 to an executable path. Returns path if allowed, None if not."""
        # If absolute or relative path, check it resolves and the basename is allowed
        if os.path.isabs(argv0) or "/" in argv0 or "\\" in argv0:
            p = Path(argv0).expanduser().resolve()
            if not p.exists():
                # Try which
                found = shutil.which(argv0)
                if found:
                    p = Path(found).resolve()
                else:
                    return None
            basename = p.name
            if basename in self.allowlist:
                return str(p)
            return None

        # Bare name — look it up and check allowlist
        if argv0 in self.allowlist:
            found = shutil.which(argv0)
            if found:
                return found
        return None

    def _validate_cwd(self, cwd: str) -> None:
        p = Path(cwd).expanduser().resolve()
        if not self.cwd_allowlist:
            # Default: must be under current working directory or HOME
            base = Path.cwd().resolve()
            home = Path.home().resolve()
            if not (str(p).startswith(str(base)) or str(p).startswith(str(home))):
                raise ValueError(f"cwd {cwd!r} is outside allowed directories")
            return
        for allowed in self.cwd_allowlist:
            if str(p).startswith(str(allowed.resolve())):
                return
        raise ValueError(f"cwd {cwd!r} is not in cwd_allowlist")

    @staticmethod
    def _has_shell_metachar(arg: str) -> bool:
        """Detect characters that would have special meaning in a shell."""
        dangerous = {";", "|", "&", "$", "`", "(", ")", "{", "}", "<", ">", "\n", "\r"}
        return any(c in dangerous for c in arg)

    def _env_allowed(self, key: str) -> bool:
        for pattern in self.env_allowlist:
            if pattern.endswith("*"):
                if key.startswith(pattern[:-1]):
                    return True
            elif key == pattern:
                return True
        return False


def validate_argv(
    argv: List[str],
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
) -> tuple[List[str], dict]:
    """Convenience: validate argv and filter env with defaults."""
    validator = ArgvValidator()
    validator.validate(argv, cwd=cwd)
    filtered_env = validator.filter_env(env)
    return argv, filtered_env
