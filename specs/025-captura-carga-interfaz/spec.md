# Feature Specification: Captura web y carga de documentos desde la interfaz

> **Revisión 2026-11-22:** la escritura web dejó de ser pública. `/configuracion`
> exige `OPERATOR_TOKEN` y CSRF, no se enlaza desde la navegación y solo deja el
> lote en Gold; la publicación se hace con `carvajal sync`. FR-001 y los
> escenarios de «visitante» se conservan como registro histórico de la entrega
> original. Ver [`docs/seguridad-operacion-corpus.md`](../../docs/seguridad-operacion-corpus.md).

**Feature Branch**: `025-captura-carga-interfaz`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-11-14
**Evidence**: `CONSTITUTION.md` §9 (2026-11-14); PRs #115, #117; `tests/test_urlguard.py`, `tests/test_capture_jobs.py`, `tests/test_api_capture.py`, `tests/test_api_config.py`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Cargar documentos o una carpeta (Priority: P1)

Un visitante sube archivos sueltos o una carpeta desde Configuración. El sistema los
lleva por Bronze, Silver y Gold, los importa a PostgreSQL y los indexa para que las
respuestas los citen; la pantalla informa archivos, documentos, fragmentos y puntos.

**Why this priority**: Es la vía para ampliar el corpus sin terminal.

**Independent Test**: Subir un documento y comprobar los conteos del informe.

**Acceptance Scenarios**:

1. **Given** el formulario de carga, **When** se envía un archivo, **Then** la respuesta confirma la actualización del corpus con los conteos del pipeline (evidencia: `tests/test_api_config.py`).
2. **Given** una carga sin archivos, **When** se envía, **Then** responde `422` con un mensaje claro; sin token de formulario responde `403`.
3. **Given** una carga desde una IP remota, **When** se envía con token, **Then** se procesa igual: no hay gate por origen ni clave de configuración.

### User Story 2 - Capturar páginas por semillas o URLs (Priority: P1)

Un visitante escribe URLs o elige un alcance, fija cuántas quiere traer y lanza la
captura. La petición solo valida y arranca un trabajo en segundo plano; la pantalla
recibe el progreso por etapas y, al terminar, el resultado.

**Why this priority**: Es la única forma de incorporar páginas sin la terminal.

**Independent Test**: Lanzar una captura pública, seguir el progreso y ver el cierre.

**Acceptance Scenarios**:

1. **Given** una captura lanzada, **When** se sigue su stream, **Then** llegan eventos de etapa y un cierre con el resultado; una reconexión tardía recibe el cierre igual (evidencia: `tests/test_capture_jobs.py`).
2. **Given** un formulario inválido, **When** se envía sin URLs en alcance manual, con más URLs que el máximo o con una URL no pública, **Then** responde `422` y no se lanza el trabajo (evidencia: `tests/test_api_capture.py`).
3. **Given** un trabajo ya en curso, **When** se envía otro, **Then** responde `409`; y si se supera el límite por IP, responde `429` con `Retry-After`.

### User Story 3 - Impedir que la captura alcance la red interna (Priority: P1)

El servidor acepta solo URLs `http` o `https` que resuelven a direcciones públicas y
en puertos 80 o 443. Las redirecciones se revalidan y la conexión se fija a la
dirección ya validada, para que un nombre no resuelva a la red interna entre ambos pasos.

**Why this priority**: La captura es pública; sin guard SSRF podría leerse la red interna.

**Independent Test**: Enviar URLs privadas, de metadatos o con credenciales y comprobar
que se rechazan sin tocar la red.

**Acceptance Scenarios**:

1. **Given** una URL con IP privada, de bucle local, de enlace local o de metadatos, **When** se valida, **Then** se rechaza antes de cualquier petición (evidencia: `tests/test_urlguard.py`).
2. **Given** una redirección hacia una dirección privada, **When** se sigue, **Then** se rechaza antes de pedirla; el cliente conecta a la IP validada y conserva el `Host` original.
3. **Given** una allowlist de dominios configurada, **When** llega un dominio ajeno, **Then** se rechaza sin resolver DNS.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001 (histórico, sustituido el 2026-11-22)**: La carga de documentos y la captura web eran públicas desde Configuración, sin autenticación ni clave, y exigían token de formulario frente a peticiones forjadas. Hoy exigen `OPERATOR_TOKEN` además del CSRF y no se enlazan desde la navegación.
- **FR-002**: La carga debe aceptar hasta 100 archivos de 5 MB como máximo, reconstruir Bronze, Silver y Gold, importar a PostgreSQL e indexar, e informar los conteos.
- **FR-003**: La captura debe aceptar URLs explícitas o semillas del alcance, poner las explícitas primero y respetar un máximo de 24 URLs por ejecución.
- **FR-004**: Antes de lanzar el trabajo, la captura debe validar esquema, IP pública, puertos 80 y 443, ausencia de credenciales y, si existe, la allowlist de dominios.
- **FR-005**: Las redirecciones deben revalidarse y la conexión debe fijarse a la IP validada; el cliente debe conservar el `Host` original.
- **FR-006**: La captura debe correr en segundo plano, emitir progreso por etapas, cerrar con resultado o error y permitir que una reconexión tardía reciba el cierre.
- **FR-007**: Solo puede correr una captura a la vez: un segundo envío responde `409`; el límite por IP es de 5 capturas por hora y responde `429` con `Retry-After`.
- **FR-008**: El cierre debe importar e indexar solo los slugs nuevos, sin podar; un fallo de PostgreSQL omite la indexación y un fallo de Qdrant no pierde el Gold.
- **FR-009**: La captura debe respetar `robots.txt` y el retardo por host cuando esa opción está activa, y rechazar un slug ya usado por otra URL.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Se aceptan hasta 100 archivos por carga y 5 MB por archivo; 0 archivos responde `422`.
- **SC-002**: Se aceptan hasta 24 URLs por ejecución; dos URLs con límite 1 responde `422` con el máximo de la ejecución.
- **SC-003**: La sexta captura de la misma IP dentro de una hora responde `429` con `Retry-After` mayor o igual que 1, y un segundo trabajo simultáneo responde `409`.
- **SC-004**: El guard rechaza IPv4 e IPv6 privadas, bucle local, enlace local y metadatos, y puertos distintos de 80 y 443, sin realizar la petición.
- **SC-005**: En el cierre, solo los slugs nuevos se importan e indexan; un fallo de PostgreSQL impide indexar y un fallo de Qdrant conserva los archivos Gold.

## Assumptions

Historial: hasta el 2026-11-14 existía un token de configuración y un gate de bucle
local; se retiraron y las acciones quedaron públicas hasta el 2026-11-22. Desde esa
fecha existe `OPERATOR_TOKEN` (con `CONFIG_TOKEN` como alias), la escritura web se
rechaza sin él, la carga y la captura solo dejan Gold y la publicación se hace con
`carvajal sync`. La autenticación por usuario sigue siendo futura (spec 029).
Convención as-built en [el inventario](../README.md) §3; detalles de operación en
[`docs/seguridad-operacion-corpus.md`](../../docs/seguridad-operacion-corpus.md).
