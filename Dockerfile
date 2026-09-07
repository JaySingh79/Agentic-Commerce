# Single image: FastAPI (8010, serves API + web/). Gradio (7860) is LEGACY, retained but not served.
# Deps via uv (mirrors AGENTS.md: uv for everything). No secrets baked in —
# runtime config flows through compose `env_file: .env`. Docker CLI is included
# so payments/mcp.py can bridge to the razorpay-mcp container via `docker exec`.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Docker CLI (static binary) for the MCP stdio bridge; fails fast if unreachable.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.3.1.tgz \
        | tar -xz -C /tmp \
    && mv /tmp/docker/docker /usr/local/bin/docker \
    && rm -rf /tmp/docker \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* \
    && docker --version

RUN useradd -m appuser

# Dependency layer first for build caching; then the project itself.
COPY --chown=appuser:appuser pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY --chown=appuser:appuser src ./src
COPY --chown=appuser:appuser web ./web
COPY --chown=appuser:appuser app.py openapi.json ./
RUN uv sync --locked --no-dev && rm -rf /root/.cache

# Writable homes/dirs for the non-root runtime user.
RUN mkdir -p /data /home/appuser/.cache && chown -R appuser:appuser /data /home/appuser /app
USER appuser
ENV HOME=/home/appuser

EXPOSE 7860 8010

# Default: the API (primary HTTP surface). Compose overrides this for the UI.
CMD ["python", "-m", "agentic_commerce.api.server"]
