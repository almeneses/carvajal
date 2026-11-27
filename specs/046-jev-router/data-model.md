# Data Model — Spec 046

Sin migraciones de base de datos ni tablas nuevas. Los cambios son: un canal nuevo en el
estado del grafo `tools` (`gatekeeper`, se reinicia en cada turno), un campo nuevo en
`ToolSpec` (`route_group`), los tipos del puerto de Jev, dos campos nuevos en
`ToolAgentFinal`, los ajustes `JEV_*`, dos conjuntos de evaluación congelados y el libro de
llamadas de la medición. Ningún texto enviado a Jev se persiste en ningún lugar.

## Tipos comunes (`agent/gatekeeper/model.py`)

`GateClass`, `GateScope` y `JevStatus` se definen en `application/ports.py` (sin
dependencias; la capa de aplicación no importa de `agent/`) y `agent/gatekeeper/model.py` los
reexporta junto con los demás. `agent/gatekeeper/__init__.py` queda vacío y
`toolagent/tools/registry.py` importa `GateScope` solo de `ports`, para que no haya ciclo
`registry → gatekeeper → toolagent` (research R1).

```python
GateClass = Literal["herramienta_estructurada", "rag", "mixta", "respuesta_directa"]
GateScope = Literal["direct", "rag", "structured", "all"]
GateSource = Literal["reglas", "jev", "llm"]
JevStatus = Literal[
    "ok", "off", "timeout", "error", "cap", "circuit_open", "auth", "invalid", "skipped"
]
ClaimsStatus = Literal["supported", "unsupported", "skipped", "unavailable", "off"]
```

Mapa clase → alcance (cuando la clase se aplica sin cautela):

| Clase | Alcance |
|---|---|
| `herramienta_estructurada` | `structured` |
| `rag` | `rag` |
| `mixta` | `all` |
| `respuesta_directa` | `direct` |

## GatekeeperDecision (canal `gatekeeper` de `ToolAgentState`)

Lo escribe el nodo `shortcut`; `recall` lo reinicia a `{}`; lo leen `after_shortcut`,
`agent`, `tools`, el streaming (`_TurnStream`) y el servicio (traza). Valor JSON.

| Campo | Tipo | Regla |
|---|---|---|
| `v` | `int` | `1`. |
| `gate_class` | `GateClass \| None` | Clase decidida (por regla o por Jev); `None` si decidió el LLM o la regla de preferencia. |
| `scope` | `GateScope` | Alcance aplicado. `direct` solo con regla de conversación o con `respuesta_directa` sobre su umbral. |
| `source` | `GateSource` | `reglas` si decidió una regla; `jev` si se usó la respuesta de Jev (aplicada, con cautela o en sombra); `llm` si no hubo Jev. |
| `rule` | `str` | Regla que decidió: `greeting`, `farewell`, `memory_forget`, `memory_unavailable`, `memory_write`, `commercial_follow_up`; vacía si no decidió una regla. `rule_decision` (pura) nunca devuelve `memory_unavailable`: devuelve `memory_forget` y el nodo `shortcut` lo reescribe si el olvido falla. |
| `cautious` | `bool` | Cautela aplicada (alcance `all` + bloque `<cautela>`). |
| `reason` | `str` | Código corto: `aplicada`, `confianza_baja`, `confianza_baja_directa`, `desacuerdo_reglas`, `mixta_reglas`, `producto_reglas`, `sombra`, `sin_jev:<JevStatus>`. |
| `confidence` | `float \| None` | `confidence` de Choice (o la mayor probabilidad si falta), redondeada a 2 decimales. |
| `probabilities` | `dict[GateClass, float]` | De Choice; vacío sin Jev. |
| `rules_route` | `str` | Ruta e intención de `decide_route` (`"commercial/commercial"`), para la regla de desacuerdo y la evaluación. |
| `jev` | `JudgeCall \| None` | Metadatos de la llamada (abajo). |
| `llm_class` | `GateClass \| None` | Solo con `source=llm`: la añade el streaming al cerrar el turno a partir del `route_group` de las consultas ejecutadas (solo `rag` → `rag`; solo `structured` → `herramienta_estructurada`; ambas → `mixta`; solo `sin_consulta` o ninguna → `respuesta_directa`). `None` en el resto. |

