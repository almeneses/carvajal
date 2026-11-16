# Tasks: Ingesta multiformato de archivos locales

**Input**: Design documents from `/specs/035-ingesta-multiformato/`
**Prerequisites**: [`plan.md`](./plan.md), [`spec.md`](./spec.md)
**Estado**: `[ ]` pendiente · `[X]` cumplida · tareas de la spec en `Status: Draft`

**Pruebas**: incluidas. El repositorio exige un escenario verificable por requisito y
`make check` antes de abrir PR.

**Organización**: por historia de usuario, para que cada una sea entregable y verificable
por separado.

## Format: `[ID] [P?] [USn] Descripción`

- **[P]**: puede ejecutarse en paralelo (archivos distintos, sin dependencias)
- **[USn]**: historia de la spec (`US1` datos desde la web · `US2` PDF · `US3` consola · `US4` índice concurrente)
- Rutas exactas desde la raíz del repositorio

## Phase 1: Preparación

**Purpose**: infraestructura compartida por todas las historias

- [ ] T001 Declarar `EXTRACTED_EXTENSIONS` con `.pdf`, `.xml`, `.json`, `.jsonl`, `.csv` y `.tsv` en `apps/api/document_formats.py`
- [ ] T002 Componer `ALLOWED_EXTENSIONS` en `apps/api/ingestion.py` a partir de la lista anterior más HTML, Markdown y TXT
- [ ] T003 [P] Añadir `data/gold/.index.jsonl.lock` a `.gitignore`
- [ ] T004 [P] Instalar `poppler-utils` en la etapa `runtime` de `Dockerfile` antes de crear el usuario sin privilegios

## Phase 2: Fundacional (bloquea las historias)

**Purpose**: extracción, índice y orquestación que ninguna historia puede dar por hecha

- [ ] T005 Implementar `extract_markdown(suffix, data)` en `apps/api/document_formats.py` con XML sin DTD, JSON, JSONL, CSV y TSV validados y salida delimitada por el nombre del formato
- [ ] T006 Implementar la extracción de PDF en `apps/api/document_formats.py` con `pdftotext`, separación por página, 20 s de tiempo máximo y 16 MB de texto máximo
- [ ] T007 Implementar `merge_index(index_file, entries, *, gold_dir=None)` en `packages/core/src/carvajal/gold.py` con `flock` y reemplazo atómico
- [ ] T008 Convertir `write_index` en `packages/core/src/carvajal/gold.py` en escritura atómica sobre archivo temporal con modo conservado
- [ ] T009 Ampliar `ingest_uploads` en `apps/api/ingestion.py` con `max_file_bytes`, `solo_extraer`, captura de `ValueError` por archivo y una sola fusión del índice al final del lote
- [ ] T010 Sustituir el `_merge_index` local de `apps/api/ingestion.py` y el de `packages/core/src/carvajal/pipeline.py` por `carvajal.gold.merge_index`
- [ ] T011 [P] Probar la validación por formato y el rechazo de XML con DTD en `tests/test_ingestion.py`
- [ ] T012 [P] Probar la escritura atómica y la fusión bajo bloqueo en `tests/test_ingestion.py`

**Checkpoint**: extracción e índice listos; las historias pueden avanzar en paralelo.

---

## Phase 3: US1 — Datos desde Configuración (P1) 🎯 MVP

**Goal**: subir XML, JSON, JSONL, CSV y TSV desde el formulario y verlos citables.

**Independent Test**: subir un archivo de cada formato y comprobar Bronze, Silver, Gold y
el informe sin errores.

### Pruebas de US1

- [ ] T013 [P] [US1] Probar la ingesta de los cinco formatos con `solo_extraer=True` en `tests/test_ingestion.py`
- [ ] T014 [P] [US1] Probar que un JSON mal formado no impide que el resto llegue a Gold en `tests/test_ingestion.py`
- [ ] T015 [P] [US1] Probar que el formulario declara la misma lista de formatos en los dos selectores y en la ayuda en `tests/test_api_config.py`
- [ ] T016 [P] [US1] Probar que el formulario entrega un archivo JSON al ingestor en `tests/test_api_config.py`

### Implementación de US1

- [ ] T017 [US1] Derivar `_markdown` hacia `extract_markdown` y usar el nombre del archivo como título en `apps/api/ingestion.py`
- [ ] T018 [US1] Pasar `formatos=sorted(ALLOWED_EXTENSIONS)` a la plantilla en `apps/api/views/config.py`
- [ ] T019 [US1] Usar la lista de formatos en los selectores de archivos y carpeta y en el texto de ayuda de `apps/api/templates/configuracion.html`
- [ ] T020 [US1] Documentar los formatos, los límites y el bloque de datos en el README

**Checkpoint**: un visitante carga datos sin terminal.

---

## Phase 4: US2 — PDF con página y aviso de OCR (P1)

**Goal**: extraer informes en PDF conservando la página y avisando cuando hace falta OCR.

**Independent Test**: un PDF multipágina llega a Gold con secciones «Página N»; uno
escaneado produce el aviso y el lote continúa.

### Pruebas de US2

