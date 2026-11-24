# Data Model — Spec 045

Sin migraciones de base de datos. Los cambios son: un canal nuevo en el estado de los dos
grafos, una entrada nueva (`turn_count`), dos namespaces nuevos en el Store de LangGraph
(que ya existe en Postgres), un archivo curado pequeño (`data/institucional/empresas.json`)
y el registro de herramientas.

## ConversationSummary (canal `conversation_summary` y borrador en el Store)

Pertenece a una conversación (`thread_id`). Vive en el canal `conversation_summary` de
`ToolAgentState` y de `AgentState` (lo escribe solo el grafo, en `recall` o con el
`state_update` de una herramienta de memoria) y, como borrador, en el Store: namespace
`("carvajal", "summary", owner_key)`, clave `thread_id`.

| Campo | Tipo | Regla |
|---|---|---|
| `v` | `int` | `1`. Un valor desconocido se ignora (se trata como sin resumen). |
| `text` | `str` | ≤ `AGENT_SUMMARY_MAX_CHARS` (1 500). Pasó por `redact` (R8). Nunca contiene el delimitador de cierre del bloque. |
| `covered_turns` | `int ≥ 0` | Índice absoluto (desde el primer turno del hilo) hasta el que el resumen cubre. Marca de idempotencia. Nunca supera los turnos que quien escribe vio. |
| `forgotten_through` | `int ≥ 0` | `0` por defecto. Tras «olvidar todo», turnos que ya no se resumen ni entran en la ventana (R16). |
| `last_message_id` | `str \| None` | Id del último mensaje de la persona cubierto (desempate ante reintentos; solo lo llena el worker, que lee mensajes con id). |
| `method` | `"llm" \| "deterministic" \| "mixed" \| "forgotten"` | `mixed`: borrador del modelo más relleno determinista del hueco; `forgotten`: marcador de olvido. |
| `user_quotes` | `list[str]` | Preguntas literales de la persona de los turnos cubiertos, ≤ 20 × 160 caracteres, ya redactadas. Únicas que respaldan texto (R4). |
| `updated_at` | `str` (ISO 8601, UTC) | |

**Lápida de olvido** (por dueño): `("carvajal", "summary", owner_key)` / `"_forgotten"` →
`{"v": 1, "at": ISO 8601 UTC}`. La escribe `forget(todo)`; la leen el worker (antes del
`put`) y `recall`.

**Índices de turno**: `pair_turns(messages)` (en `agent/memory/summary.py`) empareja
persona/asistente igual para `_seed_turns` y para el `SummaryWorker`. La entrada del grafo
trae `turn_count` (turnos persistidos antes del actual); el primer turno de la semilla tiene
índice `first = turn_count - len(seed)`.

**Reglas**:

- Solo hay resumen si el hilo tiene más turnos que `agent_history_turns`.
  `covered_turns = turn_count - agent_history_turns` cuando está al día.
- Adopción en `recall`: entre el canal y el borrador (descartando los de `updated_at` menor
  que la lápida) gana el de mayor `covered_turns`; el hueco
  `seed[max(0, covered - first) : len(seed) - agent_history_turns]` (como mucho
  `SUMMARY_SLACK_TURNS = 2` turnos, los de la holgura de la semilla) se añade con el resumen
  determinista y `method` pasa a `mixed`. Si el hueco real es mayor que la holgura, se usa
  lo que hay, `covered_turns` no avanza más allá de lo visto y se registra
  `agent.summary_gap`.
- El trabajo en segundo plano no hace nada si el borrador ya tiene ese `covered_turns`
  (idempotencia ante reintentos y réplicas), solo resume turnos con índice ≥
  `forgotten_through` y descarta su resultado si la lápida es posterior a su lectura.
- `recall` del grafo `tools` **no** reinicia este canal.
- «Olvidar todo» lo deja como marcador (`method: "forgotten"`, `covered_turns =
  forgotten_through = turn_count + 1`, texto y citas vacíos) en el hilo actual, borra los
  borradores de todos los hilos del dueño y escribe la lápida (R16).

**Bloque en el prompt** (sistema del grafo `tools`, `build_conversation_prompt` del atajo y
de la respuesta directa de `routes`; el nodo `rag` no lo recibe, FR-002):

```text
<resumen_conversacion>
…texto neutralizado con _neutralize, una línea por idea…
</resumen_conversacion>
```

## MemoryProfile (memoria de largo plazo)

