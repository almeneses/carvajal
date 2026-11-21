# Análisis de consistencia: retiro de código y dependencias sin consumidores (043)

**Ejecutado**: 2026-11-20, después de generar `tasks.md` y antes de implementar.
**Alcance**: `spec.md` ↔ `plan.md` ↔ `tasks.md` contra `.specify/memory/constitution.md` y
`CONSTITUTION.md`.
**Modo**: solo lectura sobre los artefactos; las correcciones CRITICAL se aplicaron al
plan/tareas antes de implementar (no hubo ninguna).

## Resultado

- **CRITICAL: 0.** No hay violaciones de la constitución ni requisitos sin cobertura que
  bloqueen la línea base.
- **HIGH: 0.**
- **MEDIUM: 1** (cobertura de preservación delegada en un único task de verificación).
- **LOW: 2** (notas de higiene del entorno desechable y del manejo de `--help` en la CLI).

## Cobertura de requisitos funcionales

| Requisito | ¿Tiene tarea? | Tareas | Notas |
|---|---|---|---|
| FR-001 `_safe_rollback` sin consumidores | Sí | T002, T007 | T002 registra la búsqueda; T007 retira |
| FR-002 embedder fuera de `ChatService` | Sí | T003, T008, T009 | La caracterización (T003) fija que el contrato no dependa del embedder |
| FR-003 contrato `ChatService` intacto | Sí | T003, T010 | Eventos, `history`, `ask`, `stream`, `new_conversation`, `default_chat_service` |
| FR-004 `GraphAgentRunner` sin configuración | Sí | T004, T011 | La caracterización fija `AgentTurn` y `record` |
| FR-005 `default_agent_runner` real | Sí | T004, T011, T013 | T004 fija la construcción sobre `agent_runtime()` |
| FR-006 retirar `langchain` (legacy) con evidencia | Sí | T014 | Condicional: si falla, se restaura y documenta |
| FR-007 retirar `python-dotenv` (base) con evidencia | Sí | T015 | Condicional: si falla, se restaura y documenta |
| FR-008 conservar FAISS, SQLite, Sentry y pgvector | Sí | T017 | Solo verificación; el alcance prohíbe retirarlos, así que no hay tarea de borrado |
| FR-009 sin cambios de rutas, SSE, CSRF, cuota, esquema ni CLI | Sí | T003, T004, T005, T010, T013, T016 | Caracterización + suites específicas + puerta completa |
| FR-010 imagen Docker y `/live` | Sí | T018 | Condicional a que el build de la máquina funcione; el motivo se documenta |
| FR-011 caracterización antes/después y puerta | Sí | T006, T016, T019 | Punto de control doble |
| FR-012 evidencia y reversión documentadas | Sí | T019, T020 | Evidencia en `analysis.md`/PR; reversión en `plan.md` y cuerpo del PR |

## Cobertura de criterios de éxito

| Criterio | ¿Tiene tarea? | Tareas | Notas |
|---|---|---|---|
| SC-001 0 consumidores de los tres símbolos | Sí | T002, T010, T013 | Búsqueda repetida al final |
| SC-002 puerta en verde sin perder pruebas | Sí | T016, T019 | `make check` + JS |
| SC-003 instalación base y completa + CLI | Sí | T014, T015, T016 | Incluye import de `carvajal.rag` y `--help` |
| SC-004 imagen responde en `/live` | Sí | T018 | Condicional |
| SC-005 dos declaraciones directas menos | Sí | T014, T015 | `pyproject.toml` + `uv.lock` |
| SC-006 diff solo en el alcance | Sí | T020 | Revisión del diff antes del PR |

## Hallazgos

| ID | Categoría | Severidad | Ubicación | Resumen | Recomendación |
|---|---|---|---|---|---|
| C1 | Cobertura | MEDIUM | `tasks.md` T017 | La preservación de FAISS/SQLite/Sentry/pgvector se verifica en un solo task y no tiene una prueba de «no se retiró» explícita | T017 ejecuta las suites que ejercen esas capacidades; además el diff no toca `pyproject.toml` en esas líneas, lo que se revisa en T020. Aceptable sin cambio de plan |
| D1 | Duplicación | LOW | `tasks.md` T003/T010 | La caracterización del chat y la suite existente se solapan en eventos y persistencia | Intencional: la caracterización es el punto de control previo y la suite es la red de seguridad posterior |
| E1 | Ambigüedad | LOW | `plan.md` §Verificación | El venv desechable `.venv-base-043/` debe quedar fuera del repo y borrarse al terminar | Usar ruta absoluta bajo `/tmp` o borrarla antes del commit; el `.gitignore` ya ignora `.venv*` |
| E2 | Ambigüedad | LOW | `tasks.md` T005 | `main(["ask", "--help"])` sale con `SystemExit(0)`; la prueba debe capturarlo | `pytest.raises(SystemExit)` con `code == 0` y `capsys` |

