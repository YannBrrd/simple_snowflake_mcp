# Optimized multi-stage build using uv
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

WORKDIR /app

# Copy the dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies with uv (much faster)
RUN uv sync --frozen --no-dev --no-install-project

# Copy the source code
COPY src/ ./src/

# Install the project
RUN uv sync --frozen --no-dev

# Production stage
FROM python:3.11-slim-bookworm AS production

WORKDIR /app

# Copy the virtual environment from the builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
# Copy the default configuration (otherwise the server falls back to DEFAULT_CONFIG)
COPY config.yaml /app/config.yaml

# Create a non-root user
RUN groupadd -r -g 1001 mcp && \
    useradd -r -g mcp -u 1001 -m -s /bin/bash mcp && \
    chown -R mcp:mcp /app

USER mcp

# Add the venv to PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Default command
CMD ["simple-snowflake-mcp"]
