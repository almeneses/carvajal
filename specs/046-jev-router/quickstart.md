# Quickstart — Validar la spec 046

## Requisitos

- La implementación de la 045 en la rama (registro de 20 herramientas, `sensitive.py`,
  memoria por categorías). La 046 se ejecuta sobre ella.
- `make dev`, Postgres y Qdrant arriba (`make up` o `make db qdrant`) y Ollama con
  `nomic-embed-text` para las secciones 2 a 4.
- `LLM_PROVIDER=deepseek` y `DEEPSEEK_API_KEY` en `.env` para las secciones 2 a 4.
- `TYPESAFE_API_KEY` en `.env` solo para las secciones 2 y 4. **Cada llamada gasta créditos**:
  el presupuesto total de la medición es 120 llamadas (research R11) y se lleva en
  `specs/046-jev-router/evaluacion/jev-calls.jsonl`. Las secciones 1 y 3 no llaman a Jev.
- Jev está **apagado por defecto** aunque el `.env` traiga la clave (`JEV_ENABLED=false`):
  `make api`, `carvajal eval-agent` y `carvajal eval-router` no lo llaman salvo opt-in
  explícito (`JEV_ENABLED=true`, o `--jev-ledger` en `eval-agent`). Con `JEV_LEDGER` definido,
  toda llamada queda en el libro. La skill `evaluar-agente` sigue sin gastar créditos de Jev.
- `make check` nunca llama a Jev: la fixture de `tests/conftest.py` lo apaga aunque el
  `.env` traiga la clave.

## 1. Sin red (CI)

```bash
make check
uv run pytest tests/test_jev_adapter.py tests/test_gatekeeper_rules.py \
  tests/test_gatekeeper_decide.py tests/test_gatekeeper_privacy.py \
  tests/test_claims_gate.py tests/test_tool_agent_gatekeeper.py \
  tests/test_eval_gatekeeper.py -q
uv run pytest tests/test_tool_agent_stream.py tests/test_chat_service_tools.py \
  tests/test_api_health.py tests/test_monitoring_tools.py tests/test_mcp_whatsapp.py -q
make eval-cotizador          # 18/18
```

Esperado: todo verde. Las pruebas cubren las cuatro clases y sus alcances, el atajo sin
Jev, la cautela, el desacuerdo con las reglas, la compuerta (sustitución, retención,
casos que no se juzgan), los ocho estados de fallo de Jev, el plazo, el tope, el circuito,
las cargas sin datos personales y la ausencia de texto en logs y trazas.

## 2. Demostración en vivo con Jev (opcional; ≤ 6 llamadas, presupuestadas en research R11)

Solo si el owner la quiere ver antes de producción; si no, esas 6 llamadas no se gastan. Se
hace una vez, después de la sección 4 y anotada en el mismo libro:

```bash
JEV_ENABLED=true JEV_LEDGER=specs/046-jev-router/evaluacion/jev-calls.jsonl make api
```

En `/chat?debug=1`, un hilo nuevo. El panel de depuración muestra «Decisión: …» y el
evento `trace` trae `gatekeeper` y `claims`. No repetir: el libro debe quedar en ≤ 118
líneas tras esta sección.

| # | Mensaje | Esperado en la traza | Llamadas |
|---|---|---|---|
| 1 | «Hola» | `scope=direct`, `source=reglas`, `rule=greeting`, sin herramientas, `claims.status=skipped` | 0 |
| 2 | «¿Qué es Propal y qué relación tiene con Carvajal?» | `gate_class=rag`, `scope=rag`, `source=jev`; solo consultas documentales | 2 |
| 3 | «Cotízame 500 vasos de 9 oz para tinto en Cali» | `herramienta_estructurada`, `scope=structured`; `cotizar` | 1–2 |
| 4 | «Mejor que sean 1000» | `rule=commercial_follow_up`, `scope=structured`, sin llamada al gatekeeper | 0–1 |
| 5 | «¿y eso?» (en un hilo nuevo) | confianza baja → `cautious=true`, `scope=all`; pide aclaración | 1 |

Comprobar también: con «Hola», ninguna línea de estado de herramientas; en el turno 2, la
línea «Reviso que lo que te digo tenga fuente…» antes del texto y el texto completo de una
vez.

## 3. Sin Jev (comportamiento de hoy)

```bash
make api        # Jev apagado por defecto (JEV_ENABLED=false)
```

Repetir los mensajes 1 a 3: `gatekeeper.source=llm` (salvo el saludo, `reglas`),
`scope=all`, `claims.status=off`, el texto se transmite con la retención de 48 caracteres y
las respuestas son las de la 045. `/health` muestra `"jev": {"configured": true, "enabled":
false, "available": false, …}`.

Fallos simulados (no llegan a TypeSafe: no gastan créditos):

```bash
JEV_ENABLED=true JEV_BASE_URL=http://127.0.0.1:9 make api   # conexión rechazada → error, circuito tras 3
JEV_ENABLED=true JEV_TIMEOUT_S=0.2 JEV_BASE_URL=http://10.255.255.1 make api   # plazo vencido
```

