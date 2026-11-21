# Implementation Plan: Retiro de código y dependencias sin consumidores

**Branch**: `refactor/043-codigo-dependencias` | **Date**: 2026-11-20 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/043-codigo-dependencias/spec.md`

## Summary

Retirar tres piezas sin consumidores y dos declaraciones directas de dependencias, con
evidencia reproducible y sin cambiar el comportamiento observable:

1. `_safe_rollback` de `apps/api/chat/service.py` (sin llamadas).
2. El embedder de `ChatService`: parámetro del constructor, atributo, clase
   `_DeferredEmbedder` y el argumento en `default_chat_service()`.
3. La configuración de `GraphAgentRunner`: parámetro `settings`, atributo `_settings` y la
   construcción `Settings()` de `default_agent_runner()`.
4. La declaración directa de `langchain` en el extra `legacy`, si la instalación completa
   y el import del módulo histórico de RAG lo demuestran.
5. La declaración directa de `python-dotenv` en las dependencias base, si la instalación
   base y la carga de `.env` demuestran que llega por `pydantic-settings`.

El orden es: caracterización → retiradas de código → retiradas de dependencias → puerta +
imagen → PR. FAISS, SQLite, Sentry y las referencias a pgvector de las migraciones quedan
intactos.

## Technical Context

**Language/Version**: Python 3.13 (venv ya sincronizado en el worktree con `uv`)
**Primary Dependencies**: FastAPI, Jinja2, SQLAlchemy 2.0, Alembic, LangGraph, LangChain
(integraciones sueltas), Qdrant, `pydantic-settings`; extras `capture` y `legacy`
**Storage**: PostgreSQL (app, chat, checkpoints), Qdrant (índice vectorial), SQLite
(checkpointer de demo), FAISS (CLI histórica)
**Testing**: pytest (`make check` = Ruff + `ruff format --check` + `ty` + pytest; la suite
excluye `integration`, `e2e` y `smoke`); `node --test tests/js/test_chat_voice.cjs`
**Target Platform**: Linux; imagen Docker multi-etapa `python:3.13-slim` + `uv`
**Project Type**: app web única con CLI y paquete `packages/core/src/carvajal`
**Performance Goals**: sin cambio; el cambio no añade trabajo en el camino de una petición
**Constraints**: sin servicios nuevos, sin `pip`, sin framework nuevo; una sola app
FastAPI/Jinja2; cambios quirúrgicos en `service.py` porque otros PRs tocan el archivo
**Scale/Scope**: 4 símbolos, 2 declaraciones de dependencia, ~9 archivos entre código y
pruebas y ~200 líneas netas retiradas

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principio | Verificación | Resultado |
|---|---|---|
| I. La constitución manda | El plan no cambia arquitectura, stack ni proceso; el retiro de dependencias declaradas se registra en el commit y se reporta al owner para la fila de `CONSTITUTION.md` §9 | ✅ |
| II. El corpus es el contrato | No se toca `data/gold/`, `data/external/`, `data/catalog/` ni la sincronización | ✅ |
| III. Sin fuentes no hay respuesta | No se toca el prompt, el grafo, el router ni la recuperación | ✅ |
| IV. Calidad automatizada y reproducible | `uv` sigue siendo el único gestor; `make check` en verde antes de abrir el PR; lock regenerado con `uv lock` | ✅ |
| V. Captura responsable | No se toca la captura | ✅ |
| VI. Cambios por Pull Request | Rama propia, commits convencionales, PR a `dev`, sin merge ni aprobación | ✅ |

No hay violaciones que justificar. La única decisión condicional (conservar una dependencia
si falla la evidencia) está prevista por la propia spec y no viola ningún principio.

## Project Structure

### Documentation (this feature)

```text
specs/043-codigo-dependencias/
├── spec.md
├── plan.md
├── tasks.md
├── analysis.md
└── checklists/
    └── requirements.md
