# Plan de implementación: captura web duradera para tres workers

**Rama**: `feat/047-captura-durable` | **Fecha**: 2026-11-20 | **Spec**: [spec.md](spec.md)

**Fase**: 047a (Fase 5, primera parte). La activación de exactamente tres workers en
`compose.prod.yml` y el `CMD` del Dockerfile es la entrega posterior `task-8`; este plan NO
los toca.

## Resumen

Mover a PostgreSQL el estado operativo de la captura web: el job, sus eventos de progreso,
la exclusión global y el cupo por IP. La app sigue siendo una sola FastAPI/Jinja2 con
PostgreSQL, Qdrant y los proveedores actuales; no se añaden Redis, Celery, microservicios ni
dependencias nuevas. El estado en memoria se conserva como doble de pruebas (misma API
pública de `CaptureJobs`), y la implementación real usa una migración Alembic nueva y el
motor SQLAlchemy que ya existe.

La exclusión global se resuelve con **una fila única** (`capture_control`) tomada con
`SELECT … FOR UPDATE` dentro de la misma transacción que valida el cupo, marca el job
anterior como interrumpido si su latido caducó e inserta el nuevo job. La detección de
interrumpidos usa un **latido persistido** (`heartbeat_at`) y una transición condicional
(`… WHERE status IN ('pending','running') RETURNING …`) que garantiza que un solo worker
registra cada interrupción. La recuperación vuelve a ejecutar la sincronización idempotente
de la spec 037 sobre los slugs que alcanzaron a escribirse en Gold; **no** se reanuda la
captura por URL.

## Contexto técnico

| Aspecto | Decisión |
|---|---|
| Runtime | FastAPI + uvicorn, un solo proceso por worker; el estado compartido vive en PostgreSQL |
| Esquema | Migración Alembic nueva `0008_capture_jobs` (reversible, sin editar migraciones históricas) |
| Tablas | `capture_jobs`, `capture_events`, `capture_control` (fila única), `capture_rate_limit` |
| Exclusión | Fila única + `SELECT … FOR UPDATE`; latido con umbral de caducidad; reclamación atómica del job caducado y marcador de recuperación |
| Progreso | Eventos persistidos por job con secuencia; SSE los reproduce desde cualquier worker; `Last-Event-ID` para reconexión sin duplicados |
| Cupo por IP | Contador deslizante en `capture_rate_limit` indexado por un resumen HMAC estable de la IP (nunca la IP en claro), contado dentro de la transacción que ya serializa el arranque |
| Recuperación | `sync` idempotente completo de la capa core (`only=None`, sin poda) antes de aceptar la siguiente captura; nunca captura reanudada |
| Permisos | Sin cambios: `job_id` opaco como capacidad, sin cookie ni autenticación, sin exponer IP |
| Recursos | Ancho de pool por defecto intacto (lo comparten chat, FAQ y CLI), presupuesto documentado y medido, inicialización diferida tolerante a base caída, `dispose()` en el apagado y jobs locales marcados interrumpidos |
| Tiempos | Configurables por `Settings` con los valores actuales por defecto: `CAPTURE_HEARTBEAT_SECONDS` (15 s), `CAPTURE_STALE_SECONDS` (120 s) y `CAPTURE_JOB_TTL_SECONDS` (3600 s) |
| Pruebas | Unitarias con doble en memoria; integración con PostgreSQL real (testcontainers) y dos instancias simuladas |
| Contratos | `POST /configuracion/captura`, `GET …/{job_id}`, `GET …/{job_id}/eventos` con los mismos códigos, JSON y eventos; `/live` y `/health` intactos |

## Constitution Check

| Principio | Evaluación |
|---|---|
| I. Constitución | Se actualiza el historial de decisiones y el alcance en la PR de la capacidad. Esta PR solo toca los archivos de su ámbito; la proyección para Spec Kit la sincroniza el Lead si aplica. |
| II. Corpus | No cambia Gold, la identidad por fuente, las dos huellas ni las capas. La recuperación reutiliza `sync`; la captura conserva `flock` y escritura atómica. |
| III. Fuentes | No se generan ni transforman fuentes; se recupera lo ya capturado. |
| IV. Calidad | `make check` y `make test-integration` deben quedar en verde; la migración se prueba arriba/abajo. Los dobles no requieren Docker; la integración sí (testcontainers, ya en el proyecto). |
| V. Captura responsable | Se conservan SSRF, `robots.txt`, límite de URLs, timeouts, CSRF y cupo por IP; el cupo pasa a ser compartido, lo que lo hace más estricto, no menos. |
| VI. Pull Requests | PR independiente contra `dev`, con labels y assignee; sin merge ni aprobación. |

