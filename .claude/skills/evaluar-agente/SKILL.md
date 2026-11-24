---
name: evaluar-agente
description: Evalúa el asistente de Carvajal (enrutador, retrieval, cotizador, prompts y el agente con herramientas routes frente a tools) y compara contra la línea base antes de abrir una PR. Úsala cuando un cambio toque packages/core/src/carvajal/agent/ (incluido agent/toolagent/), prompts.py, application/ask.py, application/retrieval.py, adapters/vector/, adapters/llm/, quotation/, catalog/, commercial/, apps/api/chat/agent.py o data/eval/, cuando se proponga cambiar AGENT_MODE, o cuando pidan "evaluar", "medir", "comparar contra main" o "¿empeoró el agente?".
---

# Evaluar el agente de Carvajal

Objetivo: saber, con números, si un cambio mejora o empeora el asistente. Las
evaluaciones separan **retrieval** (sin LLM), **enrutamiento** y **cotización**
(deterministas); la calidad de la respuesta se mide aparte con LangSmith.

## 1. Decide qué evaluar según el diff

```bash
git diff --name-only main...HEAD
```

| Si cambió… | Corre |
|---|---|
| `agent/router.py`, `agent/commercial_intent.py`, `agent/routers/`, `data/eval/routing_questions.json` | Enrutador |
| `application/ask.py`, `application/retrieval.py`, `adapters/vector/`, `indexing/`, `domain/chunking*`, `data/gold/` | Retrieval |
| `quotation/`, `catalog/`, `commercial/`, `data/catalog/`, `data/eval/cotizacion_cases.json` | Cotizador |
| `prompts.py`, `adapters/llm/`, `agent/graph.py` | Enrutador + experimento LangSmith (si el owner lo autoriza) |
| `agent/toolagent/`, `apps/api/chat/agent.py`, `apps/api/agent_runtime.py`, `AGENT_MODE`, `data/eval/generalization_questions.json` | Las tres locales + `eval-agent` (con permiso, §3) |

Ante la duda, corre las tres evaluaciones locales. `eval-agent` no es local: gasta
tokens de DeepSeek y solo se corre con permiso.

## 2. Evaluaciones locales (sin red externa)

Escribe siempre los informes en un directorio temporal: los scripts tienen
salidas por defecto en `docs/` y **no se sobrescriben los informes versionados**
salvo que el owner lo pida.

```bash
OUT=$(mktemp -d)

# Enrutador por reglas: instantáneo, sin LLM.
uv run carvajal eval-router --output "$OUT/router.md"

# Cotizador: casos dorados con totales esperados; sale con código 1 si falla.
make eval-cotizador

# Retrieval: requiere Qdrant y Ollama arriba (make qdrant; ollama serve).
# Si el cambio afecta el índice, reindexa antes: uv run carvajal index-qdrant --layer all
uv run python scripts/eval_retrieval.py --k 10 \
  --output "$OUT/retrieval.md" --json "$OUT/retrieval.json"
```

Si Qdrant u Ollama no responden, **no inventes resultados**: informa qué
evaluación no se pudo correr y por qué.

## 3. Evaluaciones con costo o salida a la red (pide permiso)

Estas llaman a un proveedor externo o publican en LangSmith. Confirma con el
usuario antes de correrlas:

```bash
# Enrutador LLM frente a reglas (gasta tokens del proveedor configurado).
LLM_PROVIDER=deepseek uv run carvajal eval-router --router rules --router llm --output "$OUT/router-llm.md"

# Experimentos en LangSmith (requiere LANGSMITH_API_KEY).
uv run carvajal eval-router --router rules --router llm --langsmith
uv run python scripts/eval_retrieval.py --langsmith --output "$OUT/retrieval.md" --json "$OUT/retrieval.json"
```

### Agente `routes` frente a `tools` (spec 044)

`carvajal eval-agent` corre turnos completos en proceso (persistencia en memoria, sin
Postgres) con los dos modos sobre el mismo índice, embedder y catálogo. Necesita Qdrant y
Ollama arriba y `DEEPSEEK_API_KEY`: **cada pregunta gasta tokens de DeepSeek** en ambos
modos (el `routes` redacta con el proveedor de texto; el `tools` decide y redacta). Pide
permiso al owner antes de correrlo y dile cuántos ítems vas a evaluar.

