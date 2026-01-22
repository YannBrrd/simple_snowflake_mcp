# Multi-stage build optimisé avec uv
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

WORKDIR /app

# Copier les fichiers de dépendances
COPY pyproject.toml uv.lock ./

# Installer les dépendances avec uv (beaucoup plus rapide)
RUN uv sync --frozen --no-dev --no-install-project

# Copier le code source
COPY src/ ./src/

# Installer le projet
RUN uv sync --frozen --no-dev

# Stage de production
FROM python:3.11-slim-bookworm AS production

WORKDIR /app

# Copier l'environnement virtuel depuis le builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

# Créer un utilisateur non-root
RUN groupadd -r -g 1001 mcp && \
    useradd -r -g mcp -u 1001 -m -s /bin/bash mcp && \
    chown -R mcp:mcp /app

USER mcp

# Ajouter le venv au PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Commande par défaut
CMD ["simple-snowflake-mcp"]