**Revisión del diseño**: pasa. No se añade infraestructura externa; el estado operativo
es lo único que cambia de sitio. El riesgo de migración se cubre con una bajada reversible y
compatibilidad hacia atrás: el código anterior no conoce las tablas nuevas y puede seguir
funcionando si se revierte la imagen (el `downgrade` es opcional para la reversión).

## Investigación y alternativas descartadas

1. **Advisory locks de PostgreSQL para la exclusión.** `pg_advisory_lock` libera solo al
   cerrar la sesión y la sesión vuelve al pool: administrarlo exige una conexión dedicada y
   `pg_advisory_unlock` explícito en cada salida, y no deja rastro consultable. Para detectar
   interrumpidos haría falta igualmente una marca con latido. Descartado por operativa y
   por prueba: la fila única es inspeccionable, se puede probar con dos motores y permite
   distinguir «muerto» de «vivo con latido».
2. **Redis/Celery para la cola de jobs.** Prohibido por el objetivo y desproporcionado para
   una captura global a la vez.
3. **Una captura por worker con serialización por `flock`.** `flock` ya serializa Gold, pero
   tres workers lanzarían tres capturas que se turnan minutos y consumirían cuota y conexiones;
   además el contrato actual (409 al segundo envío) se rompería. Descartado.
4. **Reanudación exacta por URL.** El pipeline no guarda un cursor por semilla y prometerlo
   sin implementarlo y probarlo sería deshonesto. Se recupera desde el punto seguro que ya
   existe: Gold escrito es la verdad; `sync` lo reconcilia.
5. **Tablas en el `metadata` global de `tables.py`.** Habría que editar un archivo compartido
   con otras specs y que no pertenece al ámbito de esta tarea. Las tablas de captura se
   declaran en `apps/api/capture_jobs.py` (mismo esquema que la migración) y la migración es
   la fuente de verdad del DDL; una prueba de integración verifica que coinciden.
6. **Conexión dedicada por stream SSE.** Mantener una conexión por cliente durante minutos
   agotaría el pool. El stream consulta en intervalos cortos con sesiones breves y libera la
   conexión entre consultas.

## Diseño

### 1. Esquema (migración `0008_capture_jobs`)

Todo el DDL es nuevo y reversible; no se toca ninguna migración histórica.

```text
capture_control                         -- fila única: exclusión de todo el despliegue
  id             smallint PK, CHECK (id = 1)
  job_id         text NULL
  lease_token    text NULL              -- testigo del worker que posee la captura
  worker         text NOT NULL DEFAULT ''
  heartbeat_at   timestamptz NULL
  updated_at     timestamptz NOT NULL DEFAULT now()

capture_jobs
  id             text PK
  status         text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','running','done','error','interrupted'))
  request        jsonb NOT NULL             -- {urls, scope, respect_robots, limit}
  outcome        jsonb NULL
  error          text NOT NULL DEFAULT ''
  worker         text NOT NULL DEFAULT ''
  created_at     timestamptz NOT NULL DEFAULT now()
  updated_at     timestamptz NOT NULL DEFAULT now()   -- latido
  started_at     timestamptz NULL
  finished_at    timestamptz NULL
  -- índices: (status, updated_at) para la detección; (created_at) para la poda
  -- NO guarda la IP: el permiso es el job_id y el cupo usa owner_hash

capture_events                          -- progreso reproducible por cualquier worker
  seq            bigint GENERATED … PK  -- orden global; se ordena por (job_id, seq)
  job_id         text NOT NULL REFERENCES capture_jobs(id) ON DELETE CASCADE
  name           text NOT NULL           -- stage | done | error
  data           jsonb NOT NULL DEFAULT '{}'
  created_at     timestamptz NOT NULL DEFAULT now()
  -- índice: (job_id, seq)

capture_rate_limit                      -- cupo deslizante compartido por IP
  id             bigint GENERATED … PK
  owner_hash     text NOT NULL           -- HMAC(sha256) estable de la IP; nunca la IP
  job_id         text NULL
  created_at     timestamptz NOT NULL DEFAULT now()
  -- índice: (owner_hash, created_at)
```

La migración inserta la fila `capture_control (id = 1)` con todo en `NULL` y, en
`downgrade()`, elimina las cuatro tablas en orden inverso. `documents`, `chunks`,
`document_versions`, `conversations`, `messages`, `usage_day` y `chat_feedback` no se tocan.

### 2. Exclusión global y arranque atómico de una captura

