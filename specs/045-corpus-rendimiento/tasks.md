# Tasks: Corpus: Gold fuera del adaptador SQL y verificación en un solo recorrido

**Input**: Design documents from `/specs/045-corpus-rendimiento/`
**Prerequisites**: [`plan.md`](./plan.md), [`spec.md`](./spec.md)

**Tests**: obligatorios. La capacidad se entrega con caracterización previa a la implementación y
medición antes/después (spec.md FR-009, FR-010).

**Organización**: por historia de usuario; las tareas fundacionales (Fase 2) bloquean a las demás.

## Format: `[ID] [P?] [Story] Descripción`

- **[P]**: puede ir en paralelo (archivos distintos, sin dependencias).
- **[Story]**: US1…US5 de `spec.md`.

## Phase 1: Setup

- [ ] T001 Crear `specs/045-corpus-rendimiento/` con `spec.md`, `plan.md`, `tasks.md`,
      `analysis.md` y `checklists/requirements.md`.
- [ ] T002 Verificar el estado del corpus y los servicios locales: 24 documentos / 125 fragmentos
      core, PostgreSQL local y Qdrant `127.0.0.1:6333`.

---

## Phase 2: Foundational (bloquea las historias)

- [ ] T003 [P] Prueba de caracterización de la lectura/fragmentación Gold que fija documento,
      posiciones, textos e IDs (`tests/test_gold_documents.py`) y reutiliza las aserciones de
      `tests/test_import_corpus.py`.
- [ ] T004 [P] Prueba de caracterización del mismo corpus contra PostgreSQL y Qdrant: mismos
      documentos, fragmentos, IDs de punto y payloads (`tests/test_sync.py`).
- [ ] T005 Prueba instrumentada que cuenta pasadas completas sobre Gold por capa en un `sync`
      con cambios y con poda; debe fallar con la implementación actual (`tests/test_sync.py`).
- [ ] T006 Prueba instrumentada que cuenta recorridos de `_payloads`/scroll por capa en
      `sync --verify`; debe fallar con la implementación actual (`tests/test_qdrant_store.py`).
- [ ] T007 Medir la línea base con `scripts/medir_corpus_045.py` y registrar tiempos y conteos en
      `specs/045-corpus-rendimiento/mediciones.md` (sección «Antes»).
- [ ] T008 Commit `test(045): caracteriza el corpus y cuenta lecturas, scrolls y embeddings`.

---

## Phase 3: US1 - Verificar en un solo recorrido (P1) 🎯 MVP

**Goal**: `sync --verify` obtiene huellas e IDs con un scroll por capa, mismo diagnóstico y sin
escrituras.

**Independent Test**: instrumentar el store y comprobar un solo `_payloads` por capa; comparar la
salida de diferencias y el código de salida con la línea base.

- [ ] T009 [US1] Añadir `IndexSnapshot` al puerto `SyncIndex` y la implementación
      `QdrantVectorStore.snapshot` en `adapters/vector/qdrant_store.py` con una sola pasada;
      `document_hashes` y `document_ids` delegan en él.
- [ ] T010 [US1] Implementar `snapshot` en `QdrantSync` (`adapters/vector/sync_qdrant.py`).
- [ ] T011 [US1] Usar `index.snapshot(layer)` en `_sync`/`_index_differences`
      (`application/sync.py`); conservar textos, orden y clasificación ausente/incompleto/
      modificado/sobrante.
- [ ] T012 [US1] Pruebas: un scroll por capa, cero escrituras, y los cuatro diagnósticos
      equivalentes a la línea base (`tests/test_sync.py`, `tests/test_qdrant_store.py`).
- [ ] T013 [US1] Commit `perf(045): verifica el índice en un solo recorrido por capa`.

**Checkpoint**: verificación con la mitad de recorridos y diagnóstico idéntico.

---

## Phase 4: US2 - Gold al dominio (P1)

**Goal**: `load_document`, `split_document`, `load_documents` y `GoldImportError` en
`carvajal/gold_documents.py`; PostgreSQL y Qdrant reutilizan el módulo.

**Independent Test**: importar y fragmentar desde el módulo nuevo y comprobar igualdad con la
caracterización de T003; inspeccionar que ningún adaptador redefine la lectura.

- [ ] T014 [US2] Crear `packages/core/src/carvajal/gold_documents.py` y mover allí la lectura,
      fragmentación, validación de slug y `GoldImportError`, con la misma lógica y docstrings.
- [ ] T015 [US2] `adapters/storage/import_corpus.py`: importar del dominio, conservar
      `GoldImportError` exportado y `migrate_gold`/`prune_layer`.
- [ ] T016 [US2] `indexing/qdrant.py`: importar `load_document`/`split_document` del dominio.
- [ ] T017 [US2] `application/sync.py`: importar `load_document`/`load_documents` del dominio.
- [ ] T018 [US2] Actualizar los imports de pruebas existentes que usaban la lectura desde el
      adaptador (`tests/test_import_corpus.py`, `tests/test_ingestion.py`).
- [ ] T019 [US2] Pruebas de que ambos caminos producen el mismo `Document` y los mismos `Chunk`, y
      de que la CLI reporta `GoldImportError`.
- [ ] T020 [US2] Commit `refactor(045): mueve la lectura y fragmentación Gold al dominio`.

**Checkpoint**: un solo dueño de la definición de documento y fragmento.

