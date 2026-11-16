# Feature Specification: Seguridad web: CSRF, CSP, cookies y cabeceras

**Feature Branch**: `022-seguridad-web`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-11-13
**Evidence**: `CONSTITUTION.md` §9 (2026-11-13); PR #98; `tests/test_security.py`, `tests/test_rendering.py`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Enviar formularios con token (Priority: P1)

Un visitante abre una pantalla y recibe, junto con el HTML, un token de formulario.
El navegador repite ese token al enviar y el servidor solo acepta la petición si
coincide con el valor firmado; quien no pasó por la pantalla no puede reutilizarlo.

**Why this priority**: Es la única defensa frente a peticiones forjadas; sin ella
cualquier página externa puede gastar la cuota del visitante.

**Independent Test**: Pedir una pantalla para obtener token y cookie, y enviar el
mismo formulario sin token, con token inventado y con el token correcto.

**Acceptance Scenarios**:

1. **Given** un formulario abierto, **When** se envía sin token o con uno ajeno o no ASCII, **Then** la respuesta es `403` y no se ejecuta el caso de uso ni se consume cuota (evidencia: `tests/test_security.py`).
2. **Given** el token emitido por la pantalla, **When** se repite en el formulario, **Then** la petición pasa; una cookie impuesta por un tercero se reemplaza por un token firmado válido.

### User Story 2 - Navegar con cabeceras y cookies endurecidas (Priority: P1)

Toda respuesta, incluida la de error, llega con las mismas cabeceras de seguridad:
política de contenido estricta, marco no incrustable y tipo no adivinable. Las
cookies no son legibles por JavaScript ni viajan en peticiones de terceros.

**Why this priority**: Una cabecera ausente en una sola ruta reabre la puerta.

**Independent Test**: Recorrer rutas correctas y de error, sobre HTTP y HTTPS, y
comparar cabeceras y atributos de cookies.

**Acceptance Scenarios**:

1. **Given** cualquier respuesta, **When** se inspeccionan sus cabeceras, **Then** la CSP no contiene `unsafe-inline` ni `unsafe-eval`, prohíbe incrustar el sitio y `X-Content-Type-Options` vale `nosniff`.
2. **Given** una petición por HTTP, **When** se inspecciona HSTS, **Then** no se anuncia; sobre HTTPS sí se anuncia y la cookie de formulario se marca `Secure`.
3. **Given** una página que emite token, **When** se inspecciona su caché, **Then** `Cache-Control` es `no-store`; los estáticos no se marcan `no-store`.

### User Story 3 - Leer HTML generado sin ejecutar código (Priority: P2)

El texto que produce el modelo se muestra formateado con una lista permitida, de
modo que un contenido malicioso no se convierta en script, marco ni enlace ejecutable.

**Why this priority**: El generador es entrada no confiable; el saneado es la
segunda barrera después de la CSP.

**Independent Test**: Renderizar Markdown con etiquetas, manejadores y enlaces
peligrosos y comprobar que el HTML resultante solo conserva lo permitido.

**Acceptance Scenarios**:

1. **Given** un texto con `script`, `iframe`, manejadores de evento o enlaces `javascript:`, **When** se renderiza, **Then** esas construcciones desaparecen y se conservan títulos, listas, tablas y código (evidencia: `tests/test_rendering.py`).
2. **Given** un enlace seguro, **When** se renderiza, **Then** conserva su URL y recibe atributos de relación que impiden abrir la ventana de origen.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Toda pantalla que acepte envío debe emitir un token de formulario en cookie y repetirlo en un campo oculto del HTML.
- **FR-002**: Las peticiones de escritura sin token, con token ajeno o con caracteres no ASCII deben responder `403` antes de evaluar la cuota y antes de llamar al proveedor.
- **FR-003**: Toda respuesta debe incluir CSP sin `unsafe-inline`, `frame-ancestors 'none'`, `X-Frame-Options: DENY` y `X-Content-Type-Options: nosniff`.
- **FR-004**: HSTS debe anunciarse solo si la petición llegó por TLS, y la cookie del token debe llevar `Secure` en ese mismo caso.
- **FR-005**: Las cookies de identidad y de formulario deben ser `HttpOnly` y `SameSite=Lax`, y una cookie de token no firmada debe reemplazarse.
- **FR-006**: Las respuestas que portan token no deben cachearse y deben sobrescribir una política de caché pública previa.
- **FR-007**: El esquema de la API no debe exponerse a visitantes anónimos: documentación interactiva y esquema OpenAPI responden `404`.
- **FR-008**: Un error no controlado debe responder `500` con mensaje genérico, sin detalles internos, y conservar las cabeceras de seguridad y el identificador de petición.
- **FR-009**: El HTML generado a partir de Markdown debe sanearse con lista permitida: sin `script`, `iframe`, manejadores de evento ni enlaces `javascript:`.
- **FR-010**: El JavaScript propio debe distinguir sus propios errores de los del navegador y enviar el token también en la petición de streaming.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Cero peticiones de escritura sin token válido pasan a cuota o proveedor, tanto en el chat clásico como en el stream SSE.
- **SC-002**: La CSP de todas las respuestas, incluidas `403` y `500`, carece de `unsafe-inline` y de `unsafe-eval`.
- **SC-003**: HSTS nunca aparece sobre HTTP y siempre sobre HTTPS; las dos cookies son `HttpOnly` y `SameSite=Lax`, y `Secure` solo sobre TLS.
- **SC-004**: El saneado elimina el 100 % de las etiquetas `script` e `iframe`, los manejadores de evento y los enlaces con esquema `javascript:` de las muestras de prueba.
- **SC-005**: Las rutas de documentación interactiva y de esquema OpenAPI responden `404` en la app pública.

## Assumptions

El token protege frente a peticiones forjadas, no frente a un XSS ya ejecutado. La
app es anónima: la autenticación por usuario es trabajo futuro (spec 029). Convención
as-built en [el inventario](../README.md) §3 y decisiones en [`CONSTITUTION.md`](../../CONSTITUTION.md) §9.