## Alineación con la constitución

- **I. La constitución manda**: el plan no cambia arquitectura ni stack; el retiro de
  dependencias se registra en el commit y se reporta al owner para la fila de §9.
- **II. El corpus es el contrato**: ninguna tarea toca `data/`.
- **III. Sin fuentes no hay respuesta**: ninguna tarea toca prompts, grafo, router ni
  recuperación (el grafo solo recibe una firma más corta, sin cambios de estado).
- **IV. Calidad automatizada y reproducible**: `uv` sigue siendo el único gestor; `make
  check` es la puerta.
- **V. Captura responsable**: sin cambios.
- **VI. Cambios por Pull Request**: rama, commits convencionales, PR a `dev`, sin merge.

## Tareas sin requisito mapeado

- T001, T019 y T020 son de proceso/entrega (documentación, evidencia y PR): no mapean a un
  FR, pero son obligatorias por el flujo del goal.
- T002 es la evidencia de FR-001/SC-001.
- T018 corresponde a FR-010/SC-004.

## Métricas

- Requisitos funcionales: 12 · criterios de éxito: 6 · tareas: 20.
- Cobertura FR con al menos una tarea: 12/12 (100 %).
- Cobertura SC con al menos una tarea: 6/6 (100 %).
- Ambigüedades abiertas: 0 (las dos LOW son notas operativas, no decisiones).
- Duplicaciones: 1 intencional (D1).
- Hallazgos CRITICAL: 0.

## Próximas acciones

Sin hallazgos CRITICAL: se puede implementar. Antes de abrir el PR, actualizar la sección
«Resultado de la implementación» de este archivo con los comandos ejecutados, la salida
exacta y la decisión final sobre `langchain` y `python-dotenv`.

## Evidencia de consumidores (T002)

Búsquedas ejecutadas sobre `origin/dev` antes de tocar código:

```text
$ grep -rn "_safe_rollback" --include="*.py" apps packages tests scripts
apps/api/chat/service.py:608:def _safe_rollback(session: Session | None) -> None:
# (las demás coincidencias están dentro de .venv/lib/.../alembic)

$ grep -rn "self\._embedder" apps/api/chat/service.py
apps/api/chat/service.py:122:        self._embedder = embedder
# única aparición: se asigna y no se lee nunca

$ grep -rn "self\._settings" apps/api/chat/agent.py
apps/api/chat/agent.py:95:        self._settings = settings
# única aparición: se asigna y no se lee nunca

$ grep -rn "^import langchain\b\|^from langchain\." --include="*.py" apps packages tests
# sin resultados; las integraciones usan langchain_core, langchain_community,
# langchain_anthropic, langchain_ollama, langchain_openai y langchain_text_splitters
```

No hay reexports indirectos: `__all__` de ambos módulos no incluye los atributos privados
retirados y los constructores solo se instancian en los puntos revisados (código real y
dobles de prueba).

## Resultado de la implementación (T019)