Córrelo cuando el cambio toque el agente de herramientas (`agent/toolagent/`), sus
prompts, el runtime o el runner del chat, el conjunto de generalización, o antes de
proponer cambiar el valor por defecto de `AGENT_MODE`. Para iterar, usa corridas
parciales (`--limit`, `--ids`); la corrida completa queda para la evidencia.

```bash
# Corrida parcial para iterar (pocos tokens).
uv run carvajal eval-agent --mode tools --ids g-001,g-025,g-046 --output "$OUT/agent-parcial.md"

# Comparativa completa sobre el conjunto de generalización congelado (57 ítems × 2 modos).
uv run carvajal eval-agent --mode routes --mode tools \
  --questions data/eval/generalization_questions.json \
  --output "$OUT/agent.md" --json "$OUT/agent.json" \
  --price-input <USD/M entrada> --price-output <USD/M salida>

# Regresión del modo tools sobre el conjunto de enrutamiento.
uv run carvajal eval-agent --mode tools --questions data/eval/routing_questions.json \
  --output "$OUT/agent-routing.md"

# Lo mismo con make: escribe en un temporal salvo que ARGS traiga --output/--json.
make eval-agent ARGS="--limit 5"
```

- Sin `--price-input`/`--price-output` el informe no calcula costo (pon los precios
  vigentes de DeepSeek; no los inventes).
- `--langsmith` registra un experimento por modo (red externa, también con permiso) en
  un dataset propio (`…-agent`), separado del de `eval-router`.
- No modifiques `data/eval/generalization_questions.json`: está congelado; una corrección
  crea `version + 1` con el visto bueno del owner. El CLI se niega a escribir `--output` o
  `--json` sobre el conjunto, sobre el mismo archivo o sobre un archivo versionado (este
  último solo con `--force`). Un `--ids` vacío falla en vez de correr todo el conjunto.
- Salida: `0` si la corrida terminó; `1` si no pudo empezar o si un modo no obtuvo ninguna
  respuesta del proveedor (todo en error o en respaldo por `provider`/`timeout`: revisa
  la clave y la red, el informe lo avisa arriba); `130` si la interrumpes (Ctrl+C), con lo
  ya corrido guardado en `--output`/`--json`.
- Al leer el informe: un turno por respaldo no cuenta como resuelto ni como compuesta
  completa, y un ítem con error cuenta como latencia `∞` en los p95.

### Jev (TypeSafe): gatekeeper y compuerta de afirmaciones (spec 046)

**Jev está apagado por defecto en toda evaluación local**: `eval-agent` no construye el
juez aunque `TYPESAFE_API_KEY` esté en el `.env`, y las pruebas fijan `JEV_ENABLED=false`
y bloquean la red a `api.typesafe.ai`. Cada llamada gasta créditos de TypeSafe y la
medición completa tiene un presupuesto de **≤ 120 llamadas** (research R11 de la spec 046).
Solo con permiso explícito del owner, y dentro de ese presupuesto:

```bash
LEDGER=specs/046-jev-router/evaluacion/jev-calls.jsonl   # libro sin texto, una línea por llamada

# Gatekeeper: reglas (0 llamadas), LLM (DeepSeek) y Jev (Choice + Score), con humo.
uv run carvajal eval-gatekeeper --smoke --judge rules --judge llm --judge jev \
  --ledger "$LEDGER" --max-jev-calls 21 --output "$OUT/gatekeeper.md"

# Compuerta de afirmaciones sobre data/eval/claims_046.json (16 llamadas).
uv run carvajal eval-claims --ledger "$LEDGER" --max-jev-calls 16 --output "$OUT/claims.md"

# Modo tools con Jev, como producción, con cuotas separadas.
uv run carvajal eval-agent --mode tools --jev-ledger "$LEDGER" \
  --max-jev-router-calls 45 --max-jev-claims-calls 30 --output "$OUT/agent-jev.md"

# Sin créditos de Jev: reproduce los veredictos grabados y corre el agente (solo DeepSeek)
# sobre los ítems que terminaron en cautela o en respuesta directa.
uv run carvajal eval-gatekeeper --replay-ledger "$LEDGER" --agent --only cautious,direct \
  --output "$OUT/gatekeeper-replay.md"
```

- `eval-gatekeeper --judge jev`, `--smoke` y `eval-claims` se niegan a correr sin
  `--ledger`; `eval-agent` solo usa Jev con `--jev-ledger`. Sin esas banderas: 0 llamadas.
- `--max-jev-calls` y las cuotas de `eval-agent` cortan la corrida antes del tope diario;
  los ítems o turnos sin llamada se declaran (`cuota`) y no cuentan en los denominadores.
