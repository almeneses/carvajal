# Feature Specification: Identidad anónima, chat persistente y cuota diaria

**Feature Branch**: `016-identidad-chat-cuota`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-11-13 → 2026-11-14
**Evidence**: [`CONSTITUTION.md` §9](../../CONSTITUTION.md) (2026-11-13 y 2026-11-14); [`tests/test_chat_identity.py`](../../tests/test_chat_identity.py); [`tests/test_chat_store.py`](../../tests/test_chat_store.py); [`tests/test_quota.py`](../../tests/test_quota.py); [`tests/test_usage_repository.py`](../../tests/test_usage_repository.py); [`tests/test_api_chat.py`](../../tests/test_api_chat.py)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Conservar una identidad anónima entre visitas (Priority: P1)

El navegador genera una sola vez un hash de 256 bits, lo guarda en `localStorage` y lo refleja en una cookie. El servidor no usa el valor crudo: deriva un dueño con HMAC y conserva además una cookie firmada como respaldo. No es autenticación: solo separa conversaciones anónimas.

**Why this priority**: sin identidad estable, recargar la página pierde el hilo y la cuota no se puede cobrar a nadie.

**Independent Test**: presentar dos veces el mismo hash de visitante y comprobar que el dueño derivado es igual, distinto del hash y de 64 caracteres; manipular la cookie firmada y comprobar que no valida (`test_la_identidad_de_localstorage_deriva_un_dueno_estable`, `test_cookie_manipulada_no_verifica`).

**Acceptance Scenarios**:

1. **Given** un hash de visitante de 64 caracteres, **When** se pide el dueño dos veces, **Then** coincide, no es el hash crudo y tiene 64 caracteres; mayúsculas y minúsculas del hash producen el mismo dueño (`test_la_identidad_de_localstorage_deriva_un_dueno_estable`).
2. **Given** una cookie firmada válida, **When** se verifica, **Then** devuelve el dueño; una firma manipulada, vacía o no ASCII no verifica y no lanza (`test_cookie_manipulada_no_verifica`, `test_una_firma_no_ascii_no_lanza_y_no_verifica`).
3. **Given** una cookie de visitante inválida, **When** hay cookie firmada, **Then** se usa la firmada; una visitante válida tiene precedencia sobre la firmada (`test_una_cookie_de_visitante_invalida_cae_a_la_cookie_firmada`, `test_la_cookie_de_visitante_tiene_precedencia_sobre_la_firmada`).
4. **Given** HTTP o HTTPS, **When** se emite la cookie, **Then** es `HttpOnly`, `SameSite=Lax`, dura 180 días y solo marca `Secure` sobre HTTPS (`test_la_cookie_http_es_httponly_lax_y_sin_secure`, `test_la_cookie_https_marca_secure`).

### User Story 2 - Recargar y recuperar la conversación (Priority: P2)

El visitante recarga el chat y ve sus turnos en orden, con las fuentes citadas. Puede abrir un hilo anterior, crear uno nuevo y reiniciar sin perder lo anterior. Un turno rechazado o fallido no se persiste.

**Why this priority**: el chat persistente es lo que separa un formulario de un asistente usable.

**Independent Test**: enviar una pregunta por `POST /chat`, recargar con la cookie emitida y comprobar que aparecen pregunta y respuesta en orden; verificar que un 422 no persiste (`test_post_ok_redirige_emite_cookie_y_conserva_el_historial`, `test_post_vacio_devuelve_422_sin_persistir`).

**Acceptance Scenarios**:

1. **Given** una conversación con turnos, **When** se recarga `/chat` con la cookie, **Then** el historial aparece en orden con fuentes citadas (`test_get_con_cookie_muestra_el_historial_en_orden`).
2. **Given** 30 turnos persistidos (60 mensajes), **When** se pide el historial, **Then** devuelve los 50 mensajes más recientes en orden ascendente (`test_history_limita_a_los_50_mensajes_mas_recientes`).
3. **Given** un hilo seleccionado, **When** se pide `/chat?thread=...`, **Then** se carga su historial y el hilo se marca activo; `/chat/new` redirige al hilo nuevo (`test_get_thread_selecciona_el_historial_y_marca_el_hilo_activo`, `test_nuevo_hilo_redirige_al_hilo_nuevo_y_abre_el_panel`).
4. **Given** un fallo del proveedor, **When** se responde 503, **Then** no se persiste el turno y el historial previo se conserva (`test_fallos_del_proveedor_devuelven_503_sin_filtrar_ni_persistir`, `test_el_error_503_conserva_el_historial_previo`).

