# web-to-pdf engine image. Runs as a non-root user.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/apps/api/src \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install uv for dependency management.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependencies first for layer caching.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Playwright Chromium + OS deps (installed as root before dropping privileges).
RUN uv run playwright install --with-deps chromium \
    && chmod -R a+rX /ms-playwright

# Application source.
COPY apps ./apps
COPY packages ./packages
COPY alembic.ini ./alembic.ini

# Non-root runtime user; owns the storage dir.
RUN groupadd --system app && useradd --system --gid app --create-home appuser \
    && mkdir -p /var/storage && chown -R appuser:app /var/storage /app
USER appuser

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "pdf_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
