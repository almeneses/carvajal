# Tareas: chat por voz (040)

**Fuentes**: [spec](spec.md), [plan](plan.md), [investigación](research.md),
[modelo](data-model.md), [contrato](contracts/chat-voz.md) y [quickstart](quickstart.md).
`[P]` indica trabajo que puede ejecutarse en paralelo tras cumplir sus prerequisitos.

## Fase 1 — Preparación

- [X] T001 Definir la matriz reproducible de navegador con/sin STT, TTS y contexto seguro en `docs/pruebas-040.md`, siguiendo `specs/040-chat-por-voz/quickstart.md`.
- [X] T002 [P] Preparar dobles de voz, reloj y formulario y una prueba inicial en `tests/js/test_chat_voice.cjs`; en el mismo PR, fijar Node `24.16.0` y ejecutar `node --test tests/js/test_chat_voice.cjs` como paso obligatorio de `Calidad` en `.github/workflows/ci.yml`. Sin micrófono, red para las pruebas ni dependencias npm de runtime.

## Fase 2 — Base común

- [X] T003 Añadir pruebas de permiso `microphone=(self)` solo al documento HTML de `/chat`, denegación en otras rutas (incluido SSE), y CSP y frame-ancestors intactos en `tests/test_security.py`.
- [X] T004 Implementar el permiso acotado de T003 en `apps/api/security.py` y documentar la excepción y el requisito de HTTPS en `CONSTITUTION.md`, `.specify/memory/constitution.md` y `docs/despliegue-ovh.md`.
- [X] T005 Cargar `apps/api/static/chat-voice.js` desde `apps/api/templates/chat.html`; armar los controles base que solo se activan con JS y un estado visible/accesible en `apps/api/static/chat.css`. Sin `unsafe-inline`.

## Fase 3 — US1: dictar con revisión (P1)

**Prueba independiente:** dictar tres preguntas, editar y enviar una; ninguna se envía sola.
Comparar ruta y campos con la forma escrita y comprobar el fallback por permiso o soporte.

- [X] T006 [P] [US1] Probar aviso previo, permiso denegado, silencio, borrador conservado, parada de **máximo 60 segundos por pregunta**, truncado a **máximo 500 caracteres** sin cortar un par sustituto UTF-16 y ausencia de envío en `tests/js/test_chat_voice.cjs`.
- [X] T007 [P] [US1] Probar en `tests/test_api_chat.py` que el botón de dictar no envía, que el formulario mantiene el límite, token y ruta actuales y que el estado de voz tiene nombre accesible.
- [X] T008 [US1] Implementar dictado en español con `SpeechRecognition`/`webkitSpeechRecognition` en `apps/api/static/chat-voice.js`; exigir contexto seguro, gestionar final, error y cancelación, usar `stop()` al llegar a 60 s y descartar callbacks tardíos.
- [X] T009 [US1] Mostrar aviso antes del primer `start()`; recordar únicamente `dictationNoticeAccepted` (booleano, **apagado por defecto**) con fallback a memoria, preservar borrador si no hay resultado y ofrecer uso escrito en `apps/api/static/chat-voice.js` y `apps/api/templates/chat.html`.
- [X] T010 [US1] Insertar texto final en el cursor/selección de `#pregunta`, aplicar el tope de 500 con aviso y normalizar cantidades inequívocas («doce onzas», «cinco mil vasos») en `apps/api/static/chat-voice.js`; probar contra las formas canónicas en `tests/js/test_chat_voice.cjs`.
- [X] T011 [US1] Validar con tres preguntas de `data/eval/routing_questions.json` y documentar paridad de ruta/campos, ausencia de autoenvío y fallback en `docs/pruebas-040.md`.

## Fase 4 — US2: escuchar respuestas (P1)

**Prueba independiente:** escuchar un turno con tarjetas y otro con citas, comprobar datos
hablados, pausar/seguir/detener y activar lectura automática sin releer el historial.

