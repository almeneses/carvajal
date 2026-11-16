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

### Corrida 1 — 2026-11-16, DeepSeek (`deepseek-chat`)

| Enrutador | Preguntas | Ruta | Intención | Datos | Violaciones de material | Salidas inválidas | Latencia media (ms) | Latencia p95 (ms) | Llamadas al modelo |
|---|---|---|---|---|---|---|---|---|---|
| rules | 110 | 100.0% | 100.0% | 100.0% | 0 | 0 | 0.4 | 0.7 | 0 |
| llm | 110 | 99.1% | 98.2% | 95.0% | 3 | 4 | 1316.7 | 1637.7 | 110 |
| decision | — | pendiente | | | | | | | |

Costo del LLM: unos 735 tokens de entrada y 100 de salida por decisión (logs `llm.completed`),
es decir, unos 81 000 de entrada y 11 000 de salida para las 110 preguntas.

Fallos del LLM (8) y su causa:

| Pregunta | Fallo | Causa |
|---|---|---|
| `c-09` sopa para calentar en microondas | `microwave` perdido; EPS recomendado | **Error del adaptador** |
| `c-10` envases para salsas aptos para microondas | `microwave` perdido; 2 referencias no aptas | **Error del adaptador** |
| `c-03`, `c-19`, `c-21` (papel, compostables, sin icopor) | `prefers_renewable` perdido | **Error del adaptador** |
| `n-05` «Mi nombre es Laura» | `greeting` en vez de `memory_write` | Error del LLM: el prompt no lista «mi nombre es» como preferencia |
| `c-12` «contenedores para almuerzos» | pide aclaración en vez de recomendar | Etiqueta discutible: «almuerzo» no dice si es caliente |
| `c-14` «bandejas para frutas» | temperatura `ambiente` en vez de `fria` | Etiqueta discutible: la fruta entera puede ir a temperatura ambiente |

**Error del adaptador.** El esquema que recibía el LLM mostraba los booleanos entre comillas
(`"microwave": "true | null"`) y el modelo respondió el texto `"true"`, que la validación
descartaba. Las 3 violaciones de material vienen de ahí: sin «microondas», la secuencia
comercial no excluyó el EPS. Corregido en esta misma PR: el esquema muestra `true | null` sin
comillas y la validación acepta el texto `"true"`. Las 4 salidas inválidas no aparecían en la
lista porque el respaldo a reglas acertaba; desde esta corrección se listan también.

### Corrida 2 — pendiente

Con el adaptador corregido y el mismo prompt, para aislar el efecto de la corrección:

```bash
uv run carvajal eval-router --router rules --router llm --output docs/eval/router.md
```

### Lectura

- **Reglas.** El 100 % no mide generalización: el conjunto se escribió junto con las reglas.
  Lo que sí demuestra es la no regresión (las 49 preguntas previas conservan la ruta del router
  de `dev`) y que ninguna recomendación viola la regla de material. Es el piso de latencia y
  costo (sin llamadas al modelo).
- **LLM.** Sin el error del adaptador, sus fallos se reducen a uno claro (`n-05`) y dos casos
  donde la etiqueta del conjunto es discutible. Cuesta unos 1,3 s y ~835 tokens por turno
  frente a menos de 1 ms de las reglas. La regla de material nunca depende del enrutador: la
  aplica `check_material`, pero un dato mal extraído (microondas, temperatura) sí cambia qué se
  valida, y por eso se mide.
- **Modelo de decisión.** Pendiente de elegir el paquete (Jev, GLiNER2.5-Decide u otro). El
  adaptador lo carga con `DECISION_MODEL=paquete.modulo:fabrica`; sin él, `eval-router`
  termina con un error controlado y el agente sigue con reglas.

Para comparar la generalización hace falta un grupo de unas 30 preguntas redactadas por alguien
que no haya visto las reglas (grupo `ciego` en `routing_questions.json`).
