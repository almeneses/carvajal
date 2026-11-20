# Tareas: chat por voz (040)

**Fuentes**: [spec](spec.md), [plan](plan.md), [investigación](research.md),
[modelo](data-model.md), [contrato](contracts/chat-voz.md) y [quickstart](quickstart.md).
`[P]` indica trabajo que puede ejecutarse en paralelo tras cumplir sus prerequisitos.

## Fase 1 — Preparación

- [ ] T001 Definir la matriz reproducible de navegador con/sin STT, TTS y contexto seguro en `docs/pruebas-040.md`, siguiendo `specs/040-chat-por-voz/quickstart.md`.
- [ ] T002 [P] Preparar dobles de reconocimiento, síntesis, reloj y formulario con `node:test` en `tests/js/test_chat_voice.cjs`, sin micrófono, red ni dependencias npm de runtime.

## Fase 2 — Base común

- [ ] T003 Añadir pruebas de permiso `microphone=(self)` solo al documento HTML de `/chat`, denegación en otras rutas (incluido SSE), y CSP y frame-ancestors intactos en `tests/test_security.py`.
- [ ] T004 Implementar el permiso acotado de T003 en `apps/api/security.py` y documentar la excepción y el requisito de HTTPS en `CONSTITUTION.md`, `.specify/memory/constitution.md` y `docs/despliegue-ovh.md`.
- [ ] T005 Cargar `apps/api/static/chat-voice.js` desde `apps/api/templates/chat.html`; armar los controles base que solo se activan con JS y un estado visible/accesible en `apps/api/static/chat.css`. Sin `unsafe-inline`.

## Fase 3 — US1: dictar con revisión (P1)

**Prueba independiente:** dictar tres preguntas, editar y enviar una; ninguna se envía sola.
Comparar ruta y campos con la forma escrita y comprobar el fallback por permiso o soporte.

- [ ] T006 [P] [US1] Probar aviso previo, permiso denegado, silencio, borrador conservado, parada de **máximo 60 segundos por pregunta**, truncado a **máximo 500 caracteres** sin cortar un par sustituto UTF-16 y ausencia de envío en `tests/js/test_chat_voice.cjs`.
- [ ] T007 [P] [US1] Probar en `tests/test_api_chat.py` que el botón de dictar no envía, que el formulario mantiene el límite, token y ruta actuales y que el estado de voz tiene nombre accesible.
- [ ] T008 [US1] Implementar dictado en español con `SpeechRecognition`/`webkitSpeechRecognition` en `apps/api/static/chat-voice.js`; exigir contexto seguro, gestionar final, error y cancelación, usar `stop()` al llegar a 60 s y descartar callbacks tardíos.
- [ ] T009 [US1] Mostrar aviso antes del primer `start()`; recordar únicamente `dictationNoticeAccepted` (booleano, **apagado por defecto**) con fallback a memoria, preservar borrador si no hay resultado y ofrecer uso escrito en `apps/api/static/chat-voice.js` y `apps/api/templates/chat.html`.
- [ ] T010 [US1] Insertar texto final en el cursor/selección de `#pregunta`, aplicar el tope de 500 con aviso y normalizar cantidades inequívocas («doce onzas», «cinco mil vasos») en `apps/api/static/chat-voice.js`; probar contra las formas canónicas en `tests/js/test_chat_voice.cjs`.
- [ ] T011 [US1] Validar con tres preguntas de `data/eval/routing_questions.json` y documentar paridad de ruta/campos, ausencia de autoenvío y fallback en `docs/pruebas-040.md`.

## Fase 4 — US2: escuchar respuestas (P1)

**Prueba independiente:** escuchar un turno con tarjetas y otro con citas, comprobar datos
hablados, pausar/seguir/detener y activar lectura automática sin releer el historial.

