# Resultados de la comparación de enrutadores (spec 036, T041)

Comparación del enrutador por reglas (`decide_route`), el enrutador LLM (`LLMRouter`) y el
adaptador de modelo de decisión (`DecisionRouter`, para Jev u otro modelo «System One») sobre
el conjunto versionado `data/eval/routing_questions.json` (110 preguntas: 49 previas a la 036
y 61 comerciales). Los tres cumplen el mismo contrato (`RouteDecision`), así que el grafo los
usa sin cambios (FR-013).

## Cómo se reproduce

```bash
uv run carvajal eval-router                                  # reglas
LLM_PROVIDER=deepseek uv run carvajal eval-router --router rules --router llm
DECISION_MODEL=paquete.modulo:fabrica uv run carvajal eval-router --router decision
uv run carvajal eval-router --router rules --router llm --output docs/eval/router.md
```

Métricas:

- **Ruta** e **intención**: aciertos sobre las 110 preguntas.
- **Datos**: aciertos por clave esperada en `RouteDecision.extra` (uso, temperatura,
  capacidad, cantidad, país, ciudad…).
- **Violaciones de material**: opciones que la secuencia comercial real recomendaría con un
  material de `constraints.forbidden_materials`. Debe ser 0 con cualquier enrutador, porque la
  regla la aplica `check_material` y no el enrutador; si no lo es, el enrutador extrajo mal la
  temperatura o el uso.
- **Salidas inválidas**: respuestas del modelo que no cumplen el contrato; el turno cae al
  enrutador de reglas y la razón empieza por «salida inválida del enrutador».
- **Latencia** de la decisión y **llamadas al modelo**, base del costo: el consumo de tokens
  de cada llamada queda en los logs del proveedor (`llm.usage`).

## Resultados

Corrida del 2026-11-16, rama `feat/036-pr7-enrutadores`:

| Enrutador | Preguntas | Ruta | Intención | Datos | Violaciones de material | Salidas inválidas | Latencia media (ms) | Latencia p95 (ms) | Llamadas al modelo |
|---|---|---|---|---|---|---|---|---|---|
| rules | 110 | 100.0% | 100.0% | 100.0% | 0 | 0 | 0.3 | 0.5 | 0 |
| llm | — | pendiente | | | | | | | |
| decision | — | pendiente | | | | | | | |

### Lectura

- **Reglas.** El 100 % no mide generalización: el conjunto se escribió junto con las reglas.
  Lo que sí demuestra es la no regresión (las 49 preguntas previas conservan la ruta del router
  de `dev`) y que ninguna recomendación viola la regla de material. Es el piso de latencia y
  costo (sin llamadas al modelo).
- **LLM.** Pendiente de una corrida con red hacia el proveedor: el entorno donde se preparó
  esta PR no alcanza `api.deepseek.com`. Las pruebas `tests/test_agent_routers.py` verifican
  con un LLM falso la validación del contrato y el respaldo a reglas.
- **Modelo de decisión.** Pendiente de elegir el paquete (Jev, GLiNER2.5-Decide u otro). El
  adaptador lo carga con `DECISION_MODEL=paquete.modulo:fabrica`; sin él, `eval-router`
  termina con un error controlado y el agente sigue con reglas.

Para ver qué tan bien generaliza cada enrutador hace falta una segunda tanda de preguntas
redactadas por otra persona, sin mirar las reglas.
