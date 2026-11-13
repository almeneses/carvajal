# syntax=docker/dockerfile:1
# Imagen de la app web (W6.1): multi-etapa y sin dependencias de desarrollo.
#
#   docker build -t carvajal-api .
#   docker run --rm -p 8000:8000 --env-file .env carvajal-api
#
# El contenedor no aplica migraciones: el runbook de W6.2 las ejecuta antes
# (`docker compose -f compose.prod.yml run --rm app alembic upgrade head`).
#
# Las imágenes base van fijadas por digest para que el build sea reproducible;
# el tag queda como comentario para saber qué versión actualizar. El digest de
# `python:3.13-slim` se consultó el 2026-11-13 y el de `uv` corresponde a la
# versión que exige `uv_build` (>=0.13.6,<0.14.0).

FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS base
# tag: python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=0 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

FROM base AS builder
# uv solo hace falta para construir el entorno; no se copia al runtime.
COPY --from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /uvx /bin/
# El paquete vive en packages/core (src-layout); el build lo necesita.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY packages/core/src ./packages/core/src
# Solo dependencias base: `capture` y `legacy` se quedan fuera del runtime web.
# `zstandard` es un backend opcional de compresión de trazas de LangSmith; sin
# tracing activo, LangSmith cae a la ingesta multipart sin comprimir. Se omite
# para no pagar 23 MB en la imagen.
RUN uv sync --frozen --no-dev --no-editable --no-install-package zstandard

FROM base AS runtime
# Usuario sin privilegios: la app solo lee plantillas y estáticos.
RUN addgroup --system app && adduser --system --ingroup app app
# El venv ya trae todo lo necesario; pip, setuptools y el resto del Python
# global no se usan en producción y solo engordan la imagen.
RUN rm -rf \
    /usr/local/lib/python3.13/site-packages/pip \
    /usr/local/lib/python3.13/site-packages/pip-*.dist-info \
    /usr/local/lib/python3.13/site-packages/setuptools \
    /usr/local/lib/python3.13/site-packages/setuptools-*.dist-info \
    /usr/local/lib/python3.13/site-packages/wheel \
    /usr/local/lib/python3.13/site-packages/wheel-*.dist-info \
    /usr/local/lib/python3.13/test \
    /usr/local/lib/python3.13/idlelib \
    /usr/local/lib/python3.13/ensurepip \
    /usr/local/lib/python3.13/pydoc_data \
    /usr/local/lib/python3.13/lib2to3 \
    /usr/local/lib/python3.13/turtledemo \
    /usr/local/bin/pip* \
    /usr/local/bin/idle3* \
    /usr/local/bin/pydoc3*
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
# --no-access-log evita duplicar la línea de acceso que ya emite structlog.
# --proxy-headers hace que request.url.scheme sea https detrás del proxy TLS
# (W6.2): de eso dependen las cookies Secure y HSTS. El proxy debe figurar en
# FORWARDED_ALLOW_IPS; por defecto uvicorn solo confía en 127.0.0.1.
CMD ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers"]
