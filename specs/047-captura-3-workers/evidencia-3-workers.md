# Evidencia de 3 workers (spec 047, fase 047b)

**Fecha**: 2026-11-20
**Rama**: `feat/047-captura-durable` (PR [#206](https://github.com/almeneses/carvajal/pull/206))
**Alcance**: activar exactamente tres workers de Uvicorn en el despliegue objetivo y
demostrar que la captura web se coordina entre procesos.

> Complementa [`evidencia.md`](evidencia.md) (fase 047a). La 047a movió el estado de la
> captura a PostgreSQL; la 047b activa el despliegue y lo verifica con tres procesos
> reales.

## 1. Qué cambia

- `compose.prod.yml`: el servicio `app` fija `UVICORN_WORKERS` con valor por defecto **3**
  y ejecuta `uvicorn --workers ${UVICORN_WORKERS:-3}`. No se añade ningún servicio.
- `Dockerfile`: el `CMD` conserva **1 worker**. Un `docker run` aislado sigue siendo
  predecible; el Compose es el único que activa los tres.
- `apps/api/agent_runtime.py`: el `setup()` de `PostgresSaver` y `PostgresStore` se
  serializa entre workers con un advisory lock de PostgreSQL. Con tres procesos, la
  primera pregunta de cada worker puede construir el runtime a la vez y las migraciones
  internas de LangGraph no son idempotentes entre procesos.
- `scripts/verificar_3_workers.py`: ensayo reproducible con tres procesos reales contra
  PostgreSQL y Qdrant locales, con base, colección y `GOLD_DIR` temporales (no toca el
  corpus versionado).
- `tests/test_compose_workers_047.py`: fija el contrato del Compose (3 workers, sin
  servicios nuevos, reversión a 1) y que la imagen conserva un worker.
- `docs/despliegue-ovh.md` §8: cómo volver a 1 worker sin perder el estado persistido.

## 2. Cómo se reproduce

```bash
# Requisitos: PostgreSQL local (POSTGRES_PORT del .env), Qdrant en :6333,
# Ollama con nomic-embed-text, y el venv sincronizado del worktree.
export UV_CACHE_DIR="$(git rev-parse --show-toplevel)/.uv-cache"
export POSTGRES_PORT=5433
.venv/bin/python scripts/verificar_3_workers.py --output .evidence/3w/resultado.json
```

El script crea una base `carvajal_3w_<pid>`, aplica `alembic upgrade head`, usa una
colección Qdrant temporal y un `GOLD_DIR` temporal; al terminar borra ambos y deja el
resultado en JSON. No modifica `data/gold/`, la base `carvajal` ni la colección de
producción.

## 3. Resultado del ensayo con tres procesos

El ensayo terminó con **19/19 comprobaciones en verde** (`HARNESS_EXIT=0`). Resultado
crudo: [`evidencia-3-workers.json`](evidencia-3-workers.json). Entorno: Linux,
Python 3.13.16, uvicorn 0.54.0, FastAPI 0.162.4, SQLAlchemy 2.1.6, psycopg 3.3.8,
Node 24.16.0, Docker 29.10.0; PostgreSQL 16 con pgvector (host `:5433`), Qdrant 1.15.4
y Ollama con `nomic-embed-text`.

| OK | Comprobación | Detalle |
|---|---|---|
| ✅ | app arranca con 3 procesos | `{"pids": [8, 9, 10], "rss_kb": {"8": 203272, "9": 203332, "10": 204620}, "rss_total_kb": 611224, "conexiones_postgres": 4}` |
| ✅ | /live responde sin base | `{}` |
| ✅ | dos POST simultáneos: uno crea el job y el otro recibe 409 | `{"statuses": [200, 409], "jobs": ["fb25957a83654b25ba29a7d3db5deb21"], "snippets": ["<!doctype html>\n<html lang=\"es\">\n  <head>\n    <meta charset=\"utf-8\" ` |
| ✅ | el job simultáneo queda registrado en PostgreSQL | `{"row": ["fb25957a83654b25ba29a7d3db5deb21", "running", "Sal:8", null, ""]}` |
| ✅ | el SSE de progreso se sirve desde cualquier worker | `{"eventos": [{"id": "1", "event": "stage", "data": "{\"url\": \"\", \"stage\": \"descubrir\", \"detail\": \"3 semillas\"}"}, {"id": "2", "event": "stage", "data` |
| ✅ | la reconexión SSE con Last-Event-ID no repite etapas | `{"last_id": "18", "replay": [{"event": "done", "data": "{\"requested\": 3, \"discovered\": 3, \"fetched\": 3, \"written\": 3, \"discarded\": 0, \"chars\": 2879,` |
| ✅ | la captura termina y converge | `{"final": {"id": "fb25957a83654b25ba29a7d3db5deb21", "status": "done", "error": "", "outcome": {"requested": 3, "discovered": 3, "fetched": 3, "written": 3, "di` |
| ✅ | no hay documentos ni fragmentos duplicados tras la captura | `{"documents": 3, "chunks": 4, "distinct_documents": 3, "distinct_chunks": 4}` |
| ✅ | la captura de recuperación arranca | `{"status": 200}` |
| ✅ | el job de recuperación está en ejecución | `{"row": ["2894779b2e8d4392bf32067d77a169ae", "running", "Sal:8"]}` |
| ✅ | un nuevo worker puede iniciar la captura de recuperación | `{"status": 200, "cuerpo": "<!doctype html>\n<html lang=\"es\">\n  <head>\n    <meta charset=\"utf-8\" />\n    <meta name=\"viewport\" content=\"width=device-wid` |
| ✅ | el worker caído deja el job detectado como interrumpido | `{"worker": "Sal:8", "row": ["2894779b2e8d4392bf32067d77a169ae", "interrupted", "Sal:8"]}` |
| ✅ | la recuperación no duplica documentos ni fragmentos | `{"documents": 4, "chunks": 7, "distinct_documents": 4, "distinct_chunks": 7}` |
| ✅ | Gold no tiene archivos repetidos | `{"gold": ["domains.md", "example_domains.md", "help.md", "home.md"]}` |
| ✅ | con cupo 1, la primera captura se acepta | `{"status": 200}` |
| ✅ | el cupo por IP se comparte y la segunda captura recibe 429 con Retry-After | `{"status": 429, "retry_after": "3598"}` |
| ✅ | la app vuelve a 1 worker | `{"padre": 122, "workers": []}` |
| ✅ | el estado persistido sigue disponible con 1 worker | `{"status": 200, "body": {"status": "ok", "documents": 5, "chunks": 11, "index": {"status": "ok", "collection": "carvajal_3w_4", "points": 11, "dimensions": 768}` |
| ✅ | con 1 worker se puede iniciar una captura | `{"status": 200}` |

Lecturas clave:

- **Tres procesos**: workers `[8, 9, 10]`; RSS por worker
  8→203272 KB, 9→203332 KB, 10→204620 KB (total **611224 KB ≈
  597 MiB**).
- **Simultaneidad**: `[200, 409]`; un solo job (`1`).
- **Caída y recuperación**: job del worker `Sal:8` queda
  `interrupted`; tras recuperar, 4 documentos y
  7 fragmentos, todos distintos
  (4/7).
- **Cupo compartido**: segunda captura `429` con
  `Retry-After: 3598`.
- **Retorno a 1**: sin workers hijos (`[]`), padre `122`;
  `/health` `200` con 5 documentos y
  11 fragmentos persistidos.

## 4. Presupuesto de conexiones y memoria

Medido en el mismo ensayo:

| Métrica | Valor |
|---|---|
| Workers | 3 procesos hijos (más el padre de uvicorn) |
| RSS por worker | 203272 KB, 203332 KB, 204620 KB |
| RSS total de los 3 workers | **611224 KB ≈ 597 MiB** |
| Límite del Compose | `mem_limit: 1g` (1024 MiB) |
| Uso de workers sobre el límite | **≈ 58 %**; queda margen para el padre, buffers HTTP y cargas |
| Conexiones a PostgreSQL al arrancar (base del ensayo) | 4 |
| Techo teórico del pool | 3 workers × (5 + 10 de desborde) = 45 conexiones, más 2 por worker del checkpointer/Store ≈ **51** < `max_connections` por defecto (100) |
| Ancho del pool | Sin cambios (lo comparten chat, FAQ, captura y CLI) |

Conclusión: `mem_limit: 1g` se mantiene; el ensayo deja evidencia para volver a
revisarlo si el RSS por worker crece (por ejemplo, al ampliar el corpus en memoria).

## 5. Imagen y arranque

```bash
docker build -t carvajal-api:047b .        # exit 0
docker run -d --name carvajal-api-047b --network host \
  -e SESSION_SECRET=... -e DATABASE_URL=... -e QDRANT_URL=http://127.0.0.1:6333 \
  -e OLLAMA_BASE_URL=http://127.0.0.1:11434 carvajal-api:047b
curl /live    # 200 {"status":"ok"}
curl /health  # 200 {"status":"ok","documents":24,"chunks":125,"index":{"status":"ok","collection":"carvajal_chunks","points":125,...}}
```

La imagen conserva el `CMD` de un worker; el Compose es el que añade `--workers 3`.
`docker compose -f compose.prod.yml config` resuelve `--workers "3"` por defecto y
`--workers "1"` con `UVICORN_WORKERS=1`; el test `tests/test_compose_workers_047.py` lo
fija.

## 6. Reversión a 1 worker

El ensayo reinició la app con `--workers 1` contra la misma base y el mismo volumen
temporal: `122` es el único proceso, sin workers hijos; `/health` respondió
`200` con 5 documentos y
11 fragmentos; el job interrumpido seguía consultable; y una
captura nueva respondió `200` y terminó. El estado persistido (job interrumpido, Gold,
PostgreSQL y Qdrant) sobrevive al cambio de 3 → 1.

## 7. Límites y riesgos

- **No hay reanudación exacta por URL.** Si un worker muere a mitad de una captura, el
  job queda `interrupted` y la recuperación vuelve a ejecutar la sincronización idempotente
  Gold → PostgreSQL → Qdrant para la capa completa; el trabajo ya escrito converge sin
  duplicar documentos. La captura no continúa donde iba.
- Mientras una recuperación está en curso, un envío nuevo puede recibir `409` (hay una
  captura global activa); se reintenta y no se promete continuidad.
- El arranque puede tardar si el job interrumpido dejó mucha deriva Gold↔almacenes; la
  sincronización es incremental y solo repara lo que falte.
- El pool de SQLAlchemy conserva su ancho por defecto (5 + 10 de desborde) porque lo
  comparten chat, FAQ, captura y CLI; con tres workers el techo teórico es 45 conexiones,
  por debajo del `max_connections` por defecto de PostgreSQL. El ensayo mide las
  conexiones reales.
- `mem_limit: 1g` del Compose: la medición del ensayo decide si hay que ajustarlo.
- Revertir la imagen basta para volver al registro en memoria; el `downgrade` de la
  migración 0008 borra solo las cuatro tablas de captura.

## 8. Integración con las otras fases (042–046)

Los PRs de las fases 042–046 se abrieron por separado y cada uno está verde contra su
base. Al combinarlos en una rama local de integración aparecieron dos ajustes de pruebas,
sin cambios de producto:

1. `tests/test_characterization_043.py`: la fase 046 retira `record()` y `AgentTurn.turns`;
   las pruebas de caracterización de la 043 que fijaban ese contrato se retiran (la 046
   añade su propia caracterización del estado técnico).
2. `tests/test_capture_durable.py` (047a): su `FakeIndex` implementaba el puerto
   `SyncIndex` anterior a la 045; se adapta a `snapshot()`/`GoldLayer`.
3. `tests/test_chat_state_layers.py` (046) y `tests/test_agent_graph.py` construyen
   `ChatService`/`GraphAgentRunner` con el embedder y la configuración que la 043 retira;
   en la rama de integración se ajustan a la firma nueva.

Orden de integración recomendado: #200 (docs 042) → #201 (paridad `main`) → #203 (043) →
#204 (044) → #202 (046) → #205 (045) → #206 (047). Tras fusionar 043 y 046 hay que aplicar
los ajustes 1 y 3; la rama de integración local con **todas** las fases demuestra que el
resultado combinado queda en verde.

## 9. Verificación de la rama de integración (todas las fases)

Rama local `integration/3-workers` con los siete PRs fusionados y los tres ajustes de
pruebas de §8 aplicados. En ese mismo árbol:

| Puerta | Resultado |
|---|---|
| `env -u OLLAMA_MODEL make check` | **1718 passed**, 70 deselected, 2 warnings en 20,87 s; Ruff, format y ty en verde |
| `env -u OLLAMA_MODEL make test-integration` | **63 passed**, 1725 deselected en 15,96 s |
| `node --test tests/js/*.cjs` | **90 passed, 0 failed** (68 de la 040 + 22 de las 044/045) |
| `scripts/verificar_3_workers.py` | **19/19 comprobaciones en verde**; JSON: [`evidencia-3-workers-integracion.json`](evidencia-3-workers-integracion.json) |
| Imagen desde la rama de integración | build OK; `GET /live → 200`; `GET /health → 200` (24 documentos, 125 fragmentos, índice `ok`) |

Comprobaciones del ensayo combinado: app arranca con 3 procesos ✅, /live responde sin base ✅, dos POST simultáneos: uno crea el job y el otro recibe 409 ✅, el job simultáneo queda registrado en PostgreSQL ✅, el SSE de progreso se sirve desde cualquier worker ✅, la reconexión SSE con Last-Event-ID no repite etapas ✅, la captura termina y converge ✅, no hay documentos ni fragmentos duplicados tras la captura ✅, la captura de recuperación arranca ✅, el job de recuperación está en ejecución ✅, un nuevo worker puede iniciar la captura de recuperación ✅, el worker caído deja el job detectado como interrumpido ✅, la recuperación no duplica documentos ni fragmentos ✅, Gold no tiene archivos repetidos ✅, con cupo 1, la primera captura se acepta ✅, el cupo por IP se comparte y la segunda captura recibe 429 con Retry-After ✅, la app vuelve a 1 worker ✅, el estado persistido sigue disponible con 1 worker ✅, con 1 worker se puede iniciar una captura ✅.

Esto cierra el criterio de cierre del goal: web, API y CLI con sus contratos en verde, la
imagen arranca, y la prueba real con tres procesos demuestra simultaneidad, cupo
compartido, progreso al reconectar, recuperación tras la caída de un worker sin duplicados
y retorno a un worker.
