"""Task-stage policy for compression aggressiveness.

Stages map to a multiplier applied to per-tool max_tokens budgets and a set
of feature flags (e.g., keep full traces in debug stage). The policy is
explicit so agents can reason about it and override per call.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Stage(str, Enum):
    EXPLORE = "explore"
    EDIT = "edit"
    DEBUG = "debug"
    VERIFY = "verify"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class StagePolicy:
    budget_multiplier: float
    keep_full_traces: bool
    keep_signatures_only: bool
    show_passed_tests: bool
    grep_context_lines: int
    git_show_diffs: bool

    @classmethod
    def for_stage(cls, stage: Stage) -> "StagePolicy":
        return _POLICIES[stage]


_POLICIES = {
    Stage.EXPLORE: StagePolicy(
        budget_multiplier=0.6,
        keep_full_traces=False,
        keep_signatures_only=True,
        show_passed_tests=False,
        grep_context_lines=0,
        git_show_diffs=False,
    ),
    Stage.EDIT: StagePolicy(
        budget_multiplier=1.0,
        keep_full_traces=False,
        keep_signatures_only=False,
        show_passed_tests=False,
        grep_context_lines=2,
        git_show_diffs=False,
    ),
    Stage.DEBUG: StagePolicy(
        budget_multiplier=1.5,
        keep_full_traces=True,
        keep_signatures_only=False,
        show_passed_tests=False,
        grep_context_lines=4,
        git_show_diffs=True,
    ),
    Stage.VERIFY: StagePolicy(
        budget_multiplier=1.2,
        keep_full_traces=True,
        keep_signatures_only=False,
        show_passed_tests=True,
        grep_context_lines=2,
        git_show_diffs=False,
    ),
    Stage.UNKNOWN: StagePolicy(
        budget_multiplier=1.0,
        keep_full_traces=False,
        keep_signatures_only=False,
        show_passed_tests=False,
        grep_context_lines=2,
        git_show_diffs=False,
    ),
}


def parse_stage(value: Optional[str]) -> Stage:
    if not value:
        return Stage.UNKNOWN
    try:
        return Stage(value.lower())
    except ValueError:
        return Stage.UNKNOWN
