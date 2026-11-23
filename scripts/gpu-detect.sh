#!/usr/bin/env bash
# Decide si el Ollama de Docker debe reservar la GPU NVIDIA. Imprime `1` o `0`.
#
#   OLLAMA_GPU=auto (por defecto)  detecta GPU y soporte de Docker
#   OLLAMA_GPU=1                   fuerza GPU (falla al arrancar si no hay)
#   OLLAMA_GPU=0                   fuerza CPU
#
# El valor sale del entorno o, si no está, de `.env` en el directorio actual
# (la raíz del repo en local; /opt/carvajal en el VPS).
#
# Lo usan el Makefile (`make up`, `make prod-up`, `make prod-bootstrap`) y
# `deploy.sh` en el VPS, para que local y producción decidan igual.
#
# `auto` exige las dos piezas: el driver (`nvidia-smi` lista una GPU) y el
# soporte de Docker (NVIDIA Container Toolkit: spec CDI de NVIDIA, el hook en
# PATH o el runtime `nvidia` registrado en el daemon). Con solo una de ellas, Compose fallaría
# con "could not select device driver", así que se elige CPU.
set -uo pipefail

mode="${OLLAMA_GPU:-}"
if [[ -z "$mode" && -f .env ]]; then
  mode="$(sed -nE "s/^OLLAMA_GPU=[\"']?([^\"']*)[\"']?[[:space:]]*$/\1/p" .env | tail -n 1)"
fi
mode="${mode:-auto}"

case "$mode" in
  1) echo 1; exit 0 ;;
  0) echo 0; exit 0 ;;
  auto) ;;
  *)
    echo "OLLAMA_GPU debe ser auto, 1 o 0 (recibido: '${mode}')" >&2
    exit 2
    ;;
esac

if ! nvidia-smi -L >/dev/null 2>&1; then
  echo 0
  exit 0
fi

cdi_spec() {
  local dir
  for dir in ${GPU_DETECT_CDI_DIRS:-/etc/cdi /var/run/cdi}; do
    compgen -G "${dir}/nvidia*.y*ml" >/dev/null 2>&1 \
      || compgen -G "${dir}/nvidia*.json" >/dev/null 2>&1 \
      && return 0
  done
  return 1
}

# Docker ≥ 28 resuelve la reserva por CDI (spec que genera nvidia-ctk); las
# versiones anteriores usan el hook o el runtime `nvidia`.
if cdi_spec \
  || command -v nvidia-container-runtime-hook >/dev/null 2>&1 \
  || docker info --format '{{range $name, $_ := .Runtimes}}{{$name}} {{end}}' 2>/dev/null \
    | tr ' ' '\n' | grep -qx 'nvidia'; then
  echo 1
else
  echo 0
fi