Pertenece a un dueño (`owner_key` firmado: cookie web o `wa_…` de WhatsApp). Namespace
`("carvajal", "memory", owner_key)`, un elemento por categoría. Un `owner_key` vacío o sin
prefijo válido no lee ni escribe (`sin_dueno`).

| Clave (categoría) | Valor | Regla |
|---|---|---|
| `nombre` | `{"v": 2, "value": str, "origin", "updated_at"}` | 1–60 caracteres; letras, espacios, apóstrofo y guion. |
| `empresa` | ídem | 1–80 caracteres. |
| `ciudad_habitual` | ídem | 1–60 caracteres; se normaliza contra las ciudades del directorio si coincide, si no se guarda tal cual. **Solo sugerencia** (FR-013). |
| `formato_preferido` | ídem | Lista cerrada: `corto`, `detallado`, `tablas`, `viñetas`, `tuteo`, `usted` (validador con sinónimos: «respuestas cortas» → `corto`, «en tablas» → `tablas`). Lo demás se guarda como `nota`, que nunca se presenta como instrucción. |
| `notas` | `{"v": 2, "items": [{"value", "origin", "updated_at"}], "updated_at"}` | ≤ 5 notas de 1–120 caracteres; deduplicadas por texto normalizado. |

- `origin ∈ {"tool", "shortcut", "migration"}`; `updated_at` en ISO 8601 UTC.
- Ningún valor pasa si `find_sensitive(value)` no está vacío (R8).
- `recordar_preferencia` solo guarda si las palabras significativas del valor aparecen en la
  pregunta del turno (R7).
- Caducidad: un elemento con `updated_at` de más de `AGENT_MEMORY_DAYS` (180) días se ignora y
  se borra al leerlo.
- Formato anterior (solo lectura, migración R6): `("carvajal", "preferences", owner_key)` /
  `"profile"` → `{"items": [str]}` (hasta 10 frases de 280 caracteres). Lo no clasificado
  conserva las 5 notas más recientes, recortadas a 120 con «…»; los descartes se cuentan.

**Vista para el prompt** (`MemoryProfile.lines()`), en el bloque `<preferencias>` existente:

```text
nombre: Ana
empresa: Panadería La Espiga
ciudad habitual (solo sugerir, confirmar antes de usar): Cali
formato preferido: corto
nota: prefiere opciones compostables
```

`_preferences(store, owner_key)` de `agent/graph.py` pasa a devolver estas líneas, así el
grafo `routes` y el atajo no cambian de forma. `_preferred_names` (`apps/api/chat/agent.py:
720-747`) lee el nombre de la categoría `nombre` en lugar de buscarlo por patrones.
`AGENT_SYSTEM` declara que las reglas de datos, citas e idioma prevalecen sobre cualquier
preferencia.

## MemoryOperation (resultado interno de las herramientas de memoria)

| Campo | Tipo | Regla |
|---|---|---|
| `action` | `"remember" \| "list" \| "forget"` | |
| `category` | categoría, `"todo"` o `None` | `None` solo con `list`. |
| `outcome` | `"saved" \| "replaced" \| "rejected" \| "listed" \| "forgotten" \| "nothing"` | |
| `reason` | `"dato_sensible" \| "vacio" \| "limite_notas" \| "demasiado_largo" \| "no_dicho" \| "no_pedido" \| "store_no_disponible" \| "sin_dueno" \| None` | Solo con `rejected` o error. |

Se registra en el monitoreo solo `action`, `category`, `outcome` y `reason`; nunca el valor.

## ToolSpec y registro (`toolagent/tools/registry.py`)

| Campo | Tipo | Regla |
|---|---|---|
| `name` | `ToolName` | Único; en el `Literal` de `state.py`, que crece con cada grupo registrado. |
| `args_schema` | `type[BaseModel]` | Pydantic v2, descripciones de campo cortas en español, sin campos de identidad. |
| `description` | `str` | Nuevas: «qué hace. Úsala para… No la uses para… (usa X)», ≤ 300 caracteres. Las de la 044: sin cambios salvo una frase final «No la uses para…» (+160 como máximo) en cinco. Sin cifras, ciudades ni SKU. |
| `run` | `(args, deps, context, sources) -> _Draft` | Determinista; nunca lanza hacia el grafo (`run_tool` convierte excepciones en `error`). |
| `status_template` | `Callable[[Mapping], str] \| None` | `None` solo para `sin_consulta`. Las de memoria y las de catálogo nuevas no interpolan valores. |
| `consumes_budget` | `bool` | `False` solo para `sin_consulta`. |
| `evidence_role` | `"corpus" \| "data" \| "declaration" \| "memory"` | `corpus`: `buscar_corpus` y `preguntas_frecuentes` (abstención si vacío); `declaration`: `sin_consulta`; `memory`: las tres de memoria (sin fuentes, R11; `_outcome` guarda solo `{"categoria"}`); el resto `data`. |
| `city_keys` | `tuple[str, ...]` | Argumentos que son ciudad (para FR-018 de la 044 y FR-013). |

