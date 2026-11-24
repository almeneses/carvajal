# Evaluación del gatekeeper (spec 046)

Conjunto: `data/eval/gatekeeper_046.json` (congelado el 2026-11-24); umbrales vigentes 0,6 y 0,8.

| Juez | Exactitud | Sin llamada | Fallos |
|---|---|---|---|
| rules | 70.0% (14/20) | sin llamada por cuota: 0 | 0 |
| llm | 90.0% (18/20) | sin llamada por cuota: 0 | 0 |
| jev | 100.0% (20/20) | sin llamada por cuota: 0 | 0 |

## Rejilla de umbrales (Jev)

| Umbral | Aplicadas | Exactitud de las aplicadas | Cobertura | Datos → directa |
|---|---|---|---|---|
| 0,5 | 20 | 100.0% (20/20) | 1.00 | 0 |
| 0,6 | 19 | 100.0% (19/19) | 0.95 | 0 |
| 0,7 | 19 | 100.0% (19/19) | 0.95 | 0 |
| 0,8 | 19 | 100.0% (19/19) | 0.95 | 0 |

Regla de research R4: `JEV_ROUTER_MIN_CONFIDENCE` = 0,6, `JEV_ROUTER_DIRECT_MIN_CONFIDENCE` = 0,6 (la rejilla no separa: se queda el valor vigente). Con n = 20 el riesgo de sobreajuste es alto.

Con los umbrales vigentes: aplicadas 100.0% (20/20); cautela en ninguna; respuesta directa en gk-16, gk-17, gk-18, gk-19, gk-20; preguntas con datos a la respuesta directa: ninguna.

## Confianza frente a Score (Jev)

| | Aciertos | Errores |
|---|---|---|
| Ítems | 20 | 0 |
| Confianza media de Choice | 0.95 | — |
| Claridad media de Score (de 0 a 4) | 2.52 | — |

## Por pregunta

| Ítem | Esperada | rules | llm | jev |
|---|---|---|---|---|
| `gk-01` | herramienta_estructurada | ✔ herramienta_estructurada | ✔ herramienta_estructurada | ✔ herramienta_estructurada 0.99 · claridad 2.52 → structured |
| `gk-02` | herramienta_estructurada | ✔ herramienta_estructurada | ✔ herramienta_estructurada | ✔ herramienta_estructurada 0.93 · claridad 2.6 → structured |
| `gk-03` | herramienta_estructurada | ✔ herramienta_estructurada | ✔ herramienta_estructurada | ✔ herramienta_estructurada 1.00 · claridad 2.18 → structured |
| `gk-04` | herramienta_estructurada | ✔ herramienta_estructurada | ✔ herramienta_estructurada | ✔ herramienta_estructurada 0.99 · claridad 3.0 → structured |
| `gk-05` | herramienta_estructurada | ✔ herramienta_estructurada | ✔ herramienta_estructurada | ✔ herramienta_estructurada 1.00 · claridad 3.34 → structured |
| `gk-06` | herramienta_estructurada | ✔ herramienta_estructurada | ✔ herramienta_estructurada | ✔ herramienta_estructurada 0.99 · claridad 2.63 → structured |
| `gk-07` | herramienta_estructurada | ✘ mixta | ✘ mixta | ✔ herramienta_estructurada 0.93 · claridad 1.72 → all |
| `gk-08` | rag | ✔ rag | ✔ rag | ✔ rag 1.00 · claridad 2.03 → rag |
| `gk-09` | rag | ✔ rag | ✔ rag | ✔ rag 0.92 · claridad 2.39 → rag |
| `gk-10` | rag | ✔ rag | ✔ rag | ✔ rag 0.99 · claridad 2.03 → rag |
| `gk-11` | rag | ✔ rag | ✔ rag | ✔ rag 0.84 · claridad 3.24 → rag |
| `gk-12` | rag | ✔ rag | ✔ rag | ✔ rag 1.00 · claridad 2.2 → rag |
| `gk-13` | rag | ✔ rag | ✔ rag | ✔ rag 1.00 · claridad 2.15 → rag |
| `gk-14` | mixta | ✔ mixta | ✔ mixta | ✔ mixta 0.59 · claridad 1.47 → all |
| `gk-15` | mixta | ✔ mixta | ✔ mixta | ✔ mixta 0.91 · claridad 1.16 → all |
| `gk-16` | respuesta_directa | ✘ rag | ✔ respuesta_directa | ✔ respuesta_directa 1.00 · claridad 3.84 → direct |
| `gk-17` | respuesta_directa | ✘ rag | ✘ rag | ✔ respuesta_directa 1.00 · claridad 2.3 → direct |
| `gk-18` | respuesta_directa | ✘ rag | ✔ respuesta_directa | ✔ respuesta_directa 1.00 · claridad 3.04 → direct |
| `gk-19` | respuesta_directa | ✘ rag | ✔ respuesta_directa | ✔ respuesta_directa 0.95 · claridad 3.82 → direct |
| `gk-20` | respuesta_directa | ✘ rag | ✔ respuesta_directa | ✔ respuesta_directa 1.00 · claridad 2.73 → direct |

## Origen del conjunto

15 preguntas de routing_questions.json (ids de origen en «source») y 5 redactadas por el asistente de desarrollo (campo «origin»)
