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

## Después

Mismo comando, mismo entorno y mismo corpus, sobre la rama `perf/045-corpus-rendimiento`.
Corrida representativa: [`medicion-despues.json`](./medicion-despues.json), también con
tres ejecuciones y medianas de tiempo.

### Conteos (deterministas)

| Escenario | Pasadas Gold antes | Pasadas Gold después | Pasadas índice antes | Pasadas índice después | Páginas scroll antes | Páginas scroll después |
|---|---:|---:|---:|---:|---:|---:|
| `sync` inicial (todo nuevo, con poda) | 4 (96 lecturas) | **1** (24) | 2 | 2 | 2 | 2 |
| `sync` sin cambios **con poda** | 4 (96) | **1** (24) | 2 | 2 | 3 | 3 |
| `sync` sin cambios **sin poda** | 2 (48) | **1** (24) | 1 | 1 | 2 | 2 |
| `sync --verify` | 1 (24) | 1 (24) | **2** | **1** | **2** | **1** |

- Las llamadas al embedder no cambian: **125** en la primera indexación y **0** cuando no hay
  texto nuevo; un cambio solo de metadatos sigue sin reembeber.
- El diagnóstico de la verificación es idéntico: las mismas **2 diferencias preexistentes** del
  documento vacío, con el mismo texto.
- El camino de escritura conserva sus dos pasadas de índice (`hashes` para decidir y `snapshot`
  dentro de la poda); la reducción a una pasada aplica a `--verify`, que es donde estaba el
  doble recorrido.

### Tiempos (mediana de tres ejecuciones, misma máquina y servicios)

| Escenario | Antes (ms) | Después (ms) | Variación |
|---|---:|---:|---:|
| `sync` inicial (dominado por 125 escrituras y 24 documentos) | 446 | 462 | ruido de carga; el ahorro de lecturas es de milisegundos |
| `sync` sin cambios **con poda** | 76 | 76 | sin cambio medible |
| `sync` sin cambios **sin poda** | 18,5 | 18,1 | sin cambio medible |
| `sync --verify` | 14,1 | **9,9** | −30 % |
| Índice sintético de 5 000 puntos: recorrido doble (antes) vs. recorrido único (después) | 107,6 (2 pasadas) | **59,0 (1 pasada)** | −45 % en el recorrido |

La ganancia de tiempo es pequeña en el corpus local porque la capa core son 24 documentos y 125
puntos; el número que escala es el de recorridos, y el micro-banco de 5 000 puntos mide el
recorrido único en la mitad del doble (una pasada de 20 páginas en vez de 40). Para la capa
extendida de producción (≈77 588 puntos), la verificación pasaría de ~600 a ~300 páginas por
capa; no se mide ese corpus aquí porque no está disponible en el entorno de desarrollo.

### Costo de embeddings después

La forma de pedir embeddings no se tocó; el código del indexador calcula el mismo texto y llama
al embedder igual. Las corridas «después» (2026-11-20, Ollama reiniciado) dieron 216–238 ms por
fragmento en una llamada y 191 ms por fragmento en lote, frente a 183–193 y 180–193 en la línea
base: la variación es del mismo orden que la carga de la máquina y **el lote no muestra una
mejora consistente**. Por eso se mantiene la decisión de la línea base: no se cambia la
interfaz del embedder ni se reindexa nada; el costo aceptado es que una reindexación completa
del núcleo (125 fragmentos) tarda ~24–29 s de CPU y una incremental solo paga los documentos
cuyo texto cambió.

## Conclusiones

1. **Verificación**: un recorrido del índice por capa (antes dos), mismo diagnóstico y cero
   escrituras. La prueba `test_verify_recorre_el_indice_una_vez_por_capa` y
   `test_snapshot_trae_huellas_e_ids_en_un_solo_recorrido` lo fijan.
2. **Lectura de Gold**: una pasada por capa en cualquier `sync` (antes 2–4), con el lote
   reutilizado por PostgreSQL y Qdrant. Lo fijan
   `test_sync_con_cambios_lee_gold_una_vez_por_capa` y la prueba de integración con PostgreSQL.
3. **Invariantes**: documentos, fragmentos, huellas, IDs de punto, payloads y diagnósticos no
   cambian; lo fijan `tests/test_corpus_caracterizacion.py` y las pruebas existentes de la 037.
4. **Embeddings**: se midió el costo por fragmento y no se cambió el modelo ni la interfaz; el
   lote no aportó una mejora consistente.
