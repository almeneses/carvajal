# Evidencia — Monitoreo del agente con LangSmith (spec 038)

> Corrida del **2026-11-19** contra LangSmith real (proyecto `carvajal`), con
> PostgreSQL, Qdrant y DeepSeek del entorno local. Las pruebas automatizadas no
> usan credenciales: esta corrida es la evidencia manual de T033.

## Entorno

```bash
# Servicios
docker ps                      # carvajal-db-1 (healthy), carvajal-qdrant-1
# Índice
125 puntos en `carvajal_chunks`, 24 documentos en PostgreSQL (migración 0007)
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
| `tool` | `548b04a7ffe143e9a53c75dc9f68966d` | `chat_turn` | `LangGraph`, `recall`, `route`, `tool`, `indicator_lookup` |
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

`metadata` de la raíz (producción, sin texto):

```json
{"request_id": "fe3de55f3ddc402abfeff2c16087ad7a", "thread_hash": "ede024270333f45a",
 "router": "rules", "route": "tool", "intent": "saludo", "tool": null, "tool_period": null,
 "preferences_count": 0, "question_chars": 74, "answer_chars": 79,
 "outcome": "ok", "ls_run_depth": 0}
```

## SC-002 · producción sin texto ni PII

Turno con la pregunta «Soy Ana Pérez, mi teléfono es 300 123 4567. ¿Ingresos de
Carvajal en 2025?» y `MONITORING_INCLUDE_CONTENT=false`. Lo almacenado:

```text
inputs raíz:  {}
outputs raíz: {'outcome': 'ok'}
metadata:     route, router, request_id, thread_hash, question_chars, answer_chars,
              intent, tool, tool_period, preferences_count, outcome
PII en todo el payload:   False
texto de la respuesta:    False
```

Los fragmentos recuperados sobreviven como `slug`, `chunk_id` y `score`, nunca
como texto. En las corridas de desarrollo (`include_content=true`) el contenido
sí viaja, como exige FR-006.

## SC-003 · servicio inalcanzable

Veinte turnos con `LANGSMITH_ENDPOINT=http://127.0.0.1:9/` (inalcanzable) y
otros veinte con el monitoreo apagado:

| Configuración | Latencia al primer fragmento (p95) |
|---|---:|
| Servicio caído | **5,3 ms** |
| Monitoreo apagado | 5,2 ms |
| Diferencia | **+0,1 ms** (límite: 100 ms) |

Todas las respuestas se emitieron y persistieron; los fallos quedaron como
`monitoring.failed` en los logs. Repetido en tres corridas: la diferencia se
mantuvo por debajo de 1 ms.

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

| Experimento | Preguntas | Ruta | Intención | Datos | Inválidas | Latencia media | Llamadas al modelo |
|---|---:|---:|---:|---:|---:|---:|---:|
| `routing-rules-2026-11-19T09:53:53Z` | 110 | 100,0 % | 100,0 % | 100,0 % | 0 | 0,29 ms | 0 |
| `routing-llm-2026-11-19T10:03:07Z` | 110 | 98,2 % | 97,3 % | 96,9 % | 0 | 3.082,8 ms | 110 |

- Dataset `carvajal-routing_questions` con **110 ejemplos**, creado en la primera
  corrida y reutilizado en la segunda sin duplicar.
- Ambas corridas tienen **110 runs enlazadas por `reference_example_id`**: la
  comparación alinea las filas por pregunta.
- Comparación pregunta a pregunta: **2 de 110** cambiaron de ruta
  (`direct → commercial` y `commercial → direct`). El resto coincide con el
  informe local de [`docs/resultados-router.md`](../../resultados-router.md).

## Marcas del visitante

`POST /chat/feedback` guarda `(request_id, useful, created_at)` en
`chat_feedback` (migración 0007). Con el monitoreo apagado los botones no se
renderizan (`data-monitoring="0"`); con el monitoreo activo y un turno no
muestreado la marca se guarda igual y **no** se crea una traza.

## Revisión de los criterios de éxito (T034)

| SC | Criterio | Resultado | Evidencia |
|---|---|---|---|
| SC-001 | 100 % de los turnos de las cuatro rutas con traza | **Cumplido** (2 rutas con agente real, 2 con el mismo tracer) | Tabla de `request_id` por ruta |
| SC-002 | 0 trazas con IP, cookies, CSRF, claves o visitante crudo; 0 con texto en producción | **Cumplido** | Turno de producción: `inputs {}`, `outputs {'outcome': 'ok'}`, sin PII ni respuesta |
| SC-003 | Servicio inalcanzable: 100 % de turnos respondidos y p95 sin empeorar más de 100 ms | **Cumplido** (+0,1 ms; 20 turnos) | Tabla de latencia |
| SC-004 | Encontrar la traza por `request_id` en menos de un minuto | **Cumplido** | Consulta por metadato `request_id` y por `thread_hash` |
| SC-005 | Dos corridas del enrutador como experimentos con métricas del informe local | **Cumplido** | 110/110 preguntas alineadas; 2 cambios de ruta |

Pendiente único: repetir `direct` y `rag` con el agente real cuando Ollama esté
levantado. No bloquea SC-001: el camino de traza es el mismo y el envío se
verificó con las otras dos rutas y con las dos simuladas.

## Pendientes

- Repetir `direct` y `rag` con el agente real cuando Ollama esté disponible en
  el entorno de la demo (`docker compose -f compose.prod.yml up ollama` y
  `ollama pull nomic-embed-text`). El resto del camino ya está evidenciado.
