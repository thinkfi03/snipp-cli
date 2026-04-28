"""YAML config file loader for snipp.

Searches (in order):
  1. Path given via --config flag
  2. ./.snipp.yaml or ./.snipp.yml (project-local)
  3. $XDG_CONFIG_HOME/snipp/config.yaml
     (defaults to ~/.config/snipp/config.yaml)

All keys are optional; missing keys fall back to Config.default().

Bug-fix history (kimik2.6 review):
  - PyYAML is a hard dep — import errors are loud, not silent.
  - Unknown config keys produce a stderr warning with the closest known key.
  - The default-config text is generated from MODEL_TOKENIZER_MAP at write
    time so the model list never goes stale.
"""

from __future__ import annotations

import difflib
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Set

import yaml

from snipp.config import Config, CompressorConfig
from snipp.tokenizer import MODEL_TOKENIZER_MAP


# Per-tool whitelist of recognized extras keys.
# Anything outside the whitelist is preserved (so plugin compressors still
# work) but a warning is emitted.
_KNOWN_EXTRAS: Dict[str, Set[str]] = {
    "grep": {"context_lines", "max_files", "max_matches_per_file"},
    "git_log": {"max_commits", "show_diffs"},
    "git_diff": {"max_files"},
    "git_status": set(),
    "cat": {"show_signatures", "keep_signatures_only"},
    "head": set(),
    "tail": set(),
    "ls": {"max_entries"},
    "find": {"max_dirs"},
    "pytest": {"show_passed", "max_failure_trace", "keep_full_traces"},
    "python_test": {"show_passed", "max_failure_trace"},
    "docker": {"max_containers"},
    "npm": set(),
    "npm_test": {"show_passed_suites"},
    "eslint": set(),
    "generic": set(),
}

_TOP_LEVEL_KEYS = {"default_max_tokens", "verbose", "model", "stage", "compressors"}
_BLOCK_RESERVED = {"enabled", "max_output_tokens"}


def _xdg_config_home() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", "")
    return Path(base).expanduser() if base else Path.home() / ".config"


def _default_config_path() -> Path:
    return _xdg_config_home() / "snipp" / "config.yaml"


def _search_paths() -> list:
    return [
        Path(".snipp.yaml"),
        Path(".snipp.yml"),
        _default_config_path(),
    ]


_DEFAULT_SEARCH_PATHS = _search_paths()


def find_config_file(explicit: Optional[str] = None) -> Optional[Path]:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        return p if p.exists() else None
    for candidate in _DEFAULT_SEARCH_PATHS:
        candidate = candidate.expanduser().resolve()
        if candidate.exists():
            return candidate
    return None


def _warn(msg: str) -> None:
    sys.stderr.write(f"snipp: {msg}\n")


def _suggest(key: str, valid: Set[str]) -> str:
    if not valid:
        return ""
    matches = difflib.get_close_matches(key, list(valid), n=1, cutoff=0.6)
    return f" (did you mean {matches[0]!r}?)" if matches else ""


