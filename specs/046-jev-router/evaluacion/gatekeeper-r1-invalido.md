# Evaluación del gatekeeper (spec 046)

Conjunto: `data/eval/gatekeeper_046.json` (congelado el 2026-11-24); umbrales vigentes 0,6 y 0,8.

Humo (`GET /v1/models`): ok, 288 ms, modelo en la lista: no.

| Juez | Exactitud | Sin llamada | Fallos |
|---|---|---|---|
| rules | 70.0% (14/20) | sin llamada por cuota: 0 | 0 |
| llm | 90.0% (18/20) | sin llamada por cuota: 0 | 0 |
| jev | — | sin llamada por cuota: 0 | 20 |

## Por pregunta

| Ítem | Esperada | rules | llm | jev |
|---|---|---|---|---|
| `gk-01` | herramienta_estructurada | ✔ herramienta_estructurada | ✔ herramienta_estructurada | invalid |
| `gk-02` | herramienta_estructurada | ✔ herramienta_estructurada | ✔ herramienta_estructurada | invalid |
| `gk-03` | herramienta_estructurada | ✔ herramienta_estructurada | ✔ herramienta_estructurada | invalid |
| `gk-04` | herramienta_estructurada | ✔ herramienta_estructurada | ✔ herramienta_estructurada | circuit_open |
| `gk-05` | herramienta_estructurada | ✔ herramienta_estructurada | ✔ herramienta_estructurada | circuit_open |
| `gk-06` | herramienta_estructurada | ✔ herramienta_estructurada | ✔ herramienta_estructurada | circuit_open |
| `gk-07` | herramienta_estructurada | ✘ mixta | ✘ mixta | circuit_open |
| `gk-08` | rag | ✔ rag | ✔ rag | circuit_open |
| `gk-09` | rag | ✔ rag | ✔ rag | circuit_open |
| `gk-10` | rag | ✔ rag | ✔ rag | circuit_open |
| `gk-11` | rag | ✔ rag | ✔ rag | circuit_open |
| `gk-12` | rag | ✔ rag | ✔ rag | circuit_open |
| `gk-13` | rag | ✔ rag | ✔ rag | circuit_open |
| `gk-14` | mixta | ✔ mixta | ✔ mixta | circuit_open |
| `gk-15` | mixta | ✔ mixta | ✔ mixta | circuit_open |
| `gk-16` | respuesta_directa | ✘ rag | ✔ respuesta_directa | circuit_open |
| `gk-17` | respuesta_directa | ✘ rag | ✘ rag | circuit_open |
| `gk-18` | respuesta_directa | ✘ rag | ✔ respuesta_directa | circuit_open |
| `gk-19` | respuesta_directa | ✘ rag | ✔ respuesta_directa | circuit_open |
| `gk-20` | respuesta_directa | ✘ rag | ✔ respuesta_directa | circuit_open |

## Origen del conjunto

15 preguntas de routing_questions.json (ids de origen en «source») y 5 redactadas por el asistente de desarrollo (campo «origin»)
