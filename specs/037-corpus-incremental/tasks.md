# Tasks: Corpus incremental, deduplicado y sincronizado

**Input**: Design documents from `/specs/037-corpus-incremental/`
**Prerequisites**: [`plan.md`](./plan.md), [`spec.md`](./spec.md)
**Estado**: `[ ]` pendiente · `[X]` cumplida

**Pruebas**: incluidas. El repositorio exige un escenario verificable por requisito y
`make check` antes de abrir PR.

**Organización**: por historia de usuario, para que cada una sea entregable y verificable por
separado. La spec 035 es la base y debe estar fusionada antes de empezar.

## Format: `[ID] [P?] [USn] Descripción`

- **[P]**: puede ejecutarse en paralelo (archivos distintos, sin dependencias)
- **[USn]**: historia de la spec (`US1` sin duplicar · `US2` todo consultable · `US3`
  reindexación incremental · `US4` capa extendida · `US5` un solo índice)
- Rutas exactas desde la raíz del repositorio

## Phase 1: Preparación

**Purpose**: identidad, registro y bloqueo compartidos por todas las historias.

- [X] T001 Probar la normalización de `source_key`, la asignación de slug y las colisiones en `tests/test_identity.py`
- [X] T002 [P] Implementar `source_key` y asignación de slug en `packages/core/src/carvajal/identity.py`
- [X] T003 Probar carga, fusión y búsqueda del registro (por `source_key`, `raw_hash` y `content_hash`) en `tests/test_registry.py`
- [X] T004 [P] Implementar `packages/core/src/carvajal/registry.py` sobre los `index.jsonl` de todas las áreas, con búsqueda por `source_key`, `raw_hash` y `content_hash`
- [X] T005 Ampliar el contrato de `merge_index`/`_read_index` y su tipado para conservar `source_key`, `raw_hash`, `layer` y `updated_at` en `packages/core/src/carvajal/gold.py`
- [X] T006 Probar el bloqueo compartido `data/.corpus.lock` y la exclusión entre procesos en `tests/test_registry.py`
- [X] T007 [P] Implementar `corpus_lock()` junto a `_index_lock` y exportarlo desde `packages/core/src/carvajal/gold.py`

**Checkpoint**: identidad, registro y bloqueo listos; las historias pueden avanzar.

## Phase 2: US1 — Agregar y reimportar sin duplicar (P1)

**Goal**: dedupe antes de Bronze, actualización en sitio y versiones.

### Pruebas de US1

- [X] T008 [P] [US1] Probar que una copia exacta con otro nombre se reporta como duplicada y no escribe capas en `tests/test_ingestion.py`
- [X] T009 [P] [US1] Probar que reimportar sin cambios no reescribe capas ni embebe en `tests/test_ingestion.py`
- [X] T010 [P] [US1] Probar que un archivo modificado actualiza el mismo `slug` y archiva versión en `tests/test_ingestion.py`
- [X] T011 [P] [US1] Probar que dos archivos homónimos con contenido distinto reciben identidades distintas en `tests/test_ingestion.py`
- [X] T012 [P] [US1] Probar que una captura sin cambios de texto no reescribe capas en `tests/test_pipeline.py`
- [X] T013 [P] [US1] Probar que una captura con texto distinto actualiza el mismo documento en `tests/test_pipeline.py`

### Implementación de US1

