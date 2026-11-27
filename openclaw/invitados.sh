#!/usr/bin/env bash
# Lista de números que el bot atiende en el grupo de WhatsApp (groupAllowFrom).
#
# El grupo está en groupPolicy=allowlist (2026-11-27): solo contesta a los números de la
# lista. Los invitados de la sustentación se conocen ese mismo día, así que se agregan en
# vivo con este script, que corre en la VM del gateway. OpenClaw aplica el cambio sin
# reiniciar.
#
#   invitados.sh agregar 3001234567 +57 300 765 4321   # 10 dígitos de Colombia o E.164
#   invitados.sh quitar +573001234567
#   invitados.sh listar                                # solo cuántos y los 4 últimos dígitos
#   invitados.sh fijar-base [--forzar]                 # antes de agregar: lista del equipo
#   invitados.sh restablecer                           # vuelve a la base (fin del evento)
set -euo pipefail

KEY=channels.whatsapp.groupAllowFrom
BASE=${INVITADOS_BASE:-$HOME/.openclaw/grupo-base.json}

uso() {
  sed -n '9,13p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

actual() { openclaw config get "$KEY"; }

# Aborta si el valor no es una lista no vacía de números E.164 (una base dañada o vacía
# dejaría al bot sin contestar en el grupo).
validar() {
  printf '%s' "$1" | python3 -c '
import json, re, sys
try:
    lista = json.load(sys.stdin)
except ValueError:
    sys.exit("La lista no es JSON válido; no se aplicó.")
if not isinstance(lista, list) or not lista or not all(
    isinstance(n, str) and re.fullmatch(r"\+[1-9]\d{7,14}", n) for n in lista
):
    sys.exit("La lista debe ser no vacía y solo de números E.164; no se aplicó.")
'
}

aplicar() {
  validar "$1"
  openclaw config set "$KEY" "$1" --strict-json >/dev/null
  echo "Listo. La lista del grupo quedó así:"
  listar
}

# Lee la lista actual por stdin y escribe la nueva en JSON.
editar() {
  python3 -c '
import json, re, sys

accion, numeros = sys.argv[1], sys.argv[2:]
lista = json.load(sys.stdin)

def normalizar(texto):
    n = re.sub(r"[\s().-]", "", texto)
    if re.fullmatch(r"3\d{9}", n):  # celular colombiano sin indicativo
        n = "+57" + n
    elif re.fullmatch(r"57\d{10}", n):
        n = "+" + n
    # Colombia: +57 y exactamente 10 dígitos nacionales; un dígito de menos daría un número
    # ajeno que pasaría por E.164.
    if not re.fullmatch(r"\+[1-9]\d{7,14}", n) or (n.startswith("+57") and len(n) != 13):
        sys.exit(f"Número inválido: {texto!r}. Usa 10 dígitos de Colombia o +indicativo.")
    return n

def completo(limpio):
    """Dígitos suficientes para cerrar un número escrito en partes."""

    digitos = limpio.lstrip("+")
    if digitos.startswith("57"):
        return len(digitos) >= 12  # 57 + 10 dígitos nacionales
    return len(digitos) >= 10


# Acepta un número escrito en varias partes («+57 300 123 4567»).
unidos, partes = [], []
for parte in numeros:
    partes.append(parte)
    limpio = re.sub(r"[\s().-]", "", "".join(partes))
    if completo(limpio):
        unidos.append("".join(partes))
        partes = []
if partes:
    resto = " ".join(partes)
    sys.exit(f"Número incompleto: {resto!r}")

for n in map(normalizar, unidos):
    if accion == "agregar" and n not in lista:
        lista.append(n)
    elif accion == "quitar":
        if n not in lista:
            sys.exit(f"No está en la lista: …{n[-4:]}")
        lista.remove(n)
if not lista:
    sys.exit("La lista no puede quedar vacía: el bot dejaría de contestar en el grupo.")
print(json.dumps(lista))
' "$@"
}

listar() {
  actual | python3 -c '
import json, sys
lista = json.load(sys.stdin)
print(f"{len(lista)} número(s): " + ", ".join("…" + n[-4:] for n in lista))
'
}

accion=${1:-}
[ $# -gt 0 ] && shift
case "$accion" in
  agregar | quitar)
    [ $# -gt 0 ] || uso
    if [ "$accion" = agregar ] && [ ! -f "$BASE" ]; then
      # Sin base, «restablecer» no tendría a qué volver y los invitados se quedarían.
      echo "No hay base en $BASE: corre fijar-base con la lista del equipo antes de agregar." >&2
      exit 1
    fi
    nueva=$(actual | editar "$accion" "$@")
    aplicar "$nueva"
    ;;
  listar) listar ;;
  fijar-base)
    lista=$(actual)
    validar "$lista"
    if [ -f "$BASE" ] && [ "${1:-}" != --forzar ]; then
      # Con invitados en la lista, la nueva base los dejaría tras «restablecer».
      extra=$(printf '%s' "$lista" | python3 -c '
import json, sys
base = json.load(open(sys.argv[1]))
print(len([n for n in json.load(sys.stdin) if n not in base]))
' "$BASE")
      if [ "$extra" != 0 ]; then
        echo "La lista tiene $extra número(s) que no están en la base (¿invitados?)." >&2
        echo "Corre restablecer antes, o fijar-base --forzar si de verdad son del equipo." >&2
        exit 1
      fi
    fi
    printf '%s\n' "$lista" >"$BASE"
    chmod 600 "$BASE"
    echo "Base guardada en $BASE"
    listar
    ;;
  restablecer)
    [ -f "$BASE" ] || { echo "No hay base en $BASE (corre fijar-base antes del evento)." >&2; exit 1; }
    aplicar "$(cat "$BASE")"
    ;;
  *) uso ;;
esac
