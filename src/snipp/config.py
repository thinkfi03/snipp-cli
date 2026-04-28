"""Configuration for snipp."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class CompressorConfig:
    enabled: bool = True
    max_output_tokens: int = 2000
    extras: Dict[str, object] = field(default_factory=dict)


def _cfg(max_tokens: int, **extras) -> CompressorConfig:
    return CompressorConfig(max_output_tokens=max_tokens, extras=extras)


@dataclass
class Config:
    default_max_tokens: int = 4000
    verbose: bool = False
    model: Optional[str] = None
    stage: Optional[str] = None
    compressors: Dict[str, CompressorConfig] = field(default_factory=dict)

    @classmethod
    def default(cls) -> "Config":
        return cls(
            compressors={
                "grep": _cfg(2000, context_lines=2, max_files=50, max_matches_per_file=20),
                "git_log": _cfg(1500, max_commits=20, show_diffs=False),
                "git_diff": _cfg(1500, max_files=20),
                "git_status": _cfg(800),
                "cat": _cfg(3000, show_signatures=True, keep_signatures_only=False),
                "head": _cfg(3000),
                "tail": _cfg(3000),
                "ls": _cfg(1000, max_entries=80),
                "find": _cfg(1500, max_dirs=30),
                "pytest": _cfg(2000, show_passed=False, max_failure_trace=80),
                "python_test": _cfg(2000),
                "docker": _cfg(2000, max_containers=30),
                "npm": _cfg(2000),
                "npm_test": _cfg(2000, show_passed_suites=False),
                "eslint": _cfg(2000),
                "generic": _cfg(4000),
            }
        )