### User Story 3 - Limitar el consumo diario por visitante (Priority: P3)

Cada visitante anónimo dispone de un cupo diario de preguntas. Al agotarlo recibe 429 con la hora de reinicio; un fallo de base al consultarlo responde 503 y no llega al modelo. Dos peticiones simultáneas no pueden gastar el mismo último cupo.

**Why this priority**: la cuota protege el costo del LLM y se evaluó como requisito de la ola 4.

**Independent Test**: conceder 2 de 2 peticiones a un visitante y comprobar que la tercera se rechaza con `Retry-After`; verificar que otro visitante y otro día tienen su propio contador (`test_concede_hasta_el_limite_y_despues_bloquea`, `test_visitantes_y_dias_son_independientes`).

**Acceptance Scenarios**:

1. **Given** un límite de 2, **When** se hacen tres peticiones, **Then** las dos primeras se conceden y la tercera no, con `retry_after` hasta la medianoche local (`test_concede_hasta_el_limite_y_despues_bloquea`).
2. **Given** una reserva concurrente por un único cupo, **When** dos sesiones compiten, **Then** solo una gana y el contador queda en 1 (`test_reserva_concurrente_no_supera_el_limite`).
3. **Given** el cupo agotado, **When** se envía la pregunta, **Then** la web responde 429 con `Retry-After` y el texto de límite diario sin llamar al modelo (`test_ask_agota_la_cuota_diaria`, `test_la_cuota_bloquea_antes_de_llamar_al_modelo`).
4. **Given** un fallo de base al consultar la cuota, **When** se pregunta, **Then** responde 503 genérico y no llama al proveedor (`test_fallo_de_base_al_consultar_la_cuota_devuelve_503`).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El servidor MUST aceptar un hash de visitante de 256 bits generado por el navegador y derivar de él un dueño estable, sin guardar ni usar el hash crudo como clave.
- **FR-002**: La identidad MUST viajar en cookies `HttpOnly` y `SameSite=Lax`, con 180 días de vigencia y `Secure` solo sobre HTTPS.
- **FR-003**: Una cookie ausente, manipulada o inválida MUST regenerar la identidad sin romper la página ni el turno.
- **FR-004**: El chat MUST persistir cada turno como pregunta y respuesta, con sus fuentes y en orden.
- **FR-005**: El historial MUST devolver como máximo los 50 mensajes más recientes, en orden ascendente.
- **FR-006**: El chat MUST permitir seleccionar un hilo por parámetro y crear uno nuevo, conservando los anteriores.
- **FR-007**: Una pregunta vacía o de más de 500 caracteres MUST dar 422 sin persistir; un fallo del proveedor MUST dar 503 sin persistir y conservando el historial previo.
- **FR-008**: La cuota MUST ser diaria y por visitante anónimo, con 20 preguntas por defecto y medianoche calculada en `QUOTA_TIMEZONE`.
- **FR-009**: Al agotar el cupo MUST responderse 429 con `Retry-After` hasta la próxima medianoche local; un fallo de base MUST dar 503 antes de llamar al modelo.
- **FR-010**: La reserva de cupo MUST ser atómica: bajo concurrencia no puede superarse el límite.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: El dueño derivado del hash de `localStorage` es estable, tiene 64 caracteres y no coincide con el hash (pruebas de identidad).
- **SC-002**: La cookie de sesión dura 180 días, es `HttpOnly` y `SameSite=Lax`, y solo lleva `Secure` sobre HTTPS.
- **SC-003**: De 60 mensajes persistidos, el historial devuelve exactamente 50 y descarta los 10 más antiguos.
- **SC-004**: Con el cupo agotado, la respuesta 429 incluye `Retry-After` igual a los segundos que faltan para la medianoche local (3600 a las 23:00 en Bogotá).
- **SC-005**: Un límite de cero no concede ni escribe contadores; dos reservas concurrentes del último cupo producen exactamente una concesión.

## Assumptions

- La identidad es anónima y portadora: no hay autenticación ni cuentas de usuario; el `user_id` autenticado llega en una ola posterior.
- El cupo cuenta preguntas del chat y del stream por igual; las pantallas de lectura no consumen.
- Las tablas de conversación y de uso diario se despliegan con migraciones versionadas.
