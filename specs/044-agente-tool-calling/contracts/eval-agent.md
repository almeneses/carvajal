# Contrato — Evaluación comparativa del agente

## Conjunto de generalización: `data/eval/generalization_questions.json`

```text
{
  "version": 1,
  "frozen_at": "YYYY-MM-DD",             # lo pone el owner al congelar
  "origin": "borrador del asistente de desarrollo, revisado por el owner",
  "items": [
    {
      "id": "g-001",
      "group": "compuesta" | "coloquial" | "seguimiento" | "adversarial" | "abstencion" | "simple",
      "turns": ["pregunta 1", "pregunta 2", ...],   # el último es el evaluado
      "expect": {
        "tools_any":   [["cotizar"], ["buscar_sedes"]],   # cada grupo debe aparecer
        "tools_none":  ["indicador_financiero"],
        "abstain":     false,
        "asks_for":    ["temperatura"],                   # needs_input esperado
        "forbidden_materials": ["EPS"],
        "total_pending": true,
        "language": "es"
      }
    }
  ]
}
```

Reglas: ≥ 50 ítems; ≥ 10 compuestos, ≥ 10 coloquiales, ≥ 10 de seguimiento, ≥ 8
adversariales, ≥ 5 de abstención. Redactado sin consultar `routing_questions.json` ni las
reglas del enrutador. Congelado = no se edita; una corrección crea `version + 1`.

## Ejecución

```bash
uv run carvajal eval-agent --mode routes --mode tools \
  --questions data/eval/generalization_questions.json --output "$OUT/agent.md"
uv run carvajal eval-agent --mode tools --questions data/eval/routing_questions.json --output "$OUT/agent-routing.md"
```

- Turnos completos en proceso, persistencia en memoria, mismo corpus y catálogo.
- `--langsmith` registra el experimento (opcional, red externa).
- En modo `routes`, las herramientas esperadas se mapean desde la ruta:
  `rag → buscar_corpus`, `tool → indicador_financiero`, `commercial → buscar_producto |
  cotizar | buscar_sedes`.

## Informe

| Métrica | routes | tools |
|---|---|---|
| Ítems resueltos (todas las verificaciones) | % | % |
| Compuestas con todas las partes | % | % |
| Abstenciones correctas | % | % |
| Violaciones de material / datos sin fuente | n | n |
| Latencia primer texto p95 / fin p95 | s | s |
| Tokens entrada / salida por turno | n | n |
| Costo medio por turno | USD | USD |
| Turnos por atajo / por respaldo | n | n |

Incluye la tabla de fallos por ítem y, al pie, la limitación de origen del conjunto (FR-016).