**Reglas**:

- Orden de decisión (`decide_gate`): reglas de conversación (`greeting`, `farewell`,
  `memory_forget`, como hoy `shortcut_intent`; el nodo `shortcut` lo reescribe a
  `memory_unavailable` si el olvido falla) → `scope="direct"`,
  sin Jev. Preferencia declarada (`decide_route` con `memory_write`) → `scope="all"`,
  `source="reglas"`, sin Jev. Cuenta igual un dato para la memoria sin esas marcas
  (`declares_memory`: pedido de recordar en imperativo, «soy Ana»/«me llamo Ana»,
  «trabajo en…», «suelo comprar en…»): una presentación no puede ir al atajo
  `gatekeeper_direct` aunque Jev la dé por `respuesta_directa` (ensayo e2e, 2026-11-25). Seguimiento comercial (`_commercial_follow_up` no vacío) →
  `scope="structured"`, sin Jev. Si no, Jev (si está disponible y `JEV_ROUTER_MODE≠off`);
  si no, `scope="all"`, `source="llm"`.
- Con Jev: se aplica la clase si `confidence ≥ JEV_ROUTER_MIN_CONFIDENCE` y, para
  `respuesta_directa`, `confidence ≥ JEV_ROUTER_DIRECT_MIN_CONFIDENCE`; si no, cautela.
  Desacuerdo fuerte (reglas `commercial`/`tool`/`commercial_clarify`/`missing_period` y Jev
  `rag`/`respuesta_directa`) → cautela. Reglas `commercial_mixed` → `scope="all"` (motivo
  `mixta_reglas`). Jev `rag` aplicada con `commercial_signal` (producto, precio, cantidad,
  capacidad o dónde comprar) → `scope="all"`, sin cautela (motivo `producto_reglas`,
  research R16).
- `JEV_ROUTER_MODE=shadow`: se llama a Jev y se registra, pero `scope="all"`,
  `cautious=False`, `reason="sombra"`.
- `mode_path` sigue siendo `"shortcut"` para todo turno que va al nodo `direct` (regla o
  Jev) y `"agent"` para el resto; `source` distingue reglas de Jev. `shortcut_intent` vale la
  regla, o `gatekeeper_direct` cuando Jev decidió la respuesta directa.
- Evaluación: el ejecutor (`toolagent/evaluation.py`) no usa `mode_path` para distinguir los
  dos casos. La comprobación `shortcut`, `ITEM_PATHS`/`SHORTCUT_INTENTS` y las métricas del
  atajo de la 044 solo cuentan `source="reglas"`; una respuesta directa de Jev se informa
  aparte (`gatekeeper_direct`) y, si el ítem necesitaba datos, como error del gatekeeper.

## Grupo de cada consulta (`ToolSpec.route_group`, registro de la 045)

| Campo nuevo | Tipo | Regla |
|---|---|---|
| `route_group` | `Literal["rag", "structured", "any"]` | Obligatorio en las 20 `ToolSpec`. Solo `sin_consulta` es `any`. |

Vista derivada (de solo lectura) en `toolagent/tools/registry.py`:

```python
TOOLS_BY_SCOPE: Mapping[GateScope, tuple[ToolName, ...]]
# "all": las 20 en el orden de TOOLS
# "rag": las de route_group "rag" + "any", en el orden de TOOLS
# "structured": las de route_group "structured" + "any", en el orden de TOOLS
# "direct": ()
```

Asignación: research R3. Prueba: cada consulta tiene exactamente un grupo, `rag ∪
structured ∪ any` = las 20, y el orden de cada alcance respeta el de `TOOLS`.

## Puerto del juez (`application/ports.py`)

