FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    FASTEMBED_CACHE_PATH=/opt/fastembed \
    PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 1000 app && mkdir -p /cache && chown app:app /cache
WORKDIR /app

# Dependencies first (cached layer), then the embedding model, then the code.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" \
    && python -c "from fastembed.rerank.cross_encoder import TextCrossEncoder; TextCrossEncoder('Xenova/ms-marco-MiniLM-L-6-v2')" \
    && chown -R app:app /opt/fastembed

# Offline IP -> country for the usage counter (IPs are never stored): db-ip.com
# "IP to Country Lite", CC BY 4.0, https://db-ip.com. Bump GEOIP_MONTH to refresh.
ARG GEOIP_MONTH=2026-11
ENV GEOIP_CSV=/opt/geo/dbip-country-lite.csv.gz
# (Cloudflare there rejects urllib's default User-Agent, hence the curl one)
RUN mkdir -p /opt/geo && python -c "import sys, urllib.request as u; \
r = u.urlopen(u.Request(sys.argv[1], headers={'User-Agent': 'curl/8.5.0'}), timeout=120); \
open(sys.argv[2], 'wb').write(r.read())" \
    "https://download.db-ip.com/free/dbip-country-lite-${GEOIP_MONTH}.csv.gz" "$GEOIP_CSV"

COPY claimtrace ./claimtrace
RUN uv sync --frozen --no-dev

USER app
EXPOSE 8000
CMD ["uvicorn", "claimtrace.web.app:app", "--host", "0.0.0.0", "--port", "8000"]
