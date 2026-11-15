# Feature Specification: Streaming SSE del chat con degradación

**Feature Branch**: `017-streaming-sse`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-11-13
**Evidence**: [`CONSTITUTION.md` §9](../../CONSTITUTION.md) (2026-11-13); [`tests/test_api_stream.py`](../../tests/test_api_stream.py); [`tests/test_ask_stream.py`](../../tests/test_ask_stream.py); [`docs/aceptacion-m2.md`](../../docs/aceptacion-m2.md) (sección C, streaming incremental real)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ver la respuesta mientras se genera (Priority: P1)

El visitante envía la pregunta por `POST /chat/stream` y ve el primer token en menos de dos segundos, sin esperar a que el modelo termine. El stream abre con `open`, anuncia las fuentes con `meta`, emite `token` incrementales y cierra con `done`, que identifica conversación, mensaje y duración.

**Why this priority**: es la diferencia perceptible entre una app que responde y una que parece colgada; la latencia del primer token se midió como requisito.

**Independent Test**: enviar una pregunta al stream y comprobar el orden `open`, `meta`, `token`, `token`, `done`, los encabezados SSE y que `done` trae los identificadores (`test_stream_emite_open_meta_tokens_y_done_en_orden`).

**Acceptance Scenarios**:

1. **Given** una pregunta válida, **When** se envía por `POST /chat/stream`, **Then** responde 200 con `text/event-stream; charset=utf-8`, `cache-control: no-store` y eventos en orden (`test_stream_emite_open_meta_tokens_y_done_en_orden`).
2. **Given** el stream abierto, **When** se mide el primer evento y el primer `token`, **Then** `open` es el primer evento y ambos llegan en menos de 2 s (`test_open_es_el_primer_evento_y_llega_en_menos_de_dos_segundos`, `test_el_primer_token_llega_en_menos_de_dos_segundos`).
3. **Given** una petición `GET` al mismo recurso, **When** se intenta disparar el stream, **Then** responde 405 y no llama al proveedor (`test_get_no_puede_disparar_el_stream`).
4. **Given** un visitante sin identidad, **When** se abre el stream, **Then** se emite la cookie y el proveedor recibe el dueño derivado (`test_stream_emite_cookie_si_el_visitante_no_tenia_identidad`).

### User Story 2 - Degradar sin mentir cuando el stream falla (Priority: P2)

Si el stream falla antes del primer token, la app completa la respuesta por la vía normal con un evento `fallback`. Si falla después de emitir texto, cierra con `error` marcado como incompleto y no finge un final feliz. Preguntas inválidas y rechazos de cuota o base también se comunican como `error` dentro del stream.

**Why this priority**: un corte silencioso haría creer al visitante que la respuesta terminó bien; la interfaz debe poder distinguirla.

**Independent Test**: inyectar un fallo antes del primer token y comprobar `open`, `meta`, `fallback`, `done` sin `token`; luego un fallo tras un token y comprobar `error` con `incomplete: true` y sin `done` (`test_fallback_antes_del_primer_token_se_emite_y_cierra`, `test_error_tras_un_token_marca_incompleto_y_no_emite_done`).

**Acceptance Scenarios**:

1. **Given** un fallo antes del primer token, **When** se responde por el stream, **Then** se emiten `open`, `meta`, `fallback` con el texto completo y `done`, sin `token` (`test_fallback_antes_del_primer_token_se_emite_y_cierra`).
2. **Given** un fallo después de un token, **When** se cierra el stream, **Then** llega `error` con `incomplete: true`, no hay `done` y el turno no se persiste (`test_error_tras_un_token_marca_incompleto_y_no_emite_done`).
3. **Given** una pregunta vacía o de más de 500 caracteres, **When** se envía al stream, **Then** responde 422 con un único evento `error` e `incomplete: false` y no llama al proveedor (`test_pregunta_invalida_devuelve_422_sse_sin_llamar_al_proveedor`).
4. **Given** cupo agotado o base caída, **When** se pide el stream, **Then** el último evento es `error`, con `Retry-After` en el 429, y no se abre el proveedor (`test_el_stream_agota_la_cuota_antes_de_llamar_al_proveedor`, `test_fallo_de_bd_en_la_cuota_del_stream_devuelve_503`).

