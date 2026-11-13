FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    FASTEMBED_CACHE_PATH=/opt/fastembed \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 1000 app
WORKDIR /app

# Dependencies first (cached layer), then the embedding model, then the code.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" \
    && chown -R app:app /opt/fastembed

COPY claimtrace ./claimtrace
RUN uv sync --frozen --no-dev

USER app
EXPOSE 8000
CMD ["uvicorn", "claimtrace.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
