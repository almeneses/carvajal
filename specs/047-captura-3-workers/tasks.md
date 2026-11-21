# Tareas: captura web duradera para tres workers (047)

**Fuentes**: [spec](spec.md) y [plan](plan.md).
`[P]` indica trabajo que puede ejecutarse en paralelo tras cumplir sus prerequisitos.

**Alcance de esta PR**: 047a. La activación de exactamente tres workers en
`compose.prod.yml` y el `CMD` del Dockerfile (fase 047b) la hace el Lead en `task-8`
después de verificar la coordinación de esta entrega; no se ejecuta aquí.

## Fase 1 — Caracterización del comportamiento actual

**Propósito**: fijar el contrato observable de hoy antes de mover el estado.

- [X] T001 [P] Caracterizar en `tests/test_capture_jobs.py` el registro en memoria: un solo
      job activo por instancia (segundo envío → `CaptureBusy`), cupo por owner en memoria
      (bloquea al mismo owner y deja pasar a otro), TTL y poda de jobs terminados, y el
      contenido del evento final `done`/`error`.
- [X] T002 [P] Caracterizar en `tests/test_api_capture.py` los contratos HTTP actuales:
      códigos 200/403/409/422/429, mensajes exactos, JSON de estado por `job_id` (claves
      `id`, `status`, `error`, `outcome`), formato SSE (`stage`/`done`/`error`), 404 de job
      inexistente y acceso por `job_id` desde un cliente sin cookie (permisos actuales).
- [X] T003 Ejecutar `env -u OLLAMA_MODEL make check` sobre la rama sin cambios de
      implementación y registrar el resultado base.

## Fase 2 — Esquema compartido

- [X] T004 Crear `migrations/versions/0008_capture_jobs.py` con las tablas
      `capture_control`, `capture_jobs`, `capture_events` y `capture_rate_limit`, la fila
      única de control y `downgrade()` reversible en orden inverso.
- [X] T005 [P] Declarar las mismas tablas en `apps/api/capture_jobs.py` (metadatos privados)
      y verificar en integración que columnas e índices coinciden con la migración.
- [X] T006 [P] Actualizar `tests/test_storage_migrations.py` para incluir las tablas nuevas
      en la comprobación de esquema y confirmar que la bajada a `base` no deja residuos.

## Fase 3 — Estado y progreso compartidos (US1)

- [X] T007 Implementar el backend PostgreSQL en `apps/api/capture_jobs.py`: lectura y
      escritura de jobs, eventos con secuencia, cierre transaccional con evento final y
      poda (TTL, tope de jobs, cupos viejos).
- [X] T008 Implementar en `apps/api/capture_jobs.py` la orquestación (`CaptureJobs`) sobre
      un backend intercambiable: hilo del runner, latido daemon, escrituras condicionadas
      al `lease_token` y evento final sintético de respaldo.
- [X] T009 Implementar el stream SSE desde la base en `apps/api/capture_jobs.py` y pasar
      `Last-Event-ID` desde `apps/api/views/config.py`; sin cambios en `apps/api/sse.py`.
- [X] T010 Cablear el backend PostgreSQL en `apps/api/main.py` y ampliar
      `tests/test_api_capture.py` para inyectar el doble en memoria sin tocar la base.

## Fase 4 — Exclusión global y cupo compartido (US2)

- [X] T011 Implementar el arranque atómico: fila única con `FOR UPDATE`, precedencia
      409 antes de 429, cupo deslizante compartido y `Retry-After` desde la marca más
      antigua.
- [X] T012 Implementar la reclamación en línea de un job con latido caducado al intentar
      una captura nueva, devolviendo 409 solo si el latido está fresco.

## Fase 5 — Interrupción y recuperación (US3)

- [X] T013 Implementar `recover_interrupted()` en `apps/api/capture_jobs.py`:
      transición condicional única, barrido de huérfanos y limpieza del control.
- [X] T014 Implementar la recuperación desde punto seguro: slugs candidatos derivados de
      los eventos y `sync_gold` idempotente, con registro y tolerancia a fallos; sin
      reanudación por URL.
- [X] T015 Implementar el apagado ordenado: marcar capturas locales como interrumpidas
      (condicionado al testigo), liberar el control y detener latidos.

## Fase 6 — Recursos, `/live` y `/health` (US4)

- [X] T016 Revisar el presupuesto de conexiones: conservar el ancho de pool por defecto
      (5 + 10) porque lo comparten chat, FAQ, captura y CLI, documentar el techo con tres
      workers, mantener el motor diferido y añadir el cierre (`dispose`) sin romper
      `@cache`.
- [X] T017 Añadir el `lifespan` en `apps/api/main.py`: recuperación best-effort al arrancar
      (tolera base caída) y apagado ordenado; confirmar que `/live` no consulta la base y
      que `/health` conserva su contrato.

## Fase 7 — Pruebas de integración con PostgreSQL real

- [X] T018 [P] `tests/test_capture_durable.py`: dos instancias con el mismo PostgreSQL;
      dos solicitudes simultáneas solo inician una captura; cupo por IP compartido.
- [X] T019 [P] `tests/test_capture_durable.py`: progreso disponible desde otra instancia y
      tras reconectar con `Last-Event-ID`, sin duplicar eventos; estado final idéntico.
- [X] T020 [P] `tests/test_capture_durable.py`: job interrumpido detectado y recuperado sin
      duplicar documentos; reclamación de un lease caducado; segunda pasada sin acciones.
- [X] T021 Verificar `/live` sin base y el contrato de `/health` en la app cableada; ajustar
      los dobles existentes sin romper la suite.

## Fase 8 — Puerta y entrega

- [X] T022 Ejecutar `env -u OLLAMA_MODEL make check`, `env -u OLLAMA_MODEL make
      test-integration`, `node --test tests/js/test_chat_voice.cjs`, y la migración
      `alembic upgrade head` + `alembic downgrade -1` en base de pruebas.
- [X] T023 Abrir el PR contra `dev` con labels `enhancement`, `backend`, `infra`, assignee
      `@me`, cuerpo en español (qué/por qué/diseño/cómo probar/evidencia/reversión/riesgos).
- [X] T024 (Lead, `task-8`) Activar exactamente tres workers en el servicio `app` de
      `compose.prod.yml` (`UVICORN_WORKERS`, por defecto 3); el `CMD` de la imagen conserva
      1 worker y el Compose lo sobreescribe. Documentar el retorno a 1 sin perder estado.
- [X] T025 Serializar el `setup()` de checkpointer y Store entre workers con un advisory
      lock de PostgreSQL, porque con tres procesos la inicialización del runtime puede
      coincidir.
- [X] T026 Prueba real con tres procesos uvicorn (`scripts/verificar_3_workers.py`):
      simultaneidad, cupo compartido, progreso y reconexión, caída de worker y
      recuperación sin duplicados, retorno a 1.
- [X] T027 Evidencia en `specs/047-captura-3-workers/evidencia-3-workers.md`, prueba del
      Compose (`tests/test_compose_workers_047.py`), arranque de la imagen y actualización
      de `CONSTITUTION.md` §9, `.specify/memory/constitution.md` y `specs/README.md`.

## Dependencias y paralelismo

- T001–T003 antes de cualquier cambio de implementación.
- T004 antes de T005, T007 y T011.
- T007–T010 (estado y progreso) antes de T011–T015.
- T016–T017 antes de T018–T021.
- T018–T020 pueden ejecutarse en paralelo si comparten fixtures; T021 cierra la suite.
- T024 depende de que T022 esté en verde y el PR esté listo para revisión.
