# Implementation Plan: Monitoreo del agente con LangSmith

**Branch**: `038-monitoreo-langsmith` | **Date**: 2026-11-18 | **Spec**: [`spec.md`](./spec.md)
**Input**: Feature specification from `/specs/038-monitoreo-langsmith/spec.md`

## Summary

Añadir trazas opcionales por turno del chat, con LangSmith, sin acoplar el chat al servicio:
una traza raíz por turno, los pasos del grafo y las llamadas al modelo como hijos, y un
cliente que redacta el contenido en producción. El monitoreo queda apagado por defecto, se
muestrea por turno y cualquier fallo del servicio se registra en los logs sin afectar la
respuesta, la persistencia ni la cuota. Las evaluaciones existentes pueden registrarse como
experimentos y el visitante puede marcar una respuesta como útil o no útil (P3).

## Technical Context

| Aspecto | Decisión | Evidencia |
|---|---|---|
| Lenguaje | Python 3.13, `uv` | `pyproject.toml` |
| Framework | FastAPI + shell HTMX; LangGraph 1.2 | `apps/api`, `agent/graph.py` |
| SDK de monitoreo | `langsmith` 0.14 (ya llega con LangChain; se declara directo) | `uv.lock` |
| Trazas | `RunTree` raíz + `tracing_context(parent=...)`; los nodos y el LLM se trazan solos | `langsmith` 0.14 |
| Redacción | `Client(hide_inputs=..., hide_outputs=...)` con lista blanca de claves | FR-006 |
| Muestreo | `random() < rate` por turno; sin muestreo no se crea la traza raíz | FR-007 |
| Persistencia de marcas | PostgreSQL, migración `0007` | FR-012 |
| Pruebas | `pytest` con cliente falso; CI sin credenciales ni red | FR-010 |
| Plataforma | Linux/VPS; navegador para las marcas | `compose.prod.yml` |
| Rendimiento | El primer token no empeora más de 100 ms (p95) con el servicio caído | SC-003 |
| Escala | Muestreo configurable; recomendado 10 % en producción | FR-007 |

## Constitution Check

*GATE: Must pass before Phase 0. Re-check after Phase 1.*

| Puerta | Cómo se cumple |
|---|---|
| I. La constitución manda | Requiere enmienda de §2 y §9 en la PR que encienda la capacidad (la 038 ya está declarada en §2) |
| II. El corpus es el contrato | No se toca `data/gold/` ni el corpus; solo se observan turnos |
| III. Sin fuentes no hay respuesta | La traza conserva `request_id` y documentos de origen (slug, puntuación) para cruzar la respuesta con el corpus |
| IV. Calidad automatizada | Cada FR tiene prueba; las pruebas no salen a la red ni piden credenciales |
| V. Captura responsable | No hay captura nueva |
| VI. Cambios por PR | Seis PRs pequeños a `dev`, cada uno ≤300 líneas |
| §3 Stack | No hay dependencia nueva de instalación: `langsmith` ya está en el árbol por LangChain; se declara directa para no depender de un transitivo |
| Privacidad | Producción sin texto; desarrollo y evaluación con contenido; nunca IP, cookies, CSRF, llaves ni `owner_key` |

**Enmienda propuesta** (`CONSTITUTION.md` §2 y §9, y `.specify/memory/constitution.md`):
pasar LangSmith de «spec 038 en borrador» a capacidad entregada, con el modo de contenido y
el muestreo como decisión registrada.

## Project Structure

### Documentation (this feature)

```text
specs/038-monitoreo-langsmith/
├── spec.md
├── plan.md              # Este archivo
├── tasks.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
packages/core/src/carvajal/
├── infrastructure/
│   ├── settings.py              # nuevas variables de monitoreo
│   └── monitoring.py            # nuevo: cliente, traza raíz, redacción, experimentos
├── agent/
│   ├── graph.py                 # sin cambios de contrato; los nodos se trazan solos
│   └── routers/evaluation.py    # datos por pregunta para el experimento
└── cli.py                       # --langsmith en eval-router

apps/api/
├── main.py                      # /health incluye el estado del monitoreo
├── chat/
│   ├── service.py               # abre y cierra la traza del turno
│   └── feedback.py              # nuevo (P3): marcas del visitante
├── views/chat.py                # POST /chat/feedback (P3)
└── templates/chat.html          # botones útil/no útil (P3)

migrations/versions/
└── 0007_chat_feedback.py        # nuevo (P3)

scripts/eval_retrieval.py        # --langsmith opcional

tests/
├── test_monitoring.py           # redacción, muestreo, fallos, salud
├── test_api_health.py           # bloque monitoring en /health
├── test_eval_experiments.py     # registro sin red
└── test_feedback.py             # P3
```

