# Quickstart — Validar la spec 044

## Requisitos

- `make dev`, Postgres y Qdrant arriba (`make up` o `make db qdrant`), Ollama con
  `nomic-embed-text` para los embeddings.
- `DEEPSEEK_API_KEY` en `.env` (las pruebas de `make check` no la necesitan).
- `data/eval/generalization_questions.json` congelado (F0).

## 1. Sin red (CI)

```bash
make check
uv run pytest tests/test_tool_agent_graph.py tests/test_tool_agent_tools.py \
  tests/test_tool_agent_stream.py tests/test_quotation_request.py -q
make eval-cotizador          # 12/12 con quote() y con quote_request()
```

Esperado: todo verde; el chat falso cubre bucle, límites, deduplicación, compuerta de
evidencia, atajo, respaldo, varios folios y replay.

## 2. Chat en vivo con el modo nuevo

```bash
AGENT_MODE=tools make api
```

En `/chat`, en un hilo nuevo:

1. «hola» → respuesta sin línea de estado y `trace.path = shortcut` (con `?debug=1`).
2. «necesito unos 2 mil vasitos para tinto en Bogotá, ¿cuánto me salen y dónde los compro?»
   → líneas de estado amigables, tarjeta de cotización con folio, sedes y citas.
3. «mejor en Cali» → nueva cotización que conserva producto y cantidad y lo dice.
4. «¿cuál es el teléfono de Carvajal en Tokio?» → abstención declarada.
5. «vasos de icopor para café caliente» → explica por qué no aplica y no lo recomienda.
6. Repetir el envío 2 con el mismo `client_turn_id` (recargar durante el envío) → misma
   respuesta y mismos folios.
7. Con `DEEPSEEK_BASE_URL` apuntando a un puerto cerrado → respuesta de respaldo con el
   aviso fijo y `trace.path = fallback`.

## 3. Evaluación comparativa

Usa la skill `evaluar-agente` y además:

```bash
OUT=$(mktemp -d)
uv run carvajal eval-agent --mode routes --mode tools \
  --questions data/eval/generalization_questions.json --output "$OUT/agent.md"
```

Esperado para aprobar el cambio de valor por defecto: SC-001…SC-008 en verde en el
informe; sin ello, `AGENT_MODE` sigue en `routes`. El 2026-11-23 el owner aprobó el cambio
con riesgos aceptados: `tools` es el valor por defecto ([evidencia §10](./evidencia.md)).
