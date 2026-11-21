# Línea base y contratos de referencia (fase 0)

**Fecha**: 2026-11-20
**Base**: `origin/dev` @ `8927ee7`
**Entorno**: host Linux, Python 3.13 (`uv` 0.13.1, Ruff 0.17.3, ty 0.0.95, pytest 8.3,
Node 24.16.0, Docker 29.10.0, Compose v5.8.0); PostgreSQL 16 con pgvector
(`carvajal-db-1`), Qdrant 1.15.4 (`carvajal_chunks`), Ollama en `127.0.0.1:11434`.

> Los tiempos son observaciones de este entorno, no garantías. Para comparar hace falta
> repetir el **mismo** comando en el **mismo** entorno; el quickstart lo describe.

## 1. Aviso de entorno (necesario para reproducir)

El shell del entorno de desarrollo exporta `OLLAMA_MODEL=qwen3:4b-instruct`. Dos pruebas
que comprueban el valor por defecto de `Settings` fallan con esa variable heredada
(`tests/test_llm_adapters.py::test_settings_trae_el_modelo_de_ollama_por_defecto` y
`tests/test_chat_tracing.py::test_la_llamada_al_modelo_es_un_hijo_de_la_raiz`). En CI no
existe esa variable. Por eso la línea base se mide con:

```bash
env -u OLLAMA_MODEL <comando>
```

No es un fallo del código y no se corrige en esta spec.

## 2. Medición base: verificación completa

Comando (desde la raíz del repo):

```bash
export UV_CACHE_DIR="$PWD/.uv-cache"   # el cache de HOME es read-only en el sandbox
env -u OLLAMA_MODEL make check
```

Resultado (`8927ee7`, 2026-11-20):

| Paso | Resultado |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | `342 files already formatted` |
| `ty check` | `All checks passed!` |
| `pytest -m "not integration and not e2e and not smoke"` | **1643 passed, 46 deselected, 2 warnings** en 17.56 s |
| Tiempo total (wall) | **20.99 s** |
| Memoria máxima del proceso `make` | **335 636 KB** (~328 MB) |
| Código de salida | **0** |

Pruebas de cliente:

```bash
node --test tests/js/test_chat_voice.cjs
```

| Resultado |
|---|
| **68 tests, 68 pass, 0 fail** en ~139 ms |

Registro crudo: `.evidence/baseline-make-check.log` (evidencia local, no versionada).

## 3. Medición base: corpus y `sync --verify`

Estado de los almacenes locales (observación del entorno, no del código):

| Almacén | Valor |
|---|---|
| `data/gold/*.md` | 24 documentos (+ `index.jsonl`) |
| `data/external/**/gold/*.md` | 479 documentos en 1 fuente (`alejandro`) |
| PostgreSQL `documents` | 24 |
| PostgreSQL `chunks` | 125 |
| Qdrant `carvajal_chunks` | 125 puntos; **payload sin campo `layer`** (índice anterior a la spec 037) |

Comando:

```bash
env -u OLLAMA_MODEL .venv/bin/carvajal sync --verify --layer all
```

Resultado (2026-11-20):

```
Verificación del corpus
  Nuevos:                  479
  Actualizados:            0
  Sin cambios:             24
  Podados:                 0
  Puntos indexados:        0
  Documentos importados:   0
  Fragmentos importados:   0
  ! <24 documentos core>: falta en Qdrant.
  ! core: Qdrant tiene 0 documentos y Gold 24.
  ! <479 documentos extended>: falta en PostgreSQL.
  ! <479 documentos extended>: falta en Qdrant.
  ! extended: Qdrant tiene 0 documentos y Gold 479.
```

| Métrica | Valor |
|---|---|
| Código de salida | **1** (hay diferencias; es el contrato de `--verify`) |
| Tiempo (wall) | **1.83 s** |
| Memoria máxima | **154 528 KB** (~151 MB) |

Lectura correcta del diagnóstico: los 125 puntos locales no llevan `layer`, así que las
consultas filtradas por capa los ignoran; es una condición **del índice local**, no un
fallo de la sincronización. La línea base de la fase 045 se tomará con un índice local
consistente y con medición instrumentada (ver §5).

## 4. Contratos que no pueden cambiar

### 4.1 Formato SSE

```text
event: open
data: {"stream": "chat"}

event: token
data: {"text": "La compañía"}

event: done
data: {"conversation_id": "...", "message_id": "...", "thread_id": "...", "route": "rag", "elapsed_ms": 812, "request_id": "..."}

```

- Un bloque por evento, `event:` + `data:` en JSON UTF-8 y línea en blanco final.
- Error de petición (CSRF, pregunta vacía/larga, cuota, proveedor): **un solo** evento
  `error` con `{"message": ..., "incomplete": false}` y cabecera `X-Carvajal-Error: 1`;
  códigos 403, 422, 429 y 503 respectivamente.
- Encabezados de stream: `Cache-Control: no-cache`, `X-Accel-Buffering: no`.

