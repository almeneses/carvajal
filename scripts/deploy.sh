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

for required in .env compose.prod.yml Caddyfile; do
  if [[ ! -f "$required" ]]; then
    echo "==> Falta ${DIR}/${required}. El workflow copia compose.prod.yml y" >&2
    echo "    Caddyfile; .env se prepara a mano antes del primer despliegue." >&2
    exit 1
  fi
done

for key in DOMAIN SESSION_SECRET; do
  if ! grep -Eq "^${key}=.+" .env; then
    echo "==> Falta ${key} en ${DIR}/.env (ver docs/despliegue-ovh.md)." >&2
    exit 1
  fi
done

# El runbook exige 600: el .env contiene SESSION_SECRET y contraseñas.
if [[ "$(stat -c '%a' .env 2>/dev/null || true)" != "600" ]]; then
  echo "==> Ajustando permisos de .env a 600" >&2
  chmod 600 .env
fi

COMPOSE=(docker compose -f compose.prod.yml)
STATE_FILE=".deployed_tag"
HEALTH_FILE="$(mktemp)"
trap 'rm -f "$HEALTH_FILE" "${DIR}"/.env.??????' EXIT

# Tag anterior: el último despliegue sano o, en su defecto, el que ya estaba
# en .env (por ejemplo, la imagen construida con `make prod-up`).
PREV_TAG="$(cat "$STATE_FILE" 2>/dev/null || true)"
if [[ -z "$PREV_TAG" ]]; then
  PREV_TAG="$(sed -nE 's|^APP_IMAGE=.*:([^:]+)$|\1|p' .env | head -n 1)"
fi

export APP_IMAGE="${IMAGE_REPO}:${IMAGE_TAG}"

remember_image() {
  # Deja el tag desplegado en .env para que los comandos manuales
  # (`docker compose exec`, `logs`, `run`) usen la misma imagen. Se reescribe
  # el archivo completo (no `sed -i`) para no corromper valores con `|` y
  # conservar los permisos 600.
  local tag="$1"
  local image="${IMAGE_REPO}:${tag}"
  local tmp
  tmp="$(mktemp "${DIR}/.env.XXXXXX")"
  chmod 600 "$tmp"
  if grep -q '^APP_IMAGE=' .env 2>/dev/null; then
    grep -v '^APP_IMAGE=' .env > "$tmp" || true
  else
    cat .env > "$tmp"
  fi
  printf 'APP_IMAGE=%s\n' "$image" >> "$tmp"
  mv -f "$tmp" .env
  chmod 600 .env
}

wait_live() {
  local port="$1"
  local attempts="${2:-30}"
  for _ in $(seq 1 "$attempts"); do
    if curl -fsS --max-time 5 --connect-timeout 2 \
      "http://127.0.0.1:${port}/live" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

published_port() {
  "${COMPOSE[@]}" port app 8000 2>/dev/null \
    | sed -nE 's/.*:([0-9]+)$/\1/p' \
    | head -n 1
}

# Valida un puerto numérico; si Compose no devuelve un valor usable, usa 8000.
resolve_port() {
  local port
  port="$(published_port)"
  if [[ "$port" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$port"
  else
    printf '8000\n'
  fi
}

rollback() {
  echo "==> Despliegue fallido; rollback a ${PREV_TAG:-<sin versión anterior>}" >&2
  # Solo se cambia la imagen: las migraciones ya aplicadas no se revierten.
  # Mantén migraciones compatibles hacia atrás (expand/contract).
  if [[ -n "$PREV_TAG" ]]; then
    export APP_IMAGE="${IMAGE_REPO}:${PREV_TAG}"
    remember_image "$PREV_TAG"
    # Se recrea el stack completo: si el fallo vino de caddy u otro servicio,
    # relanzar solo `app` dejaría el resto en el estado roto.
    if ! "${COMPOSE[@]}" up -d --no-build --remove-orphans; then
      echo "==> El rollback no pudo recrear el stack; revisa los logs." >&2
      exit 1
    fi
    local port
    port="$(resolve_port)"
    if ! wait_live "$port" 15; then
      echo "==> La versión anterior tampoco responde en /live (puerto ${port})." >&2
      echo "    Revisa: docker compose -f compose.prod.yml logs app" >&2
      exit 1
    fi
    echo "==> Rollback a ${PREV_TAG} verificado en /live" >&2
  else
    echo "==> No hay versión anterior; revisa los logs:" >&2
    echo "    docker compose -f compose.prod.yml logs app" >&2
  fi
  exit 1
}

echo "==> Imagen: ${APP_IMAGE}"
"${COMPOSE[@]}" pull app

echo "==> Preparando la base"
# `compose run` no garantiza que `db` esté listo: se arranca y se espera aquí
# para que Alembic no falle por conexión rechazada en el primer despliegue.
"${COMPOSE[@]}" up -d --wait --wait-timeout 120 db

echo "==> Migraciones"
if ! "${COMPOSE[@]}" run --rm --no-build app alembic upgrade head; then
  rollback
fi

echo "==> Recreando contenedores"
if ! "${COMPOSE[@]}" up -d --no-build --remove-orphans; then
  rollback
fi

# Puerto publicado real de la app (puede ser distinto de 8000 si APP_PORT
# cambió en .env). Se consulta a Compose para no adivinar desde el shell.
HEALTH_PORT="$(resolve_port)"

echo "==> Esperando /live en 127.0.0.1:${HEALTH_PORT}"
if ! wait_live "$HEALTH_PORT" 30; then
  rollback
fi

echo "==> Comprobando /health"
SALUD=0
HEALTH_CODE=""
for _ in $(seq 1 30); do
  HEALTH_CODE="$(curl -s --max-time 5 --connect-timeout 2 \
    -o "$HEALTH_FILE" -w '%{http_code}' \
    "http://127.0.0.1:${HEALTH_PORT}/health" || true)"
  if [[ "$HEALTH_CODE" == "200" ]]; then
    SALUD=1
    break
  fi
  # Solo se acepta el 503 de índice ausente (`index.status == missing`), que
  # ocurre en el primer despliegue antes de indexar. El cuerpo solo contiene
  # `"status":"missing"` en ese caso; Qdrant caído devuelve `"unavailable"`.
  # Cualquier otro 503 se reintenta y, si persiste, revierte.
  if [[ "$HEALTH_CODE" == "503" ]] \
    && grep -q '"status":"missing"' "$HEALTH_FILE"; then
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

# Solo se escribe .env cuando el despliegue quedó sano. Si el `up` falla,
# .env conserva el tag anterior y rollback lo reutiliza.
remember_image "$IMAGE_TAG"
printf '%s\n' "$IMAGE_TAG" > "$STATE_FILE"
echo "==> Despliegue completado: ${IMAGE_TAG}"
