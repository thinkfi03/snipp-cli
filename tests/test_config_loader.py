"""Tests for config file loader."""

import pytest
from pathlib import Path

from snipp.config_loader import (
    find_config_file,
    load_config,
    write_default_config,
)
from snipp.config import Config


class TestFindConfigFile:
    def test_explicit_path(self, tmp_path):
        p = tmp_path / "my-config.yaml"
        p.write_text("default_max_tokens: 1000")
        assert find_config_file(str(p)) == p.resolve()

    def test_explicit_missing_returns_none(self, tmp_path):
        assert find_config_file(str(tmp_path / "no-such.yaml")) is None

    def test_no_file_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        import snipp.config_loader as cl
        monkeypatch.setattr(cl, "_DEFAULT_SEARCH_PATHS", [
            tmp_path / ".snipp.yaml",
            tmp_path / ".snipp.yml",
            tmp_path / ".config" / "snipp" / "config.yaml",
        ])
        assert find_config_file() is None

    def test_finds_local_dot_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".snipp.yaml").write_text("")
        assert find_config_file() == (tmp_path / ".snipp.yaml").resolve()

    def test_finds_local_yml_variant(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".snipp.yml").write_text("")
        assert find_config_file() == (tmp_path / ".snipp.yml").resolve()


class TestLoadConfig:
    def test_empty_file_uses_defaults(self, tmp_path):
        p = tmp_path / "empty.yaml"
        p.write_text("")
        cfg = load_config(str(p))
        assert isinstance(cfg, Config)
        assert cfg.default_max_tokens == 4000

    def test_loads_scalar_fields(self, tmp_path):
        p = tmp_path / "cfg.yaml"
        p.write_text("""
default_max_tokens: 8000
verbose: true
model: gpt-4o
stage: debug
""")
        cfg = load_config(str(p))
        assert cfg.default_max_tokens == 8000
        assert cfg.verbose is True
        assert cfg.model == "gpt-4o"
        assert cfg.stage == "debug"

    def test_loads_compressor_block(self, tmp_path):
        p = tmp_path / "cfg.yaml"
        p.write_text("""
compressors:
  grep:
    max_output_tokens: 500
    context_lines: 0
    max_files: 10
  pytest:
    enabled: false
    show_passed: true
""")
        cfg = load_config(str(p))
        grep = cfg.compressors["grep"]
        assert grep.max_output_tokens == 500
        assert grep.extras["context_lines"] == 0
        assert grep.extras["max_files"] == 10

        pytest_cfg = cfg.compressors["pytest"]
        assert pytest_cfg.enabled is False
        assert pytest_cfg.extras["show_passed"] is True

    def test_preserves_unmentioned_defaults(self, tmp_path):
        p = tmp_path / "cfg.yaml"
        p.write_text("compressors:\n  cat:\n    max_output_tokens: 999\n")
        cfg = load_config(str(p))
        assert cfg.compressors["grep"].max_output_tokens == 2000  # default
        assert cfg.compressors["cat"].max_output_tokens == 999

    def test_no_file_returns_defaults(self, tmp_path):
        assert load_config(str(tmp_path / "missing.yaml")).default_max_tokens == 4000


class TestWriteDefaultConfig:
    def test_writes_to_given_path(self, tmp_path):
        target = tmp_path / "out.yaml"
        path = write_default_config(str(target))
        assert path == target.resolve()
        text = path.read_text()
        assert "default_max_tokens: 4000" in text
        assert "compressors:" in text
        assert "grep:" in text

    def test_creates_parent_dirs(self, tmp_path):
        target = tmp_path / "a" / "b" / "config.yaml"
        path = write_default_config(str(target))
        assert path.exists()

    def test_default_path_uses_xdg(self, tmp_path, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        path = write_default_config()
        assert path == tmp_path / ".config" / "snipp" / "config.yaml"
        assert path.exists()