`PostgresCaptureBackend.acquire()` usa **dos transacciones cortas** con un marcador de
recuperación en medio:

1. **Reserva** (`BEGIN` … `COMMIT`): `SELECT job_id, lease_token, heartbeat_at FROM
   capture_control WHERE id = 1 FOR UPDATE` serializa entre procesos.
   - Si hay `job_id` con latido fresco y ese job sigue `pending`/`running`: `CaptureBusy`
     (409, mismo mensaje actual).
   - Si hay `job_id` con latido caducado, o apunta a un job ya terminal: transición
     condicional `UPDATE capture_jobs SET status = 'interrupted', … WHERE status IN
     ('pending','running') RETURNING …` (solo un worker la gana) y la fila de control pasa a
     **marcador de recuperación**: conserva `job_id`, guarda `heartbeat_at = now()` y
     `worker = yo`, con `lease_token = NULL`.
   - Si no hay `job_id`, el marcador se toma igual (latido fresco) para que otra solicitud no
     entre mientras se recupera.
2. **Recuperación ordenada** (fuera de transacción, bajo `corpus_lock`): sincronización
   **completa** de la capa core con `only=None` y sin poda (`sync_gold` → `sync_corpus`), que
   converge Gold → PostgreSQL → Qdrant. Se ejecuta antes de aceptar la captura nueva; si
   falla, se registra y se continúa (la nueva captura volverá a sincronizar al terminar).
3. **Inicio** (`BEGIN` … `COMMIT`): con la fila de control aún retenida por el marcador,
   cuenta el cupo por `owner_hash` en la ventana; si no queda, calcula `Retry-After` con la
   marca más antigua, limpia el marcador y lanza `CaptureRateLimited` (429). Si queda,
   inserta el job nuevo (`pending`), registra la marca de cupo y actualiza la fila de control
   con `job_id`, `lease_token`, `worker` y `heartbeat_at = now()`.
4. Si cualquier paso falla, el marcador se libera en `finally` y no queda ni job nuevo ni
   cupo consumido. La precedencia actual se conserva: primero «en curso», después «límite».

El cupo se calcula con `owner_hash = HMAC-SHA256(SESSION_SECRET, ip)` cuando hay secreto y,
en desarrollo, con `sha256("carvajal-captura:" + ip)`; el valor es estable entre workers y no
permite leer la IP desde la tabla. Ni el hash ni la IP se devuelven en las respuestas ni se
escriben en los logs.

El `lease_token` (UUID) es el testigo del worker: **todas** las escrituras del job
(`mark_running`, latido, evento, cierre) van condicionadas a
`WHERE id = :job AND lease_token = :token AND status = 'running'`. Si el job fue reclamado
por otro worker, el worker viejo deja de escribir estado; si su hilo sigue vivo y termina,
solo deja Gold, que es idempotente y la siguiente `sync` reconcilia.

### 3. Progreso consultable desde cualquier worker

- El runner sigue emitiendo `CaptureEvent`; `CaptureJobs` los persiste como eventos
  `stage` con `{stage, url, detail}` y sella el latido.
- El cierre persiste en **una sola transacción** el estado terminal (`done`/`error`), el
  `outcome` y el evento final (`done` con el mismo `asdict(outcome)` de hoy, o `error` con
  «La captura no se pudo completar.»). Así nunca hay un job terminal sin evento final.
- `GET …/{job_id}/eventos` lee los eventos de la base y los emite con el mismo `encode()` de
  `apps/api/sse.py`; añade una línea `id: <seq>` por evento. El navegador (EventSource)
  reenvía automáticamente `Last-Event-ID` al reconectar y el endpoint reproduce solo lo que
  falta; sin cabecera reproduce todo. Después del evento final termina. Los estados
  terminales sin evento final registrado (base restaurada a mano) emiten el final sintético
  como hoy.
- `GET …/{job_id}` reconstruye `CaptureJob` desde la fila (mismos campos en el JSON).
- La poda (TTL de una hora, tope de 20 jobs y cupos viejos) se ejecuta al arrancar una
  captura y nunca borra un job en curso.

### 4. Detección y recuperación de interrumpidos

- **Latido**: un hilo daemon por captura activa actualiza `capture_jobs.updated_at` y
  `capture_control.heartbeat_at` cada `heartbeat_seconds` (por defecto 15 s) mientras el job
  esté en `running` y el `lease_token` coincida.
