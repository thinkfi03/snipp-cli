"""Pydantic schemas for all MCP tool inputs and outputs."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

class MCPError(BaseModel):
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# compress
# ---------------------------------------------------------------------------

class CompressInput(BaseModel):
    output: str = Field(..., max_length=50_000_000, description="Raw tool output to compress (50MB cap)")
    command: Optional[str] = Field(None, max_length=4096, description="Original command string for tool detection")
    query: Optional[str] = Field(None, max_length=4096, description="Optional user query for ranking")
    stage: Optional[Literal["explore", "edit", "debug", "verify"]] = Field(None, description="Task stage multiplier")
    model: Optional[str] = Field(None, max_length=128, description="Tokenizer model identifier")
    max_tokens: Optional[int] = Field(None, gt=0, le=200_000, description="Hard token budget override")
    session_id: Optional[str] = Field(None, max_length=64, description="Session ID for handle storage")


class CompressOutput(BaseModel):
    compressed: str = Field(..., description="Compressed output text")
    original_tokens: int = Field(..., ge=0)
    compressed_tokens: int = Field(..., ge=0)
    reduction_pct: float = Field(..., ge=0.0, le=100.0)
    tool_type: str = Field(..., description="Detected or specified tool type")
    strategy: str = Field(..., description="Compression strategy used")
    tokenizer: str = Field(..., description="Tokenizer backend name")
    exact_tokens: bool = Field(..., description="Whether token count is exact")
    fidelity: Dict[str, Any] = Field(default_factory=dict, description="Compressor-specific fidelity metrics")
    handles: List[str] = Field(default_factory=list, description="Elision handles generated")
    elapsed_ms: float = Field(..., ge=0.0, description="Wall-clock time in milliseconds")


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

class RunInput(BaseModel):
    argv: List[str] = Field(..., min_length=1, max_length=64, description="Command argv array (no shell)")
    query: Optional[str] = Field(None, max_length=4096)
    stage: Optional[Literal["explore", "edit", "debug", "verify"]] = None
    model: Optional[str] = None
    timeout_seconds: int = Field(default=300, gt=0, le=3600)
    cwd: Optional[str] = Field(None, description="Working directory (validated against allowlist)")
    env: Optional[Dict[str, str]] = Field(None, description="Environment variables (filtered)")
    stdin: Optional[str] = Field(None, max_length=10_000_000)
    session_id: Optional[str] = Field(None, max_length=64)


class RunOutput(CompressOutput):
    exit_code: int = Field(..., description="Subprocess exit code")
    stderr: str = Field(default="", description="Stderr content (truncated to 64KB)", max_length=65_536)
    timeout: bool = Field(default=False, description="True if subprocess timed out")
    oom_killed: bool = Field(default=False, description="True if subprocess was OOM-killed")


# ---------------------------------------------------------------------------
# expand
# ---------------------------------------------------------------------------

class ExpandInput(BaseModel):
    handle: str = Field(..., min_length=8, max_length=64, description="Elision handle (raw hex or wrapped [<<elide:...>>])")
    session_id: Optional[str] = Field(None, max_length=64)


class ExpandOutput(BaseModel):
    content: str = Field(..., description="Original elided content")
    source: Optional[str] = Field(None, description="Source metadata from store")
    lines: int = Field(..., ge=0)
    bytes_: int = Field(..., ge=0, alias="bytes")
    age_seconds: float = Field(..., ge=0.0)
    model_config = ConfigDict(populate_by_name=True)


# ---------------------------------------------------------------------------
# detect
# ---------------------------------------------------------------------------

class DetectInput(BaseModel):
    command: Optional[str] = Field(None, max_length=4096)
    output: str = Field(..., max_length=1_000_000, description="Output sample for heuristic detection (1MB cap)")


class DetectOutput(BaseModel):
    tool: str = Field(..., description="Detected tool type")
    confidence: float = Field(..., ge=0.0, le=1.0)
    source: str = Field(..., description="Detection heuristic that matched")


# ---------------------------------------------------------------------------
# list_handles
# ---------------------------------------------------------------------------

class HandleInfo(BaseModel):
    handle: str
    source: Optional[str]
    lines: int
    bytes_: int = Field(alias="bytes")
    age_seconds: float
    model_config = ConfigDict(populate_by_name=True)


class ListHandlesOutput(BaseModel):
    handles: List[HandleInfo] = Field(default_factory=list)
    total: int = Field(..., ge=0)
    session_id: str


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------

class HealthOutput(BaseModel):
    version: str = Field(..., description="Server version")
    uptime_seconds: float = Field(..., ge=0.0)
    tokenizer_backends: Dict[str, bool] = Field(default_factory=dict, description="Available tokenizer backends")
    plugins_loaded: List[str] = Field(default_factory=list)
    sessions_active: int = Field(..., ge=0)
    handles_total: int = Field(..., ge=0)
    cache_size_bytes: int = Field(..., ge=0)
    last_error: Optional[str] = Field(None, description="Last error (redacted, 100 chars max)")


# ---------------------------------------------------------------------------
# register_plugin
# ---------------------------------------------------------------------------

class RegisterPluginInput(BaseModel):
    tool_name: str = Field(..., pattern=r"^[a-z][a-z0-9_]{0,31}$")
    argv0: str = Field(..., pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,31}$")
    executable: str = Field(..., description="Absolute path to plugin executable")
    timeout_seconds: int = Field(default=10, gt=0, le=120)
    persistent: bool = Field(default=False, description="Persist to ~/.config/snipp/plugins.yaml")

    @field_validator("executable")
    @classmethod
    def _abs_path(cls, v: str) -> str:
        from pathlib import Path
        p = Path(v)
        if not p.is_absolute():
            raise ValueError("executable must be an absolute path")
        return str(p)


class RegisterPluginOutput(BaseModel):
    tool_name: str
    registered: bool
    persistent: bool
    message: str


# ---------------------------------------------------------------------------
# explore_repo
# ---------------------------------------------------------------------------

class ExploreRepoOutput(BaseModel):
    context: str = Field(..., description="Structured codebase context document")
    files_scanned: int = Field(..., ge=0, description="Number of source files scanned")
    symbols_found: int = Field(..., ge=0, description="Total symbols extracted")
    tokens: int = Field(..., ge=0, description="Token count of the returned context")
    elapsed_ms: float = Field(..., ge=0.0, description="Wall-clock time in milliseconds")


# ---------------------------------------------------------------------------
# show_symbol
# ---------------------------------------------------------------------------

class ShowSymbolOutput(BaseModel):
    symbol: Optional[str] = Field(None, description="Symbol name")
    kind: Optional[str] = Field(None, description="Symbol kind (class, function, method, etc.)")
    file: Optional[str] = Field(None, description="Relative file path")
    signature: Optional[str] = Field(None, description="Full signature text")
    body_preview: Optional[str] = Field(None, description="First 5 lines of body")
    found: bool = Field(..., description="Whether the symbol was found")
    elapsed_ms: float = Field(..., ge=0.0, description="Wall-clock time in milliseconds")
