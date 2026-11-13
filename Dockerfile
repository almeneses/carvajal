# syntax=docker/dockerfile:1
# Imagen de la app web (W6.1): multi-etapa y sin dependencias de desarrollo.
#
#   docker build -t carvajal-api .
#   docker run --rm -p 8000:8000 --env-file .env carvajal-api
#
# El contenedor no aplica migraciones: el runbook de W6.2 las ejecuta antes
# (`docker compose -f compose.prod.yml run --rm app alembic upgrade head`).

FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=0 \
    PATH="/app/.venv/bin:$PATH"
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /uvx /bin/
WORKDIR /app

FROM base AS builder
# El paquete vive en packages/core (src-layout); el build lo necesita.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY packages/core/src ./packages/core/src
RUN uv sync --frozen --no-dev --no-editable

FROM base AS runtime
# Usuario sin privilegios: la app solo lee plantillas y estáticos.
RUN addgroup --system app && adduser --system --ingroup app app
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
COPY apps ./apps
USER app
EXPOSE 8000
# Vitalidad del proceso: /live no toca la base, así el contenedor no se marca
# enfermo cuando Postgres está caído.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/live', timeout=2)"
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