```python
@dataclass(frozen=True)
class JudgeCall:  # metadatos de una llamada; nunca texto
    kind: Literal["router", "claims"]
    status: JevStatus
    latency_ms: int
    model: str  # el que devolvió la API (p. ej. "jev-1.14.0"); "" si no hubo respuesta
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class RouteQuery:
    question: str  # ya pasó por outgoing_question
    context: str = ""  # una línea, ya redactada; vacía si no aplica


@dataclass(frozen=True)
class RouteVerdict:
    choice: GateClass
    confidence: float
    probabilities: Mapping[GateClass, float]
    clarity: float | None  # Score: valor esperado real en [0, 4]; solo en la medición (D1)
    clarity_confidence: float | None  # confidence de la respuesta Score; solo en la medición
    call: JudgeCall


@dataclass(frozen=True)
class ClaimsQuery:
    answer: str  # outgoing_answer: sin datos de la persona, con los del directorio
    evidence: tuple[tuple[str, str], ...]  # (consulta, texto), ya acotada; puede incluir
    # ("dicho_por_la_persona", valores estructurados)
    # sin la pregunta: no viaja a la compuerta (FR-018)


@dataclass(frozen=True)
class ClaimsVerdict:
    p_supported: float  # Noul: probabilidad de que todas las afirmaciones estén respaldadas
    call: JudgeCall


class JudgeUnavailable(Exception):
    status: JevStatus  # off, timeout, error, cap, circuit_open, auth, invalid
    call: JudgeCall | None


class RouteJudge(Protocol):
    def available(self) -> bool: ...
    def classify(self, query: RouteQuery, *, with_score: bool = False) -> RouteVerdict: ...


class ClaimsJudge(Protocol):
    def available(self) -> bool: ...
    def check(self, query: ClaimsQuery) -> ClaimsVerdict: ...
```

`classify` y `check` lanzan `JudgeUnavailable` ante cualquier fallo; nunca otra excepción
hacia el grafo o el streaming. `available()` no hace red: configurado, encendido, circuito
cerrado y bajo el tope.

## ClaimsVerdict en el turno (`ToolAgentFinal.claims`)

Valor JSON que el streaming añade al cierre y el servicio envía en la traza.

| Campo | Tipo | Regla |
|---|---|---|
| `status` | `ClaimsStatus` | `off` (compuerta apagada o juez no disponible al empezar), `skipped` (texto no juzgable: atajo, respuesta directa de Jev, abstención, reemplazo, solo memoria, solo preguntas, demasiado largo), `supported`, `unsupported`, `unavailable` (falló la llamada, o no se hizo por falta de plazo del turno o de cuota de la corrida). |
| `p` | `float \| None` | `p_supported`, 2 decimales. |
| `threshold` | `float` | `JEV_CLAIMS_THRESHOLD` vigente. |
| `reason` | `str` | Para `skipped`: `atajo`, `directa`, `abstencion`, `reemplazo`, `memoria`, `solo_preguntas`, `demasiado_larga`. Para `unavailable` sin llamada: `sin_plazo` (no quedaba plazo del turno) o `cuota` (cuota de la corrida agotada); con llamada fallida, el `JevStatus`. |
| `substituted` | `bool` | El texto del modelo se sustituyó por el neutro. |
| `jev` | `JudgeCall \| None` | Metadatos de la llamada. |

`ToolAgentFinal` gana `gatekeeper: dict` (copia del canal) y `claims: dict`.

## Ajustes (`infrastructure/settings.py`)

