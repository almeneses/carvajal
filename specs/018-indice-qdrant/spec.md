# Feature Specification: Índice vectorial Qdrant del M2

**Feature Branch**: `018-indice-qdrant`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-11-13
**Evidence**: [`CONSTITUTION.md` §9](../../CONSTITUTION.md) (2026-11-13); [`tests/test_qdrant_store.py`](../../tests/test_qdrant_store.py); [`docs/aceptacion-m2.md`](../../docs/aceptacion-m2.md) (sección A); [`docs/evidence/m2/health.json`](../../docs/evidence/m2/health.json)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preparar el índice desde el corpus (Priority: P1)

Quien despliega construye el índice una vez con `carvajal index-qdrant` (o `make index`), sin que la web lo reconstruya al arrancar. El comando calcula embeddings de 768 dimensiones, escribe puntos con identificadores estables y poda lo que ya no pertenece al corpus. Reejecutarlo no duplica nada.

**Why this priority**: sin índice preparado y reproducible el RAG del M2 no tiene fuente; sin idempotencia cada recarga infla la colección.

**Independent Test**: ejecutar `carvajal index-qdrant --recreate` y comprobar 24 documentos y 125 fragmentos con 768 dimensiones; reejecutar sin `--recreate` y verificar que los puntos no se duplican (`test_upsert_es_idempotente_y_guarda_procedencia`, [`docs/aceptacion-m2.md`](../../docs/aceptacion-m2.md)).

**Acceptance Scenarios**:

1. **Given** una colección inexistente, **When** se prepara el índice, **Then** se crea con 768 dimensiones y distancia coseno (`test_ensure_collection_crea_con_dimension_y_coseno`).
2. **Given** el mismo fragmento indexado dos veces, **When** se repite el upsert, **Then** el identificador es el mismo y no se duplica el punto (`test_upsert_es_idempotente_y_guarda_procedencia`).
3. **Given** puntos que ya no están en el corpus, **When** se ejecuta la poda, **Then** solo se eliminan los obsoletos (`test_prune_elimina_solo_los_puntos_obsoletos`).
4. **Given** un corpus de referencia, **When** se indexa desde cero, **Then** el comando reporta 24 documentos, 125 fragmentos y 768 dimensiones ([`docs/aceptacion-m2.md`](../../docs/aceptacion-m2.md), sección A).

### User Story 2 - Recuperar fragmentos con procedencia verificable (Priority: P2)

La búsqueda devuelve fragmentos con puntaje y metadatos suficientes para citar y abrir la fuente: documento, URL, título, tipo de fuente, identificador de fragmento y texto. Se puede filtrar por capa y por sección.

**Why this priority**: una respuesta sin URL ni documento comprobable no es defendible ante el cliente.

**Independent Test**: pedir una búsqueda con filtros y comprobar que el resultado trae puntaje, documento, metadatos y URL; abrir la URL del payload y encontrar el texto citado (`test_search_traduce_score_payload_y_filtros`, `test_payload_incluye_la_url_para_abrir_la_fuente`).

**Acceptance Scenarios**:

1. **Given** una consulta y filtros de capa y sección, **When** se busca, **Then** se traducen puntaje, fragmento, metadatos y filtros a la respuesta (`test_search_traduce_score_payload_y_filtros`).
2. **Given** un fragmento indexado, **When** se lee su payload, **Then** incluye `url`, `source_type`, `text` y el identificador del fragmento (`test_payload_incluye_la_url_para_abrir_la_fuente`).
3. **Given** una ficha o metadato distinto para el mismo texto, **When** se calcula el identificador, **Then** cambia para no mezclar procedencias (`test_point_id_cambia_si_cambia_la_ficha`).

### User Story 3 - Ver la salud del índice sin tumbar el proceso (Priority: P3)

La salud del proceso y la del índice están separadas: `/health` informa estado, colección, puntos y dimensiones, y responde 503 cuando Qdrant no está disponible, mientras `/live` sigue en 200. La web arranca con el índice ya construido y no intenta indexar.

**Why this priority**: una caída de Qdrant no debe reiniciar el contenedor ni marcar enfermo al proceso.

**Independent Test**: con Qdrant caído, comprobar que la salud del índice devuelve `unavailable` sin lanzar y que `/live` responde 200 (`test_health_no_lanza_si_qdrant_no_responde`; [`docs/aceptacion-m2.md`](../../docs/aceptacion-m2.md), sección A).

**Acceptance Scenarios**:

1. **Given** Qdrant sin responder, **When** se consulta la salud del índice, **Then** devuelve `status: unavailable` y el tipo de error, sin lanzar (`test_health_no_lanza_si_qdrant_no_responde`).
2. **Given** una colección de 384 dimensiones, **When** se valida contra el contrato, **Then** se rechaza con un error que nombra la dimensión incompatible (`test_ensure_collection_rechaza_dimension_incompatible`).
3. **Given** el índice preparado, **When** se consulta `/health`, **Then** devuelve `index.status: ok`, la colección, 125 puntos y 768 dimensiones, o 503 si falta ([`docs/evidence/m2/health.json`](../../docs/evidence/m2/health.json)).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `carvajal index-qdrant` MUST indexar el corpus Gold con embeddings de 768 dimensiones y reportar documentos y fragmentos procesados.
- **FR-002**: Los identificadores de punto MUST ser estables: el mismo fragmento y la misma ficha producen el mismo identificador, y un cambio de ficha lo cambia.
- **FR-003**: Reejecutar la indexación MUST ser idempotente: los puntos existentes se actualizan, no se duplican.
- **FR-004**: La indexación MUST podar los puntos que ya no pertenecen al corpus; `--recreate` MUST reconstruir la colección desde cero.
- **FR-005**: La colección MUST usar 768 dimensiones y distancia coseno, y MUST rechazar una colección de dimensión incompatible.
- **FR-006**: Cada punto MUST guardar documento, URL, título, tipo de fuente, identificador de fragmento y texto.
- **FR-007**: La búsqueda MUST devolver puntaje, fragmento y metadatos, y MUST aceptar filtros por capa y sección.
- **FR-008**: La salud del índice MUST informar estado, colección, puntos y dimensiones, y MUST devolver `unavailable` sin lanzar si Qdrant no responde.
- **FR-009**: `/health` MUST responder 503 cuando el índice no está disponible, mientras `/live` MUST seguir respondiendo 200.
- **FR-010**: La web MUST arrancar con el índice ya construido y MUST NOT indexar durante el arranque ni durante una petición.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: La indexación desde cero deja 24 documentos, 125 fragmentos de 768 dimensiones en `carvajal_chunks` ([`docs/evidence/m2/health.json`](../../docs/evidence/m2/health.json)).
- **SC-002**: Reejecutar la indexación mantiene 125 puntos y 0 duplicados; la poda de 1 punto obsoleto sobre 2 elimina exactamente 1.
- **SC-003**: Una colección de 384 dimensiones se rechaza antes de escribir y el error nombra la dimensión esperada.
- **SC-004**: `/health` devuelve `points: 125` y `dimensions: 768` con el índice sano, y 503 con `index.status` no disponible cuando Qdrant cae.
- **SC-005**: Con Qdrant caído, `/live` responde 200 y la consulta de salud del índice no lanza excepción.

## Assumptions

- Los embeddings se calculan localmente con Ollama y `nomic-embed-text`, independientemente del proveedor del LLM.
- Qdrant se levanta con volumen persistente; el índice sobrevive a reinicios del contenedor.
- El prototipo del Módulo 1 conserva pgvector; esta spec documenta el índice evaluado del M2.
