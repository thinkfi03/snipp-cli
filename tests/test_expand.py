"""Tests for the expand round-trip sidecar."""

import os
import tempfile
from pathlib import Path

from snipp.expand import ExpansionStore, find_handles, HANDLE_RE


def test_round_trip(tmp_path):
    store = ExpansionStore(session="t1", root=tmp_path)
    h = store.store("alpha\nbeta\ngamma\n", source="test")
    assert h.startswith("[<<elide:") and h.endswith(">>]")
    out = store.expand(h)
    assert out == "alpha\nbeta\ngamma\n"


def test_idempotent_storage(tmp_path):
    store = ExpansionStore(session="t2", root=tmp_path)
    h1 = store.store("x" * 100)
    h2 = store.store("x" * 100)
    assert h1 == h2


def test_find_handles_in_text():
    text = "before [<<elide:abcd1234:5L>>] middle [<<elide:beef0001:10L>>] end"
    handles = find_handles(text)
    assert handles == {"abcd1234": 5, "beef0001": 10}


def test_handle_regex_rejects_garbage():
    assert HANDLE_RE.search("[<<elide:NOTHEX:5L>>]") is None
    assert HANDLE_RE.search("[<<elide:abcd1234:5L>>]") is not None


def test_unknown_handle_returns_none(tmp_path):
    store = ExpansionStore(session="t3", root=tmp_path)
    assert store.expand("ffffffff") is None


def test_empty_content_returns_empty_handle(tmp_path):
    store = ExpansionStore(session="t4", root=tmp_path)
    assert store.store("") == ""
