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

for required in .env compose.prod.yml; do
  if [[ ! -f "$required" ]]; then
    echo "==> Falta ${DIR}/${required}. El workflow copia compose.prod.yml;" >&2
    echo "    .env se prepara a mano antes del primer despliegue." >&2
    exit 1
  fi
done

for key in SESSION_SECRET; do
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
trap 'rm -f "${DIR}"/.env.??????' EXIT

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

check_health() {
  # 0 = sano; 2 = solo falta el índice inicial; 1 = catálogo inválido u otro 503.
  local port="$1"
  local attempts="${2:-30}"
  local code body
  for _ in $(seq 1 "$attempts"); do
    body="$(mktemp)"
    code="$(curl -s --max-time 5 --connect-timeout 2 \
      -o "$body" -w '%{http_code}' \
      "http://127.0.0.1:${port}/health" || true)"
    if [[ "$code" == "200" ]]; then
      rm -f "$body"
      return 0
    fi
    if [[ "$code" == "503" ]] \
      && grep -q '"status":"missing"' "$body" \
      && ! grep -Eq '"catalog":\{[^}]*"status":"invalid"' "$body"; then
      rm -f "$body"
      return 2
    fi
    rm -f "$body"
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
    # Se recrea el stack completo: si el fallo vino de otro servicio,
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
    set +e
    check_health "$port" 15
    rollback_health=$?
    set -e
    if [[ "$rollback_health" -eq 1 ]]; then
      echo "==> La versión anterior no es compatible: catálogo inválido o /health no responde." >&2
      echo "    Las migraciones aplicadas no se revierten; revisa el runbook." >&2
      exit 1
    fi
    echo "==> Rollback a ${PREV_TAG} verificado en /live y /health" >&2
  else
    echo "==> No hay versión anterior; revisa los logs:" >&2
    echo "    docker compose -f compose.prod.yml logs app" >&2
  fi
  exit 1
}

echo "==> Imagen: ${APP_IMAGE}"
"${COMPOSE[@]}" pull app

# Aprovisionamiento comercial (catálogo y sedes/precios). La imagen lo trae en
# /app/seed-data, pero el volumen `appdata` tapa /app/data: sin esta siembra, una
# versión que añade archivos al contrato arranca con el catálogo inválido,
# /health falla y el despliegue hace rollback. Se copian solo los archivos
# ausentes (como el perfil `bootstrap`), sin pisar los del volumen, y se valida
# antes de migrar: si falla, nada cambió aún y la versión en marcha sigue igual.
# Imágenes anteriores a la 043 no traen /app/seed-data y se saltan el paso.
SEED_PROVISIONING='
set -e
seed_dir() {
  src="$1"; dest="$2"
  [ -d "$src" ] || return 0
  mkdir -p "$dest"
  find "$src" -type f | while IFS= read -r file; do
    target="$dest/${file#"$src"/}"
    if [ ! -e "$target" ]; then
      mkdir -p "$(dirname "$target")"
      cp "$file" "$target"
      echo "    sembrado: ${target#/app/}"
    fi
  done
}
[ -d /app/seed-data ] || { echo "    la imagen no trae /app/seed-data; se omite"; exit 0; }
seed_dir /app/seed-data/catalog /app/data/catalog
seed_dir /app/seed-data/commercial /app/data/commercial
seed_dir /app/seed-data/institucional /app/data/institucional
cd /app
actual="$(python -m carvajal.commercial version)"
if [ -n "${EXPECTED_COMMERCIAL_VERSION:-}" ] && [ "$actual" != "$EXPECTED_COMMERCIAL_VERSION" ]; then
  echo "    catálogo comercial $actual; se esperaba $EXPECTED_COMMERCIAL_VERSION" >&2
  exit 1
fi
python -m carvajal.commercial validar >/dev/null
echo "    catálogo comercial $actual válido"
'

echo "==> Aprovisionamiento comercial"
if ! "${COMPOSE[@]}" run --rm --no-deps -T app sh -c "$SEED_PROVISIONING"; then
  echo "==> El aprovisionamiento comercial no es válido; no se despliega." >&2
  echo "    La versión en marcha sigue intacta. Revisa docs/despliegue-ovh.md." >&2
  exit 1
fi

echo "==> Preparando la base"
# `compose run` no garantiza que `db` esté listo: se arranca y se espera aquí
# para que Alembic no falle por conexión rechazada en el primer despliegue.
"${COMPOSE[@]}" up -d --wait --wait-timeout 120 db

echo "==> Migraciones"
if ! "${COMPOSE[@]}" run --rm app alembic upgrade head; then
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
set +e
check_health "$HEALTH_PORT" 30
SALUD=$?
set -e

case "$SALUD" in
  0)
    echo "==> Salud OK (base, catálogo comercial e índice)"
    ;;
  2)
    # Primer despliegue: la app responde, el catálogo es válido y aún no hay
    # índice. La indexación es un paso aparte (docs/despliegue-ovh.md).
    echo "==> Aviso: el índice de Qdrant no existe todavía; indexa el corpus." >&2
    ;;
  *)
    # Un fallo de catálogo se distingue del índice ausente: `check_health`
    # solo acepta el 503 con `"status":"missing"` y catálogo no inválido.
    echo "==> /health no quedó listo (código ${SALUD}):" >&2
    curl -s --max-time 5 "http://127.0.0.1:${HEALTH_PORT}/health" >&2 || true
    echo >&2
    rollback
    ;;
esac

# Solo se escribe .env cuando el despliegue quedó sano. Si el `up` falla,
# .env conserva el tag anterior y rollback lo reutiliza.
remember_image "$IMAGE_TAG"
printf '%s\n' "$IMAGE_TAG" > "$STATE_FILE"
echo "==> Despliegue completado: ${IMAGE_TAG}"
