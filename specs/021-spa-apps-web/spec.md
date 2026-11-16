# Feature Specification: Frontend propio: shell SPA y contrato JSON de lectura

**Feature Branch**: `021-spa-apps-web`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-11-13
**Evidence**: `CONSTITUTION.md` §9 (2026-11-13); PRs #105, #110; `tests/test_web_app.py`, `tests/test_api_read.py`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Recorrer las pantallas sin recargar (Priority: P1)

Un visitante entra al corpus, abre un documento y vuelve a la lista. La cabecera
y las pestañas siguen en su sitio y solo cambia la vista; cada pantalla es una ruta
real que se puede compartir y recargar.

**Why this priority**: Es la prueba de la frontera HTTP desacoplada: el frontend no
calcula respuestas ni habla con la base.

**Independent Test**: Pedir las cinco rutas de pantalla y comprobar que devuelven
el mismo HTML, tipo `text/html` y sin caché.

**Acceptance Scenarios**:

1. **Given** un visitante en cualquier ruta de pantalla, **When** pide `/`, `/faq`, `/corpus`, `/documents` o `/documents/{id}`, **Then** recibe `200`, tipo `text/html` y el mismo documento del shell, con `Cache-Control: no-store` (evidencia: `tests/test_web_app.py`).
2. **Given** el shell cargado, **When** el navegador resuelve la ruta, **Then** la vista pedida queda visible, la pestaña activa se marca con `aria-current` y la marca lleva al corpus.

### User Story 2 - Explorar y filtrar el corpus por JSON (Priority: P1)

Un visitante busca por texto, filtra por sección, tipo de fuente y capa, y avanza
por páginas sin perder el conjunto filtrado; la interfaz recibe la lista por JSON.

**Why this priority**: Sin contrato de lectura el frontend propio no puede existir.

**Independent Test**: Consultar el corpus con filtros y paginación y comparar total,
página, tamaño, `has_next`, facetas y campos de cada documento.

**Acceptance Scenarios**:

1. **Given** 30 documentos, **When** se pide la página 2 con tamaño 10, **Then** la respuesta informa `total=30`, `has_next=true` y devuelve 10 documentos (evidencia: `tests/test_api_read.py`).
2. **Given** filtros de sección, capa y tipo de fuente, **When** se pagina, **Then** `total` y `has_next` describen el conjunto filtrado antes de paginar.
3. **Given** una búsqueda con espacios, mayúsculas o comodines, **When** se aplica, **Then** se recorta a 200 caracteres, ignora mayúsculas y no interpreta `%` ni `_` como comodines.

### User Story 3 - Leer un documento con su ficha (Priority: P2)

Un visitante abre un documento desde la lista o desde una cita y recibe su ficha
con el contenido formateado; si hay versiones archivadas, puede abrir una.

**Why this priority**: Sin ficha y versiones el documento es texto suelto y no una
fuente auditable.

**Independent Test**: Pedir un documento, una de sus versiones y una versión de otro
documento; comprobar contenido, huella y códigos de error.

**Acceptance Scenarios**:

1. **Given** un documento con versiones, **When** se pide su identificador, **Then** la respuesta trae ficha, HTML saneado, huella corta, fecha de captura en formato legible e ISO y la lista de versiones (evidencia: `tests/test_api_read.py`).
2. **Given** una versión archivada, **When** se pide con su identificador, **Then** cambian contenido, huella y fecha; una versión de otro documento responde `404` sin filtrar su contenido.
3. **Given** un documento cuya URL de origen no es segura, **When** se pide, **Then** el enlace no se expone y no se sirve como HTML enlazable.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El shell debe servir `200` con el mismo HTML para `/`, `/faq`, `/corpus`, `/documents` y `/documents/{id}`, con tipo `text/html` y `Cache-Control: no-store`.
- **FR-002**: La navegación debe exponer las pestañas FAQ, Corpus y Documento; la marca debe llevar al corpus y la pestaña Asistente no debe mostrarse.
- **FR-003**: El contrato de corpus debe paginar y filtrar por `q`, `section`, `source_type` y `layer`, con `total` y `has_next` calculados sobre el conjunto filtrado.
- **FR-004**: El tamaño de página válido va de 1 a 100, con 24 por defecto; un valor fuera de rango o no numérico se sustituye por 24 y una página inválida se normaliza a la primera.
- **FR-005**: El contrato de corpus debe devolver las facetas de secciones y tipos de fuente y, por documento, `id`, `url`, `title`, `slug`, `section`, `layer`, `source_type` y `captured_at`.
- **FR-006**: El contrato de documento debe devolver ficha, HTML saneado, huella corta, fechas legible e ISO, versión solicitada y lista de versiones.
- **FR-007**: El contrato debe resolver una cita por slug y devolver `404` cuando el documento o la versión no existen o no pertenecen al documento.
- **FR-008**: Un fallo de base de datos debe responder `503` con un mensaje genérico, sin detalles internos ni credenciales.
- **FR-009**: La CSP del frontend debe permitir scripts y estilos solo del propio origen, `connect-src` solo hacia la base de la API y nunca interpolar un host ajeno.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Las cinco rutas de pantalla responden `200` con HTML idéntico y `no-store` en la misma corrida de pruebas.
- **SC-002**: La paginación se comporta igual con 30 documentos: página 2 y tamaño 10 devuelven 10 elementos con `has_next=true`; tamaño 999 vuelve a 24 y página 0 se normaliza a 1.
- **SC-003**: La búsqueda literal distingue `100%` de `100X`, encuentra `PÁGINA` por `página` y recorta 250 caracteres a 200.
- **SC-004**: La CSP del frontend no contiene `unsafe-inline` ni `unsafe-eval`, y un `Host` manipulado no aparece en `connect-src`.
- **SC-005**: El contrato de documento nunca devuelve etiquetas `script` ni enlaces `javascript:` en el HTML saneado.

## Assumptions

La lectura se valida contra un proveedor inyectado, no contra la base real, salvo
las pruebas de integración. La convención as-built está en [el inventario](../README.md)
§3 y las decisiones en [`CONSTITUTION.md`](../../CONSTITUTION.md) §9. La evidencia
visual del shell es [`docs/evidence/m2/frontend-desktop.png`](../../docs/evidence/m2/frontend-desktop.png).
