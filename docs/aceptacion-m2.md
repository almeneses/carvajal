# Matriz de aceptación — Módulo 2

> Estado verificado el **13 de noviembre de 2026**. Base de integración:
> `origin/dev` en `f82e48e` (merge de #98). Rama de esta entrega:
> `feat/w7-m2-agente`. Los comandos se ejecutan desde la raíz del repo.

## A. RAG obligatorio sobre Qdrant

| Criterio | Estado | Evidencia / comando |
|---|---|---|
| Qdrant con volumen persistente y configuración por entorno | ✅ | `compose.yml`, `compose.prod.yml`, variables `QDRANT_*`; `make qdrant`. |
| Corpus indexado desde cero | ✅ | `uv run carvajal index-qdrant --recreate`: 24 documentos, 125 fragmentos, 768 dims. |
| IDs estables válidos para Qdrant | ✅ | UUID5 en `point_id()`; `tests/test_qdrant_store.py::test_upsert_es_idempotente...`. |
| Indexación idempotente y poda al cambiar el corpus | ✅ | `index_gold` + `prune()`; `test_prune_elimina_solo_los_puntos_obsoletos`. |
| Dimensión/métrica coherentes | ✅ | `ensure_collection` valida 768/coseno; test de dimensión incompatible. |
| Metadatos de documento, URL, procedencia y fragmento | ✅ | Payload con `document_id`, `url`, `title`, `source_type`, `chunk_id`, `text`; `docs/evidence/m2/smoke.json`. |
| Actualización del índice definida | ✅ | Reejecutar `make index` (upsert + poda); `--recreate` para cambio de modelo. Documentado en README. |
| Arranque web con índice preparado | ✅ | `make api` no indexa; `/health` devuelve `points=125` (`docs/evidence/m2/health.json`). |
| RAG integrado en la ruta real del chat | ✅ | `ChatService` → `GraphAgentRunner` → `QdrantVectorStore`; smoke real. |
| Top-k y citas verificables | ✅ | 6 fuentes con `url`, `title`, `source_type`; clic abre la fuente real. `rag_ciudad` en smoke. |
| Abstención con evidencia insuficiente | ✅ | Umbral + nombres propios; `q31` (Tokio) devuelve 0 fuentes y abstención. |
| Salud del proceso separada de la del índice | ✅ | `/live` 200 sin base; `/health` 503 si Qdrant falta, con `index.status`. |
| Registro de fragmentos indexados | ✅ | Salida del indexador y `/health` (`points`). |
| Dataset de evaluación reutilizando `questions.json` | ✅ | `data/eval/retrieval_questions.json` (31 consultas etiquetadas). |
| Reporte reproducible con top-k por consulta | ✅ | `docs/eval/retrieval-report.md` y `retrieval-results.json`; `make index-eval`. |
| Recall@10 / nDCG@10 | ✅ | **Recall@10 = 0.8444**, **nDCG@10 = 0.8002** (k=10). |
| Una pregunta sin evidencia documentada | ✅ | `q31`; `abstained=true`, 0 fuentes. |
| Retrieval inspeccionable separado de la respuesta | ✅ | El script no llama al LLM; el reporte incluye extractos y URLs. |

## B. Memorias, herramienta y router

| Criterio | Estado | Evidencia / comando |
|---|---|---|
| Grafo LangGraph con checkpointer real | ✅ | `PostgresSaver` en `apps/api/agent_runtime.py`; `tests/test_agent_graph.py`. |
| Esquema reproducible del checkpointer | ✅ | `setup()` en la primera pregunta, antes de abrir transacción (evita bloqueo de índice concurrente). |
| `thread_id` estable y pertenencia al dueño | ✅ | `thread_id = conversations.id`; `get_owned()`; `test_un_hilo_ajeno_no_se_puede_usar`. |
| Estado influye en seguimiento | ✅ | Router `follow_up` reescribe con la pregunta anterior; smoke `rag_seguimiento`. |
| Supervivencia a reinicio | ✅ | Reinicio real + `thread_id` recuperado; `docs/evidence/m2/smoke.json`. |
| Historial de UI sin duplicar turnos | ✅ | `client_turn_id` único (índice parcial) y replay; `test_reintento...`. |
| Store persistente con namespace por dueño | ✅ | `PostgresStore`, namespace `("carvajal","preferences",owner_key)`. |
| Preferencia visible y recuperada en otro hilo | ✅ | `memoria_guardar` y `memoria_recuperar_otro_hilo` (`memory=1`). |
| Aislamiento entre sesiones | ✅ | `aislamiento_otro_visitante` (`memory=0`). |
| Datos personales innecesarios evitados | ✅ | Solo se guarda la frase de preferencia declarada, máximo 280 chars. |
| Herramienta `@tool` con `args_schema` Pydantic | ✅ | `indicator_lookup`, `IndicatorQuery`; `tests/test_agent_router.py`. |
| Caso válido, parámetros inválidos y combinación inexistente | ✅ | EBITDA 2025-09; nombre/período inválido; EBITDA 2024 ausente. |
| Router explícito `tool`/`rag`/`direct` | ✅ | `decide_route`, orden documentado; tests de las tres rutas. |
| Saludo sin Qdrant ni herramienta | ✅ | `test_directo_no_consulta_qdrant` y smoke `directa`. |
| Consulta estructurada ejecuta la herramienta real | ✅ | Smoke `herramienta` (`tool=ebitda`, valor y URL). |
| Consulta documental usa Qdrant | ✅ | Smoke `rag_ciudad` (6 fuentes Qdrant). |
| Seguimiento elíptico recupera contexto | ✅ | `intent=follow_up`, mismo hilo, año 1904. |
| Ruta sin evidencia | ✅ | `sin_evidencia`: abstención sin fuentes. |
| Respuesta interrumpida identificada | ✅ | `status='incomplete'` y evento `error.incomplete=true`; `test_stream_falla_tras_un_token...`. |
| Cierre de recursos y consistencia checkpoint/historial | ✅ | Sesión cerrada antes del LLM; `test_el_stream_libera_la_conexion...`; reintento idempotente. |

## C. Frontend, trazas y entrega

| Criterio | Estado | Evidencia / comando |
|---|---|---|
| Frontera HTTP clara y backend dueño de credenciales/modelo | ✅ | `apps/web` solo consume `/api/*`; API concentra LLM/Qdrant/Store. |
| Frontend y API arrancables por separado | ✅ | `make web` (:8001) y `make api` (:8000); URL oficial del M2. |
| Diseño reutilizado y responsive | ✅ | `docs/evidence/m2/frontend-mobile.png` y `frontend-desktop.png`. |
| Streaming incremental real | ✅ | Eventos `token` en smoke y `tests/test_api_stream.py`. |
| Fuentes clicables | ✅ | `url` en `sources[]`; smoke `rag_con_fuente_url`. |
| Errores 4xx/5xx sin duplicar turno | ✅ | SSE `error` con mensaje, JS no reintenta un HTTP explícito; tests 403/429/503. |
| Caída de red, recarga y reinicio | ✅ | Reintento manual idempotente; `/api/chat/history`; reinicio real. |
| Cuota y CSRF conservados | ✅ | `tests/test_security.py`, `test_api_m2.py`; 403 antes de cuota. |
| Cookies, streaming y CORS al separar orígenes | ✅ | `test_cors_permite_el_origen_del_frontend`; `HttpOnly/SameSite=Lax`. |
| Logs con `request_id`, `thread_id`, ruta, duración, resultado, tool y fuentes | ✅ | `chat.turn_completed` en `apps/api/chat/service.py`; `RequestContextMiddleware`. |
| Evidencia anonimizada de los cuatro casos | ✅ | `docs/evidence/m2/smoke.json` (sin cookies ni claves). |
| Primer token medido | ✅/⚠️ | Mediana ~2,7 s; máximo 5,5 s por latencia de DeepSeek (corrida previa 1,4–2,3 s). Depende del proveedor externo; no se promete <3 s p95. |
| README con instalación, variables, arranque, migraciones, índice y URL | ✅ | README § Entrega M2. |
| Diagrama real del M2 | ✅ | Mermaid en README y `docs/arquitectura-m2.md`. |
| Enlace al reporte de retrieval | ✅ | README § Evaluación y `docs/demo-m2.md`. |
| Evidencia de PR fusionadas y revisión | ✅ | #98 merge `f82e48e`; 74 PR fusionadas; 39 con revisión automática publicada; 3 con aviso de revisión parcial/truncada. No hay revisión del rol formal del curso registrada en GitHub: se distingue de la revisión automática de Dippy/DeepSeek. |
| PR existentes | ✅ | #98 integrada con `--admin` autorizado; #72/#83/#86/#43 cerradas conservando ramas; #99/#100 conservadas para OIDC (colisión de `0004`); #40–#45 en mantenimiento de `main`. |
| Demo 12 min + 3 de preguntas | ✅ | `docs/demo-m2.md`. |
| `make check` e integración | ✅ | 430 unitarias + 41 de integración; `docs/evidence/m2/tests-*.txt`. |
| Ensayo desde clon limpio | ⚠️ | Pendiente de repetir tras el commit final; comandos en README. |
| Defensa contra instrucciones incrustadas / evaluación LangSmith / Playwright | ⚠️ | Deseable no ejecutado; el prompt ya neutraliza delimitadores y el contrato SSE está probado con HTTP. |

## Pendiente por credenciales o servicios externos

- DeepSeek: la demo real necesita `DEEPSEEK_API_KEY`; sin red se usa Ollama.
- Qdrant y Postgres: `compose` los levanta, pero el índice debe prepararse con
  `make index`.
- GitHub: la nueva PR de esta rama queda a decisión del owner; el merge de #98
  usó la autorización puntual del enunciado.
- `#99/#100`: la migración `0004` de #99 colisiona con `0004_usage_day` de
  `dev`; se retomarán con una migración renumerada.
