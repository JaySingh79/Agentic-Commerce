# Single image: FastAPI (8010, serves API + web/). Gradio (7860) is LEGACY, retained but not served.
# Deps via uv (mirrors AGENTS.md: uv for everything). No secrets baked in —
# runtime config flows through compose `env_file: .env`. Razorpay payments reach
# MCP over its hosted remote server (streamable HTTP), so no Docker CLI/socket
# is needed in this image.
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV HOST=0.0.0.0
ENV API_PORT=8010
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN useradd -m appuser

# Dependency layer first for build caching; then the project itself.
COPY --chown=appuser:appuser pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY --chown=appuser:appuser src ./src
COPY --chown=appuser:appuser web ./web
COPY --chown=appuser:appuser openapi.json ./
RUN uv sync --locked --no-dev && rm -rf /root/.cache

# Writable homes/dirs for the non-root runtime user.
RUN mkdir -p /data /home/appuser/.cache && chown -R appuser:appuser /data /home/appuser /app
USER appuser
ENV HOME=/home/appuser

EXPOSE 7860 8010

# Default: the API (primary HTTP surface). Compose overrides this for the UI.
CMD ["python", "-m", "agentic_commerce.api.server"]
