# Simulación de conversaciones (spec 044)

- **Escenarios:** data/eval/simulacion_escenarios_r3.json
- **Fecha (UTC):** 20261124T130252Z
- **Modo del agente:** tools (AGENT_MODE=tools)
- **Modelo:** deepseek-flash
- **Concurrencia:** 6
- **Transcripciones:** `transcripts.jsonl` en el directorio temporal de la corrida (no versionado)

## Agregados

| Métrica | Valor |
| --- | --- |
| Conversaciones | 12 (max_turns: 12) |
| Turnos | 32 |
| Caminos | agent: 32 |
| Respaldo (path=fallback) | 0 (0.0 %) |
| Turnos con datos sin fuente | 1 (3.1 %; 1 cifras) |
| Turnos con materiales no aptos | 0 (de 8 con tarjetas y uso inferido) |
| Abstenciones | 0 (0.0 %) |
| Errores | 0 turnos; 0 conversaciones |
| Turnos no persistidos como completos | 0 |
| Folios emitidos | 2 |

## Latencias

| Medida | p50 | p95 |
| --- | --- | --- |
| Primera línea de estado | 1.58 s | 2.03 s |
| Primer texto | 3.13 s | 4.71 s |
| Total del turno | 3.65 s | 6.61 s |

## Tokens y costo estimado

| Rol | Entrada | Salida | Costo |
| --- | --- | --- | --- |
| Asistente | 624141 | 11104 | 0.2006 USD |
| Cliente simulado | 14206 | 3443 | 0.0084 USD |
| **Total** | | | 0.2090 USD |

Precios: 0.3 USD/M de entrada, 1.2 USD/M de salida.

## Conversaciones

| Escenario | Turnos | Cierre | Respaldo | Sin fuente | No aptos | Errores | Juez |
| --- | --- | --- | --- | --- | --- | --- | --- |
| s-r3-01 | 3 | max_turns | 0 | 0 | 0 | 0 | pendiente |
| s-r3-02 | 2 | max_turns | 0 | 0 | 0 | 0 | pendiente |
| s-r3-03 | 3 | max_turns | 0 | 0 | 0 | 0 | pendiente |
| s-r3-04 | 3 | max_turns | 0 | 0 | 0 | 0 | pendiente |
| s-r3-05 | 2 | max_turns | 0 | 0 | 0 | 0 | pendiente |
| s-r3-06 | 3 | max_turns | 0 | 0 | 0 | 0 | pendiente |
| s-r3-07 | 2 | max_turns | 0 | 0 | 0 | 0 | pendiente |
| s-r3-08 | 2 | max_turns | 0 | 0 | 0 | 0 | pendiente |
| s-r3-09 | 3 | max_turns | 0 | 1 | 0 | 0 | pendiente |
| s-r3-10 | 3 | max_turns | 0 | 0 | 0 | 0 | pendiente |
| s-r3-11 | 3 | max_turns | 0 | 0 | 0 | 0 | pendiente |
| s-r3-12 | 3 | max_turns | 0 | 0 | 0 | 0 | pendiente |

## Hallazgos por turno

- `s-r3-09` turno 2: sin fuente ['83 mil millones']

> La utilidad de las respuestas no se mide aquí: el campo `judge` de cada transcripción queda para el veredicto de los revisores.