```

### Source Code (repo root, worktree `.worktrees/043`)

```text
apps/api/chat/service.py     # retira _safe_rollback, embedder, _DeferredEmbedder; ajusta default_chat_service
apps/api/chat/agent.py       # retira settings de GraphAgentRunner y de default_agent_runner
pyproject.toml               # retira langchain (legacy) y python-dotenv (base)
uv.lock                      # regenerado con uv lock
tests/test_chat_service.py   # dobles de prueba sin embedder
tests/test_chat_tracing.py   # dobles de prueba sin embedder
tests/test_monitoring_smoke.py
tests/test_monitoring_e2e.py
tests/test_agent_graph.py    # GraphAgentRunner sin settings
tests/test_productos.py
tests/test_characterization_043.py   # pruebas de caracterización nuevas
```

No se modifican `apps/api/runtime.py` (el acceso perezoso `ask_components` lo sigue usando
`_DeferredLLM`, que no cambia), ni `apps/api/views/`, `static/`, `templates/`, el corpus ni
el grafo.

## Phase 0 — Investigación (evidencia recogida antes del plan)

| Pregunta | Evidencia | Decisión |
|---|---|---|
| ¿Quién llama a `_safe_rollback`? | `grep -rn "_safe_rollback" apps packages tests scripts` → solo la definición en `service.py:608`; el resto son coincidencias de Alembic dentro de `.venv` | Retirar |
| ¿Dónde se usa el embedder de `ChatService`? | Solo se asigna en `__init__` (`self._embedder`); ninguna otra aparición en la clase. `_DeferredEmbedder` solo existe para `default_chat_service` | Retirar parámetro, atributo, clase y argumento; mantener `_DeferredLLM` intacto para no tocar `runtime.py` |
| ¿Quién usa `GraphAgentRunner.settings`? | Solo se asigna en `__init__`; `prepare`/`record` no leen `self._settings` | Retirar parámetro y atributo; ajustar dobles de prueba |
| ¿Alguien importa `langchain` de forma directa? | `grep -rn "^import langchain\|^from langchain\."` → 0 resultados. Las integraciones usan `langchain_core`, `langchain_community`, `langchain_anthropic`, `langchain_ollama`, `langchain_openai`, `langchain_text_splitters` | Retirar `langchain` del extra `legacy` y validar con `uv sync --all-extras` + import de `carvajal.rag` |
| ¿Quién instala `python-dotenv`? | `uv.lock` muestra `python-dotenv` como dependencia de `pydantic-settings` (base) y de `testcontainers` (dev) | Retirar la declaración directa base y validar `uv sync` + lectura de `.env` |
| ¿Qué debe conservarse? | La extra `legacy` sigue con `faiss-cpu`, `langchain-anthropic` y `langchain-community`; `agent_checkpointer=sqlite`; `sentry-sdk` en base; migración histórica con pgvector | Conservar sin cambios |

**Resultado**: no quedan `NEEDS CLARIFICATION`; todas las decisiones tienen evidencia o un
comando de validación asociado.

## Phase 1 — Diseño

### Caracterización (antes de tocar código)

Archivo nuevo `tests/test_characterization_043.py` con tres bloques, todos verdes en
`origin/dev` y en la rama:

1. **ChatService**: con SQLite en memoria y dobles de LLM/runner, fija que `stream` emite
   `open`, `trace`, `meta`, `token` y `done`; que `ask` recompone texto y citas; que el
   historial persiste el turno; y que `default_chat_service()` es cacheado, perezoso y no
   resuelve clientes al construirse (sin depender de la firma del constructor ni de
   `ask_components`, para que el test siga pasando tras el retiro).
2. **Runner del agente**: con un grafo doble, fija los campos de `AgentTurn` devuelto por
   `GraphAgentRunner.prepare`, la adición única de un turno en `record`, el aviso sin
   excepción cuando el checkpointer falla y que `default_agent_runner()` se construye sobre
   `apps.api.agent_runtime.agent_runtime` con un grafo doble (sin depender de la firma).
3. **CLI**: `build_parser()` mantiene `prog`, subcomandos `ask` y `faq`, pregunta posicional
   y salida por defecto; `main(["ask", "--help"])` y `main(["faq", "--help"])` salen con
   código 0 y muestran su ayuda.

El test se escribe contra el árbol actual y se ejecuta con `env -u OLLAMA_MODEL uv run
pytest tests/test_characterization_043.py` antes de cualquier cambio; ese es el punto de
control que se repite al final.

### Retiradas (cada una en su commit)

1. `refactor(043): retira el rollback sin consumidores de ChatService`.
2. `refactor(043): retira el embedder sin uso de ChatService y su cableado`.
3. `refactor(043): retira la configuración no usada de GraphAgentRunner`.
4. `refactor(043): retira las dependencias directas sin uso (langchain, python-dotenv)`,
   con el `uv.lock` regenerado.

Cada commit deja la suite en verde y no reformatea `service.py` entero: los cambios son
quirúrgicos para no chocar con los otros PRs de la fase.

### Decisiones de diseño

- **No tocar `apps/api/runtime.py`**: `_DeferredLLM` sigue llamando a
  `runtime.ask_components()`, así que el acceso perezoso y `tests/test_api.py` no cambian.
  Se retira la pieza que sobra (el embedder del propio `ChatService`), no un contrato
  compartido.
- **La firma de `GraphAgentRunner` se simplifica a `GraphAgentRunner(graph)`**; el grafo ya
  contiene toda la configuración tipada (vive en `AgentDeps`), así que pasar `Settings` era
  redundante.
- **Dependencias condicionales**: si `uv sync --all-extras` o el import de `carvajal.rag`
  falla sin `langchain`, se restaura la línea y se documenta. Igual para `python-dotenv` y
  la carga de `.env`.
- **Puerta**: `env -u OLLAMA_MODEL make check` y `node --test tests/js/test_chat_voice.cjs`;
  además `uv sync` base en un venv desechable (`.venv-base-043/`, fuera del repo trackeado)
  para demostrar la instalación base sin extras.

### Verificación (contratos)

| Contrato | Cómo se verifica |
|---|---|
| Eventos del chat y persistencia | `pytest tests/test_chat_service.py tests/test_api_chat.py tests/test_api_stream.py` + caracterización |
| `default_chat_service` perezoso y cacheado | `pytest tests/test_api.py -k difiere` + caracterización |
| Runner del agente | `pytest tests/test_agent_graph.py tests/test_productos.py` + caracterización |
| CLI histórica | `pytest tests/test_cli.py tests/test_rag.py`; `uv run carvajal --help`; `uv run python -c "import carvajal.rag"` |
| Dependencias completas | `uv sync --all-extras` (código 0) y `uv sync` base en venv desechable |
| Imagen | `docker build -t carvajal-api:043 .`; arranque con `docker run` y `GET /live` |
| Puerta completa | `env -u OLLAMA_MODEL make check` + `node --test tests/js/test_chat_voice.cjs` |

## Complexity Tracking

Sin violaciones: no hay estructura nueva, ni capas nuevas, ni dependencias nuevas.

## Riesgos y reversión

| Riesgo | Mitigación |
|---|---|
| Un consumidor dinámico del embedder o de `settings` no visible con `grep` | Pruebas de caracterización y suite completa; retirada en commits separados y revertibles |
| `langchain` resulta necesario por un import indirecto | La validación es instalar y ejecutar; si falla, se conserva la declaración y se documenta |
| `python-dotenv` deja de instalarse en algún escenario | `uv sync` base + prueba de `Settings` leyendo `.env`; si falla, se conserva |
| Conflicto con otros PRs de la fase en `service.py` | Cambios quirúrgicos, sin reformateo del archivo |
| Build de Docker inviable en la máquina | Se reporta el motivo exacto; no se cambia el Dockerfile para sortearlo |

**Reversión**: `git revert` de los commits de la fase (son independientes y pequeños), o
restaurar `pyproject.toml`/`uv.lock` desde `dev` y ejecutar `uv lock && uv sync --all-extras`.