- [X] T014 [US1] Añadir `source_key` a `UploadedDocument` y calcular `raw_hash` antes de escribir Bronze en `apps/api/ingestion.py`
- [X] T015 [US1] Consultar el registro y saltar duplicados exactos o versiones sin cambios en `apps/api/ingestion.py`
- [X] T016 [US1] Reutilizar el `slug` de la identidad existente y actualizar en sitio cuando cambia el texto en `apps/api/ingestion.py`
- [X] T017 [US1] Pasar `source_key` como `file:<raíz>/<ruta relativa>` desde la consola y deduplicar antes de extraer y escribir capas en `apps/api/ingest_cli.py`
- [X] T018 [US1] Añadir la compuerta de `content_hash` contra el registro en `packages/core/src/carvajal/pipeline.py`
- [X] T019 [US1] Registrar `source_key`, `raw_hash`, `layer` y `updated_at` al escribir el índice en `apps/api/ingestion.py` y `packages/core/src/carvajal/pipeline.py`
- [X] T020 [US1] Reportar duplicados, sin cambios y actualizados en `IngestReport` y en el resumen de la consola
- [X] T065 [US1] Buscar `content_hash` en el registro y saltar/reportar duplicados semánticos entre documentos en `apps/api/ingestion.py` y `packages/core/src/carvajal/pipeline.py`
- [X] T066 [P] [US1] Probar el PDF re-codificado y el HTML con otro contenedor como duplicado semántico en `tests/test_ingestion.py`
- [X] T067 [P] [US1] Probar que mover un archivo no crea documento y se reporta con la identidad existente en `tests/test_ingestion.py`
- [X] T068 [US1] Detectar `source_key` repetido con contenido distinto dentro de un lote y rechazar el segundo en `apps/api/ingestion.py`
- [X] T069 [P] [US1] Probar el rechazo de dos archivos homónimos en una carga web y que el primero llega a Gold en `tests/test_ingestion.py`

**Checkpoint**: importar dos veces no duplica; modificar actualiza.

## Phase 3: US2 — Todo lo que entra se puede consultar (P1)

**Goal**: un único `sync_corpus` y todos los caminos terminan ahí.

### Pruebas de US2

- [ ] T021 [P] [US2] Probar `sync_corpus` con dobles: nuevos, actualizados y sin cambios en `tests/test_sync.py`
- [ ] T022 [P] [US2] Probar que un fallo de Qdrant no pierde Gold y que la segunda corrida repara en `tests/test_sync.py`
- [ ] T023 [P] [US2] Probar la poda de documentos ausentes en `tests/test_sync.py`
- [ ] T024 [P] [US2] Probar que dos sincronizaciones simultáneas se serializan (la segunda espera) en `tests/test_sync.py`
- [ ] T025 [P] [US2] Probar que `capture` por consola sincroniza y que `--solo-gold` no toca servicios en `tests/test_cli.py`
- [ ] T071 [P] [US2] Probar que una muestra de hasta diez documentos por capa se recupera por título en el top-10 en `tests/test_retrieval.py`
- [ ] T072 [P] [US2] Probar la regresión de los límites de carga (100/5 MB web y 100 MB consola) en `tests/test_ingestion.py`
- [ ] T073 [P] [US2] Probar el aviso y la selección determinista cuando el núcleo supera los 500 documentos de la FAQ en `tests/test_faq.py`
- [ ] T074 [P] [US2] Probar que `sync --verify` no escribe y devuelve código distinto de cero ante diferencias en `tests/test_sync.py`

### Implementación de US2

- [ ] T026 [US2] Crear `sync_corpus` en `packages/core/src/carvajal/application/sync.py` con inyección de sesión, índice y embedder
- [ ] T027 [US2] Implementar el informe de sincronización (nuevos, actualizados, sin cambios, podados, errores) en `application/sync.py`
- [ ] T028 [US2] Añadir el comando `sync` con `--layer`, `--prune` y `--verify` en `packages/core/src/carvajal/cli.py`
- [ ] T029 [US2] Hacer que `capture` llame a `sync_corpus` con los slugs capturados y añadir `--solo-gold` en `cli.py` y `pipeline.py`
- [ ] T030 [US2] Sustituir `importar_gold`/`indexar_gold` de `apps/api` por `sync_corpus` en `apps/api/ingestion.py` y `apps/api/web_capture.py` (el job de `capture_jobs.py` sólo delega en `ingest_urls`)
- [ ] T031 [US2] Ejecutar la sincronización bajo `corpus_lock()` y documentar el orden de bloqueo