- **Detección al arrancar** (`lifespan` de la app; tolerante a base caída):
  1. `SELECT … FOR UPDATE` de la fila de control. Si su latido caducó (por defecto 120 s),
     marca el job como `interrupted` con la transición condicional y deja el control como
     marcador de recuperación. Si el control apunta a un job ya terminal pero quedó retenido,
     también se recupera.
  2. Barrido de jobs `pending`/`running` cuyo `updated_at` caducó aunque no estén en el
     control (huérfanos de una inconsistencia).
  3. **Recuperación ordenada**: antes de liberar el marcador/aceptar tráfico de captura, el
     worker que ganó la transición ejecuta `sync_gold(gold_dir, None)` —capa core completa,
     sin poda— para converger Gold → PostgreSQL → Qdrant. La transición condicional y el
     marcador con latido fresco garantizan que solo un worker la ejecuta y que otro arranque
     no acepta una captura mientras se recupera. Después, el marcador se libera.
- **Detección al intentar capturar**: la fase de reserva del §2 hace lo mismo en línea; el
  solicitante espera la recuperación (bajo `corpus_lock`) antes de que su captura arranque.
  Si otro worker está recuperando, la solicitud recibe 409 y puede reintentar.
- **Punto seguro exacto**: Gold es la verdad ya escrita; `sync` compara huellas y reimporta o
  reindexa solo lo nuevo o cambiado. No hace falta que el evento del documento haya llegado:
  por eso la sincronización es completa y no solo de slugs derivados de eventos. **No** se
  reanuda la captura ni se promete continuar por la URL del corte. El usuario ve el job
  interrumpido y puede lanzar una captura nueva; repetir captura y `sync` no duplica
  documentos (identidad por fuente, deduplicación por `raw_hash`/`content_hash`, escritura
  atómica de Gold y huellas de sincronización).
- **Apagado ordenado**: `CaptureJobs.shutdown()` marca las capturas locales del worker como
  interrumpidas (condicionado al testigo), libera el control, detiene los latidos y
  `dispose()` del motor; el siguiente arranque reconcilia. Si el proceso muere sin apagado
  ordenado, el latido caduca y la siguiente pasada lo detecta.

### 5. Recursos, conexiones y cierre

- El motor se sigue creando de forma diferida (`runtime.session_factory`) para que importar
  el módulo y responder `/live` no abra conexiones.
- **El ancho del pool no se toca**: `build_engine` conserva el valor por defecto de
  SQLAlchemy (`pool_size=5`, `max_overflow=10`) porque el mismo motor lo usan chat, FAQ y
  CLI; un cambio global de ancho afectaría flujos ajenos a esta spec.
- Presupuesto documentado y medido: 3 workers × 15 conexiones máximas teóricas = 45, más el
  checkpointer/Store del agente que abre su propio pool; queda por debajo de
  `max_connections` por defecto (100). La medición de integración comprueba que las
  operaciones de estado y stream devuelven las conexiones al pool (`checkedout() == 0`) y no
  retienen una conexión por cliente.
- Las consultas de estado y de stream usan sesiones breves; ninguna conexión se retiene
  durante el stream ni durante la captura. La captura en sí usa las conexiones de
  `sync` como hoy.
- `lifespan` de la app: en el arranque ejecuta la recuperación de forma ordenada (si la base
  no responde, registra y continúa: `/live` no depende de la base); uvicorn no atiende
  tráfico hasta terminar el arranque, así que no se acepta una captura mientras se recupera;
  en el apagado ejecuta `CaptureJobs.shutdown()` y cierra el motor. Uvicorn apaga el lifespan
  antes de matar el proceso; `stop_grace_period` de 10 s del compose es suficiente porque el
  apagado no espera a que termine la captura: la marca como interrumpida.
- Memoria: la exclusión global garantiza como máximo una captura en todo el despliegue, así
  que solo hay un hilo de captura y uno de latido además de los workers; las colas en
  memoria del registro actual desaparecen.

### 6. Configuración y wiring del camino de captura

Todos los tiempos operativos del estado duradero se leen de `Settings` con los
valores actuales como default, para que el ensayo de tres procesos pueda acortarlos por
entorno sin tocar código:

| Variable | Default | Uso |
|---|---|---|
| `CAPTURE_HEARTBEAT_SECONDS` | 15 s | intervalo del latido de una captura activa |
| `CAPTURE_STALE_SECONDS` | 120 s | umbral de caducidad para declarar interrumpido un job |
| `CAPTURE_JOB_TTL_SECONDS` | 3600 s | retención de jobs, eventos y marcas de cupo |

En producción se usan los defaults; `main.py` los pasa a `PostgresCaptureBackend` y a
`CaptureJobs`. El ensayo real de `task-8` solo necesita exportar valores pequeños.

