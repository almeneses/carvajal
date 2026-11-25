# Evidencia: evaluación del agente (spec 053, T017)

Corrida el 2026-11-25 con permiso del owner (tokens de DeepSeek). El cambio toca el prompt
del agente (`prompts.py`: propuesta y elección) y el servicio de chat; la búsqueda en el
corpus y el índice no cambian.

## Evaluaciones locales

| Evaluación | Resultado |
|---|---|
| Enrutador (`carvajal eval-router`, 130 preguntas) | Ruta, intención y datos 100 %; 0 violaciones de material |
| Cotizador (`make eval-cotizador`) | 18/18 casos OK |
| Retrieval | No se corre: la rama no toca retrieval ni el índice |

## Agente `tools` frente a `main` (conjunto de generalización, 57 ítems)

`uv run carvajal eval-agent --mode tools --questions data/eval/generalization_questions.json
--price-input 0.30 --price-output 1.20` en esta rama y en un worktree de `main`, con el
mismo índice, embedder y catálogo.

| Métrica | `main` | Rama (1.ª versión del prompt) | Rama (final) |
|---|---|---|---|
| Ítems resueltos | 86,0 % (49/57) | 86,0 % (49/57) | 86,0 % (49/57) |
| Resueltos con equivalencias | 89,5 % (51/57) | 87,7 % (50/57) | 89,5 % (51/57) |
| Compuestas con todas las partes | 50,0 % (6/12) | 50,0 % | 50,0 % |
| Abstenciones correctas | 66,7 % (4/6) | 66,7 % | 66,7 % |
| Violaciones de material / datos sin fuente | 0 / 0 | 0 / 1 | **0 / 0** |
| Primer texto p95 / fin p95 (s) | 5,04 / 5,93 | 4,63 / 5,96 | 4,24 / 5,12 |
| Costo medio por turno (USD) | 0,00600 | 0,00589 | 0,00593 |

**Hallazgo y corrección.** La primera redacción de la regla («lo que devuelve cotizar es una
propuesta: preséntala y pregunta si la confirma») volvió al agente más cauto para llamar a
`cotizar` con datos ambiguos: en 3 repeticiones de los ítems que cambiaron, g-024 («vasos
para café, 300, Bogotá») y g-033 fallaron 2/3 en la rama y 0/3 en `main` (el «300» se leyó
como capacidad y se repitió el «200 oz» del error de la herramienta). La regla final dice
que llamar a `cotizar` no registra nada y que se usa como siempre; con ella, en 3
repeticiones, g-024 3/3, g-033 2/3, g-029 3/3 (en `main` 0/3) y 0 datos sin fuente.

Los fallos que quedan (g-008, g-019, g-030, g-039, g-041, g-048, g-049) son los conocidos de
`main` (`specs/044-agente-tool-calling/evidencia.md` §10, riesgos aceptados SC-004/SC-005) y
varían entre corridas por la no determinación del modelo.

**Limitación.** El conjunto no incluye foto, elección ni confirmación: mide que el cambio
del prompt no empeore el resto del agente. La elección y la confirmación las cubren las
pruebas (`tests/test_followup.py`, `tests/test_foto_cotizacion.py`). Limitación de origen
del conjunto (FR-016 de la 044): borrador del asistente de desarrollo, revisado y congelado
por el owner el 2026-11-23.