Comandos ejecutados en `.worktrees/043`, rama `refactor/043-codigo-dependencias`
(PR [#203](https://github.com/almeneses/carvajal/pull/203)), con
`UV_CACHE_DIR=<repo>/.uv-cache` y `env -u OLLAMA_MODEL` donde aplica.

### Caracterización antes y después

```text
# Antes de tocar código (rama en origin/dev):
$ env -u OLLAMA_MODEL uv run pytest tests/test_characterization_043.py
13 passed, 1 warning in ~3 s
# (ver commit 15682e2, que solo añade la caracterización y la spec)

# Después del retiro:
$ env -u OLLAMA_MODEL uv run pytest tests/test_characterization_043.py
13 passed, 1 warning in 3.03s
```

### Búsqueda final de consumidores (SC-001)

```text
$ grep -rn "_safe_rollback" apps packages tests scripts      → 0 (solo el docstring de la caracterización)
$ grep -rn "self\._embedder" apps/api/chat/service.py       → 0 (aplicación/ask.py y sync_qdrant.py conservan el suyo, correcto)
$ grep -rn "self\._settings" apps/api/chat/agent.py         → 0
```

### Puerta de calidad

```text
$ env -u OLLAMA_MODEL make check
uv run ruff check .                → All checks passed!
uv run ruff format --check .       → 338 files already formatted
uv run ty check                    → All checks passed!
pytest -m "not integration and not e2e and not smoke"
                                   → 1656 passed, 46 deselected, 2 warnings in 16.57s
$ node --test tests/js/test_chat_voice.cjs
                                   → tests 68, pass 68, fail 0
```

La suite pasa de 1643 a 1656 pruebas: las 13 nuevas son la caracterización de la fase.

### Instalación y dependencias (SC-003 y SC-005)

```text
$ uv lock
Resolved 120 packages in 2.09s
Removed langchain v1.4.7

$ uv sync --all-extras
Uninstalled 2 packages in 5ms
  - langchain==1.4.7
Installed 1 package in 1ms
  ~ carvajal==0.1.0 (from file:///.../.worktrees/043)

$ uv run python -c "...find_spec..."
langchain: None
dotenv: True          # llega por pydantic-settings
faiss: True           # extra legacy intacto

$ uv run python -c "from carvajal import rag; ..."
rag ok: ask build_retriever

$ uv run carvajal --help        → exit 0 (14 subcomandos listados)
$ uv run carvajal ask --help    → exit 0
$ uv run carvajal faq --help    → exit 0
```

Instalación **base** en un venv desechable (sin extras, con grupo de desarrollo):

```text
$ UV_PROJECT_ENVIRONMENT=/tmp/carvajal-base-043-venv uv sync --frozen
$ /tmp/carvajal-base-043-venv/bin/python -c "..."
langchain: None
faiss: None
dotenv: True
paquete ok
$ /tmp/carvajal-base-043-venv/bin/carvajal --help   → exit 0
$ ... Settings(_env_file=None)                      → settings ok: ollama
```

CLI completa como herramienta instalada (`uv tool install`):

```text
$ UV_TOOL_DIR=/tmp/t043 UV_TOOL_BIN_DIR=/tmp/b043 uv tool install ".[capture,legacy]"
Installed 1 executable: carvajal                 → exit 0
$ carvajal --help / carvajal ask --help / carvajal faq --help   → exit 0
$ /tmp/t043/carvajal/bin/python -c "from carvajal import rag; ..."
rag ok: ask
langchain: None
faiss: True
dotenv: True
```

Aclaración sobre el comando documentado `uv tool install "carvajal[capture,legacy]"`: en
esta máquina resuelve el paquete homónimo de PyPI `carvajal==0.2.1`, que no declara los
extras `capture`/`legacy`, y falla con `error: Failed to install entrypoints for carvajal`
(exit 2). El proyecto no está publicado con ese nombre, así que la validación local
equivalente es `uv tool install ".[capture,legacy]"` desde la raíz del repositorio; esa sí
instala el ejecutable y los imports completos. Conviene que el owner corrija el comando en
`AGENTS.md`/`README.md` en una PR aparte (fuera del alcance de la 043).

### Imagen Docker (SC-004)

```text
$ DOCKER_CONFIG=/tmp/docker-config-043 BUILDX_CONFIG=/tmp/buildx-043 \
    docker build -t carvajal-api:043 .
#24 naming to docker.io/library/carvajal-api:043 done   → exit 0

$ docker run -d --name carvajal-api-043 -p 18043:8000 carvajal-api:043
$ curl -s -w "%{http_code}" http://127.0.0.1:18043/live
http_code=200
{"status":"ok"}
```

El primer `docker build` falló por el entorno, no por el Dockerfile:
`failed to update builder last activity time: open /home/alejo/.docker/buildx/activity/...:
read-only file system`. Con `DOCKER_CONFIG`/`BUILDX_CONFIG` en un directorio escribible el
build termina y la imagen responde en `/live`. No se modificó el Dockerfile.

### Decisión final sobre las dependencias

- **`langchain` (paquete paraguas, extra `legacy`)**: **retirado**. Ningún módulo lo importa,
  el lock ya no lo resuelve y `uv sync --all-extras`, el import de `carvajal.rag`, la CLI y
  la herramienta instalada funcionan. Las integraciones sueltas (`langchain-community`,
  `langchain-anthropic`, `langchain-ollama`, `langchain-openai`, `langchain-text-splitters`)
  siguen declaradas.
- **`python-dotenv` (base)**: **retirado**. Sigue instalado como dependencia de
  `pydantic-settings` (base) y `testcontainers` (desarrollo); `Settings` lee `.env` y la
  prueba de caracterización `test_la_configuracion_lee_el_archivo_dotenv` lo demuestra.

### Consumidores adicionales encontrados al pasar `ty`

- `apps/api/chat/service.py`: constructor de `ChatService` sin embedder.
- `scripts/evidencia_monitoreo_038.py`: construía `ChatService` con un `Embedder` falso y
  `ty` lo detectó; se retiró el doble (commit `fd83c77`). Es el único consumidor fuera de
  `tests/` que apareció al validar, tal como pedía la spec («revisa consumidores antes de
  quitar símbolos públicos»).