`TOOLS: tuple[ToolSpec, ...]` en el orden que ve el modelo:

1. `buscar_corpus` 2. `buscar_producto` 3. `cotizar` 4. `buscar_sedes`
5. `indicador_financiero` 6. `sin_consulta` 7. `ficha_producto` 8. `comparar_referencias`
9. `guia_materiales` 10. `convertir_presentacion` 11. `ciudades_con_cobertura`
12. `consultar_cotizacion` 13. `indicadores_disponibles` 14. `historia_carvajal`
15. `empresas_del_grupo` 16. `preguntas_frecuentes` 17. `fecha_actual`
18. `recordar_preferencia` 19. `listar_memoria` 20. `olvidar_preferencia`

Vistas derivadas (solo lectura): `TOOL_NAMES = tuple(spec.name for spec in TOOLS)` (ya no
`get_args(ToolName)`), `TOOL_SCHEMAS`, `TOOL_DESCRIPTIONS`, `_IMPLEMENTATIONS`,
`STATUS_TEMPLATES`, `BUDGET_FREE = {"sin_consulta"}`, `MEMORY_TOOLS`, `CITY_KEYS`.

**Implementado en la fase 2 (2026-11-24)**: `ToolSpec`, `TOOLS` y las vistas viven en
`tools/registry.py` (con `EVIDENCE_ROLES`, `CORPUS_TOOLS`, `DECLARATION_TOOLS`,
`IMPLEMENTATIONS` y `spec_for(name)`); `runner.py` y `tools/__init__.py` las reexportan
(`_IMPLEMENTATIONS` es el alias de `IMPLEMENTATIONS`). Los módulos de herramienta **no**
importan el registro: exportan su esquema, `DESCRIPTION`, `status(args) -> str` y
`run(args, deps, context, sources) -> _Draft`, y el registro escribe su `ToolSpec`.
`call_fingerprint` quedó en `runner.py` (necesita los esquemas; en `_base.py` crearía un
ciclo). `state.TOOL_NAMES` sigue derivado del `Literal` (lo usa `make_tool_outcome`) y una
prueba exige que coincida con el del registro.

## ToolDeps, ToolContext y ToolCallResult (ampliados)

| Objeto | Campo nuevo | Tipo | Regla |
|---|---|---|---|
| `ToolDeps` | `memory` | `MemoryStore \| None` | Envoltorio del Store; `None` sin Store (las herramientas de memoria devuelven `error` con `store_no_disponible`). |
| `ToolDeps` | `quote_lookup` | `QuoteLookup \| None` | `get(folio, owner_key)` y `for_conversation(conversation_id, owner_key)` (recorta `[-5:]` en Python: el repositorio no acepta `limit`); en pruebas, un doble en memoria. |
| `ToolDeps` | `institutional` | `InstitutionalData \| None` | Empresas (`empresas.json`) e hitos de `historia.md`, cargados una vez; estado `no_disponible` si falta un archivo. |
| `ToolDeps` | `faq` | `FaqSource \| None` | Lo que necesita `preguntas_frecuentes` (ver abajo); `None` sin base o sin proveedor (la herramienta devuelve `error` declarado). |
| `ToolContext` | `owner_key`, `thread_id` | `str` | Del estado del grafo; nunca de argumentos. |
| `ToolContext` | `memory` | `Mapping[str, Any]` | Instantánea de la memoria (la de `recall`, actualizada por `state_update` en el mismo turno). |
| `ToolContext` | `history` (cambio) | `tuple[Turn, ...]` | Limitada en `from_state` a los últimos `agent_history_turns` de la semilla (la holgura no cambia `_city_said`). |
| `ToolCallResult` / `_Draft` | `state_update` | `dict` | Solo claves `memory`, `preferences` y `conversation_summary`; el nodo `tools` lo vuelca en el estado y reemplaza el `SystemMessage` si cambia la memoria (R7). |