- [ ] T021 [P] [US2] Probar que el número de página se conserva y las páginas vacías se omiten en `tests/test_ingestion.py`
- [ ] T022 [P] [US2] Probar el aviso de OCR ante un PDF sin texto extraíble en `tests/test_ingestion.py`
- [ ] T023 [P] [US2] Probar el mensaje de dependencia ausente cuando no hay extractor en `tests/test_ingestion.py`
- [ ] T024 [P] [US2] Probar que un PDF dañado o que excede el tiempo se reporta sin abortar el lote en `tests/test_ingestion.py`

### Implementación de US2

- [ ] T025 [US2] Añadir a la ayuda del formulario el aviso de página conservada y de OCR previo en `apps/api/templates/configuracion.html`
- [ ] T026 [US2] Verificar `pdftotext` dentro de la imagen con `docker run` y registrar la evidencia

**Checkpoint**: los PDF entran al corpus con cita por página o se rechazan con motivo.

---

## Phase 5: US3 — Carpetas por consola (P2)

**Goal**: procesar un corpus local grande por lotes, con parada opcional en Gold.

**Independent Test**: apuntar la consola a una carpeta con subcarpetas y comprobar resumen,
código de salida y documentos en Gold.

### Pruebas de US3

- [ ] T027 [P] [US3] Probar el recorrido recursivo con archivos ajenos ignorados y lotes de un archivo en `tests/test_ingestion.py`
- [ ] T028 [P] [US3] Probar `--solo-extraer` sin PostgreSQL ni Qdrant en `tests/test_ingestion.py`
- [ ] T029 [P] [US3] Probar el código de salida distinto de cero con un archivo ilegible o sobredimensionado en `tests/test_ingestion.py`
- [ ] T030 [P] [US3] Probar la ruta sin archivos admitidos en `tests/test_ingestion.py`

### Implementación de US3

- [ ] T031 [US3] Crear `apps/api/ingest_cli.py` con `argparse`, recorrido recursivo, lotes por número y bytes, y resumen por lote
- [ ] T032 [US3] Ajustar el límite de 100 MB por archivo y el código de salida según el informe
- [ ] T033 [US3] Documentar las dos órdenes de la consola y el destino de cada capa en el README

**Checkpoint**: un operador procesa carpetas reales sin partir el lote a mano.

---

## Phase 6: US4 — Índice concurrente (P2)

**Goal**: cargas y capturas solapadas conservan todas las entradas y nunca dejan el índice
truncado.

**Independent Test**: diez actualizaciones concurrentes y comprobación de las diez entradas.

### Pruebas de US4

- [ ] T034 [P] [US4] Probar diez actualizaciones concurrentes con `ThreadPoolExecutor` en `tests/test_ingestion.py`
- [ ] T035 [P] [US4] Probar que la poda conserva las entradas vigentes y descarta las que no tienen documento en Gold en `tests/test_ingestion.py`
- [ ] T036 [P] [US4] Probar que una captura y una carga seguidas conservan las entradas de ambas en `tests/test_pipeline.py`

### Implementación de US4

- [ ] T037 [US4] Encadenar fusión y poda en `pipeline.capture` usando `merge_index` con `gold_dir`
- [ ] T038 [US4] Confirmar que el archivo de bloqueo queda fuera del control de versiones

**Checkpoint**: ninguna carga pierde el índice frente a otra carga o captura.

---

## Phase 7: Pulido y documentación

- [X] T039 [P] Registrar en `CONSTITUTION.md` §9 y §2 la ampliación de alcance, junto con la spec (PR #131)
- [ ] T040 [P] Actualizar `CONSTITUTION.md` §6.1 con la dependencia del extractor y marcar en §10 la entrega de la extracción de PDF
- [ ] T041 [P] Añadir la fila de esta capacidad a `specs/README.md`
- [ ] T042 Ejecutar `make check` y anotar el número de pruebas en el PR
- [ ] T043 Revisar que ningún mensaje de error expone rutas internas ni detalles del entorno

---

## Dependencies & Execution Order

### Phase Dependencies

- **Preparación (1)**: sin dependencias.
- **Fundacional (2)**: depende de 1 y bloquea todas las historias.
- **US1 (3)** y **US2 (4)**: dependen de 2; comparten `document_formats.py` (T005 y T006
  son archivos distintos en la práctica, pero la misma unidad lógica).
- **US3 (5)**: depende de 2 (T009) y se beneficia de US1 y US2 para tener formatos que
  recorrer.
- **US4 (6)**: depende de 2 (T007, T008) y no depende de US1–US3.
- **Pulido (7)**: depende de las historias que se entreguen.

### Within Each User Story

- Pruebas antes de la implementación, y en rojo antes de tocar el código.
- Extracción antes de la orquestación; orquestación antes de la interfaz.
- La historia termina cuando su prueba independiente pasa.

### Parallel Opportunities

- T003 y T004 en paralelo con T001–T002.
- T011 y T012 en paralelo al cerrar la fase fundacional.
- US1, US2 y US4 pueden ir en paralelo tras el checkpoint; US3 espera a US1 y US2 para
  tener formatos reales.
- Todas las pruebas `[P]` de una historia se escriben en paralelo.

## Implementation Strategy

1. Preparación + fundacional → `make check` en verde con las pruebas nuevas.
2. US1 → MVP: datos desde la web.
3. US2 → PDF con página y aviso de OCR.
4. US3 → consola para volumen.
5. US4 → cierre de concurrencia.
6. Pulido: constitución, inventario y presupuesto de cambios del PR.
