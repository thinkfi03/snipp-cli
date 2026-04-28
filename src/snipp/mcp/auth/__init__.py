"""Auth layer for HTTP transport. Stdio has no auth (process-level isolation)."""

from snipp.mcp.auth.bearer import BearerAuth

__all__ = ["BearerAuth"]