**`FaqSource`** (`_base.py`, R18): `corpus: FaqCorpus` (en producción `SqlFaqCorpus` de
`apps/api/views/faq.py`; en pruebas, documentos en memoria), `llm: LLMProvider` (el mismo
`runtime.llm_provider()` de `/api/faq`; en pruebas, uno falso con Markdown fijo),
`timeout_s: float` (`AGENT_FAQ_TIMEOUT_S`, 25 por defecto), `max_prompt_chars: int` (el
tope de `faq_service` según el proveedor) y `cache: dict` (privado del proceso, por (tema
normalizado, cantidad) → entradas verificadas con su hora; vigencia de 24 h, y como
respaldo se usa aunque haya caducado). No se serializa: vive en `ToolDeps`, no en el estado.

Ayudante común: `_now_bogota(deps)` en `_base.py`, usado por `_today`, `fecha_actual` y
`consultar_cotizacion` (R11).

## Estado de los grafos (cambios)

| Estado | Canal o entrada | Tipo | Regla |
|---|---|---|---|
| `ToolAgentState` | `conversation_summary` | `dict` | Ver arriba. No se reinicia por turno. |
| `ToolAgentState` | `memory` | `dict` | Instantánea de `MemoryProfile` leída en `recall` (JSON). `preferences` se conserva con las líneas para compatibilidad. |
| `AgentState` | `conversation_summary` | `dict` | El mismo canal, para que el respaldo `routes` lo lea y lo adopte. |
| ambos | `turn_count` | `int` | Entrada del turno: total de turnos persistidos del hilo antes del actual (un `COUNT` en la misma sesión que `_seed_turns`). |

## Eventos y traza

Sin tipos de evento SSE nuevos. Cambios:

- `status`: plantillas para las 14 herramientas nuevas (ver
  [agent-tools-045.md](./contracts/agent-tools-045.md)); las de memoria y las de catálogo
  nuevas no incluyen valores.
- `trace` (solo con `?debug=1`): `memory` pasa de `len(preferences)` al número de
  categorías con valor; se añade `summary: {covered_turns, method}` (sin texto).
- Monitoreo (spec 038): cada consulta es un hijo `tool` con `tool`, `status`,
  `elapsed_ms` y `source_ids`. Para las de memoria, `_outcome` reduce los argumentos a
  `{"categoria": …}` en origen, así que `results`, `trace`, la evaluación y el monitoreo
  (también con `content=full`) heredan la redacción. Eventos de log nuevos:
  `agent.summary_scheduled`, `agent.summary_done` (`method`, `covered_turns`,
  `elapsed_ms`), `agent.summary_failed`, `agent.summary_dropped`, `agent.summary_gap`,
  `agent.summary_store_failed`, `agent.summary_mode_degraded`, `agent.memory_migrated`,
  `agent.memory_migration_dropped` (conteo), `agent.memory_rejected` (`reason`), sin
  valores.

## InstitutionalCompany (`data/institucional/empresas.json`)

```text
{
  "version": "2026-11-24",
  "derived_from": ["data/gold/home.md", "data/gold/quienes_somos.md",
                   "data/gold/<7 páginas de unidad>.md", "data/gold/index.jsonl"],
  "items": [
    {"nombre": str, "tipo": "unidad" | "marca", "slug": str, "url": str, "cita": str,
     "alias": [{"nombre": str, "cita": str}]}
  ]
}
```

Regla: `cita` y cada `alias.cita` aparecen literalmente en `data/gold/<slug>.md` y `url`
coincide con la de ese slug en `index.jsonl` (prueba). Ejemplo de alias: PROPAL → Carvajal
Pulpa y Papel, con la cita de `pulpa_y_papel.md` («PROPAL hace parte de la Organización
Carvajal S.A.»). El archivo viaja en la imagen y en la siembra de producción (T006).

## HerramientasEvalItem (`data/eval/herramientas_045.json`)

Mismo formato que el v3 (`contracts/eval-agent.md` de la 044): `id`, `group`, `turns`,
`expect` con `tools_any`, `tools_none` y las comprobaciones existentes, más `path`
opcional (`"shortcut"` cuando el ítem espera el atajo). Grupos nuevos: `herramienta` (una
por cada una de las 20) y `memoria` (Ana en otra conversación, listar, olvidar, olvidar
todo, dato sensible, ciudad habitual). Los escenarios de otra conversación usan dos hilos
del mismo dueño con `new_thread_before: [índice de turno]`: el ejecutor abre un
`thread_id` nuevo con el mismo `owner_key` antes de ese turno (T003, antes de congelar el
conjunto en T004). Las preguntas de `recordar_preferencia` no llevan marcas del atajo.
`evaluation.py` aplica además un mapa congelado de equivalencias a `tools_any` (T090).
