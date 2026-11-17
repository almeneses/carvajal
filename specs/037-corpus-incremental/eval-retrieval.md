# Evaluación de retrieval con prioridad de capas (T047)

> Ejecutado el 2026-11-17 en local con Qdrant (`carvajal_chunks`) y Ollama
> (`nomic-embed-text`) disponibles.

## Qué se midió

`make index-eval` ejecuta `carvajal index-qdrant --layer all` y después
`scripts/eval_retrieval.py` sobre las 30 consultas etiquetadas de
`data/eval/retrieval_questions.json` (k=10). La evaluación usa el mismo
`LayerPriorityStore` que el chat, así que mide la política desplegada y no un
filtro plano que diluiría el núcleo.

- **Antes**: índice con la capa núcleo ya indexada (125 puntos).
- **Después**: corrida con `--layer all`, que indexa el núcleo y cada fuente
  extendida declarada en `data/external/*/gold`.

## Resultado

| Corrida | Puntos | Recall@10 | nDCG@10 |
|---|---:|---:|---:|
| Antes (`docs/eval/retrieval-037-antes.md`) | 125 | 0.8000 | 0.7791 |
| Después (`docs/eval/retrieval-report.md`) | 125 | 0.8000 | 0.7791 |

Las métricas del núcleo no empeoran: son idénticas.

## Bloqueo documentado

En este entorno no hay corpus extendido real que indexar: `data/external/alejandro`
solo contiene su `manifest.json` y ningún `gold/*.md`, así que la corrida
`--layer all` no añadió puntos. Por eso el "después" coincide con el "antes" y
los 125 puntos.

La comprobación con un corpus donante real (origen, categoría y entidad en el
front matter) queda pendiente de que exista ese corpus. Cuando lo haya, el mismo
comando `make index-eval` produce la comparación y la prioridad del chat
(`LayerPriorityStore`) ya está cubierta por `tests/test_retrieval.py`.
