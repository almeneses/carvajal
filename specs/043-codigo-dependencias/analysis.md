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

## Resultado de la implementación

_(se completa en T019)_
