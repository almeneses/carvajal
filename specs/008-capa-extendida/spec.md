# Feature Specification: Capa extendida: importación de corpus externo

**Feature Branch**: `008-capa-extendida`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-10-30
**Evidence**: [CONSTITUTION.md §9](../../CONSTITUTION.md) (2026-10-30); PRs #12, #14; [docs/informe.md](../../docs/informe.md) §3.4; [tests/test_importer.py](../../tests/test_importer.py), [tests/test_corpus.py](../../tests/test_corpus.py); [data/external/alejandro/manifest.json](../../data/external/alejandro/manifest.json)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Importar el corpus de un compañero como capa separada (Priority: P1)

El equipo ejecuta `uv run carvajal import` y el catálogo del corpus donante se convierte en una capa extendida propia. Solo se importa HTML, que se vuelve a extraer con el extractor del proyecto; los PDFs quedan fuera y, si el HTML no deja texto suficiente, se usa el Markdown del donante como respaldo. La salida conserva la procedencia de cada documento y un manifiesto de la importación.

**Why this priority**: el corpus del compañero aporta cientos de fuentes que el núcleo no cubre; es la capacidad que cierra esa brecha sin tocar el entregable curado.

**Independent Test**: importar un corpus de prueba de una entrada produce un documento extendido con procedencia, un índice JSONL de una línea y un manifiesto con un documento y su categoría ([tests/test_importer.py](../../tests/test_importer.py)).

**Acceptance Scenarios**:

1. **Given** un catálogo con una entrada y su HTML, **When** se importa, **Then** se escribe un documento extendido con URL, título, categoría, entidad, origen, release, capa y hash.
2. **Given** un HTML con menú y pie, **When** se re-extrae, **Then** el documento extendido conserva el contenido editorial y descarta el *chrome*.
3. **Given** una entrada de una categoría no permitida, **When** se importa, **Then** no entra a la capa extendida.

---

### User Story 2 - Evitar duplicados y residuos entre importaciones (Priority: P1)

La importación descarta las URLs que ya están en el núcleo, normaliza las variantes de una URL (esquema, `www`, barra final y fragmento), deduplica por hash del texto y reemplaza la salida de la corrida anterior. La capa resultante queda en 479 documentos y ~1,40 millones de caracteres, por debajo del límite de tamaño.

**Why this priority**: la capa extendida no puede duplicar el núcleo ni acumular basura entre corridas; eso es lo que permite cargarla siempre.

**Independent Test**: una URL del núcleo se descarta, dos documentos con el mismo texto importan uno y una segunda importación elimina el documento de la corrida anterior; el manifiesto registra 479 documentos.

**Acceptance Scenarios**:

1. **Given** un documento cuya URL ya existe en el núcleo, **When** se importa, **Then** se descarta y no se escribe.
2. **Given** dos documentos con el mismo texto en URLs distintas, **When** se importan, **Then** solo entra uno y el otro se cuenta como duplicado.
3. **Given** una segunda importación con contenido nuevo, **When** termina, **Then** no quedan documentos de la corrida anterior.

---

### User Story 3 - Recuperar la capa extendida sin desbordar el prompt (Priority: P2)

La capa extendida se carga siempre que existe y se recupera con el índice vectorial, citando URL, categoría y capa; no se consolida en el prompt porque su volumen desbordaría el contexto. El estado del corpus informa cuántos documentos son núcleo y cuántos extendidos.

**Why this priority**: mantenerla fuera del prompt conserva el límite de caracteres del sistema y aprovecha su contenido en la recuperación.

**Independent Test**: [tests/test_corpus.py](../../tests/test_corpus.py) mezcla capas con su procedencia y demuestra que el prompt solo recibe el núcleo; [tests/test_rag.py](../../tests/test_rag.py) confirma que la capa extendida se omite del prompt consolidado y se etiqueta al recuperarla.

**Acceptance Scenarios**:

1. **Given** la capa extendida presente, **When** se construye el prompt consolidado, **Then** solo entra el núcleo curado.
2. **Given** una pregunta cuya respuesta está en la capa extendida, **When** se responde, **Then** el fragmento se recupera y se cita con su procedencia y capa.
3. **Given** el estado del corpus, **When** se consulta, **Then** separa documentos del núcleo y de la capa extendida.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Importar por comando un corpus externo a una capa extendida separada del núcleo.
- **FR-002**: Leer el catálogo del donante y re-extraer su HTML con el extractor propio; PDFs fuera, y Markdown del donante como respaldo si el HTML no deja texto suficiente.
- **FR-003**: Filtrar por las categorías permitidas: identidad, gobierno, negocio, sostenibilidad, tecnología y noticias.
- **FR-004**: Deduplicar contra el núcleo por URL normalizada y por hash del contenido.
- **FR-005**: Registrar procedencia completa: URL, título, sección, categoría, entidad, origen, release, fecha de captura, hash, tipo de fuente `colaborador` y capa extendida.
- **FR-006**: Generar un índice JSONL y un manifiesto con el total y el recuento por categoría.
- **FR-007**: Reemplazar la salida de la corrida anterior en cada importación.
- **FR-008**: Cargar la capa extendida siempre que exista, excluirla del prompt consolidado y recuperarla y citarla con URL y categoría.
- **FR-009**: Informar en el estado del corpus cuántos documentos son núcleo y cuántos extendidos.
- **FR-010**: Respetar el límite de tamaño total de la capa extendida.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: La importación deja 479 documentos (~1,40 millones de caracteres), por debajo del límite de 1,5 millones.
- **SC-002**: Cero documentos de la capa extendida duplican una URL o un contenido del núcleo.
- **SC-003**: El 100 % de los documentos extendidos registra procedencia y capa.
- **SC-004**: El manifiesto informa 479 documentos con el desglose por categoría (negocio 275, sostenibilidad 114, tecnología 38, noticias 26, gobierno 16, identidad 10).
- **SC-005**: El prompt consolidado se mantiene bajo su límite de caracteres y la capa extendida se recupera y se cita.

## Assumptions

- El corpus donante está disponible localmente con su catálogo y su HTML o Markdown.
- La capa extendida es solo de recuperación; no se consolida en el prompt de las tareas.
- La importación es reproducible y se puede repetir sin acumular residuos.
