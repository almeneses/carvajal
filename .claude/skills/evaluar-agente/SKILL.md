---
name: evaluar-agente
description: Evalúa el asistente de Carvajal (enrutador, retrieval, cotizador y prompts) y compara contra la línea base antes de abrir una PR. Úsala cuando un cambio toque packages/core/src/carvajal/agent/, prompts.py, application/ask.py, application/retrieval.py, adapters/vector/, adapters/llm/, quotation/, catalog/, commercial/ o data/eval/, o cuando pidan "evaluar", "medir", "comparar contra main" o "¿empeoró el agente?".
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

Ante la duda, corre las tres evaluaciones locales.

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
