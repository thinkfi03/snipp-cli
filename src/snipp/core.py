"""Core compression dispatcher with stage + tokenizer support."""

from __future__ import annotations

from typing import Optional, Type

from snipp.detector import detect_tool, ToolType
from snipp.config import Config, CompressorConfig
from snipp.compressors.base import CompressResult, BaseCompressor
from snipp.compressors import (
    GrepCompressor,
    GitLogCompressor,
    GitDiffCompressor,
    GitStatusCompressor,
    CatCompressor,
    LsCompressor,
    FindCompressor,
    PytestCompressor,
    GenericCompressor,
    DockerCompressor,
    NpmTestCompressor,
    NpmCompressor,
    EslintCompressor,
)
from snipp.stage import StagePolicy, parse_stage
from snipp.tokenizer import get_tokenizer


_REGISTRY: dict = {
    ToolType.GREP: GrepCompressor,
    ToolType.GIT_LOG: GitLogCompressor,
    ToolType.GIT_DIFF: GitDiffCompressor,
    ToolType.GIT_STATUS: GitStatusCompressor,
    ToolType.CAT: CatCompressor,
    ToolType.HEAD: CatCompressor,
    ToolType.TAIL: CatCompressor,
    ToolType.LS: LsCompressor,
    ToolType.FIND: FindCompressor,
    ToolType.PYTEST: PytestCompressor,
    ToolType.PYTHON_TEST: PytestCompressor,
    ToolType.DOCKER: DockerCompressor,
    ToolType.NPM: NpmCompressor,
    ToolType.NPM_TEST: NpmTestCompressor,
    ToolType.ESLINT: EslintCompressor,
    ToolType.GENERIC: GenericCompressor,
}


def register_compressor(tool: ToolType, cls: Type[BaseCompressor]) -> None:
    """Register a compressor for a known ToolType.

    DO NOT use this for plugins that introduce new tool names — it overwrites
    the existing dispatcher entry. Use `register_plugin_compressor` instead.
    """
    _REGISTRY[tool] = cls


# Name-keyed plugin registry — separate namespace from _REGISTRY so registering
# a plugin (e.g. "kubectl") never clobbers the GENERIC fallback.
_PLUGIN_REGISTRY: dict = {}
_PLUGIN_PREDICATES: dict = {}


def register_plugin_compressor(
    tool_name: str,
    cls: Type[BaseCompressor],
    *,
    predicate=None,
) -> None:
    """Register a plugin compressor under a tool_name without clobbering ToolType.

    Args:
        tool_name: Plugin name (e.g. "kubectl"). Routed via predicate, not ToolType.
        cls: BaseCompressor subclass.
        predicate: Optional callable (command, output) -> bool. If True for a given
            input, the plugin is selected ahead of ToolType-based dispatch.
            If None, the plugin is only invoked when explicitly addressed via
            `compress_output(..., command=tool_name + " ...")`.
    """
    _PLUGIN_REGISTRY[tool_name] = cls
    if predicate is not None:
        _PLUGIN_PREDICATES[tool_name] = predicate


def unregister_plugin_compressor(tool_name: str) -> None:
    _PLUGIN_REGISTRY.pop(tool_name, None)
    _PLUGIN_PREDICATES.pop(tool_name, None)


def list_plugin_compressors() -> list:
    return sorted(_PLUGIN_REGISTRY.keys())


def _build_kwargs(cfg: CompressorConfig, policy: StagePolicy) -> dict:
    kwargs = {
        "max_tokens": int(cfg.max_output_tokens * policy.budget_multiplier),
    }
    extras = dict(cfg.extras)
    if "context_lines" in extras:
        extras["context_lines"] = max(extras["context_lines"], policy.grep_context_lines)
    if "show_passed" in extras:
        extras["show_passed"] = policy.show_passed_tests or extras["show_passed"]
    if "show_signatures" in extras:
        extras["keep_signatures_only"] = policy.keep_signatures_only or extras.get(
            "keep_signatures_only", False
        )
    if "show_diffs" in extras:
        extras["show_diffs"] = policy.git_show_diffs or extras["show_diffs"]
    extras["keep_full_traces"] = policy.keep_full_traces
    kwargs.update(extras)
    return kwargs


def compress_output(
    output: str,
    command: Optional[str] = None,
    query: Optional[str] = None,
    config: Optional[Config] = None,
    *,
    model: Optional[str] = None,
    stage: Optional[str] = None,
) -> CompressResult:
    cfg = config or Config.default()
    chosen_model = model or cfg.model
    tokenizer = get_tokenizer(chosen_model)
    policy = StagePolicy.for_stage(parse_stage(stage or cfg.stage))

    # Plugin dispatch: predicate match OR command argv0 matches a plugin name
    plugin_cls = _resolve_plugin(command, output)
    if plugin_cls is not None:
        comp_cfg = cfg.compressors.get("generic") or CompressorConfig(
            max_output_tokens=cfg.default_max_tokens
        )
        kwargs = _build_kwargs(comp_cfg, policy)
        accepted = _accepted_kwargs(plugin_cls)
        filtered = {k: v for k, v in kwargs.items() if k in accepted or k == "max_tokens"}
        filtered["tokenizer"] = tokenizer
        try:
            compressor = plugin_cls(**filtered)
        except TypeError:
            compressor = plugin_cls(max_tokens=filtered["max_tokens"], tokenizer=tokenizer)
        return compressor.compress(output, query=query)

    tool = detect_tool(command, output)
    name = tool.value
    comp_cfg = cfg.compressors.get(name) or cfg.compressors.get("generic")
    if comp_cfg is None:
        comp_cfg = CompressorConfig(max_output_tokens=cfg.default_max_tokens)

    cls = _REGISTRY.get(tool, GenericCompressor)
    kwargs = _build_kwargs(comp_cfg, policy)
    accepted = _accepted_kwargs(cls)
    filtered = {k: v for k, v in kwargs.items() if k in accepted or k == "max_tokens"}
    filtered["tokenizer"] = tokenizer
    try:
        compressor = cls(**filtered)
    except TypeError:
        compressor = cls(max_tokens=filtered["max_tokens"], tokenizer=tokenizer)
    return compressor.compress(output, query=query)


def _resolve_plugin(command: Optional[str], output: str) -> Optional[Type[BaseCompressor]]:
    """Return a plugin compressor class if the input matches a plugin, else None.

    Resolution order:
      1. Any registered predicate that returns True for (command, output)
      2. The argv0 of `command` matches a registered plugin name
    """
    if not _PLUGIN_REGISTRY:
        return None
    # 1. Predicate match (first registered wins; deterministic by insertion order)
    for tool_name, predicate in _PLUGIN_PREDICATES.items():
        try:
            if predicate(command, output):
                return _PLUGIN_REGISTRY.get(tool_name)
        except Exception:
            continue
    # 2. argv0 match
    if command:
        cmd = command.strip()
        if cmd:
            try:
                import shlex
                parts = shlex.split(cmd)
            except ValueError:
                parts = cmd.split()
            for p in parts:
                if "=" in p and p.split("=", 1)[0].isupper():
                    continue
                argv0 = p.split("/")[-1].lower()
                if argv0 in _PLUGIN_REGISTRY:
                    return _PLUGIN_REGISTRY[argv0]
                break
    return None


def _accepted_kwargs(cls: Type[BaseCompressor]) -> set:
    import inspect
    try:
        sig = inspect.signature(cls.__init__)
        return set(sig.parameters.keys())
    except (TypeError, ValueError):
        return set()
