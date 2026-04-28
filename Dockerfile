# Multi-stage build for snipp MCP server
# Primary transport: stdio (HTTP disabled pending Phase 7)

FROM python:3.13-slim AS builder

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir build && \
    python -m build --wheel && \
    pip install --no-cache-dir dist/*.whl

# --- Runtime ---
FROM python:3.13-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin/snipp* /usr/local/bin/

ENV PYTHONUNBUFFERED=1
ENV CC_COMPRESS_CACHE=/app/.cache/snipp

# stdio transport is the default and only supported transport
ENTRYPOINT ["snipp-mcp"]
CMD ["--transport", "stdio"]
