"""Agrega los logs structlog de producción (JSON por línea) en Markdown, sin texto de usuarios.

Uso (en tu terminal; nada se guarda en bruto si no quieres):
  ssh -p "$ENSAYO_APP_SSH_PORT" "$ENSAYO_APP_SSH" \
    "docker logs --since 2026-11-27T18:23:00Z carvajal-prod-app-1 2>&1" \
    | python3 -I agregar_logs.py web
  (y lo mismo con carvajal-prod-mcp-1 y «whatsapp»)

Solo cuenta metadatos: evento, nivel, ruta, herramienta, estado, milisegundos, p de la
compuerta, fuente del gatekeeper. Nunca imprime mensajes, correos ni teléfonos.
"""

import json
import sys
from collections import Counter, defaultdict

EVENTOS_CLAVE = (
    "cotizacion.registrada",
    "pedido.creado",
    "tool_agent.abstained",
    "tool_agent.limit_reached",
    "tool_agent.replaced",
    "claims.replaced_with_data",
    "tool_agent.card_imitation_stripped",
    "cotizacion.tapa_incompatible",
    "whatsapp.photo_rejected",
    "order_pdf.served",
    "voice.call",
    "mcp.unauthorized",
    "webhook.rejected",
)


def pct(valores, q):
    if not valores:
        return "-"
    valores = sorted(valores)
    return round(valores[min(len(valores) - 1, int(q * len(valores)))], 1)


def tabla(titulo, cabecera, filas):
    print(f"\n### {titulo}\n")
    print("| " + " | ".join(cabecera) + " |")
    print("|" + "---|" * len(cabecera))
    for fila in filas:
        print("| " + " | ".join(str(c) for c in fila) + " |")


def main():
    canal = sys.argv[1] if len(sys.argv) > 1 else "?"
    eventos, avisos = Counter(), Counter()
    herramientas, ms_herramienta = Counter(), defaultdict(list)
    rutas = defaultdict(list)
    compuerta, histograma, gatekeeper = Counter(), Counter(), Counter()
    primero = ultimo = None
    for linea in sys.stdin:
        try:
            e = json.loads(linea)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        ev, nivel = e.get("event", "?"), e.get("level", "?")
        ts = e.get("timestamp")
        primero, ultimo = primero or ts, ts or ultimo
        eventos[ev] += 1
        if nivel in {"warning", "error", "critical"}:
            avisos[(nivel, ev)] += 1
        if ev == "chat.turn_completed":
            rutas[e.get("route")].append(e.get("duration_ms") or 0)
        elif ev == "tool_agent.tool_called":
            herramientas[(e.get("tool"), e.get("status"))] += 1
            if isinstance(e.get("elapsed_ms"), (int, float)):
                ms_herramienta[e.get("tool")].append(e["elapsed_ms"])
        elif ev == "claims.judged":
            compuerta[(e.get("status"), e.get("substituted"))] += 1
            if isinstance(e.get("p"), (int, float)):
                histograma[min(9, int(e["p"] * 10))] += 1
        elif ev == "gatekeeper.decided":
            gatekeeper[e.get("source")] += 1
    turnos = sum(len(v) for v in rutas.values())
    print(
        f"## Canal {canal}: {primero} → {ultimo}; {turnos} turnos, {sum(eventos.values())} eventos"
    )
    tabla(
        "Latencia por ruta (ms)",
        ["ruta", "n", "p50", "p95", "máx"],
        [
            (r, len(v), pct(v, 0.5), pct(v, 0.95), max(v))
            for r, v in sorted(rutas.items(), key=lambda x: -len(x[1]))
        ],
    )
    tabla(
        "Herramientas",
        ["herramienta", "n", "p50 ms", "máx ms", "estados"],
        [
            (
                t,
                len(v),
                pct(v, 0.5),
                max(v),
                {s: n for (tt, s), n in herramientas.items() if tt == t},
            )
            for t, v in sorted(ms_herramienta.items(), key=lambda x: -len(x[1]))
        ],
    )
    tabla(
        "Compuerta de afirmaciones",
        ["estado", "sustituida", "n"],
        [(*k, n) for k, n in compuerta.most_common()],
    )
    tabla("Histograma de p", ["decil", "n"], sorted(histograma.items()))
    tabla("Gatekeeper por fuente", ["fuente", "n"], gatekeeper.most_common())
    tabla("Eventos clave", ["evento", "n"], [(k, eventos[k]) for k in EVENTOS_CLAVE])
    tabla("Avisos y errores", ["nivel", "evento", "n"], [(*k, n) for k, n in avisos.most_common()])


if __name__ == "__main__":
    main()
