# Feature Specification: Chunking de documentos e índice FAISS persistido

**Feature Branch**: `009-chunking-faiss`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-10-28
**Evidence**: [CONSTITUTION.md §9](../../CONSTITUTION.md) (2026-10-28); commits `42cb6c1`, `0eb63be`; [docs/arquitectura.md](../../docs/arquitectura.md) §1 y §4; [tests/test_rag.py](../../tests/test_rag.py), [tests/test_domain.py](../../tests/test_domain.py), [docs/evidence/m2/health.json](../../docs/evidence/m2/health.json)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Fragmentar el corpus y recuperar el contexto de cada pregunta (Priority: P1)

Cada documento del corpus se divide en fragmentos de hasta 1000 caracteres con 200 de solape. Cada fragmento hereda los metadatos de su documento —slug, dominio, tipo de fuente, capa y categoría— para que la cita sea rastreable. Ante una pregunta se recuperan los 5 fragmentos más similares y se entregan al modelo como contexto.

**Why this priority**: sin recuperación no hay RAG; el fragmento es la unidad que se indexa, se recupera y se cita.

**Independent Test**: [docs/arquitectura.md](../../docs/arquitectura.md) documenta el corte 1000/200 y `k=5`; [tests/test_rag.py](../../tests/test_rag.py) demuestra que el contexto etiqueta cada fragmento con su fuente y capa.

**Acceptance Scenarios**:

1. **Given** un documento de 2500 caracteres, **When** se fragmenta, **Then** produce fragmentos de hasta 1000 caracteres con 200 de solape entre contiguos.
2. **Given** una pregunta del usuario, **When** se recupera contexto, **Then** se usan los 5 fragmentos más similares del índice.
3. **Given** un fragmento recuperado, **When** se cita, **Then** conserva slug, dominio, tipo de fuente y capa del documento original.

---

### User Story 2 - Arrancar en segundos reutilizando el índice persistido (Priority: P1)

El índice se guarda en disco junto con una huella SHA-256 del corpus fragmentado. Si el corpus no cambió, los arranques siguientes cargan el índice en segundos; si cambió, la huella difiere y el índice se reconstruye. Una captura idempotente que no cambia contenido no invalida el índice.

**Why this priority**: calcular embeddings en CPU tarda minutos; sin caché la interfaz no es demostrable ni usable.

**Independent Test**: [tests/test_rag.py](../../tests/test_rag.py) demuestra que la primera construcción crea el índice y la huella, la segunda carga el índice guardado y un corpus distinto fuerza una reconstrucción.

**Acceptance Scenarios**:

1. **Given** el corpus sin cambios y la huella coincidente, **When** se construye el recuperador, **Then** se carga el índice existente sin recalcular embeddings.
2. **Given** un cambio en el contenido del corpus, **When** se arranca, **Then** la huella difiere y el índice se reconstruye desde cero.
3. **Given** una captura idempotente sin cambios de contenido, **When** termina, **Then** el índice existente no se invalida.

---

### User Story 3 - Calcular embeddings locales sin desbordar el servicio (Priority: P2)

Los vectores se calculan con Ollama y el modelo local de embeddings. Los textos se envían en lotes de 32 para no desbordar el servicio; la primera construcción del índice tarda minutos y las siguientes abren en segundos.

**Why this priority**: sin lotes el servicio local falla por tamaño; con persistencia, el costo de minutos se paga una sola vez por corpus.

**Independent Test**: [tests/test_rag.py](../../tests/test_rag.py) demuestra que 70 textos se piden en lotes de 32, 32 y 6; [docs/arquitectura.md](../../docs/arquitectura.md) registra 753 s para ~118 fragmentos en la primera construcción.

**Acceptance Scenarios**:

1. **Given** 70 fragmentos por vectorizar, **When** se calculan embeddings, **Then** se piden en tres lotes de 32, 32 y 6.
2. **Given** que el servicio de embeddings no responde, **When** se construye el índice, **Then** el error se informa y no se responde sin recuperación.
3. **Given** el índice recién construido, **When** se hace una pregunta, **Then** la respuesta cita los fragmentos recuperados.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Fragmentar documentos con tamaño máximo de 1000 caracteres y solape de 200.
- **FR-002**: Heredar en cada fragmento slug, dominio, tipo de fuente, capa y categoría del documento.
- **FR-003**: Recuperar los 5 fragmentos más similares por pregunta.
- **FR-004**: Calcular embeddings locales con Ollama y el modelo de embeddings en lotes de 32.
- **FR-005**: Persistir el índice en disco junto con una huella SHA-256 del corpus fragmentado.
- **FR-006**: Reutilizar el índice cuando la huella coincide y reconstruirlo cuando el corpus cambia.
- **FR-007**: No invalidar el índice cuando una captura no cambia el contenido.
- **FR-008**: Aplicar el mismo contrato de fragmentación a todo índice derivado del corpus.
- **FR-009**: Informar del estado del índice y no responder sin contexto recuperado cuando el servicio falla.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: El corpus vigente de 24 documentos produce 125 fragmentos ([docs/evidence/m2/health.json](../../docs/evidence/m2/health.json)); la entrega inicial fue de ~118 fragmentos sobre 21 documentos ([docs/arquitectura.md](../../docs/arquitectura.md) §4).
- **SC-002**: Un segundo arranque sin cambios reutiliza el índice y recalcula 0 embeddings.
- **SC-003**: Un cambio de contenido produce una huella distinta y reconstruye el índice.
- **SC-004**: La primera construcción tarda minutos (medido: 753 s para ~118 fragmentos) y los arranques posteriores abren en segundos.
- **SC-005**: El 100 % de los fragmentos conserva fuente y capa, y toda cita muestra la fuente.

## Assumptions

- Ollama está disponible en local con el modelo de embeddings descargado.
- FAISS sobre CPU es suficiente para el volumen del prototipo.
- El índice se construye sobre el corpus cargado: el núcleo y, cuando existe, la capa extendida.
