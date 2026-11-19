# Tasks: Monitoreo del agente con LangSmith

**Spec**: [`spec.md`](./spec.md) · **Plan**: [`plan.md`](./plan.md) · **Estado**: pendiente

## Format: `[ID] [P?] [Story] Description`

- **[P]**: puede ejecutarse en paralelo (archivos distintos)
- **[USn]**: historia de usuario de la spec
- **PR n**: agrupación sugerida en Pull Requests a `dev`

## Phase 1: Setup (infraestructura compartida)

- [ ] T001 Declarar `langsmith>=0.14` como dependencia directa en `pyproject.toml` y actualizar
      `uv.lock` con `uv lock` (ya llega por LangChain; se evita depender de un transitivo)
- [ ] T002 Añadir a `packages/core/src/carvajal/infrastructure/settings.py` las variables
      `monitoring_enabled`, `langsmith_api_key`, `langsmith_project`, `langsmith_endpoint`,
      `monitoring_sample_rate` (0..1), `monitoring_include_content` y `monitoring_timeout_ms`;
      validar rangos y documentar que la llave nunca se registra
- [ ] T003 [P] Documentar las variables en `.env.example` con valores seguros
      (`MONITORING_ENABLED=false`, `MONITORING_SAMPLE_RATE=0.2`,
      `MONITORING_INCLUDE_CONTENT=false`) y una nota de que no se usan `LANGCHAIN_TRACING_V2`
      ni `LANGSMITH_TRACING`

## Phase 2: Fundacional (bloquea las historias)

**PR 1 — Configuración y módulo de monitoreo**

- [ ] T004 Crear `packages/core/src/carvajal/infrastructure/monitoring.py` con
      `MonitoringStatus`, `monitoring_status(settings)`, `build_client(settings)` y
      `redact(payload, include_content)`; sin efectos si `monitoring_enabled` es falso
- [ ] T005 [P] `tests/test_monitoring.py`: la lista blanca conserva ruta, intención, herramienta,
      modelo, tokens, puntuaciones y errores; elimina pregunta, respuesta, prompt, fragmentos,
      nombres, teléfonos y `owner_key`; con contenido activo no redacta
- [ ] T006 Implementar `build_client` con `Client(hide_inputs=..., hide_outputs=...)`,
      `timeout_ms`, `auto_batch_tracing=True` y `tracing_error_callback` que solo registra en
      los logs; el endpoint es opcional y la llave sale del entorno
- [ ] T007 Implementar `TurnTracer` (protocolo y `NoopTracer`): `turn(request_id, thread_hash,
      router, question)` como *context manager*, muestreo con `random()` inyectable y estado
      `ok`/`cancelled`/`failed`; sin muestreo no se importa ni se construye el cliente
- [ ] T008 [P] `tests/test_monitoring.py`: tasa 0 no crea traza, tasa 1 sí, el muestreo usa la
      función inyectada, y un cliente que lanza no propaga la excepción al turno

**Checkpoint**: módulo probado sin red ni credenciales.

## Phase 3: US1 — Seguir un turno de punta a punta (P1) 🎯 MVP

**PR 2 — Traza del turno y salud**

- [ ] T009 [US1] Inyectar `TurnTracer` en `ChatService` (`apps/api/chat/service.py`) con valor
      por defecto desde `Settings`; abrir la traza después de resolver el hilo y cerrarla en
      `finally`, incluyendo cancelación y error
- [ ] T010 [US1] Pasar a la raíz `request_id` (contextvars de structlog), `thread_hash`
      (SHA-256 truncado), `router` (`settings.agent_router`) y, al cerrar, `route`, `intent`,
      `tool` y `outcome`; no usar `owner_key` ni el hilo crudo
- [ ] T011 [US1] Añadir las llamadas al LLM y los nodos del grafo como hijos automáticos con
      `tracing_context(parent=raíz)`; verificar que `ChatOpenAI`/`ChatOllama` quedan dentro del
      contexto y que el replay no genera traza
- [ ] T012 [US1] `tests/test_chat_tracing.py`: con tracer falso, un turno `direct`, uno `rag`,
      uno `tool` y uno `commercial` abren una raíz con la ruta correcta; la cancelación y el
      error cierran la traza una sola vez
- [ ] T013 [US1] `tests/test_chat_tracing.py`: el primer token no espera al cierre de la traza;
      con un tracer que duerme, el stream sigue emitiendo (no bloquea)
- [ ] T014 [US2] `apps/api/main.py`: `/health` incluye
      `monitoring: {enabled, sample_rate, content}` sin llave ni endpoint; `tests/test_api_health.py`
      cubre activo, apagado y sin credenciales
- [ ] T015 [P] [US2] Prueba de privacidad: una pregunta con nombre y teléfono no deja ese texto
      en el payload redactado ni en los metadatos de la raíz
- [ ] T016 [P] [US2] Prueba de aislamiento: cliente que lanza en `post()` y en `end()`; el turno
      responde, persiste y consume cuota igual, y el fallo queda en `monitoring.failed`

**Checkpoint**: US1 y US2 funcionan con el monitoreo apagado y con un doble; sin red.

## Phase 4: US3 — Comparar enrutadores y evaluaciones (P2)

**PR 3 — Experimentos de evaluación**