**Checkpoint**: cualquier entrada termina consultable; un `sync` repara diferencias.

## Phase 4: US3 — Reindexar solo lo que cambió (P2)

**Goal**: no recalcular embeddings de documentos sin cambios.

### Pruebas de US3

- [ ] T033 [P] [US3] Probar que una segunda sincronización sin cambios no emite embeddings en `tests/test_sync.py`
- [ ] T034 [P] [US3] Probar que un documento acortado no deja puntos viejos en `tests/test_sync.py`
- [ ] T035 [P] [US3] Probar el borrado por `document_id` con dos documentos que comparten título en `tests/test_qdrant_store.py`
- [ ] T036 [P] [US3] Probar la poda por capa sin tocar la otra capa en `tests/test_sync.py`

### Implementación de US3

- [ ] T037 [US3] Reutilizar y probar `delete_by_document` (ya existe) en `packages/core/src/carvajal/adapters/vector/qdrant_store.py`
- [ ] T038 [US3] Añadir poda por `layer` (scroll del filtro y borrado de ids obsoletos) en `qdrant_store.py`
- [ ] T039 [US3] Refactorizar `index_gold` para aceptar varios `(directorio, capa)` y un conjunto `only` en `packages/core/src/carvajal/indexing/qdrant.py`
- [ ] T040 [US3] Incorporar el cálculo de documentos cambiados y el borrado previo por documento en `application/sync.py`

**Checkpoint**: reimportar cientos de archivos sólo procesa los cambiados.

## Phase 5: US4 — Capa extendida con prioridad (P2)

**Goal**: recuperar y citar la capa extendida sin desplazar al núcleo.

### Pruebas de US4

- [ ] T041 [P] [US4] Probar que una pregunta con respuesta en ambas capas cita el núcleo en `tests/test_retrieval.py`
- [ ] T042 [P] [US4] Probar que una pregunta sólo respondida por la capa extendida la cita con su origen, categoría y entidad en `tests/test_retrieval.py`
- [ ] T043 [P] [US4] Probar que la FAQ sigue leyendo sólo documentos `core` en `tests/test_faq.py`
- [ ] T062 [P] [US4] Probar que el explorador HTML y la API del corpus filtran por `source_type` y muestran `layer`/`source_type` (el filtro por capa ya está cubierto) en `tests/test_api_corpus.py` y `tests/test_api_read.py`

### Implementación de US4

- [ ] T044 [US4] Indexar en una sola corrida el núcleo y cada fuente extendida con su `layer` y su procedencia en `application/sync.py` e `indexing/qdrant.py`
- [ ] T075 [US4] Añadir el filtro por `source_type` al explorador HTML y conservar las columnas de capa y tipo de procedencia en `apps/api/views/corpus.py` y `apps/api/templates/corpus.html`
- [ ] T063 [US4] Extender `Document` (`domain/entities.py`), `load_document` (`adapters/storage/import_corpus.py`), el metadata de `index_gold` y `payload_for`/`Source` (`qdrant_store.py`, `application/ask.py`) con `origin`, `category` y `entity`
- [ ] T064 [P] [US4] Probar que una cita extendida conserva los tres campos y que un documento sin ellos no falla en `tests/test_retrieval.py`
- [ ] T045 [US4] Implementar el envoltorio de recuperación con prioridad núcleo → extendida en `packages/core/src/carvajal/application/retrieval.py`
- [ ] T046 [US4] Usar el envoltorio en `apps/api/agent_runtime.py` sin cambiar el contrato del agente
- [ ] T047 [US4] Ejecutar `make index-eval` (con `--layer all`, depende de T070) antes y después y registrar la evidencia en `docs/eval/` y `specs/037-corpus-incremental/`
- [ ] T070 [US4] Añadir `--layer core|extended|all` a `carvajal index-qdrant` y hacer que `make index`/`index-eval` usen `--layer all` en `cli.py` y `Makefile`

