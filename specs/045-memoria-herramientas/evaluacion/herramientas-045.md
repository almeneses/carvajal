# Evaluación del agente: routes frente a tools

Conjunto: `data/eval/herramientas_045.json` (versión 1, congelado el 2026-11-24); ítems evaluados: 26.

| Métrica | tools |
|---|---|
| Ítems resueltos (todas las verificaciones) | 96.2% (25/26) |
| Ítems resueltos con equivalencias de herramientas | 96.2% (25/26) |
| Compuestas con todas las partes | — |
| Abstenciones correctas | — |
| Violaciones de material / datos sin fuente | 0 / 0 |
| Respuestas de memoria (`sin_consulta` en pregunta factual) | 2 |
| Latencia primer texto p95 / fin p95 (s) | 3.25 / 3.88 |
| Latencia primer texto p50 / fin p50 (s) | 2.63 / 2.77 |
| Primera línea de estado p95 (s) | 1.61 |
| Tokens entrada / salida por turno | 16026 / 167 |
| Costo medio por turno (USD) | 0.00501 |
| Turnos por atajo / por respaldo | 1 / 0 |
| Ítems con error | 0 |
| Turnos por respaldo del proveedor (`provider`/`timeout`) | 0 |

Tokens: en `tools`, `usage_metadata` de todos los pasos del modelo del turno; en `routes`, el uso del proveedor de texto si lo publica (sin dato si no). Solo se mide el último turno de cada ítem. Un ítem con error cuenta como latencia infinita (∞) en el primer texto y el fin. Un turno por respaldo no cuenta como resuelto ni como compuesta completa.

## Fallos por ítem

| Ítem | Grupo | Modo | Verificación | Detalle |
|---|---|---|---|---|
| `h045-12` | herramienta | tools | tools_any | faltan [['consultar_cotizacion']]; usó ninguna |

## Limitación de origen del conjunto (FR-016)

redactado por el asistente de desarrollo (Claude Code) a partir del quickstart y de los contratos de la spec 045, antes de implementar las herramientas nuevas; no se ajusta después de ver resultados del agente