| Variable | Tipo y rango | Por defecto | Uso |
|---|---|---|---|
| `JEV_ENABLED` | `bool` | `false` | Interruptor general; se enciende de forma explícita (producción: `.env` del servidor; local: opt-in, FR-031) |
| `JEV_LEDGER` | `str` (ruta) | `""` | Si está definido, el cliente anota cada llamada (sin texto) en ese libro |
| `TYPESAFE_API_KEY` | `SecretStr` | `""` | Clave; solo del entorno |
| `JEV_BASE_URL` | `str` | `https://api.typesafe.ai/v1` | Base de la API |
| `JEV_MODEL` | `str` | `jev-1.14.0` | Modelo fijado |
| `JEV_TIMEOUT_S` | `float` 0,2–3,0 | `0.8` | Plazo total por llamada |
| `JEV_ROUTER_MODE` | `on \| shadow \| off` | `on` | Gatekeeper |
| `JEV_ROUTER_MIN_CONFIDENCE` | `float` 0–1 | `0.6` | Umbral general (R4) |
| `JEV_ROUTER_DIRECT_MIN_CONFIDENCE` | `float` 0–1 | `0.8` | Umbral de `respuesta_directa` |
| `JEV_CLAIMS_MODE` | `on \| off` | `on` | Compuerta de afirmaciones |
| `JEV_CLAIMS_THRESHOLD` | `float` 0–1 | `0.3` (`0.5` hasta el 2026-11-26) | No respaldada si `p < umbral` |
| `JEV_MAX_CALLS_PER_DAY` | `int` 0–100 000 | `1000` | Tope por proceso; `0` = sin llamadas |
| `JEV_MAX_EVIDENCE_CHARS` | `int` 1 000–40 000 | `12000` | Tope de la evidencia de la compuerta |

Validación: `JEV_ROUTER_DIRECT_MIN_CONFIDENCE ≥ JEV_ROUTER_MIN_CONFIDENCE`. Propiedad
derivada `jev_active = JEV_ENABLED and bool(TYPESAFE_API_KEY)`: la clave sola no enciende
Jev, porque el `.env` local del owner la trae y cada `make api` o `eval-agent` gastaría
créditos fuera del libro (research R9). `carvajal eval-agent` construye el juez solo con
`--jev-ledger`, con su propio contador por corrida y cuotas separadas
(`--max-jev-router-calls`, `--max-jev-claims-calls`) en lugar de `JEV_MAX_CALLS_PER_DAY`.

## Estado del cliente (`adapters/judge/jev.py`, en memoria del proceso)

| Campo | Regla |
|---|---|
| `calls_today`, `day` | Contador del día de Bogotá; al cambiar el día vuelve a 0. |
| `failures` | Fallos seguidos (plazo, red, 429, 5xx, 529, inválida); un `ok` lo reinicia. |
| `open_until` | Circuito abierto hasta: +60 s con 3 fallos seguidos; +15 min con 401/402/403. |
| `last_error` | Última categoría (`timeout`, `http_429`, `http_5xx`, `auth`, `invalid`…), sin detalle. |
| `last_model` | Modelo devuelto por la última respuesta correcta. |
| `ledger` | Ruta de `JEV_LEDGER` (o la que pase el comando de evaluación); si existe, cada llamada, también la fallida, añade una línea sin texto. |

## Bloque `jev` de `/health`

```json
{
  "configured": true,
  "enabled": true,
  "available": true,
  "model": "jev-1.14.0",
  "router": "on",
  "claims": "on",
  "calls_today": 37,
  "last_error": null
}
```

Sin runtime construido: `available = configured and enabled`, `calls_today = 0`.

## Conjuntos de evaluación (congelados antes de medir)

### `data/eval/gatekeeper_046.json`

```json
{
  "version": "2026-11-24",
  "frozen_at": "<fecha de congelación>",
  "origin": "15 preguntas de routing_questions.json (ids de origen) y 5 redactadas por el asistente de desarrollo",
  "description": "…",
  "items": [
    {"id": "gk-01", "question": "…", "context": "", "expected": "rag",
     "source": "routing_questions.json#r-q01", "rules_route": "rag/documental"}
  ]
}
```