**Checkpoint**: la capa extendida suma sin diluir.

## Phase 6: US5 — Un solo índice (P3)

**Goal**: retirar `/ask` y pgvector.

### Pruebas de US5

- [ ] T048 [P] [US5] Probar que `/ask` ya no existe y que el chat, la FAQ y el explorador siguen funcionando en `tests/test_api_m2.py`
- [ ] T049 [P] [US5] Retirar `tests/test_vector_store.py` y las pruebas de `PgVectorStore`

### Implementación de US5

- [ ] T050 [US5] Eliminar la ruta y el handler de `/ask` en `apps/api/main.py` y las plantillas que lo enlacen
- [ ] T051 [US5] Eliminar `packages/core/src/carvajal/adapters/vector/pgvector_store.py`, la columna `embedding` de `tables.py` y la dependencia `pgvector` de `pyproject.toml`/`uv.lock`
- [ ] T052 [US5] Añadir `migrations/versions/0006_drop_chunk_embeddings.py` (reversible), probar la migración y actualizar las pruebas de tablas y migraciones (`tests/test_storage_tables.py:64-70`, `tests/test_storage_migrations.py:61,93-95`)
- [ ] T053 [US5] Actualizar `docs/` y `README` para declarar el chat como único camino de respuesta

**Checkpoint**: no queda un índice vacío que parezca funcionar.

## Phase 7: Adopción, documentación y pulido

- [ ] T054 [US1] Implementar `sync --adopt` para completar `source_key` y `raw_hash` en el registro existente sin reindexar, resolviendo las fuentes repetidas (vigente + `legacy:<slug>`)
- [ ] T055 [US1] Probar la adopción con un índice legado (URL, slug con hash, subidas homónimas que caen a `legacy:<slug>` y dos versiones legadas de la misma subida) en `tests/test_registry.py`
- [ ] T056 Ejecutar la adopción y la poda en el VPS siguiendo `docs/despliegue-ovh.md` y registrar el antes/después de `/health` (manual: requiere acceso al servidor)
- [ ] T057 Documentar en `README.md` la identidad, la deduplicación, `carvajal sync` y la prioridad de capas
- [ ] T058 Registrar en `CONSTITUTION.md` §2, §9 y §10 las decisiones de identidad, dedupe, sincronización, prioridad y retiro de pgvector
- [ ] T059 [P] Añadir la fila de la 037 a `specs/README.md` y sincronizar `.specify/memory/constitution.md`
- [ ] T060 Ejecutar `make check` y anotar el número de pruebas en el PR
- [ ] T061 Revisar que los errores del informe no exponen rutas internas ni credenciales

## Dependencies & Execution Order

- **Preparación (1)**: sin dependencias; bloquea todo.
- **US1 (2)**: depende de 1.
- **US2 (3)**: depende de 1 y de US1 (necesita identidad y registro para decidir qué sincronizar).
- **US3 (4)**: depende de US2.
- **US4 (5)**: depende de US2 y US3; T047 depende de T070.
- **US5 (6)**: independiente; puede ir en paralelo con US4.
- **Pulido (7)**: depende de las historias entregadas.

## Parallel Opportunities

- T001–T005 en paralelo por archivo; T006–T007 después del registro.
- Las pruebas `[P]` de cada historia se escriben en paralelo.
- US5 puede ejecutarse mientras se cierra US4.
- T057–T059 en paralelo al cierre.

## Implementation Strategy

1. Preparación y registro → `make check` en verde.
2. US1 → reimportar no duplica (MVP de datos).
3. US2 → todo queda indexado.
4. US3 → coste sostenible.
5. US4 → capa extendida.
6. US5 → limpieza del índice heredado.
7. Adopción y documentación.