### User Story 3 - Persistir el turno exactamente una vez (Priority: P3)

El stream no debe duplicar ni perder el turno: un `done` persiste una sola vez, un `fallback` también persiste una sola vez y un `error` no persiste nada. La respuesta incompleta queda identificada para que el usuario pueda reintentar sin duplicar.

**Why this priority**: la duplicación de turnos fue un defecto real de la ola; la persistencia exacta es requisito de consistencia.

**Independent Test**: contar las escrituras al cerrar un stream feliz, un fallback y un error; deben ser 1, 1 y 0 (`test_stream_emite_open_meta_tokens_y_done_en_orden`, `test_fallback_antes_del_primer_token_se_emite_y_cierra`, `test_error_tras_un_token_marca_incompleto_y_no_emite_done`).

**Acceptance Scenarios**:

1. **Given** un cierre con `done`, **When** termina el stream, **Then** el turno se persiste una sola vez y `done` identifica conversación y mensaje (`test_stream_emite_open_meta_tokens_y_done_en_orden`).
2. **Given** un cierre con `fallback`, **When** termina el stream, **Then** el turno se persiste una sola vez y no se emitió `token` (`test_fallback_antes_del_primer_token_se_emite_y_cierra`).
3. **Given** un cierre con `error` tras un token, **When** termina el stream, **Then** no hay persistencia y la respuesta queda `incomplete` (`test_error_tras_un_token_marca_incompleto_y_no_emite_done`; [`docs/aceptacion-m2.md`](../../docs/aceptacion-m2.md), sección B).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El stream MUST aceptar solo `POST`; un `GET` MUST responder 405 sin llamar al proveedor.
- **FR-002**: La respuesta MUST declarar `text/event-stream`, `cache-control: no-store` y desactivar el buffering de proxies.
- **FR-003**: El contrato de eventos MUST incluir `open`, `meta` con fuentes, `token` incrementales, `done` con identificadores, `fallback` y `error`.
- **FR-004**: El primer evento MUST ser `open` y el primer `token` MUST llegar en menos de 2 s con el proveedor disponible.
- **FR-005**: Un fallo antes del primer token MUST completarse con `fallback` y `done`, sin `token`.
- **FR-006**: Un fallo después de emitir texto MUST cerrar con `error` e `incomplete: true`, sin `done` ni persistencia.
- **FR-007**: Una pregunta inválida MUST responder 422 con evento `error` e `incomplete: false`, sin llamar al proveedor.
- **FR-008**: Cupo agotado MUST responder 429 con `Retry-After` y evento `error`; un fallo de base MUST responder 503; en ambos casos sin abrir el proveedor.
- **FR-009**: La identidad anónima MUST emitirse en el stream y el proveedor MUST recibir el dueño derivado de la cookie.
- **FR-010**: El turno MUST persistirse exactamente una vez al cerrar con `done` o `fallback`, y ninguna vez al cerrar con `error`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `open` es el primer evento y el primer `token` aparece en menos de 2 s; la corrida real documentó mediana ~1,8 s y máximo 2,0 s ([`docs/aceptacion-m2.md`](../../docs/aceptacion-m2.md), sección C).
- **SC-002**: Dos fragmentos de respuesta producen exactamente los eventos `open`, `meta`, `token`, `token`, `done`, con un solo `done`.
- **SC-003**: Un `fallback` persiste 1 vez; un `error` tras token persiste 0 veces y marca `incomplete: true`.
- **SC-004**: Los rechazos 422, 429 y 503 no invocan al proveedor ni abren el stream de generación.
- **SC-005**: Cada turno cerrado con éxito produce un único par pregunta/respuesta en el historial.

## Assumptions

- El proveedor de LLM expone un modo de fragmentos; si falla antes del primer fragmento se usa la respuesta completa como `fallback`.
- La persistencia y la cuota son las de la especificación 016; esta spec cubre el transporte y el cierre del stream.
- Los eventos de traza internos no forman parte del contrato observable del visitante.