- [X] T012 [P] [US2] Probar controles, preferencia apagada por defecto y persistente, autolectura solo tras `done`, lectura manual sin streaming o al recargar historial y cancelación al ocultar, cambiar de hilo o dictar en `tests/js/test_chat_voice.cjs`.
- [X] T013 [P] [US2] Añadir aserciones para datos visibles de tarjeta (incluida ausencia de precio), títulos citados y controles de historial/stream en `tests/test_productos.py` y `tests/test_api_chat.py`.
- [X] T014 [US2] Incorporar controles «Escuchar», «Pausar», «Reanudar» y «Detener» en `apps/api/templates/_chat_thread.html` y `apps/api/templates/_chat_stream.html`; marcar datos visibles de las tarjetas en `apps/api/templates/_productos.html`, coordinando los selectores si la 039 toca esa plantilla.
- [X] T015 [US2] Emitir `chat:turn-complete` desde `apps/api/static/chat-stream.js` al finalizar un stream con `done`, después de formatear el turno y liberar el formulario; no emitir para error o historial.
- [X] T016 [US2] Implementar síntesis en español y `autoRead` (booleano, **apagado por defecto**); construir texto/segmentos desde el DOM, sin URLs ni códigos técnicos, y coordinar voz y visibilidad en `apps/api/static/chat-voice.js`.
- [X] T017 [US2] Verificar autolectura de `done`, lectura manual tras POST sin streaming o al recargar, teclado, bloqueo de reproducción automática y comportamiento con lector de pantalla según `docs/pruebas-040.md`.

## Fase 5 — US3: conversación manos libres (P3)

**Prueba independiente:** hacer una pregunta comercial y un seguimiento por voz en el mismo
hilo; comprobar salida por «terminar», silencio y error sin enviar esas señales como preguntas.

- [X] T018 [P] [US3] Probar ciclo y deduplicación, **10 segundos sin voz**, «terminar» y parada ante error, cuota, ocultación o síntesis cancelada. Cubrir en `tests/js/test_chat_voice.cjs` activación con borrador previo y escritura con el modo activo seguida de un resultado tardío: no borrar ni enviar ese texto.
- [X] T019 [US3] Exponer activación explícita y aviso de autoenvío en `apps/api/templates/chat.html`; modo temporal **apagado al entrar o recargar**, disponible solo con soporte de dictado, síntesis y streaming y **borrador vacío**.
- [X] T020 [US3] Conectar un `form.requestSubmit()` por resultado final válido en `apps/api/static/chat-voice.js`; exigir ciclo vigente, hilo visible y **borrador vacío** antes de insertar solo esa transcripción. Si hay escritura manual, desactivar, preservar el borrador e ignorar resultados tardíos; conservar las salidas por error, silencio, «terminar», ocultación y parada manual. Reescuchar solo tras síntesis normal.
- [X] T021 [US3] Distinguir el origen manos libres solo en el cliente de `apps/api/static/chat-stream.js`; notificar `chat:turn-failed`, devolver el borrador y evitar reenvío automático al fallar, sin cambios a CSRF/cuota ni al fallback de envíos manuales.

## Fase 6 — Validación y cierre

- [ ] T022 Medir SC-001…SC-003 como fija `specs/040-chat-por-voz/quickstart.md`: 40 intentos por navegador y flujo con marcas monotónicas, p95, fallos y proporción bajo umbral; registrar resultados y paridad en `specs/040-chat-por-voz/evidencia.md`.
- [X] T023 Verificar SC-004/005 (sin audio persistido, axe y teclado); cubrir CSRF, CSP, longitud y cuota por visitante en `tests/test_security.py`, `tests/test_api_chat.py`, `tests/test_api_stream.py` y `tests/js/test_chat_voice.cjs`. Confirmar que dictar o escuchar sin enviar no cobra cuota, y que el texto de voz usa el mismo flujo que el escrito. Documentar en `specs/040-chat-por-voz/evidencia.md`.
- [ ] T024 Ejecutar `make check` y `node --test tests/js/test_chat_voice.cjs`, registrar resultados en `specs/040-chat-por-voz/evidencia.md` y actualizar `specs/040-chat-por-voz/spec.md`, `CONSTITUTION.md` y `.specify/memory/constitution.md` tras verificar los criterios de la entrega completa.

## Dependencias, paralelismo y entrega

- T001 y T002 son independientes; T003 precede a T004. La base precede a US1/US2.
- T022 y T024 quedan abiertas: SC-001–SC-003 se midieron en Chrome 157/Linux
  (ver `evidencia.md`), pero falta repetir la matriz de navegadores. T024 espera
  a que esa matriz esté completa.
- Preparar pruebas de cliente y de servidor de US1 (T006/T007) en paralelo; conectar UI
  y lógica al integrarlas. En US2, T012/T013 pueden avanzar en paralelo; T016 depende de
  los marcadores y del evento T014/T015.
- US2 no necesita dictado para probar lectura, pero integra la exclusión mutua con US1.
  Implementar las dos en serie si comparten `chat-voice.js`. US3 requiere ambas; T018
  puede prepararse mientras se define la UI T019, antes de integrar T020/T021.
- Primer incremento: US1. **MVP**: US1 + US2, con evidencia de los criterios aplicables.
  Segundo incremento: US3. Si faltan TLS o soporte, declararlos y no anunciar micrófono
  o manos libres como probados en ese entorno.