- `wc -l "$LEDGER"` es el gasto: compáralo con el presupuesto antes y después.
- La sección «Gatekeeper y compuerta» de `eval-agent` distingue el atajo por reglas de la
  respuesta directa de Jev: una pregunta con datos enviada a la directa es un error del
  gatekeeper.

## 4. Compara contra la línea base

La línea base es `main`. Usa primero los informes versionados:

- Enrutador: `docs/resultados-router.md`
- Retrieval: `docs/eval/retrieval-report.md` (Recall@10 = 0.80, nDCG@10 = 0.78 a 2026-11-17)

Si el informe versionado está desactualizado o el cambio toca el corpus o el
índice, mide `main` en un worktree aislado con las mismas órdenes:

```bash
git worktree add "$OUT/base" main
(cd "$OUT/base" && uv run carvajal eval-router --output "$OUT/router-base.md")
git worktree remove "$OUT/base"
```

## 5. Criterios de regresión

Bloquean la PR (repórtalos como fallo):

- Cualquier **violación de material** distinta de 0 en el enrutador.
- Cualquier caso del cotizador en `FALLO`.
- Baja de **Recall@10 o nDCG@10 mayor de 0.03** frente a la línea base.
- Una consulta de abstención (p. ej. `q31`) que deje de abstenerse.

Advierten (repórtalos, no bloquean):

- Baja de exactitud de ruta, intención o datos del enrutador.
- Subida de la latencia p95 del enrutador LLM mayor del 20 %.

### Criterios del agente con herramientas (spec 044, SC-001…SC-008)

Con el informe de `eval-agent` (fila → criterio). Como **regresión**, bloquean una PR que
toque el modo `tools`:

- **SC-001**: «Violaciones de material / datos sin fuente» distinto de `0 / 0` en `tools`
  (cifras, precios, existencias o plazos sin resultado de herramienta que los respalde).
- **SC-002**: algún caso dorado del cotizador en `FALLO` o baja de Recall@10 / nDCG@10
  mayor de 0.03 (evaluaciones locales de §2).
- **SC-005**: «Abstenciones correctas» por debajo del 100 %.
- **SC-007**: un saludo o una preferencia que no vaya por el atajo (fallo `shortcut`), o
  una caída del proveedor sin respuesta de respaldo.

Advierten en una PR y, además, son la **aprobación del cambio de `AGENT_MODE`**: el owner
solo pasa el valor por defecto a `tools` si el informe completo sobre el conjunto de
generalización cumple todos a la vez (más los bloqueantes de arriba), salvo que apruebe
con riesgos aceptados y los deje escritos en la evidencia de la spec. Excepción vigente:
el 2026-11-23 el owner pasó `AGENT_MODE` a `tools` con SC-001, SC-004, SC-005 y SC-006a
fuera de meta como riesgos aceptados (`specs/044-agente-tool-calling/evidencia.md` §10).
Los criterios de aprobación son:

- **SC-003**: «Ítems resueltos» de `tools` ≥ 85 % y al menos 15 puntos por encima de
  `routes`.
- **SC-004**: «Compuestas con todas las partes» ≥ 90 % en `tools`.
- **SC-006**: primer texto p95 < 5 s y fin p95 < 20 s en `tools`. **SC-006a**: primera
  línea de estado p95 < 1 s (el informe la trae; la medición en el chat real va a la
  evidencia aparte).
- **SC-008**: «Costo medio por turno» reportado (con `--price-input`/`--price-output`) y no
  mayor del triple del de `routes`.

El informe cierra con la limitación de origen del conjunto (FR-016): cítala siempre que
presentes los resultados. La decisión de cambiar `AGENT_MODE` es del owner, no del agente.

**Ojo con el sobreajuste.** El enrutador por reglas da 100 % porque sus reglas se
escribieron contra `routing_questions.json`. Una mejora que solo sube esa cifra
no demuestra generalización: si añades reglas, añade también preguntas nuevas
que no hayan servido para escribirlas y repórtalas por separado.

## 6. Informe

Entrega una tabla corta con métrica, base, cambio y delta, más los fallos
concretos (ID de la pregunta y causa). Si el resultado acompaña una PR, pega la
tabla en la sección de pruebas del cuerpo. Actualiza `docs/eval/` o
`docs/resultados-router.md` solo si el owner lo pide o si la PR cambia la línea
base a propósito.
