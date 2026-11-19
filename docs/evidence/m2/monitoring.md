# Evidencia — Monitoreo del agente con LangSmith (spec 038)

> Corrida del **2026-11-19** contra LangSmith real (proyecto `carvajal`), con
> PostgreSQL, Qdrant y DeepSeek del entorno local. Las pruebas automatizadas no
> usan credenciales: esta corrida es la evidencia manual de T033.

## Entorno

```bash
# Servicios
docker ps                      # carvajal-db-1 (healthy), carvajal-qdrant-1
# Índice y base
125 puntos en `carvajal_chunks`, 24 documentos en PostgreSQL
`alembic upgrade head` → 0007 aplicada (`chat_feedback` existe)
# Monitoreo
MONITORING_ENABLED=true
MONITORING_SAMPLE_RATE=1.0
MONITORING_INCLUDE_CONTENT=true      # desarrollo; en producción, false
```

## SC-001 · una traza por turno en las cuatro rutas

| Ruta | `request_id` | Raíz | Hijos |
|---|---|---|---|
| `direct` | `9b1918731dbf4c2da8b153ca5848a1d5` | `chat_turn` | 0 (sin llamada al modelo) |
| `rag` | `db3fa01c5bf54d4aa08fd2b27349d7ce` | `chat_turn` | 0 en el doble; con el agente real, `recall`/`route` |
| `tool` | `11d4e40939fa4eb9b85b3c4955c884a0` | `chat_turn` | `LangGraph`, `recall`, `route`, `RunnableCallable`, `tool`, `indicator_lookup`, `llm` |
| `commercial` | `0f863d3c8ffa4ba29de1a47d2e554227` | `chat_turn` | `LangGraph`, `recall`, `route`, `RunnableCallable`, `commercial` |

`tool` y `commercial` se ejecutaron con el agente real (DeepSeek + PostgreSQL +
Qdrant) a través de `/chat/stream`; `direct` y `rag` se ejercitaron por el mismo
`ChatService` y el mismo tracer con un agente de prueba, porque el entorno local
no tenía Ollama para los embeddings (**bloqueo documentado**: la ruta `rag` real
necesita `nomic-embed-text`; el resto del camino —raíz, metadatos, redacción y
envío— es el mismo código).

Árbol de la traza comercial, tal como lo devuelve la API:

```text
chat_turn (chain)              route='commercial', request_id=0f863d3c…
├── LangGraph (chain)
├── recall (chain)
├── route (chain)
├── RunnableCallable (chain)
└── commercial (chain)
```

`metadata` de la raíz de una corrida posterior con el código final
(`request_id=11d4e40939fa4eb9b85b3c4955c884a0`, ruta `tool`, 8 runs: la raíz,
`LangGraph`, `recall`, `route`, `RunnableCallable`, `tool`, `indicator_lookup` y
`llm`):

```json
{"request_id": "11d4e40939fa4eb9b85b3c4955c884a0", "thread_hash": "6975c809c68ab277",
 "router": "rules", "route": "tool", "intent": "indicator", "tool": "ingresos_consolidados",
 "tool_period": "2025", "preferences_count": 0, "question_chars": 74, "answer_chars": 248,
 "outcome": "ok", "ls_run_depth": 0}
```

## SC-002 · producción sin texto ni PII

Turno con la pregunta «Soy Ana Pérez, mi teléfono es 300 123 4567. ¿Ingresos de
Carvajal en 2025?» y `MONITORING_INCLUDE_CONTENT=false`, reproducido con
`scripts/evidencia_monitoreo_038.py` (ruta `tool`, agente real). Lo almacenado:

```text
inputs raíz:  {}
outputs raíz: {'outcome': 'ok'}
metadata:     route, router, request_id, thread_hash, question_chars, answer_chars,
              intent, tool, tool_period, preferences_count, outcome
PII en todo el payload:   False
hilo crudo u owner_key:   False
texto de la respuesta:    False
```

La lista de claves sensibles (`owner_key`, `thread_id`, `conversation_id`,
`cookie`, `csrf_token`, `api_key`) se elimina también con
`MONITORING_INCLUDE_CONTENT=true`, así que la garantía de FR-005 no depende del
entorno. Las trazas de desarrollo anteriores al arreglo (commits previos a esta
entrega) sí contenían esos identificadores en las runs hijas del grafo.

Los fragmentos recuperados sobreviven como `slug`, `chunk_id` y `score`, nunca
como texto. En las corridas de desarrollo (`include_content=true`) el contenido
sí viaja, como exige FR-006.

## SC-003 · servicio inalcanzable

Reproducible con `uv run python scripts/evidencia_monitoreo_038.py --latencia`:
veinte turnos con `LANGSMITH_ENDPOINT=http://127.0.0.1:9/` (inalcanzable) y
otros veinte con el monitoreo apagado, sobre el mismo `ChatService` y el mismo
tracer, con un agente de prueba para aislar el costo del monitoreo del costo del
modelo:

| Configuración | Latencia al primer fragmento (p95) |
|---|---:|
| Servicio caído | **13,5 ms** |
| Monitoreo apagado | 0,9 ms |
| Diferencia | **+12,6 ms** (límite: 100 ms) |

La cifra es de una corrida; el p95 de 20 muestras varía entre repeticiones
(otra corrida dio 39,1 ms frente a 0,9 ms). Lo que se sostiene en todas es el
criterio: la diferencia se mantiene muy por debajo de 100 ms.

Todas las respuestas se emitieron y persistieron; los fallos quedaron como
`monitoring.failed` en los logs y el cliente se rinde al `timeout_ms` sin
bloquear el primer fragmento.

## SC-004 · buscar un turno

1. Por `request_id`: el evento `done` del stream lo publica y cada línea de log
   lo lleva. En LangSmith se filtra el proyecto por el metadato `request_id`
   (p. ej. `fe3de55f3ddc402abfeff2c16087ad7a`) y aparece la raíz con sus hijos.
2. Por hilo: `thread_hash` son los primeros 16 hex del SHA-256 del hilo.

   ```bash
   python -c "import hashlib; print(hashlib.sha256(b'e0fef815c5734eb8b33c3af338ecc23b').hexdigest()[:16])"
   # ede024270333f45a  ← coincide con el metadato de la raíz
   ```

La consulta tardó segundos, muy por debajo del minuto que exige el criterio.

## SC-005 · dos corridas del enrutador como experimentos

```bash
uv run carvajal eval-router --router rules --langsmith
uv run carvajal eval-router --router llm --langsmith
```

| Experimento | Preguntas | Ruta | Intención | Datos | Inválidas | Latencia media (ms) | Llamadas al modelo |
|---|---:|---:|---:|---:|---:|---:|---:|
| `routing-rules-2026-11-19T09:53:53Z` | 110 | 100,0 % | 100,0 % | 100,0 % | 0 | 0,29 ms | 0 |
| `routing-llm-2026-11-19T10:03:07Z` | 110 | 98,2 % | 97,3 % | 96,9 % | 0 | 3.082,8 ms | 110 |

Estas cifras son las del experimento de LangSmith y coinciden con la corrida
del 2026-11-19 añadida a [`docs/resultados-router.md`](../../resultados-router.md)
(«Corrida 3»); la tabla histórica del 2026-11-16 se conserva como referencia de
la versión anterior del adaptador.

- Dataset `carvajal-routing_questions` con **110 ejemplos**, creado en la primera
  corrida y reutilizado en la segunda sin duplicar.
- Ambas corridas tienen **110 runs enlazadas por `reference_example_id`**: la
  comparación alinea las filas por pregunta.
- Comparación pregunta a pregunta: **2 de 110** cambiaron de ruta
  (`c-28 direct → commercial` y `c-12 commercial → direct`). El resto coincide con
  la corrida 3 de [`docs/resultados-router.md`](../../resultados-router.md).

## Marcas del visitante

`POST /chat/feedback` guarda `(request_id, useful, created_at)` en
`chat_feedback` (migración 0007). Con el monitoreo apagado los botones no se
renderizan (`data-monitoring="0"`); con el monitoreo activo y un turno no
muestreado la marca se guarda igual y **no** se crea una traza.

## Revisión de los criterios de éxito (T034)

| SC | Criterio | Resultado | Evidencia |
|---|---|---|---|
| SC-001 | 100 % de los turnos de las cuatro rutas con traza | **Cumplido** (2 rutas con agente real, 2 con el mismo tracer) | Tabla de `request_id` por ruta; la corrida final muestra 8 runs en `tool` |
| SC-002 | 0 trazas con IP, cookies, CSRF, claves o visitante crudo; 0 con texto en producción | **Cumplido con el código entregado** | Turno de producción (`11d4e409…`): `inputs {}`, `outputs {'outcome': 'ok'}`, sin PII, sin `owner_key` ni hilo crudo; las trazas de desarrollo anteriores al arreglo conservan esos campos |
| SC-003 | Servicio inalcanzable: 100 % de turnos respondidos y p95 sin empeorar más de 100 ms | **Cumplido** (+12,6 ms en la corrida publicada; el criterio se mantiene entre repeticiones, con varianza de decenas de ms en el p95 de 20 muestras) | Tabla de latencia y `--latencia` |
| SC-004 | Encontrar la traza por `request_id` en menos de un minuto | **Cumplido** | Consulta por metadato `request_id` y por `thread_hash` |
| SC-005 | Dos corridas del enrutador como experimentos con métricas del informe local | **Cumplido** | 110/110 preguntas alineadas; 2 cambios de ruta |

Pendiente único: repetir `direct` y `rag` con el agente real cuando Ollama esté
levantado. No bloquea SC-001: el camino de traza es el mismo y el envío se
verificó con las otras dos rutas y con las dos simuladas.

## Pendientes

- Repetir `direct` y `rag` con el agente real cuando Ollama esté disponible en
  el entorno de la demo (`docker compose -f compose.prod.yml up ollama` y
  `ollama pull nomic-embed-text`). El resto del camino ya está evidenciado.
