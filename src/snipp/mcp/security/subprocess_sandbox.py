"""Subprocess sandboxing: ulimits, process groups, safe execution.

All subprocess-spawning code in the MCP server should route through
`sandboxed_popen()` to get consistent resource limits and cleanup behavior.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Defaults per roadmap §6.1
DEFAULT_RLIMIT_AS = 2 * 1024 * 1024 * 1024  # 2GB address space
DEFAULT_RLIMIT_FSIZE = 100 * 1024 * 1024    # 100MB per file
# RLIMIT_NPROC is per-USER (not per-process subtree) on POSIX. Setting a low
# value like 64 here would make fork() fail in any dev environment that
# already has more processes — the kernel applies it user-wide. We default
# to 0 = "do not change". Use cgroups or a container if you need a real cap.
DEFAULT_RLIMIT_NPROC = 0
DEFAULT_RLIMIT_CPU = 600                    # 600s CPU time


def _set_limits(
    rlimit_as: int = DEFAULT_RLIMIT_AS,
    rlimit_fsize: int = DEFAULT_RLIMIT_FSIZE,
    rlimit_nproc: int = DEFAULT_RLIMIT_NPROC,
    rlimit_cpu: int = DEFAULT_RLIMIT_CPU,
) -> None:
    """Set resource limits in the child process. Called via preexec_fn."""
    try:
        import resource
    except ImportError:
        return  # Windows

    # Address space (virtual memory)
    try:
        resource.setrlimit(resource.RLIMIT_AS, (rlimit_as, rlimit_as))
    except (ValueError, OSError) as e:
        logger.debug("RLIMIT_AS not supported: %s", e)

    # File size
    try:
        resource.setrlimit(resource.RLIMIT_FSIZE, (rlimit_fsize, rlimit_fsize))
    except (ValueError, OSError) as e:
        logger.debug("RLIMIT_FSIZE not supported: %s", e)

    # Number of processes — only set if explicitly requested (>0). Setting
    # RLIMIT_NPROC to a small value here breaks fork() in busy environments
    # because it's per-user, not per-process.
    if rlimit_nproc and rlimit_nproc > 0:
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (rlimit_nproc, rlimit_nproc))
        except (ValueError, OSError) as e:
            logger.debug("RLIMIT_NPROC not supported: %s", e)

    # CPU time
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (rlimit_cpu, rlimit_cpu))
    except (ValueError, OSError) as e:
        logger.debug("RLIMIT_CPU not supported: %s", e)

    # Start a new process group so we can kill the entire tree later
    try:
        os.setpgrp()
    except (AttributeError, OSError) as e:
        logger.debug("setpgrp not supported: %s", e)


def sandboxed_popen(
    argv: List[str],
    *,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    stdin_data: Optional[str] = None,
    timeout_seconds: int = 300,
    rlimit_as: int = DEFAULT_RLIMIT_AS,
    rlimit_fsize: int = DEFAULT_RLIMIT_FSIZE,
    rlimit_nproc: int = DEFAULT_RLIMIT_NPROC,
    rlimit_cpu: int = DEFAULT_RLIMIT_CPU,
) -> subprocess.Popen:
    """Launch a subprocess with ulimits and process-group isolation.

    Returns the Popen object. The caller is responsible for:
      - calling safe_wait() or safe_kill() for cleanup
      - handling stdout/stderr to avoid pipe-buffer deadlocks
    """
    kwargs: Dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
        # preexec_fn runs in the child after fork but before exec
        "preexec_fn": lambda: _set_limits(rlimit_as, rlimit_fsize, rlimit_nproc, rlimit_cpu),
    }

    if cwd:
        kwargs["cwd"] = cwd
    if env:
        kwargs["env"] = env
    if stdin_data is not None:
        kwargs["stdin"] = subprocess.PIPE

    return subprocess.Popen(argv, **kwargs)


def safe_kill(proc: subprocess.Popen) -> None:
    """Kill a process and its entire process group."""
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGKILL)
        logger.info("Killed process group %s (pid %s)", pgid, proc.pid)
    except (ProcessLookupError, OSError) as e:
        logger.debug("Process group kill failed: %s", e)
        # Fallback: kill just the leader
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def safe_wait(
    proc: subprocess.Popen,
    timeout: float,
    stdin_data: Optional[bytes] = None,
) -> tuple[bytes, bytes, bool]:
    """Wait for process with timeout, return (stdout, stderr, timed_out).

    Uses communicate() for simple cases; falls back to manual read if needed.
    """
    try:
        stdout, stderr = proc.communicate(input=stdin_data, timeout=timeout)
        return stdout, stderr, False
    except subprocess.TimeoutExpired:
        safe_kill(proc)
        # Drain any remaining output after kill
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = b"", b""
        return stdout, stderr, True
