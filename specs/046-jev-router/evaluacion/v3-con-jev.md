# Evaluación del agente: routes frente a tools

Conjunto: `data/eval/generalization_v3.json` (versión 3, congelado el 2026-11-24); ítems evaluados: 40.

| Métrica | tools |
|---|---|
| Ítems resueltos (todas las verificaciones) | 70.0% (28/40) |
| Ítems resueltos con equivalencias de herramientas | 70.0% (28/40) |
| Compuestas con todas las partes | 66.7% (4/6) |
| Abstenciones correctas | 33.3% (1/3) |
| Violaciones de material / datos sin fuente | 0 / 1 |
| Respuestas de memoria (`sin_consulta` en pregunta factual) | 2 |
| Latencia primer texto p95 / fin p95 (s) | 5.84 / 5.84 |
| Latencia primer texto p50 / fin p50 (s) | 3.68 / 3.68 |
| Primera línea de estado p95 (s) | 3.01 |
| Tokens entrada / salida por turno | 16841 / 314 |
| Costo medio por turno (USD) | 0.00543 |
| Turnos por atajo / por respaldo | 0 / 0 |
| Ítems con error | 0 |
| Turnos por respaldo del proveedor (`provider`/`timeout`) | 0 |

Tokens: en `tools`, `usage_metadata` de todos los pasos del modelo del turno; en `routes`, el uso del proveedor de texto si lo publica (sin dato si no). Solo se mide el último turno de cada ítem. Un ítem con error cuenta como latencia infinita (∞) en el primer texto y el fin. Un turno por respaldo no cuenta como resuelto ni como compuesta completa.

## Gatekeeper y compuerta (spec 046)

| Métrica | tools |
|---|---|
| Llamadas a Jev (gatekeeper / compuerta) | 35 / 22 |
| Latencia por llamada p50 / p95 (ms) | 149 / 262 |
| Tokens de Jev entrada / salida | 61227 / 2593 |
| Estados de Jev | ok 57 |
| Decisiones por alcance | all 8, rag 2, structured 30 |
| Fuente de la decisión | jev 35, llm 4, reglas 1 |
| Turnos con decisión de Jev (sin reglas ni cuota) | 100.0% (35/35) |
| Turnos con cautela | 3 (v3-023, v3-024, v3-025) |
| Estados de la compuerta | skipped 8, supported 19, unavailable 10, unsupported 3 |
| Sustituciones de la compuerta | 3 (v3-005, v3-021, v3-027) |
| Atajo por reglas / respuesta directa de Jev | 0 / 0 |
| Errores del gatekeeper (pregunta con datos a la directa) | ninguno |
| Turnos sin decisión por cuota (gatekeeper / compuerta) | 4 / 10 |

Los turnos sin decisión por la cuota de la corrida se declaran y no cuentan en el denominador de los turnos con decisión de Jev (SC-004). La respuesta directa de Jev no cuenta como atajo de las reglas.

## Fallos por ítem

| Ítem | Grupo | Modo | Verificación | Detalle |
|---|---|---|---|---|
| `v3-003` | compuesta | tools | tools_any | faltan [['buscar_producto']]; usó ['buscar_corpus', 'empresas_del_grupo'] |
| `v3-004` | compuesta | tools | tools_any | faltan [['buscar_corpus']]; usó ['empresas_del_grupo', 'indicador_financiero'] |
| `v3-004` | compuesta | tools | abstain_or_cites | cifras ['septiembre de 2025'] |
| `v3-012` | precio | tools | abstain_or_cites | cifras ['32'] |
| `v3-017` | supuesto | tools | abstain_or_cites | cifras ['400'] |
| `v3-019` | supuesto | tools | tools_any | faltan [['cotizar']]; usó ['buscar_producto', 'ficha_producto'] |
| `v3-022` | seguimiento | tools | abstain_or_cites | cifras ['September 2025', '2.58'] |
| `v3-022` | seguimiento | tools | datos_con_fuente | sin fuente ['2.58'] |
| `v3-026` | seguimiento | tools | abstain_or_cites | cifras ['14'] |
| `v3-035` | abstencion | tools | abstain_or_cites | cifras ['50', '7'] |
| `v3-036` | abstencion | tools | language | idioma es |
| `v3-037` | abstencion | tools | tools_any | faltan [['indicador_financiero', 'buscar_corpus']]; usó ['indicadores_disponibles'] |
| `v3-039` | abstencion | tools | abstain | no se abstuvo |
| `v3-040` | abstencion | tools | abstain | no se abstuvo |

## Limitación de origen del conjunto (FR-016)

redactado por tres agentes independientes que no vieron el prompt del agente, su código ni los conjuntos v1/v2; consolidado y validado contra el catálogo por un cuarto agente; congelado antes del ajuste de AGENT_SYSTEM