### 4.2 Readiness y vitalidad

- `GET /live` → `200 {"status": "ok"}` sin tocar base ni proveedores.
- `GET /health` → `200` si PostgreSQL responde y Qdrant está `ok`/`not_configured`; `503`
  en caso contrario. Cuerpo: `{"status", "documents", "chunks", "index", "monitoring"?}`.
- `GET /api/health` → mismo readiness en JSON (`status`, `documents`, `chunks`, `index`).

### 4.3 Cookies e identidad

| Cookie | Contenido | Atributos |
|---|---|---|
| `carvajal_visitante` | hash anónimo de 256 bits del visitante | `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` solo sobre HTTPS |
| `carvajal_sesion` | `owner_key` firmado con HMAC-SHA256 (`SESSION_SECRET`) | `HttpOnly`, `SameSite=Lax`, `Path=/`, `Secure` solo sobre HTTPS, 180 días |
| `carvajal_csrf` | token de doble cookie | `SameSite=Lax`; se compara en bytes con el formulario |

Sin `SESSION_SECRET` el proceso usa un secreto aleatorio por worker: con 3 workers las
cookies dejarían de validar entre procesos. El Compose ya lo exige obligatorio.

### 4.4 CLI

- `sync --verify` devuelve **1** si hay diferencias y **0** si todo está en orden.
- `sync --adopt --verify` juntos: **2** (opciones incompatibles).
- El resto de comandos devuelve **1** ante error y **0** en éxito (tabla en
  [inventario §3](inventario.md#3-comandos-cli)).
- El comando documentado `uv tool install "carvajal[capture,legacy]"` resuelve el paquete
  homónimo de PyPI (`carvajal==0.2.1`, sin esos extras) y falla; la forma verificada en
  esta entorno es `uv tool install ".[capture,legacy]"` desde la raíz del repositorio.
  Queda anotado para corregir `AGENTS.md`/`README.md` en una PR de documentación.

### 4.5 Cuota, CSRF y orden de controles

El orden es el mismo en formulario, stream web y stream de API: **CSRF → validación de
longitud (máx. 500) → cuota → caso de uso**. Un CSRF inválido no consume cuota. El cupo
diario se reserva en PostgreSQL (`usage_day`) con medianoche en `QUOTA_TIMEZONE`.

## 5. Métricas instrumentadas que la fase 045 debe refinar

Estas cifras se derivan del código de `8927ee7`; el número exacto se medirá con dobles en
la fase 045 antes de optimizar:

| Métrica | Estado base (código) |
|---|---|
| Recorridos de Qdrant por capa en `sync --verify` | **2**: `document_hashes()` y `document_ids()`, cada uno recorre `_payloads()` |
| Lecturas completas de Gold por capa en `sync` completo | **1** para huellas (`_gold_fingerprints`) + **1** por importación a PostgreSQL (`migrate_gold`) + **1** por indexación (`index_gold`) = hasta **3** |
| Fragmentación de Gold por documento cambiado | **2**: una para PostgreSQL (`split_document`) y otra para Qdrant |
| Embeddings por fragmento | **1** llamada al embedder por fragmento en documentos nuevos/cambiados; 0 si solo cambian metadatos (ya probado en `test_un_cambio_solo_de_metadatos_actualiza_el_payload_sin_reembeder`) |
| Conteo de verificaciones del índice | `document_hashes` + `document_ids` en `_sync` (modo verify) |

La medición antes/después de la fase 045 se documentará en
`specs/045-corpus-rendimiento/mediciones.md` con el mismo entorno y el mismo corpus.

## 5.bis Smoke de arranque con 3 workers (solo mecanismo)

Se verificó que el código de `8927ee7` arranca con Uvicorn nativo de 3 workers, sin
cambiar nada:

```bash
.venv/bin/uvicorn apps.api.main:app --host 127.0.0.1 --port 8099 --workers 3 --no-access-log
# INFO: Started parent process [5]
# INFO: Started server process [8] / [9] / [10]
# GET /live -> 200 {"status": "ok"}
```

Es solo la prueba del mecanismo: en esa versión el estado de captura sigue en memoria de
cada worker, por eso la activación en el Compose depende de la fase 047 (coordinación
compartida). La prueba real de 3 procesos se registra en
`specs/047-captura-3-workers/evidencia-3-workers.md`.

## 6. Capacidad y presupuestos (para la fase 047)

- Hoy: 1 worker. Caches por proceso (`@cache`): `session_factory`, `ask_components`
  (embedder + LLM), `qdrant_store`, `agent_runtime`, `_default_handlers`.
- Con 3 workers: 3 copias de cada cache y de cada pool SQLAlchemy (5 + 10 conexiones de
  desborde por worker), más las conexiones del checkpointer y del Store del agente.
- La memoria por worker se medirá con la imagen en la fase 047; no se promete un valor.
- `RAG_TOP_K`, `RAG_MIN_SCORE` y el modelo de embeddings no cambian en este goal.