- [ ] T012 [P] [US2] Probar controles, ausencia de autolectura inicial, memoria de la preferencia, lectura solo tras `done` y cancelación al ocultar, cambiar de hilo o dictar en `tests/js/test_chat_voice.cjs`.
- [ ] T013 [P] [US2] Añadir aserciones para datos visibles de tarjeta (incluida ausencia de precio), títulos citados y controles de historial/stream en `tests/test_productos.py` y `tests/test_api_chat.py`.
- [ ] T014 [US2] Incorporar controles «Escuchar», «Pausar», «Reanudar» y «Detener» en `apps/api/templates/_chat_thread.html` y `apps/api/templates/_chat_stream.html`; marcar datos visibles de las tarjetas en `apps/api/templates/_productos.html`, coordinando los selectores si la 039 toca esa plantilla.
- [ ] T015 [US2] Emitir `chat:turn-complete` desde `apps/api/static/chat-stream.js` al finalizar un stream con `done`, después de formatear el turno y liberar el formulario; no emitir para error o historial.
- [ ] T016 [US2] Implementar síntesis en español y `autoRead` (booleano, **apagado por defecto**); construir texto/segmentos desde el DOM, sin URLs ni códigos técnicos, y coordinar voz y visibilidad en `apps/api/static/chat-voice.js`.
- [ ] T017 [US2] Verificar lectura manual y automática, teclado, rechazo de reproducción automática y comportamiento con lector de pantalla según `docs/pruebas-040.md`.

## Fase 5 — US3: conversación manos libres (P3)

**Prueba independiente:** hacer una pregunta comercial y un seguimiento por voz en el mismo
hilo; comprobar salida por «terminar», silencio y error sin enviar esas señales como preguntas.

- [ ] T018 [P] [US3] Probar el ciclo envío → lectura → reescucha, deduplicación, **10 segundos sin voz**, «terminar» aislado y parada ante error, cuota, pestaña oculta o síntesis cancelada en `tests/js/test_chat_voice.cjs`.
- [ ] T019 [US3] Exponer activación explícita y aviso de autoenvío en `apps/api/templates/chat.html`; modo temporal **apagado al entrar o recargar**, solo si dictado, síntesis y streaming están disponibles.
- [ ] T020 [US3] Conectar en `apps/api/static/chat-voice.js` un único `form.requestSubmit()` por resultado final válido, con el ID del ciclo y el hilo visible; reescuchar solo al acabar la síntesis normalmente y desactivar ante error, silencio, «terminar», pérdida de visibilidad o parada manual.
- [ ] T021 [US3] Distinguir el origen manos libres solo en el cliente de `apps/api/static/chat-stream.js`; notificar `chat:turn-failed`, devolver el borrador y evitar reenvío automático al fallar, sin cambios a CSRF/cuota ni al fallback de envíos manuales.

## Fase 6 — Validación y cierre

- [ ] T022 Medir SC-001…SC-003 con la muestra y el protocolo de `specs/040-chat-por-voz/quickstart.md`; registrar paridad, p95 y navegadores en `specs/040-chat-por-voz/evidencia.md`.
- [ ] T023 Verificar SC-004/005 (ningún audio en servidor/BD, auditoría axe y teclado) y los controles de CSRF, cuota y límites con `tests/test_security.py`, `tests/test_api_stream.py` y `specs/040-chat-por-voz/evidencia.md`.
- [ ] T024 Ejecutar `make check` y `node --test tests/js/test_chat_voice.cjs`, registrar resultados en `specs/040-chat-por-voz/evidencia.md` y actualizar `specs/040-chat-por-voz/spec.md`, `CONSTITUTION.md` y `.specify/memory/constitution.md` tras verificar los criterios de la entrega completa.

## Dependencias, paralelismo y entrega

- T001 y T002 son independientes; T003 precede a T004. La base precede a US1/US2.
- Preparar pruebas de cliente y de servidor de US1 (T006/T007) en paralelo; conectar UI
  y lógica al integrarlas. En US2, T012/T013 pueden avanzar en paralelo; T016 depende de
  los marcadores y del evento T014/T015.
- US2 no necesita dictado para probar lectura, pero integra la exclusión mutua con US1.
  Implementar las dos en serie si comparten `chat-voice.js`. US3 requiere ambas; T018
  puede prepararse mientras se define la UI T019, antes de integrar T020/T021.
- Primer incremento: US1. **MVP**: US1 + US2, con evidencia de los criterios aplicables.
  Segundo incremento: US3. Si faltan TLS o soporte, declararlos y no anunciar micrófono
  o manos libres como probados en ese entorno.
