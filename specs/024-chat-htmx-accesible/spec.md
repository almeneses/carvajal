# Feature Specification: Chat HTMX con hilos, configuración y escala accesible

**Feature Branch**: `024-chat-htmx-accesible`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-11-14
**Evidence**: `CONSTITUTION.md` §9 (2026-11-14); PRs #114, #117, #118, #120; `tests/test_api_m2.py`

> **Cambio posterior (2026-11-21):** la vista del chat dejó de mostrar el contador,
> el bloque de fuentes y sus referencias numéricas. US2, FR-004, FR-005 y SC-004
> documentan la entrega histórica. El SSE y la API conservan las fuentes, y el
> texto aparece en bloques ya formateados, no como Markdown crudo.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Conversar en un hilo y volver a él (Priority: P1)

Un visitante escribe en un compositor anclado al pie y recibe la respuesta por
streaming. Un panel lateral lista los hilos anteriores y, al elegir uno, la
conversación se retoma por su identificador sin recargar el resto del sitio.

**Why this priority**: Sin hilos persistentes el chat es una caja de texto que se pierde.

**Independent Test**: Enviar una pregunta, crear un hilo nuevo y volver al anterior.

**Acceptance Scenarios**:

1. **Given** un chat abierto, **When** se envía una pregunta, **Then** el compositor viaja con el hilo activo y el stream emite `open`, `meta`, `token` y `done` en ese orden (evidencia: `tests/test_api_m2.py`).
2. **Given** el panel de hilos, **When** se selecciona uno, **Then** la ruta lo resuelve por `?thread=`, el activo se marca con `aria-current` y un hilo ajeno responde `404`.
3. **Given** el botón de hilo nuevo, **When** se envía sin token, **Then** se rechaza con `403`; con token se crea el hilo y se vuelve a él.

### User Story 2 - Leer la fuente citada sin salir del chat (Priority: P2)

Cada respuesta muestra sus fuentes contraídas con el número de citas. Al abrir una,
un panel lateral trae el documento formateado y resalta el fragmento citado.

**Why this priority**: La respuesta solo es verificable si la fuente se abre en contexto.

**Independent Test**: Desplegar fuentes, abrir una y comprobar documento y resaltado.

**Acceptance Scenarios**:

1. **Given** una respuesta con fuentes, **When** se inspecciona el bloque de citas, **Then** está contraído por defecto y su resumen indica cuántas hay.
2. **Given** una fuente con documento, **When** se abre, **Then** el panel resuelve el documento por su slug, muestra el HTML saneado y resalta el fragmento o declara que no lo localizó.

### User Story 3 - Alimentar el corpus desde Configuración (Priority: P2)

La navegación superior incluye Configuración con carga de archivos o carpeta y
captura web, y declara las etapas que recorre el contenido hasta quedar disponible.

**Why this priority**: Sin entrada en la interfaz, ampliar el corpus depende de la terminal.

**Independent Test**: Abrir Configuración y comprobar los dos formularios y las etapas.

**Acceptance Scenarios**:

1. **Given** la pestaña Configuración, **When** se carga, **Then** ofrece carga de archivos y carpeta, captura web, y declara Bronze, Silver, Gold, PostgreSQL y Qdrant.
2. **Given** el formulario de carga, **When** se envía sin archivos, **Then** responde `422`; sin token responde `403` (evidencia: `tests/test_api_config.py`).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `/chat` debe mostrar el hilo con el compositor al pie y recibir la respuesta por streaming, sin recargar la página.
- **FR-002**: El hilo activo debe resolverse por `?thread=` y el panel lateral debe listar los hilos del visitante con vista previa, fecha y marca de activo.
- **FR-003**: Crear un hilo debe exigir token de formulario y devolver al visitante al hilo nuevo.
- **FR-004**: Las fuentes deben estar contraídas por defecto, mostrar su número e incluir tipo, sección, capa, puntuación y extracto al desplegarse.
- **FR-005**: Abrir una fuente debe resolver su documento en un panel lateral y resaltar el fragmento citado, con mensaje explícito si no hay coincidencia.
- **FR-006**: La pestaña Configuración debe ofrecer carga de archivos o carpeta y captura web, y declarar las etapas Bronze, Silver, Gold, PostgreSQL y Qdrant.
- **FR-007**: La navegación superior debe incluir Chat, FAQ, Corpus y Configuración; `/` debe redirigir a `/chat` y la pestaña Asistente no debe mostrarse.
- **FR-008**: Las respuestas deben formatearse con Markdown saneado y las interrumpidas deben marcarse como incompletas.
- **FR-009**: El shell HTMX debe usar 18 px de texto base, 54 px de alto de control y contenedores de 1360 px y 1680 px, con navegación móvil en dos columnas.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: La escala accesible se mide en 18 px de texto base, 54 px de control y contenedores de 1360 px y 1680 px; la navegación móvil ocupa dos columnas.
- **SC-002**: El stream entrega los eventos en el orden `open`, `meta`, `token`, `done`, y un `403`, `429` o `503` llega como evento `error`.
- **SC-003**: Un hilo ajeno responde `404`, un hilo nuevo exige token y el identificador seleccionado viaja en la petición del turno.
- **SC-004**: Las fuentes aparecen contraídas y el panel lateral resuelve el documento citado y resalta el fragmento en escritorio y en móvil.
- **SC-005**: La evidencia visual cubre chat, hilos, configuración y panel de documento (ver [`docs/evidence/m2/`](../../docs/evidence/m2/)).

## Assumptions

La escala accesible se aplica solo al shell HTMX (:8000); la SPA (:8001) queda fuera
de esta capacidad. La identidad sigue siendo anónima: los hilos separan visitantes,
no autentican personas. Convención as-built en [el inventario](../README.md) §3 y
contexto en [`CONSTITUTION.md`](../../CONSTITUTION.md) §9.
