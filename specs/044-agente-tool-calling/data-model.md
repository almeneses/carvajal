# Data Model — Spec 044

Sin migraciones. Los cambios son de estado en memoria del grafo y de la forma del JSON que
ya se guarda en `messages.sources` y `quotes.payload`.

## ToolAgentState (estado del grafo `tools`)

| Campo | Tipo | Regla |
|---|---|---|
| `question` | `str` | Pregunta del turno, sin vacíos. |
| `owner_key`, `thread_id` | `str` | Igual que el modo `routes`. |
| `seed_turns` | `list[Turn]` | Últimos `agent_history_turns` de Postgres (fuente de verdad). |
| `messages` | `list[BaseMessage]` (reductor `add_messages`) | Sistema + historial (R7) + pregunta + pasos del turno. |
| `preferences` | `list[str]` | Del Store por dueño; datos de estilo, nunca instrucciones. |
| `commercial_context` | `dict` | Producto, uso, temperatura, microondas, capacidad, cantidad, país, ciudad, domicilio vigentes; se actualiza con los argumentos de `buscar_producto`/`cotizar`. |
| `tool_calls_used` | `int` | ≤ `agent_max_tool_calls` (4). |
| `seen_calls` | `list[str]` | Huella `nombre+args` normalizados; una repetida devuelve el resultado anterior sin ejecutar. |
| `sources` | `list[dict]` | Fuentes del turno numeradas (R6), serializadas como `Source` + `n`; `SourceRegistry` es un ayudante sin estado que opera sobre esta lista. |
| `results` | `list[dict]` | Un `ToolOutcome` serializado por herramienta, en orden. |
| `quotes` | `list[dict]` | Cotizaciones exitosas distintas, máx. 3 (R5). |
| `cards` | `dict` | Resultado comercial combinado para `_productos.html`. |
| `mode_path` | `"shortcut" \| "agent"` | Camino del grafo `tools`. El respaldo corre en el grafo `routes`: el servicio lo registra como `path: "fallback"` en `trace` y en el monitoreo (FR-014). |
| `missing` | `list[str]` | Lo que faltó al cortar por límite (FR-010). |

**Serialización**: todo el estado se guarda con el checkpointer de Postgres, así que cada
campo es JSON-serializable (tipos básicos, `list`, `dict` y mensajes de LangChain); no se
guardan clases propias ni `set`.

Transiciones: `recall → shortcut → (direct → END) | (agent ⇄ tools → END)`. `agent → END`
cuando el paso no tiene herramientas o `tool_calls_used == límite` (se invoca sin
herramientas). La compuerta de evidencia (R3) actúa en el servicio sobre el estado final.

## ToolOutcome

| Campo | Tipo | Regla |
|---|---|---|
| `tool` | `str` | Una de las 6 de [agent-tools.md](./contracts/agent-tools.md). |
| `args` | `dict` | Validados por el esquema Pydantic de la herramienta. |
| `status` | `"ok" \| "empty" \| "needs_input" \| "error"` | `empty` = sin evidencia; `needs_input` = falta un dato (FR-005). |
| `source_ids` | `list[int]` | Números `[n]` asignados en el registro. |
| `elapsed_ms` | `int` | Para el monitoreo. |

**Evidencia del turno** = existe al menos un `ToolOutcome` con `status == "ok"` y
`source_ids` no vacío.

## Source (sin cambios de forma)

El `Source` de `application.ask` se conserva (`chunk_id`, `slug`, `section`, `layer`,
`score`, `excerpt`, `url`, `title`, `source_type`, …). Se añade solo el campo opcional
`n` (número de cita) al serializar; el lector actual lo ignora.

## Resultado comercial en `messages.sources` (compatibilidad)

```text
{
  ...campos actuales (options, ficha, offers, sites, channel, ...),
  "quote":  <primera cotización>   # se conserva para lectores actuales y el replay de la 043
  "quotes": [<cotización 1>, <cotización 2>, ...]   # nuevo; incluye la primera
}
```

Cada cotización conserva el bloque `quote: {id, folio, issued_at, retention_until,
valid_until, days}` de la 043. Lectores nuevos iteran `quotes` y caen a `[quote]` si falta.

## Quote (tabla `quotes`, sin cambios de esquema)

- Una fila por cotización mostrada; varias filas pueden compartir `message_id`.
- Folio: `i = 0` → `sha256("{conversation_id}:{client_turn_id}")[:12]` (igual que hoy);
  `i ≥ 1` → `sha256("{conversation_id}:{client_turn_id}:{i}")[:12]`; sin `client_turn_id`,
  aleatorio como hoy.
- Todas se insertan en la transacción de `_persist` (FR-012a).

## QuoteRequest (entrada estructurada del motor, R4)

| Campo | Tipo | Regla |
|---|---|---|
| `family` / `use` | `str \| None` | Al menos uno; si faltan ambos → `needs_input`. |
| `capacity_oz` | `float \| None` | Tolerancia `CAPACITY_TOLERANCE_OZ`. |
| `temperature` | `"caliente" \| "fria" \| "ambiente" \| None` | Requerida si el uso no la implica (FR-005). |
| `microwave`, `prefers_renewable` | `bool \| None` | |
| `quantity` | `int ≥ 1 \| None` | `None` → `needs_input`. |
| `city` | `str \| None` | Normalizada contra el dataset; `None` → `needs_input` con ciudades disponibles. |
| `delivery` | `bool \| None` | `None` → envío pendiente, total pendiente. |

## Generalization item (`data/eval/generalization_questions.json`)

Ver [eval-agent.md](./contracts/eval-agent.md).
