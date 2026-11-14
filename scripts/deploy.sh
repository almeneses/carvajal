#!/usr/bin/env bash
# Despliegue de producción en el VPS. Lo invoca .github/workflows/deploy.yml:
#
#   IMAGE_REPO=ghcr.io/almeneses/carvajal-api bash deploy.sh sha-<commit>
#
# Requisitos: Docker + Compose, `.env` en este directorio y, si el paquete de
# GHCR es privado, una sesión previa de `docker login ghcr.io`.
#
# Descarga el tag inmutable, aplica migraciones, recrea los contenedores y
# espera /live y /health. Si algo falla, vuelve al tag anterior.
set -euo pipefail

IMAGE_TAG="${1:?Uso: deploy.sh <tag>}"
IMAGE_REPO="${IMAGE_REPO:-ghcr.io/almeneses/carvajal-api}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

COMPOSE=(docker compose -f compose.prod.yml)
STATE_FILE=".deployed_tag"
PREV_TAG="$(cat "$STATE_FILE" 2>/dev/null || true)"
HEALTH_FILE="$(mktemp)"
trap 'rm -f "$HEALTH_FILE"' EXIT

export APP_IMAGE="${IMAGE_REPO}:${IMAGE_TAG}"

remember_image() {
  # Deja el tag desplegado en .env para que los comandos manuales
  # (`docker compose exec`, `logs`, `run`) usen la misma imagen.
  local tag="$1"
  local image="${IMAGE_REPO}:${tag}"
  if grep -q '^APP_IMAGE=' .env 2>/dev/null; then
    sed -i "s|^APP_IMAGE=.*|APP_IMAGE=${image}|" .env
  else
    printf 'APP_IMAGE=%s\n' "$image" >> .env
  fi
}

rollback() {
  echo "==> Despliegue fallido; rollback a ${PREV_TAG:-<sin versión anterior>}" >&2
  if [[ -n "$PREV_TAG" ]]; then
    export APP_IMAGE="${IMAGE_REPO}:${PREV_TAG}"
    remember_image "$PREV_TAG"
    "${COMPOSE[@]}" up -d --no-build app
  else
    echo "==> Revisa los logs: docker compose -f compose.prod.yml logs app" >&2
  fi
  exit 1
}

echo "==> Imagen: ${APP_IMAGE}"
"${COMPOSE[@]}" pull app

echo "==> Migraciones"
"${COMPOSE[@]}" run --rm app alembic upgrade head

echo "==> Recreando contenedores"
if ! "${COMPOSE[@]}" up -d --no-build --remove-orphans; then
  rollback
fi

echo "==> Esperando /live"
LISTO=0
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${APP_PORT:-8000}/live" >/dev/null 2>&1; then
    LISTO=1
    break
  fi
  sleep 2
done
if [[ "$LISTO" -ne 1 ]]; then
  rollback
fi

echo "==> Comprobando /health"
SALUD=0
for _ in $(seq 1 15); do
  HEALTH_CODE="$(curl -s -o "$HEALTH_FILE" -w '%{http_code}' \
    "http://127.0.0.1:${APP_PORT:-8000}/health" || true)"
  if [[ "$HEALTH_CODE" == "200" ]]; then
    SALUD=1
    break
  fi
  if [[ "$HEALTH_CODE" == "503" ]] && grep -q '"status":"missing"' "$HEALTH_FILE"; then
    SALUD=2
    break
  fi
  sleep 2
done

case "$SALUD" in
  1)
    echo "==> Salud OK"
    ;;
  2)
    # Primer despliegue: la app responde pero el índice aún no se ha construido.
    # La indexación es un paso aparte (docs/despliegue-ovh.md).
    echo "==> Aviso: el índice de Qdrant no existe todavía; indexa el corpus." >&2
    ;;
  *)
    echo "==> /health no quedó listo (último código: ${HEALTH_CODE:-sin respuesta}):" >&2
    cat "$HEALTH_FILE" >&2 || true
    echo >&2
    rollback
    ;;
esac

remember_image "$IMAGE_TAG"
printf '%s\n' "$IMAGE_TAG" > "$STATE_FILE"
echo "==> Despliegue completado: ${IMAGE_TAG}"