- [ ] T017 [US3] Exponer en `agent/routers/evaluation.py` las salidas por pregunta que ya
      calcula `evaluate_router` (ruta, intención, datos, latencia, inválidas) sin cambiar el
      informe local
- [ ] T018 [US3] Añadir `--langsmith` a `carvajal eval-router` (`packages/core/src/carvajal/cli.py`):
      con el flag, registra el experimento; sin el flag, no importa LangSmith ni toca la red
- [ ] T019 [US3] Crear el dataset de forma idempotente desde `data/eval/routing_questions.json`
      y registrar un experimento con commit, fecha, enrutador, modelo y métricas; reutilizar el
      dataset si ya existe
- [ ] T020 [P] [US3] Añadir `--langsmith` a `scripts/eval_retrieval.py` con el mismo contrato
      (Recall@10, nDCG@10, commit y fecha)
- [ ] T021 [US3] `tests/test_eval_experiments.py`: el flag apagado no importa LangSmith; el flag
      encendido usa un cliente falso; un fallo del servicio deja el informe local y no cambia
      el código de salida
- [ ] T022 [P] [US3] Documentar en `docs/resultados-router.md` cómo repetir una corrida como
      experimento y cómo comparar dos corridas

**Checkpoint**: las dos evaluaciones se registran sin volver a llamar al modelo.

## Phase 5: US4 — Marcar respuestas (P3)

**PR 4 — Marcas del visitante**

- [ ] T023 [US4] Migración `0007_chat_feedback.py`: tabla `chat_feedback` con `request_id`
      único, `useful` booleano y `created_at`; reversible con `downgrade`
- [ ] T024 [US4] Repositorio `apps/api/chat/feedback.py` con `record(request_id, useful)` como
      upsert idempotente y lectura por `request_id` para las pruebas
- [ ] T025 [US4] `POST /chat/feedback` en `apps/api/views/chat.py`: CSRF obligatorio, sin cuota,
      `request_id` válido, `useful` explícito; responde `204` y no guarda texto
- [ ] T026 [US4] Botones «Útil / No útil» en `apps/api/templates/chat.html` solo cuando el
      monitoreo está activo; HTMX sin recarga y deshabilitados tras marcar
- [ ] T027 [US4] `tests/test_feedback.py`: sin CSRF `403`; con monitoreo apagado no se muestran
      los botones; doble marca del mismo `request_id` no duplica fila; turno sin traza conserva
      la marca
- [ ] T028 [US4] Verificar que la marca no fuerza el muestreo ni crea una traza nueva

**Checkpoint**: P3 entregable por separado; si se pospone, el resto del monitoreo sigue.

## Phase 6: Pulido y transversal

**PR 5 — Constitución y documentación**

- [ ] T029 Actualizar `CONSTITUTION.md` §2 y §9 y `.specify/memory/constitution.md`: LangSmith
      entregado, modo de contenido, muestreo y apagado por defecto
- [ ] T030 [P] Actualizar `docs/despliegue-ovh.md` y `README.md` con las variables, el modo
      metadata-only de producción y el presupuesto de 5.000 trazas/mes (20 % de muestreo);
      añadir el bloque `monitoring` a la explicación de `/health`
- [ ] T031 [P] Documentar el plan de LangSmith (límite mensual, muestreo recomendado) y el
      procedimiento para apagar el monitoreo sin tocar el chat

**PR 6 — Evidencia**

- [ ] T032 Crear `docs/evidence/m2/monitoring.md` con: traza de ejemplo de un turno comercial,
      búsqueda por `request_id`, prueba de que producción no lleva texto, y comparación de dos
      experimentos
- [ ] T033 Ejecutar `make check` y una corrida manual con `MONITORING_ENABLED=true` y
      `MONITORING_INCLUDE_CONTENT=true` en local; registrar el resultado y los tiempos
- [ ] T034 Revisar cada SC de la spec contra la evidencia y anotar lo que quede pendiente

## Dependencies & Execution Order

- **Fase 1 → Fase 2**: sin el módulo y la configuración no se puede trazar nada.
- **US1 y US2 comparten la misma PR**: la traza sin política de privacidad no se entrega.
- **US3** depende de que la configuración y el cliente existan (Fase 2), no de US1.
- **US4** es independiente de US1–US3 salvo el estado del monitoreo para mostrar los botones.
- **Pulido** va al final; la enmienda de la constitución se hace en la PR que enciende la
  capacidad, no antes.

## Parallel Opportunities

- T003, T005, T008 y T015 pueden ir en paralelo con el resto de su fase.
- T020 y T022 pueden ir en paralelo con T018/T019 si se separan los archivos.
- T030 y T031 pueden ir en paralelo con T029.
- US3 y US4 pueden desarrollarse en paralelo por dos personas una vez cerrada la Fase 2.

## Implementation Strategy

1. **MVP**: PR 1 + PR 2 → una traza por turno, apagada por defecto, con salud y privacidad.
2. **Segundo incremento**: PR 3 → experimentos.
3. **Tercero**: PR 4 → marcas (P3).
4. **Cierre**: PR 5 + PR 6 → constitución, documentación y evidencia.

Cada PR deja `make check` en verde y no requiere credenciales de LangSmith para las pruebas.