| Campo | Regla |
|---|---|
| `expected` | `GateClass`. Mapa desde `routing_questions.json`: `rag/*` → `rag`; `commercial/commercial`, `commercial/commercial_custom`, `tool/indicator`, `direct/commercial_clarify`, `direct/missing_period` → `herramienta_estructurada`; `commercial/commercial_mixed` → `mixta` (salvo que su otra parte sea otro dato estructurado, entonces `herramienta_estructurada`: c-54, «vasos de café de 12 oz y los ingresos de Carvajal en 2024», es estructurada; las mixtas son c-52 y c-53). |
| `context` | Línea de contexto tal como la enviaría producción (pregunta anterior), o vacía. |
| Composición | 7 `herramienta_estructurada` (c-54 incluida), 6 `rag`, 2 `mixta` (c-52, c-53) y 5 `respuesta_directa` redactadas (4 sobre el asistente y un saludo coloquial que las reglas no detectan, «quiubo pues, todo bien?»). |

### `data/eval/claims_046.json`

```json
{
  "version": "2026-11-24",
  "frozen_at": "<fecha>",
  "origin": "prueba de Jev del 2026-11-24 (v3-r4 y perturbaciones), evidencia reconstruida con run_tool",
  "items": [
    {"id": "cl-001-orig", "v3": "v3-001", "variant": "original",
     "question": "…", "answer": "…",
     "evidence": [{"consulta": "buscar_producto", "texto": "…"}],
     "label": "unsupported", "note": "«la de 16 oz es la más usada para sopas» sin fuente"}
  ]
}
```

`label`: `supported` o `unsupported`. 16 ítems: 8 originales (6 `supported`, 2
`unsupported`) y 8 perturbadas (`unsupported`). La evidencia se construye con el mismo
código que producción (`build_claims_query`), así que viaja igual que en un turno real. La
`question` del ítem no viaja: solo sirve para derivar `dicho_por_la_persona`.

## Libro de llamadas de la medición (`specs/046-jev-router/evaluacion/jev-calls.jsonl`)

Una línea por llamada, sin texto:

```json
{"ts": "2026-11-25T15:04:05Z", "run": "gatekeeper", "item": "gk-07", "kind": "router",
 "status": "ok", "latency_ms": 271, "model": "jev-1.14.0", "input_tokens": 412,
 "output_tokens": 9, "choice": "rag", "confidence": 0.83,
 "probabilities": {"herramienta_estructurada": 0.09, "rag": 0.83, "mixta": 0.05,
 "respuesta_directa": 0.03}, "clarity": 3, "clarity_confidence": 0.71}
```

Para `kind="claims"`: `p_supported` y `threshold` en lugar de `choice`, `confidence`,
`probabilities`, `clarity` y `clarity_confidence`. `run` vale `smoke`, `gatekeeper`,
`claims`, `v3`, `demo`, `prod` o `dev` (llamadas anotadas por `JEV_LEDGER` fuera de una
corrida). El total de líneas es el gasto de la medición (SC-008).

## Eventos y metadatos

- **Evento SSE `trace`** (modo `tools` y `routes`): campos nuevos `gatekeeper` y `claims`
  (contrato §6). En `routes`, `gatekeeper` lleva `source="reglas"`, `gate_class` de
  `map_route(decision)` y `scope` vacío; `claims` es `{"status": "off"}`.
- **`map_route(route, intent) -> GateClass`** (`agent/gatekeeper/model.py`): el mapa de
  `gatekeeper_046.json` más `direct/greeting`, `direct/memory_write` → `respuesta_directa`.
  Lo usan la traza de `routes` y la evaluación.
- **LangSmith** (claves nuevas en `SAFE_KEYS`): `gatekeeper_route`, `gatekeeper_scope`,
  `gatekeeper_source`, `gatekeeper_llm_class`, `gatekeeper_confidence`, `gatekeeper_cautious`, `gatekeeper_rule`,
  `claims_status`, `claims_p`, `kind`, `confidence`, `probabilities`, `p_supported`,
  `threshold`, `jev_status`. Hijo `jev` por llamada con los campos de `JudgeCall`.
- **Logs**: `gatekeeper.decided` (los campos de la decisión sin probabilidades por clase),
  `jev.call` (`JudgeCall`), `jev.circuit_open`, `jev.auth_or_credits`, `jev.cap_reached`,
  `claims.judged` (`status`, `p`, `substituted`). Nunca texto.
