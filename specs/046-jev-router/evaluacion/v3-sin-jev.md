# Evaluación del agente: routes frente a tools

Conjunto: `data/eval/generalization_v3.json` (versión 3, congelado el 2026-11-24); ítems evaluados: 40.

| Métrica | tools |
|---|---|
| Ítems resueltos (todas las verificaciones) | 72.5% (29/40) |
| Ítems resueltos con equivalencias de herramientas | 75.0% (30/40) |
| Compuestas con todas las partes | 50.0% (3/6) |
| Abstenciones correctas | 0.0% (0/3) |
| Violaciones de material / datos sin fuente | 0 / 1 |
| Respuestas de memoria (`sin_consulta` en pregunta factual) | 1 |
| Latencia primer texto p95 / fin p95 (s) | 4.19 / 5.30 |
| Latencia primer texto p50 / fin p50 (s) | 2.93 / 3.58 |
| Primera línea de estado p95 (s) | 1.86 |
| Tokens entrada / salida por turno | 18345 / 307 |
| Costo medio por turno (USD) | 0.00587 |
| Turnos por atajo / por respaldo | 0 / 0 |
| Ítems con error | 0 |
| Turnos por respaldo del proveedor (`provider`/`timeout`) | 0 |

Tokens: en `tools`, `usage_metadata` de todos los pasos del modelo del turno; en `routes`, el uso del proveedor de texto si lo publica (sin dato si no). Solo se mide el último turno de cada ítem. Un ítem con error cuenta como latencia infinita (∞) en el primer texto y el fin. Un turno por respaldo no cuenta como resuelto ni como compuesta completa.

## Gatekeeper y compuerta (spec 046)

| Métrica | tools |
|---|---|
| Llamadas a Jev (gatekeeper / compuerta) | 0 / 0 |
| Latencia por llamada p50 / p95 (ms) | — / — |
| Tokens de Jev entrada / salida | 0 / 0 |
| Estados de Jev | — |
| Decisiones por alcance | all 39, structured 1 |
| Fuente de la decisión | llm 39, reglas 1 |
| Turnos con decisión de Jev (sin reglas ni cuota) | 0.0% (0/39) |
| Turnos con cautela | 0 (—) |
| Estados de la compuerta | off 40 |
| Sustituciones de la compuerta | 0 (—) |
| Atajo por reglas / respuesta directa de Jev | 0 / 0 |
| Errores del gatekeeper (pregunta con datos a la directa) | ninguno |
| Turnos sin decisión por cuota (gatekeeper / compuerta) | 0 / 0 |

Los turnos sin decisión por la cuota de la corrida se declaran y no cuentan en el denominador de los turnos con decisión de Jev (SC-004). La respuesta directa de Jev no cuenta como atajo de las reglas.

## Fallos por ítem

| Ítem | Grupo | Modo | Verificación | Detalle |
|---|---|---|---|---|
| `v3-004` | compuesta | tools | tools_any | faltan [['buscar_corpus']]; usó ['empresas_del_grupo', 'indicador_financiero'] |
| `v3-004` | compuesta | tools | abstain_or_cites | cifras ['septiembre de 2025'] |
| `v3-006` | compuesta | tools | tools_any | faltan [['buscar_sedes']]; usó ['buscar_producto', 'ciudades_con_cobertura'] |
| `v3-017` | supuesto | tools | abstain_or_cites | cifras ['400', '16'] |
| `v3-019` | supuesto | tools | tools_any | faltan [['cotizar']]; usó ['buscar_producto'] |
| `v3-022` | seguimiento | tools | abstain_or_cites | cifras ['2.58'] |
| `v3-022` | seguimiento | tools | datos_con_fuente | sin fuente ['2.58'] |
| `v3-026` | seguimiento | tools | abstain_or_cites | cifras ['14'] |
| `v3-035` | abstencion | tools | abstain_or_cites | cifras ['50', '7'] |
| `v3-036` | abstencion | tools | abstain | no se abstuvo |
| `v3-037` | abstencion | tools | tools_any | faltan [['indicador_financiero', 'buscar_corpus']]; usó ['indicadores_disponibles'] |
| `v3-039` | abstencion | tools | abstain | no se abstuvo |
| `v3-040` | abstencion | tools | abstain | no se abstuvo |

## Limitación de origen del conjunto (FR-016)

redactado por tres agentes independientes que no vieron el prompt del agente, su código ni los conjuntos v1/v2; consolidado y validado contra el catálogo por un cuarto agente; congelado antes del ajuste de AGENT_SYSTEM
