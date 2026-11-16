# Feature Specification: Shell web FastAPI: salud, chat y pantallas del corpus

**Feature Branch**: `015-shell-fastapi`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-11-11 → 2026-11-13
**Evidence**: [`CONSTITUTION.md` §9](../../CONSTITUTION.md) (2026-11-11 y 2026-11-13); [`tests/test_api.py`](../../tests/test_api.py); [`tests/test_api_corpus.py`](../../tests/test_api_corpus.py); [`tests/test_api_documents.py`](../../tests/test_api_documents.py)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preguntar y recibir una respuesta citada (Priority: P1)

Un visitante abre la app, escribe una pregunta y recibe HTML con el texto formateado y las fuentes numeradas. La portada redirige al chat; una pregunta vacía o larga se rechaza sin gastar modelo.

**Why this priority**: sin pregunta, respuesta citada y rechazo honesto no hay aplicación web defendible.

**Independent Test**: pedir `/` y comprobar el redirect; enviar `POST /ask` y verificar fuentes con documento, puntaje, capa y tipo (`test_ask_devuelve_respuesta_y_fuentes`).

**Acceptance Scenarios**:

1. **Given** la app arrancada, **When** se pide `/`, **Then** responde 303 a `/chat` y el chat da 200 con formulario, `lang="es"` y región `aria-live` (`test_la_portada_redirige_al_chat`).
2. **Given** una pregunta válida, **When** se envía por `POST /ask`, **Then** la respuesta trae texto formateado, citas y por fuente documento, puntaje, capa y tipo (`test_ask_devuelve_respuesta_y_fuentes`, `test_ask_formatea_el_texto_generado_y_sus_citas`).
3. **Given** una pregunta vacía, **When** se envía, **Then** responde 422 con aviso legible y no consume cuota (`test_pregunta_vacia_devuelve_422`, `test_pregunta_vacia_no_consume_cuota`).
4. **Given** un fallo del proveedor o del índice, **When** se responde, **Then** devuelve 503 genérico sin trazas (`test_fallos_de_proveedor_devuelven_503`, `test_error_interno_no_filtra_detalles`).

### User Story 2 - Separar vitalidad de preparación (Priority: P2)

Quien opera el contenedor distingue una vitalidad que responde aunque la base esté caída de una preparación que confirma base e índice con sus conteos, sin exponer credenciales.

**Why this priority**: sin `/live` una caída de Postgres reinicia un proceso sano; sin `/health` no hay verificación de despliegue.

**Independent Test**: con la base caída, `/live` da 200 y `/health` 503 degradado; con todo sano, `/health` da los conteos (`test_live_responde_aunque_la_base_no_este`, `test_health_devuelve_estado_y_conteos`).

**Acceptance Scenarios**:

1. **Given** la base caída, **When** se pide `/live`, **Then** responde 200 con `{"status": "ok"}` sin consultarla (`test_live_responde_aunque_la_base_no_este`).
2. **Given** base e índice disponibles, **When** se pide `/health`, **Then** responde 200 con `status`, `documents`, `chunks` e `index` (`test_health_devuelve_estado_y_conteos`).
3. **Given** un fallo de base, **When** se pide `/health`, **Then** responde 503 con `status: degraded`, detalle genérico e `index: unknown`, sin IP ni usuario (`test_health_degradado_no_filtra_detalles`).
4. **Given** el índice no disponible, **When** se pide `/health`, **Then** responde 503 con `index.status` distinto de `ok` mientras `/live` sigue en 200 ([`docs/aceptacion-m2.md`](../../docs/aceptacion-m2.md), sección A).

### User Story 3 - Explorar el corpus y abrir un documento (Priority: P3)

El visitante navega el corpus por sección y capa con paginación, abre un documento con metadatos y Markdown saneado, y consulta versiones archivadas cuando existen.

**Why this priority**: permite auditar la fuente de cada respuesta sin salir de la app.

**Independent Test**: pedir `/corpus` con 51 documentos y comprobar 50 enlaces y `page=2`; abrir un documento y comprobar metadatos, contenido y versiones (`test_la_primera_pagina_muestra_50_documentos_y_enlaza_la_siguiente`, `test_muestra_metadatos_y_contenido_actual`).

**Acceptance Scenarios**:

1. **Given** 51 documentos, **When** se pide `/corpus`, **Then** muestra 50 y el enlace siguiente; la página 2 usa `offset=50` (`test_la_segunda_pagina_usa_offset_50_y_muestra_el_resto`).
2. **Given** filtros de sección y capa, **When** se pagina, **Then** se conservan y una capa desconocida se ignora (`test_los_filtros_se_combinan_y_la_paginacion_los_conserva`, `test_una_capa_desconocida_se_ignora`).
3. **Given** un documento existente, **When** se pide `/documents/{id}`, **Then** muestra metadatos, hash abreviado y contenido; versión ajena o documento inexistente da 404 (`test_version_de_otro_documento_devuelve_404`, `test_documento_inexistente_devuelve_404`).
4. **Given** títulos con HTML o URLs `javascript:`, **When** se renderizan, **Then** el texto se escapa y la URL insegura no se enlaza (`test_el_titulo_del_documento_se_escapa`, `test_la_url_de_origen_insegura_no_se_renderiza_como_enlace`).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `GET /live` MUST responder 200 sin depender de Postgres, del índice ni de un LLM.
- **FR-002**: `GET /health` MUST devolver conteos de documentos y fragmentos y el estado del índice, con 503 si base o índice faltan.
- **FR-003**: Los errores MUST ser genéricos: sin cadena de conexión, mensaje del proveedor ni traza.
- **FR-004**: `GET /chat` MUST servir el compositor con formulario accesible, navegación y región viva.
- **FR-005**: `POST /ask` MUST devolver HTML con respuesta, fuentes numeradas y metadatos de cada fuente.
- **FR-006**: Una pregunta vacía o de más de 500 caracteres MUST rechazarse con 422 sin consultar cuota ni modelo.
- **FR-007**: `GET /corpus` MUST paginar de a 50 documentos, combinar filtros de sección y capa y conservarlos en la paginación.
- **FR-008**: Una página inválida MUST normalizarse a la primera y una capa desconocida MUST ignorarse sin error.
- **FR-009**: `GET /documents/{id}` MUST mostrar metadatos, contenido saneado y versiones; documento o versión ajena MUST dar 404.
- **FR-010**: El HTML del visitante y los datos del corpus MUST escaparse; una URL de origen insegura MUST mostrarse como texto.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `/live` responde 200 con la base caída y `/health` 503 degradado sin filtrar la cadena de conexión.
- **SC-002**: `/health` real devuelve 24 documentos, 125 fragmentos y `carvajal_chunks` con 125 puntos de 768 dimensiones ([`docs/evidence/m2/health.json`](../../docs/evidence/m2/health.json)).
- **SC-003**: La pantalla de corpus muestra 50 documentos por página y conserva sección y capa al paginar.
- **SC-004**: Ninguna respuesta 4xx/5xx contiene IP, puerto, usuario de base, mensaje del proveedor ni traza.
- **SC-005**: Un documento con versión archivada se abre por `?version=` y vuelve a la actual sin enlaces rotos.

## Assumptions

- El corpus de referencia son 24 documentos y 125 fragmentos al 13 de noviembre.
- Esta spec cubre las pantallas HTML y los contratos HTTP, no la SPA separada.
- La identidad del visitante y la cuota se detallan en la especificación 016.
