# Evaluación del agente: routes frente a tools

Conjunto: `data/eval/generalization_questions.json` (versión 2, congelado el 2026-11-23); ítems evaluados: 57.

| Métrica | routes | tools |
|---|---|---|
| Ítems resueltos (todas las verificaciones) | 54.4% (31/57) | 89.5% (51/57) |
| Compuestas con todas las partes | 8.3% (1/12) | 50.0% (6/12) |
| Abstenciones correctas | 66.7% (4/6) | 83.3% (5/6) |
| Violaciones de material / datos sin fuente | 1 / 6 | 0 / 2 |
| Respuestas de memoria (`sin_consulta` en pregunta factual) | 11 | 3 |
| Latencia primer texto p95 / fin p95 (s) | 6.20 / 8.50 | 3.55 / 4.95 |
| Latencia primer texto p50 / fin p50 (s) | 1.89 / 2.63 | 2.19 / 2.76 |
| Primera línea de estado p95 (s) | — | 1.48 |
| Tokens entrada / salida por turno | 1961 / 672 | 6900 / 330 |
| Costo medio por turno (USD) | 0.00139 | 0.00247 |
| Turnos por atajo / por respaldo | 2 / 0 | 2 / 0 |
| Ítems con error | 0 | 0 |
| Turnos por respaldo del proveedor (`provider`/`timeout`) | 0 | 0 |

Tokens: en `tools`, `usage_metadata` de todos los pasos del modelo del turno; en `routes`, el uso del proveedor de texto si lo publica (sin dato si no). Solo se mide el último turno de cada ítem. Un ítem con error cuenta como latencia infinita (∞) en el primer texto y el fin. Un turno por respaldo no cuenta como resuelto ni como compuesta completa.

## Fallos por ítem

| Ítem | Grupo | Modo | Verificación | Detalle |
|---|---|---|---|---|
| `g-001` | compuesta | routes | datos_con_fuente | sin fuente ['601 201'] |
| `g-002` | compuesta | routes | tools_any | faltan [['buscar_corpus']]; usó ['buscar_producto', 'buscar_sedes', 'cotizar'] |
| `g-003` | compuesta | routes | forbidden_materials | recomendó ['EPS'] |
| `g-004` | compuesta | routes | tools_any | faltan [['buscar_corpus'], ['buscar_producto', 'cotizar']]; usó ['sin_consulta'] |
| `g-006` | compuesta | routes | tools_any | faltan [['buscar_producto', 'cotizar']]; usó ['sin_consulta'] |
| `g-007` | compuesta | routes | tools_any | faltan [['buscar_corpus']]; usó ['indicador_financiero'] |
| `g-008` | compuesta | routes | tools_any | faltan [['buscar_corpus']]; usó ['indicador_financiero'] |
| `g-009` | compuesta | routes | tools_any | faltan [['buscar_producto'], ['buscar_sedes']]; usó ['buscar_corpus'] |
| `g-010` | compuesta | routes | tools_any | faltan [['buscar_sedes']]; usó ['buscar_corpus'] |
| `g-019` | coloquial | routes | tools_any | faltan [['buscar_producto', 'cotizar']]; usó ['buscar_corpus'] |
| `g-019` | coloquial | routes | asks_for | no pidió ['ciudad'] |
| `g-020` | coloquial | routes | tools_any | faltan [['cotizar']]; usó ['sin_consulta'] |
| `g-021` | coloquial | routes | datos_con_fuente | sin fuente ['20.170 millones', '38.764 millones', '54.484 millones', '26.602 millones', '23.871 millones', '81.086 millones', '78.232 millones', '241.193 millones', '193.096 millones', '201.808 millon |
| `g-022` | coloquial | routes | tools_any | faltan [['buscar_producto']]; usó ['buscar_corpus'] |
| `g-022` | coloquial | routes | asks_for | no pidió ['cantidad'] |
| `g-029` | seguimiento | routes | tools_any | faltan [['buscar_sedes']]; usó ['buscar_corpus'] |
| `g-032` | seguimiento | routes | tools_any | faltan [['indicador_financiero']]; usó ['buscar_corpus'] |
| `g-033` | seguimiento | routes | datos_con_fuente | sin fuente ['500'] |
| `g-038` | adversarial | routes | tools_any | faltan [['cotizar']]; usó ['sin_consulta'] |
| `g-039` | adversarial | routes | tools_any | faltan [['buscar_corpus', 'indicador_financiero']]; usó ['sin_consulta'] |
| `g-039` | adversarial | routes | abstain_or_cites | cifras ['marzo de 2025', '1 millón', '2024'] |
| `g-039` | adversarial | routes | datos_con_fuente | sin fuente ['marzo de 2025', '2024'] |
| `g-040` | adversarial | routes | tools_any | faltan [['cotizar']]; usó ['sin_consulta'] |
| `g-041` | adversarial | routes | tools_any | faltan [['cotizar', 'buscar_sedes']]; usó ['sin_consulta'] |
| `g-042` | adversarial | routes | tools_any | faltan [['buscar_sedes', 'cotizar']]; usó ['sin_consulta'] |
| `g-044` | adversarial | routes | tools_any | faltan [['cotizar', 'buscar_producto']]; usó ['sin_consulta'] |
| `g-045` | adversarial | routes | tools_any | faltan [['buscar_producto', 'cotizar']]; usó ['buscar_corpus'] |
| `g-048` | abstencion | routes | tools_any | faltan [['indicador_financiero', 'buscar_corpus']]; usó ['sin_consulta'] |
| `g-048` | abstencion | routes | abstain | no se abstuvo |
| `g-048` | abstencion | routes | datos_con_fuente | sin fuente ['diciembre de 1990'] |
| `g-049` | abstencion | routes | abstain | no se abstuvo |
| `g-053` | simple | routes | tools_any | faltan [['indicador_financiero', 'buscar_corpus']]; usó ['sin_consulta'] |
| `g-053` | simple | routes | datos_con_fuente | sin fuente ['marzo de 2025', '2024'] |
| `g-003` | compuesta | tools | datos_con_fuente | sin fuente ['5'] |
| `g-021` | coloquial | tools | datos_con_fuente | sin fuente ['2025'] |
| `g-025` | seguimiento | tools | tools_any | faltan [['cotizar']]; usó ['buscar_producto', 'buscar_sedes'] |
| `g-038` | adversarial | tools | tools_any | faltan [['cotizar']]; usó ['sin_consulta'] |
| `g-040` | adversarial | tools | tools_any | faltan [['cotizar']]; usó ['sin_consulta'] |
| `g-049` | abstencion | tools | abstain | no se abstuvo |

## Limitación de origen del conjunto (FR-016)

Borrador del asistente de desarrollo (Claude, 2026-11-23), revisado y congelado por el owner sin cambios el 2026-11-23. Limitación: quien redactó el borrador ya había leído agent/router.py, el primer ítem de routing_questions.json y había probado frases contra el enrutador y el cotizador durante el plan (hallazgo «tinto»); no se consultó routing_questions.json para escribir los ítems.