---

## Phase 5: US3 - Una lectura de Gold por capa y sync (P2)

**Goal**: `sync_corpus` lee Gold una vez por capa y entrega el lote a ambos almacenes.

**Independent Test**: la prueba de T005 pasa con una lectura por capa y los conteos de importación
e indexación no cambian.

- [ ] T021 [US3] Añadir `GoldLayer` y `read_gold_layer` a `application/sync.py`; `gold_hashes` y
      `gold_index_hashes` pasan a ser vistas compatibles.
- [ ] T022 [US3] `SyncDatabase.import_gold(gold: GoldLayer, ...)`; `PostgresSync` usa
      `import_documents` sin releer Gold. Poda de capa con la unión de documentos del lote.
- [ ] T023 [US3] `SyncIndex.index_gold(gold: GoldLayer, ...)`; `QdrantSync` pasa `documents=` a
      `index_gold` y poda con `gold.hashes` sin releer Gold.
- [ ] T024 [US3] `indexing/qdrant.py`: aceptar `documents=` ya leído (con `gold_dirs` para fichas,
      capas y poda), conservando la vía por directorio para la CLI.
- [ ] T025 [US3] `import_corpus.py`: extraer `import_documents(session, documents, *, layer,
      prune, only)` y dejar `migrate_gold` como `load_documents` + `import_documents`.
- [ ] T026 [US3] `_sync` usa `read_gold_layer` una vez por capa, filtra por `only` en memoria y
      pasa el lote; conserva el manejo de errores por capa y por almacén.
- [ ] T027 [US3] Pruebas: una pasada sobre Gold por capa con cambios y con poda, equivalencia de
      conteos, poda multi-fuente, capa vacía y fallos parciales (PostgreSQL caído / Qdrant caído)
      con la lectura única (`tests/test_sync.py`, `tests/test_sync_adapters.py`).
- [ ] T028 [US3] Commit `perf(045): sincroniza con una sola lectura de Gold por capa`.

**Checkpoint**: el número de lecturas de Gold ya no depende de los almacenes.

---

## Phase 6: US4 - Metadatos sin reembeber (P2)

**Goal**: la garantía de la entrega anterior sigue intacta tras la refactorización.

- [ ] T029 [US4] Reejecutar y ajustar (solo si el contrato interno cambió) la prueba de cambio
      solo de metadatos: cero llamadas al embedder, payload actualizado y puntos iguales
      (`tests/test_sync.py`).
- [ ] T030 [US4] Prueba de que un cambio de texto sigue reembediendo solo ese documento.

**Checkpoint**: el atajo de payload no se perdió al mover el indexado.

---

## Phase 7: US5 - Medición antes/después (P3)

**Goal**: números reproducibles con el mismo comando y entorno, y decisión justificada sobre el
costo de embeddings por fragmento.

- [ ] T031 [US5] Crear `scripts/medir_corpus_045.py`: cuenta lecturas de Gold, recorridos de
      `_payloads`, llamadas al embedder y tiempos de `sync` y `sync --verify` con el corpus local.
- [ ] T032 [US5] Ejecutar la medición «Después» y completar
      `specs/045-corpus-rendimiento/mediciones.md` con tiempos, conteos, entorno y comando.
- [ ] T033 [US5] Documentar la decisión sobre embeddings por fragmento (optimizar o aceptar) con
      los números medidos.
- [ ] T034 [US5] Commit `perf(045): documenta la medición antes/después`.

---

## Phase 8: Polish y puerta de calidad

- [ ] T035 [P] Actualizar `CONSTITUTION.md` §9 con la decisión (Gold en dominio, instantánea del
      índice, lote por capa) y `specs/README.md` con la entrada 045.
- [ ] T036 [P] Verificar que no cambian `pyproject.toml`, `uv.lock`, `migrations/`, el modelo de
      embeddings, la salida de la CLI ni los contratos de la web; y revisar que `analysis.md` y
      `mediciones.md` reflejan el estado final.
- [ ] T037 `env -u OLLAMA_MODEL make check`.
- [ ] T038 `env -u OLLAMA_MODEL make test-integration` (Docker/testcontainers).
- [ ] T039 `node --test tests/js/test_chat_voice.cjs`.
- [ ] T040 Push de `perf/045-corpus-rendimiento`, PR a `dev` con labels `refactor,data,backend`,
      assignee `@me`, cuerpo con medición antes/después, evidencia y reversión.

## Dependencies

- Fase 2 (T003–T008) bloquea todas las historias.
- US1 (T009–T013) es el MVP y no depende de US2/US3.
- US2 (T014–T020) bloquea a US3 (el lote usa el dominio).
- US3 (T021–T028) bloquea la medición final de US5.
- US4 (T029–T030) se valida tras US2/US3.
- T037–T040 dependen de que todas las historias estén implementadas.

## Parallel

- T003 y T004 en paralelo (archivos distintos).
- T005 y T006 en paralelo.
- T014–T017 no pueden ir en paralelo entre sí por dependencias, pero T016 y T017 sí son
  independientes entre sí una vez creado el módulo.
- T035 y T036 en paralelo con T037 (documentación vs. puerta).

## MVP

US1 (T009–T013) por sí sola entrega la reducción de recorridos de la verificación con el mismo
diagnóstico; el resto son los otros dos objetivos de la capacidad.
