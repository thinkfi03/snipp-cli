"""Tests for stage policy and streaming compressor."""

from snipp.stage import Stage, StagePolicy, parse_stage
from snipp.streaming import stream_lines


def test_parse_stage():
    assert parse_stage("debug") == Stage.DEBUG
    assert parse_stage("nonsense") == Stage.UNKNOWN
    assert parse_stage(None) == Stage.UNKNOWN


def test_debug_keeps_full_traces():
    p = StagePolicy.for_stage(Stage.DEBUG)
    assert p.keep_full_traces is True
    assert p.budget_multiplier > 1.0


def test_explore_is_terse():
    p = StagePolicy.for_stage(Stage.EXPLORE)
    assert p.keep_signatures_only is True
    assert p.budget_multiplier < 1.0


def test_streaming_yields_chunks():
    lines = ["file.py:1:hit\n"] * 10000
    chunks = list(stream_lines(iter(lines), command="grep -r hit .", flush_lines=2000))
    assert len(chunks) >= 5
    for c in chunks:
        assert "grep results" in c or "matches" in c


def test_streaming_small_input_yields_one_chunk():
    chunks = list(stream_lines(iter(["a\n", "b\n"]), command=""))
    assert len(chunks) == 1