def load_config(path: Optional[str] = None) -> Config:
    cfg = Config.default()
    found = find_config_file(path)
    if found is None:
        return cfg

    try:
        raw = yaml.safe_load(found.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        _warn(f"failed to parse {found}: {e}")
        return cfg

    if not isinstance(raw, dict):
        _warn(f"{found}: expected a mapping at top level, got {type(raw).__name__}")
        return cfg

    for k in raw.keys():
        if k not in _TOP_LEVEL_KEYS:
            _warn(f"{found}: unknown top-level key {k!r}{_suggest(k, _TOP_LEVEL_KEYS)}")

    if "default_max_tokens" in raw:
        try:
            cfg.default_max_tokens = int(raw["default_max_tokens"])
        except (TypeError, ValueError):
            _warn(f"{found}: default_max_tokens must be an int, got {raw['default_max_tokens']!r}")
    if "verbose" in raw:
        cfg.verbose = bool(raw["verbose"])
    if "model" in raw:
        cfg.model = str(raw["model"]) if raw["model"] is not None else None
    if "stage" in raw:
        cfg.stage = str(raw["stage"]) if raw["stage"] is not None else None

    compressors_raw = raw.get("compressors", {})
    if isinstance(compressors_raw, dict):
        for name, block in compressors_raw.items():
            if not isinstance(block, dict):
                _warn(f"{found}: compressors.{name} must be a mapping; ignoring")
                continue

            existing = cfg.compressors.get(name)
            if existing is None:
                if name not in _KNOWN_EXTRAS:
                    known_names = set(_KNOWN_EXTRAS.keys())
                    _warn(
                        f"{found}: unknown compressor {name!r}{_suggest(name, known_names)}; "
                        "creating empty config (ignored unless a plugin registers it)"
                    )
                existing = CompressorConfig()
                cfg.compressors[name] = existing

            if "enabled" in block:
                existing.enabled = bool(block["enabled"])
            if "max_output_tokens" in block:
                try:
                    existing.max_output_tokens = int(block["max_output_tokens"])
                except (TypeError, ValueError):
                    _warn(
                        f"{found}: compressors.{name}.max_output_tokens must be int, "
                        f"got {block['max_output_tokens']!r}"
                    )

            valid_extras = _KNOWN_EXTRAS.get(name, set())
            for k, v in block.items():
                if k in _BLOCK_RESERVED:
                    continue
                if valid_extras and k not in valid_extras:
                    _warn(
                        f"{found}: compressors.{name}.{k} is not a recognized "
                        f"option{_suggest(k, valid_extras | _BLOCK_RESERVED)}"
                    )
                existing.extras[k] = v

    return cfg


def _generate_model_list_block() -> str:
    """Build a comment block listing supported models from MODEL_TOKENIZER_MAP."""
    by_backend: Dict[str, list] = {}
    for model, spec in MODEL_TOKENIZER_MAP.items():
        backend = spec.split(":", 1)[0] if ":" in spec else spec
        by_backend.setdefault(backend, []).append(model)

    pretty = {
        "anthropic": "Anthropic",
        "tiktoken": "OpenAI",
        "hf": "Open-weight",
    }
    lines = []
    for backend in ("anthropic", "tiktoken", "hf"):
        if backend not in by_backend:
            continue
        models = sorted(by_backend[backend])
        # Wrap at ~70 chars per line for readability.
        chunks = []
        cur: list = []
        cur_len = 0
        for m in models:
            if cur and cur_len + len(m) + 2 > 60:
                chunks.append(", ".join(cur))
                cur = [m]
                cur_len = len(m)
            else:
                cur.append(m)
                cur_len += len(m) + 2
        if cur:
            chunks.append(", ".join(cur))
        prefix = f"#   {pretty[backend]}: "
        for i, chunk in enumerate(chunks):
            lines.append(prefix + chunk if i == 0 else f"#       {chunk}")
    return "\n".join(lines)


def write_default_config(path: Optional[str] = None) -> Path:
    target = Path(path).expanduser().resolve() if path else _default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    model_block = _generate_model_list_block()

    default_yaml = f"""# snipp configuration
# Place this file at $XDG_CONFIG_HOME/snipp/config.yaml
# (defaults to ~/.config/snipp/config.yaml) or
# ./.snipp.yaml for project-local overrides.
#
# Supported models (auto-generated from MODEL_TOKENIZER_MAP):
{model_block}

# Global defaults
default_max_tokens: 4000
model: null          # any of the supported models above, or a dated API id
stage: null          # explore | edit | debug | verify

# Per-tool settings — unknown keys produce a warning to stderr.
compressors:
  grep:
    max_output_tokens: 2000
    context_lines: 2
    max_files: 50
    max_matches_per_file: 20

  git_log:
    max_output_tokens: 1500
    max_commits: 20
    show_diffs: false

  git_diff:
    max_output_tokens: 1500
    max_files: 20

  git_status:
    max_output_tokens: 800

  cat:
    max_output_tokens: 3000
    show_signatures: true
    keep_signatures_only: false

  head:
    max_output_tokens: 3000

  tail:
    max_output_tokens: 3000

  ls:
    max_output_tokens: 1000
    max_entries: 80

  find:
    max_output_tokens: 1500
    max_dirs: 30

  pytest:
    max_output_tokens: 2000
    show_passed: false
    max_failure_trace: 80

  python_test:
    max_output_tokens: 2000

  docker:
    max_output_tokens: 2000
    max_containers: 30

  npm:
    max_output_tokens: 2000

  npm_test:
    max_output_tokens: 2000
    show_passed_suites: false

  eslint:
    max_output_tokens: 2000

  generic:
    max_output_tokens: 4000
"""
    target.write_text(default_yaml, encoding="utf-8")
    return target
