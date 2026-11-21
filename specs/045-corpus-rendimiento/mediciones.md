# Mediciones de la fase 045: antes y después

Este informe compare la línea base (antes de la implementación) con el resultado de la
refactorización, con **el mismo comando, el mismo corpus y los mismos servicios**. No se
declara ninguna mejora sin el número correspondiente.

## Comando y entorno

```bash
export UV_CACHE_DIR=/home/alejo/projects/DataScience/tecnicas_avanzadas/carvajal/.uv-cache
env -u OLLAMA_MODEL uv run python scripts/medir_corpus_045.py \
  --label antes \
  --json specs/045-corpus-rendimiento/medicion-antes.json
```

- **Corpus**: `data/gold` del worktree (24 documentos, 125 fragmentos core).
- **PostgreSQL**: contenedor local `carvajal-db` en `127.0.0.1:5433`; el script crea la base
  temporal `carvajal_045_medicion` con migraciones Alembic y la elimina al terminar.
- **Qdrant**: `127.0.0.1:6333`; el script usa la colección temporal
  `carvajal_045_medicion` y la elimina al terminar. Tampoco toca la base ni la colección de
  desarrollo (`carvajal`) ni producción.
- **Embedder**: para los escenarios de sincronización, un embedder determinista de 768
  dimensiones que cuenta llamadas (no altera el modelo ni los vectores de producción). Para el
  costo de embeddings, `nomic-embed-text` real servido por Ollama local.
- **Fecha**: 2026-11-20. **Máquina**: estación de desarrollo compartida; los tiempos son de
  pared y varían con la carga.
- Se ejecutó **tres veces** el mismo comando: los **conteos son deterministas** (idénticos en las
  tres corridas) y los **tiempos** de la tabla son la mediana de las tres. El JSON versionado es
  una corrida representativa.

### Qué cuenta el script

| Métrica | Cómo se cuenta |
|---|---|
| Pasadas sobre Gold | invocaciones de `load_document` divididas por documentos de la capa (una pasada lee cada Markdown una vez) |
| Pasadas sobre el índice | invocaciones de `QdrantVectorStore._payloads` por capa (una pasada lógica; las páginas internas del cliente se cuentan aparte) |
| Páginas de scroll | llamadas al `scroll` del cliente Qdrant |
| Llamadas al embedder | llamadas a `Embedder.embed` en la corrida |

## Línea base (antes)

Corrida representativa: [`medicion-antes.json`](./medicion-antes.json).

| Escenario | Tiempo (ms) | Pasadas Gold | Pasadas índice | Páginas scroll | Llamadas embedder |
|---|---|---:|---:|---:|---:|
| `sync` inicial (todo nuevo, con poda) | 446 | **4** (96 lecturas / 24 documentos) | 2 | 2 | 125 |
| `sync` sin cambios **con poda** | 87 | **4** (96) | 2 | 3 | 0 |
| `sync` sin cambios **sin poda** | 21 | **2** (48) | 1 | 2 | 0 |
| `sync --verify` | 16 | 1 (24) | **2** | 2 | 0 |

Notas de la línea base:

- Las **cuatro pasadas** del primer `sync` son: huellas del servicio, `migrate_gold` de
  PostgreSQL, `index_gold` de Qdrant y `gold_hashes` para la poda del índice.
- El `sync` sin cambios **con poda** vuelve a importar la capa (la poda es autoritativa) y vuelve
  a leer Gold para la poda del índice: otras cuatro pasadas.
- El `sync` sin cambios **sin poda** hace dos pasadas: la de huellas y una relectura del indexador
  porque el documento vacío (`sostenibilidad`, sin fragmentos) nunca publica huella en Qdrant y
  siempre se considera pendiente. Es un comportamiento preexistente que esta fase **no cambia**.
- La verificación recorre el índice **dos veces** por capa: una para huellas y otra para IDs.
- La verificación reporta 2 diferencias preexistentes para el documento vacío
  (`sostenibilidad: falta en Qdrant.` y el conteo de Qdrant 23 vs Gold 24). Es el mismo
  diagnóstico que debe salir después; no forma parte de esta fase corregirlo.
- Micro-banco del índice (colección sintética de 5 000 puntos, 500 documentos, 40 páginas por
  pasada): **107,6 ms** para dos pasadas (mediana). Es la referencia para extrapolar el costo
  del segundo recorrido de la verificación.

### Costo de pedir embeddings por fragmento

Medido con `nomic-embed-text` real y los 125 fragmentos del corpus local, con el mismo texto que
vectoriza el índice (`título · sección. ficha\nfragmento`):

| Modalidad | Total (ms) | Media por fragmento (ms) | p50 (ms) | p95 (ms) |
|---|---:|---:|---:|---:|
| Una llamada por fragmento (código actual) | 23 541 | 188,32 | 205,91 | 269,12 |
| Lote con el mismo modelo (alternativa medida, no implementada) | 23 205 | 185,64 | — | — |

**Decisión**: no se cambia la forma de pedir embeddings. El lote no mejora el costo medido
(~1,4 %), y el costo real de un `sync` incremental ya está acotado por el atajo de metadatos: solo
se reembeben documentos cuyo texto vectorizado cambió. Cambiar la interfaz del embedder añadiría
superficie sin una ganancia medida. Queda documentado como costo aceptado.

## Después de la implementación

_(Se completa con la misma corrida una vez implementada la fase; el JSON queda en
`medicion-despues.json`.)_