**Structure Decision**: la lógica de LangSmith vive en `infrastructure/monitoring.py` para
que la app y la CLI la compartan sin invertir dependencias; `apps/api` solo la inyecta.

## Diseño

### Configuración

| Variable | Por defecto | Regla |
|---|---|---|
| `MONITORING_ENABLED` | `false` | Sin esto no se crea cliente ni se envía nada |
| `LANGSMITH_API_KEY` | vacío | Obligatoria si el monitoreo está activo; nunca se registra |
| `LANGSMITH_PROJECT` | `carvajal` | Proyecto de LangSmith |
| `LANGSMITH_ENDPOINT` | vacío | Solo para un servicio compatible o pruebas |
| `MONITORING_SAMPLE_RATE` | `0.2` | Se aplica solo si está activo; `0` equivale a apagado |
| `MONITORING_INCLUDE_CONTENT` | `false` | `true` en desarrollo/evaluación; `false` en producción |
| `MONITORING_TIMEOUT_MS` | `2000` | Timeout del cliente; no bloquea el turno |

No se usan las variables globales `LANGSMITH_TRACING`/`LANGCHAIN_TRACING_V2`: el encendido es
explícito y por turno, para que una variable de entorno heredada no empiece a enviar contenido.

**Presupuesto para la demo**: el plan gratuito de LangSmith incluye 5.000 trazas al mes. Con
muestreo del 20 % se cubren hasta 25.000 turnos al mes, y una corrida de evaluación de los
tres enrutadores sobre 110 preguntas consume unas 330 trazas; dos corridas y la evaluación de
retrieval dejan margen de sobra. Si el uso supera 4.000 trazas en el mes, bajar el muestreo a
0,1 o apagarlo; el límite no se toca desde el código. En local y evaluación se usa 1,0.

### Traza de un turno

```text
chat_turn (RunTree, raíz)
├── recall (nodo LangGraph)
├── route (nodo LangGraph)
├── rag | tool | commercial | direct (nodo)
│   ├── tool/quote (si aplica)
│   └── llm (ChatOpenAI/ChatOllama: modelo, tokens, latencia)
└── persist (si se marca como paso)
```

- La raíz se crea al resolver el hilo y se cierra en el `finally` del stream, de modo que una
  cancelación del visitante deja la traza como `cancelled`.
- `metadata` de la raíz: `request_id` (el mismo de los logs), `thread_hash` (SHA-256 truncado
  del hilo), `router` (`rules`/`llm`/`decision`) y, al cerrar, `route`, `intent`, `tool` y
  `outcome` (`ok`/`cancelled`/`failed`).
- Los hijos los produce LangGraph y LangChain dentro de `tracing_context(parent=raíz)`; no se
  instrumentan nodos a mano.
- En desarrollo la raíz guarda pregunta y respuesta; en producción solo longitudes y
  metadatos. La decisión se toma con `MONITORING_INCLUDE_CONTENT`.

### Redacción

Lista blanca de claves que sobreviven en producción (recursiva, sin importar la profundidad):
`route`, `intent`, `tool`, `tool_name`, `tool_period`, `slug`, `chunk_id`, `layer`,
`source_type`, `score`, `scores`, `model`, `model_name`, `provider`, `token_usage`,
`usage_metadata`, `input_tokens`, `output_tokens`, `total_tokens`, `duration_ms`,
`error_type`, `status`. Todo lo demás (`question`, `answer`, `prompt`, `system`, `content`,
`excerpt`, `text`, `messages`, `inputs`, `outputs`) se reemplaza por `"[redactado]"` o se
elimina. Con contenido activo se devuelve el diccionario sin tocar.

