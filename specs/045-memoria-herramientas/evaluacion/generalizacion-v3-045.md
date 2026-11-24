# Evaluación del agente: routes frente a tools

Conjunto: `data/eval/generalization_v3.json` (versión 3, congelado el 2026-11-24); ítems evaluados: 40.

| Métrica | tools |
|---|---|
| Ítems resueltos (todas las verificaciones) | 67.5% (27/40) |
| Ítems resueltos con equivalencias de herramientas | 67.5% (27/40) |
| Compuestas con todas las partes | 83.3% (5/6) |
| Abstenciones correctas | 33.3% (1/3) |
| Violaciones de material / datos sin fuente | 0 / 2 |
| Respuestas de memoria (`sin_consulta` en pregunta factual) | 2 |
| Latencia primer texto p95 / fin p95 (s) | 4.90 / 6.80 |
| Latencia primer texto p50 / fin p50 (s) | 3.31 / 4.06 |
| Primera línea de estado p95 (s) | 2.76 |
| Tokens entrada / salida por turno | 19232 / 329 |
| Costo medio por turno (USD) | 0.00616 |
| Turnos por atajo / por respaldo | 0 / 0 |
| Ítems con error | 0 |
| Turnos por respaldo del proveedor (`provider`/`timeout`) | 0 |

Tokens: en `tools`, `usage_metadata` de todos los pasos del modelo del turno; en `routes`, el uso del proveedor de texto si lo publica (sin dato si no). Solo se mide el último turno de cada ítem. Un ítem con error cuenta como latencia infinita (∞) en el primer texto y el fin. Un turno por respaldo no cuenta como resuelto ni como compuesta completa.

## Fallos por ítem

| Ítem | Grupo | Modo | Verificación | Detalle |
|---|---|---|---|---|
| `v3-004` | compuesta | tools | tools_any | faltan [['buscar_corpus']]; usó ['empresas_del_grupo', 'indicador_financiero'] |
| `v3-004` | compuesta | tools | abstain_or_cites | cifras ['septiembre de 2025'] |
| `v3-009` | precio | tools | abstain_or_cites | cifras ['2', '4'] |
| `v3-017` | supuesto | tools | abstain_or_cites | cifras ['400', '16'] |
| `v3-019` | supuesto | tools | tools_any | faltan [['cotizar']]; usó ['buscar_producto', 'ficha_producto'] |
| `v3-021` | seguimiento | tools | abstain_or_cites | cifras ['9'] |
| `v3-022` | seguimiento | tools | abstain_or_cites | cifras ['September 2025', '2.58'] |
| `v3-022` | seguimiento | tools | datos_con_fuente | sin fuente ['2.58'] |
| `v3-025` | seguimiento | tools | abstain_or_cites | cifras ['3'] |
| `v3-026` | seguimiento | tools | abstain_or_cites | cifras ['14'] |
| `v3-032` | adversarial | tools | abstain_or_cites | cifras ['30%'] |
| `v3-035` | abstencion | tools | abstain_or_cites | cifras ['50', '7'] |
| `v3-036` | abstencion | tools | language | idioma es |
| `v3-039` | abstencion | tools | abstain | no se abstuvo |
| `v3-040` | abstencion | tools | abstain | no se abstuvo |
| `v3-040` | abstencion | tools | datos_con_fuente | sin fuente ['diciembre de 2024'] |

## Limitación de origen del conjunto (FR-016)

redactado por tres agentes independientes que no vieron el prompt del agente, su código ni los conjuntos v1/v2; consolidado y validado contra el catálogo por un cuarto agente; congelado antes del ajuste de AGENT_SYSTEM
