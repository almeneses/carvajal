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
        "language": "es",
        "no_tool_allowed": false          # true solo si sin_consulta es aceptable
        # … más claves opcionales: ver la tabla de abajo
      }
    }
  ]
}
```

Claves de `expect` (todas opcionales; una ausente no se verifica):

| Clave | Verificación |
|---|---|
| `tools_any` | Cada grupo interno debe tener al menos una herramienta llamada en el turno. |
| `tools_none` | Ninguna de estas herramientas se llama. |
| `abstain` | La respuesta es la abstención declarada. |
| `abstain_or_cites` | Abstención, o toda cifra de la respuesta aparece en un resultado de herramienta. |
| `asks_for` | El turno pide al menos uno de estos datos (`needs_input` o `sin_consulta(pedir_dato)`). |
| `forbidden_materials` | Ninguna tarjeta ni opción recomendada tiene estos materiales. |
| `total_pending` | Toda cotización mostrada tiene el total ausente. |
| `max_quote_cards` | Número máximo de tarjetas de cotización. |
| `no_invented_terms` | No afirma plazos ni inventario sin evidencia vigente. |
| `no_currency_conversion` | No presenta precios convertidos entre monedas. |
| `shortcut` | El turno lo atiende el atajo, sin decisión del proveedor. |
| `language` | Idioma de la respuesta (`es` por defecto). |
| `no_tool_allowed` | `sin_consulta` es aceptable como única herramienta. |

Además, en todos los ítems: toda cifra, precio o fecha de la respuesta debe aparecer en un
resultado de herramienta del turno (SC-001).

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
| Respuestas de memoria (`sin_consulta` en pregunta factual) | n | n |
| Latencia primer texto p95 / fin p95 | s | s |
| Tokens entrada / salida por turno | n | n |
| Costo medio por turno | USD | USD |
| Turnos por atajo / por respaldo | n | n |

Incluye la tabla de fallos por ítem y, al pie, la limitación de origen del conjunto (FR-016).
