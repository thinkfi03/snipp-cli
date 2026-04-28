"""CC-Compress: intent-aware tool output compressor for coding agents."""

__version__ = "0.2.0"

from snipp.compressors.base import CompressResult, BaseCompressor
from snipp.config import Config, CompressorConfig
from snipp.core import compress_output, register_compressor
from snipp.detector import detect_tool, detect_tool_with_confidence, ToolType
from snipp.expand import ExpansionStore, default_store
from snipp.config_loader import load_config, write_default_config
from snipp.stage import Stage, StagePolicy, parse_stage
from snipp.tokenizer import Tokenizer, get_tokenizer, count_tokens

__all__ = [
    "CompressResult",
    "BaseCompressor",
    "Config",
    "CompressorConfig",
    "compress_output",
    "register_compressor",
    "detect_tool",
    "detect_tool_with_confidence",
    "ToolType",
    "ExpansionStore",
    "default_store",
    "load_config",
    "write_default_config",
    "Stage",
    "StagePolicy",
    "parse_stage",
    "Tokenizer",
    "get_tokenizer",
    "count_tokens",
]
