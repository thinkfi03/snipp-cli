"""MCP server for snipp.

Exposes snipp functionality as an MCP server for AI coding agents.
Primary transport: stdio. HTTP transport available behind opt-in flag.

Install with the [mcp] extra: pip install snipp-cli[mcp]
"""

try:
    from snipp.mcp.server import create_server, main
    __all__ = ["create_server", "main"]
except ImportError:
    # mcp extra not installed
    __all__ = []