### Integración con el chat

- `ChatService.__init__(..., tracer: TurnTracer | None = None)`. El valor por defecto se
  construye desde `Settings`; las pruebas inyectan un doble.
- `TurnTracer.turn(...)` es un *context manager* síncrono que envuelve desde
  `runner.prepare` hasta el último token. `ChatService.stream` lo abre después de resolver
  el hilo y lo cierra en `finally`; si el turno falla, recibe el error.
- El tracer no conoce LangGraph: solo abre el contexto. Los nodos y el LLM aparecen solos.
- Un turno no muestreado no crea cliente, raíz ni hijos.
- El replay de un turno guardado no se traza: no hay llamada al modelo.

### Salud

`/health` añade:

```json
"monitoring": {"enabled": true, "sample_rate": 0.1, "content": "metadata"}
```

Nunca incluye la llave ni la URL del servicio.

### Experimentos de evaluación

- `carvajal eval-router --router rules|llm|decision --langsmith` y
  `scripts/eval_retrieval.py --langsmith`.
- Se crea (o reutiliza) un dataset con las preguntas locales y se registra un experimento
  con las salidas ya calculadas por el informe local; no se vuelve a llamar al modelo.
- Metadatos: commit, fecha, enrutador, modelo, config y métricas agregadas. Sin `--langsmith`
  el comportamiento es idéntico al actual y no hay red.
- Un fallo del servicio degrada a «experimento no registrado» y deja el informe local.

### Marcas del visitante (P3)

- Migración `0007`: `chat_feedback(request_id, useful, created_at)`, con `request_id` único
  e idempotente (volver a marcar actualiza).
- `POST /chat/feedback` con CSRF y sin cuota; responde `204` y no persiste texto.
- Los botones solo se renderizan con el monitoreo activo; sin él, no aparecen.
- La marca se asocia a la traza por `request_id` cuando existe; no fuerza el muestreo.

## Plan de PRs

| PR | Alcance | Archivos principales | Límite |
|---|---|---|---|
| 1 | Configuración y módulo de monitoreo con redacción y salud | `settings.py`, `infrastructure/monitoring.py`, `tests/test_monitoring.py` | ~250 |
| 2 | Traza raíz y muestreo integrados al turno | `chat/service.py`, `apps/api/main.py`, `tests/test_api_health.py` | ~250 |
| 3 | Experimentos de evaluación | `cli.py`, `scripts/eval_retrieval.py`, `tests/test_eval_experiments.py` | ~250 |
| 4 | Marcas del visitante (P3) | migración `0007`, `feedback.py`, `views/chat.py`, plantilla, pruebas | ~250 |
| 5 | Enmienda de constitución y documentación | `CONSTITUTION.md`, `.specify/memory/constitution.md`, `docs/`, `.env.example` | ~200 |
| 6 | Evidencia de los SC | `docs/evidence/m2/monitoring.md`, corridas | ~150 |

## Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Fuga de contenido en producción | Lista blanca por defecto; prueba con pregunta que contiene nombre y teléfono; `MONITORING_INCLUDE_CONTENT=false` |
| El SDK bloquea el turno | Cliente en segundo plano, timeout corto y `try/except`; prueba con cliente que lanza |
| Trazas duplicadas o huérfanas | Una raíz por turno, cierre en `finally`; replay sin traza |
| Costo por volumen | Plan gratuito de 5.000 trazas/mes; muestreo 20 % por defecto (hasta 25.000 turnos/mes), 100 % solo en local/evaluación y apagado por defecto |
| Dependencia transitiva | Declarar `langsmith` directo; no se añade nada al runtime web |
| Marcas sin traza | Se guardan igual por `request_id`; no fuerzan muestreo |

## Orden de entrega

1. PR 1 y 2: traza de un turno y salud. Es el MVP de US1 + US2.
2. PR 3: experimentos (US3).
3. PR 4: marcas (US4, P3).
4. PR 5 y 6: constitución, documentación y evidencia.

Cada PR deja `make check` en verde y no requiere credenciales para las pruebas.