El camino de captura toma rutas y conexiones de `Settings()`, nunca de constantes
fijas del módulo: `web_capture.ingest_urls` usa `settings.gold_dir`, `raw_dir`,
`processed_dir`, `index_file`, `seeds`, `external_seeds` y los límites de captura;
`ingestion.sync_gold` construye Qdrant con `settings.qdrant_*` y la sincronización usa
`settings.gold_dir`; `runtime.session_factory` resuelve `settings.database_url` de forma
diferida; `apps/api/urlguard` importa constantes de `carvajal.config`, que a su vez se
derivan de `Settings()` al importar (mismo entorno). La capa extendida (`external_dir`)
no participa en la captura web, que solo toca el núcleo, pero `carvajal sync` la resuelve
también desde `Settings()`.

### 7. Compatibilidad y dobles de pruebas

- La API pública de `CaptureJobs` (`start`, `get`, `stream`, `stream_async`) y sus errores
  (`CaptureBusy`, `CaptureRateLimited`) no cambian; las vistas `views/config.py` solo añaden
  el `Last-Event-ID` al stream.
- `CaptureJobs()` sin backend explícito usa el **backend en memoria** con el comportamiento
  actual (un job activo por instancia, cupo en memoria, TTL y tope de 20). Las pruebas
  unitarias existentes siguen funcionando sin PostgreSQL; solo se ajusta la prueba de
  reconexión al comportamiento nuevo (reproducir eventos ya persistidos).
- `main.py` cablea `CaptureJobs(store=PostgresCaptureBackend(session_factory, …))` con el
  cupo y la ventana de `Settings` y la política de latido; las pruebas de API inyectan el
  doble en `app.state.captures` como hoy.

### 8. Contratos conservados

| Endpoint | Contrato |
|---|---|
| `POST /configuracion/captura` | 200 parcial/página; 403 CSRF; 409 «Ya hay una captura en curso. Espera a que termine.»; 422 validaciones actuales; 429 mensaje actual + `Retry-After`. |
| `GET /configuracion/captura/{job_id}` | 200 `{id, status, error, outcome}`; 404 `{"error": "La captura no existe o expiró."}`. |
| `GET /configuracion/captura/{job_id}/eventos` | 200 `text/event-stream` con `stage`, `done`, `error` y los mismos campos; 404 igual. |

`/live` sigue devolviendo `{"status": "ok"}` sin base. `/health` conserva claves, conteos,
estado del índice, bloque `monitoring` y códigos 200/503.

## Estructura

```text
apps/api/capture_jobs.py        # backend PostgreSQL + backend en memoria + orquestación
apps/api/main.py                # cableado del backend, lifespan (recuperación/apagado)
apps/api/runtime.py             # pool acotado, engine diferido y dispose
apps/api/views/config.py        # Last-Event-ID en el stream; resto del contrato intacto
migrations/versions/0008_capture_jobs.py
tests/test_capture_jobs.py      # unitarias del registro (doble en memoria)
tests/test_api_capture.py       # contrato HTTP y caracterización
tests/test_capture_durable.py   # integración con PostgreSQL real y dos instancias
tests/test_settings.py          # defaults y overrides de los tiempos de captura
specs/047-captura-3-workers/    # spec, plan, tasks, analysis y checklist
CONSTITUTION.md                 # fila de la decisión en §9 (mínimo)
.specify/memory/constitution.md # proyección sincronizada
specs/README.md                 # fila de la spec 047
```

No se tocan `compose.prod.yml`, el `CMD` del Dockerfile, `apps/api/chat/`,
`views/chat.py`, `views/stream.py`, `views/api.py`, `views/faq.py`, `static/` ni
`templates/base.html`.

## Entrega

1. Caracterización del comportamiento actual (commit `test(047)`).
2. Migración y backend PostgreSQL con su orquestación (commit `feat(047)`).
3. Pruebas de integración con dos instancias y PostgreSQL real (base de `make
   test-integration` para el ensayo real de `task-8`): arranque simultáneo → un 409 y un
   solo job; cupo por IP compartido; progreso/estado desde la otra instancia; interrupción
   transaccional única y recuperación sin duplicar; `/live` sin base. Dobles actualizados.
4. Puerta verde: `make check`, `make test-integration`, `node --test
   tests/js/test_chat_voice.cjs`, `alembic upgrade head` y `alembic downgrade -1`.
5. PR contra `dev` con evidencia, reversión y riesgos; `task-8` (tres workers) queda para
   el Lead.