Esperado: el turno responde como en la sección 3, sin esperar más que el plazo; los logs
muestran `jev.call` con `status=error|timeout` y, al tercer fallo, `jev.circuit_open`; sin
texto de la pregunta en ningún log.

## 4. Medición (una corrida de cada una; ≤ 120 llamadas en total)

```bash
mkdir -p specs/046-jev-router/evaluacion
LEDGER=specs/046-jev-router/evaluacion/jev-calls.jsonl

# 4.1 Humo + gatekeeper: 1 + 20 llamadas (Choice + Score en la misma petición)
uv run carvajal eval-gatekeeper --smoke --judge rules --judge llm --judge jev \
  --questions data/eval/gatekeeper_046.json --ledger "$LEDGER" --max-jev-calls 21 \
  --output specs/046-jev-router/evaluacion/gatekeeper.md \
  --json specs/046-jev-router/evaluacion/gatekeeper.json
uv run carvajal eval-gatekeeper --judge rules \
  --questions data/eval/routing_questions.json \
  --output specs/046-jev-router/evaluacion/gatekeeper-reglas-130.md    # 0 llamadas

# 4.2 Compuerta: 16 llamadas
uv run carvajal eval-claims --questions data/eval/claims_046.json \
  --ledger "$LEDGER" --max-jev-calls 16 \
  --output specs/046-jev-router/evaluacion/claims.md \
  --json specs/046-jev-router/evaluacion/claims.json

# 4.3 v3 en modo tools sin Jev (línea base del mismo día): 0 llamadas (sin --jev-ledger)
uv run carvajal eval-agent --mode tools \
  --questions data/eval/generalization_v3.json \
  --output specs/046-jev-router/evaluacion/v3-sin-jev.md \
  --json specs/046-jev-router/evaluacion/v3-sin-jev.json

# 4.4 v3 en modo tools con Jev: ≤ 75 llamadas (cuotas de la corrida: 45 + 30)
uv run carvajal eval-agent --mode tools \
  --questions data/eval/generalization_v3.json --jev-ledger "$LEDGER" \
  --max-jev-router-calls 45 --max-jev-claims-calls 30 \
  --output specs/046-jev-router/evaluacion/v3-con-jev.md \
  --json specs/046-jev-router/evaluacion/v3-con-jev.json

# 4.5 Cautela y respuesta directa sin créditos de Jev: reproduce los veredictos grabados en
#     4.1 (ReplayJudge) y corre el agente real (solo DeepSeek) sobre esos ítems
uv run carvajal eval-gatekeeper --replay-ledger "$LEDGER" --agent \
  --questions data/eval/gatekeeper_046.json --only cautious,direct \
  --output specs/046-jev-router/evaluacion/gatekeeper-replay.md

wc -l "$LEDGER"     # ≤ 112 tras 4.1–4.5; ≤ 118 tras la demostración opcional (§2)
```

Tras 4.1, fijar los umbrales con la regla de research R4 y registrarlos en la evidencia (si
cambian, también en `settings.py` y `.env.example`) **antes** de 4.4. Tras 4.4, auditar cada
turno con `claims.substituted=true` y cada turno con cautela, y escribir
`specs/046-jev-router/evidencia.md` con la tabla de SC-001 a SC-010.

Esperado: SC-001 (≥ 16/20; ≥ 90 % de las aplicadas; 0 preguntas con datos a respuesta
directa), SC-003 (≥ 9/10 detectadas; ≤ 1/6 falsas alarmas), SC-004 (con Jev ≥ sin Jev − 1;
violaciones y datos sin fuente sin aumentar), SC-006 (p95 por llamada ≤ 0,5 s; fin del
turno ≤ sin Jev + 1,0 s) y SC-008 (libro ≤ 120 líneas).

## 5. Producción

1. Antes de activar: revisar el DPA de TypeSafe y su plazo de retención (T063, resultado en
   `docs/research/jev.md`); si no se completa, la PR pide al owner la aceptación explícita
   del riesgo.
2. En el `.env` del servidor (fuera del repositorio; acceso autorizado por el owner):
   `JEV_ENABLED=true`, `TYPESAFE_API_KEY=…`, `JEV_MODEL=jev-1.14.0` y los umbrales finales si difieren de los
   valores por defecto. `compose.prod.yml` pasa las variables al contenedor. Mientras el
   código de la 046 no esté desplegado, el servidor las ignora.
3. Tras el merge y el despliegue (los decide el owner): `curl -s <host>/health | jq .jev` →
   `configured: true`, `enabled: true`, `available: true`, `model: "jev-1.14.0"`; y
   `agent.mode_configured: "tools"`.
4. Un turno real en `/chat?debug=1` con una pregunta documental (2 llamadas, anotadas en el
   libro): la traza muestra `source=jev` y `claims.status` distinto de `off`.
