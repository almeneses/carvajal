# Data Model: spec 051

Nada nuevo se persiste salvo el turno normal del chat. Las entidades viven en memoria.

## PreparedPhoto (`carvajal.food.photo`)

| Campo | Tipo | Regla |
|---|---|---|
| `data` | `bytes` | JPEG re-codificado, sin metadatos |
| `width`, `height` | `int` | lado mayor ≤ 1568, lado menor ≥ 280 |

Errores: `PhotoRejected(reason)` con `reason ∈ {"formato", "pequena", "grande", "alargada",
"ilegible"}` y un mensaje para la persona.

## FoodProfile (`carvajal.food.profile`)

| Campo | Tipo | Origen |
|---|---|---|
| `food_p` | `float` 0-1 | Noul `es_comida` |
| `kind` / `kind_confidence` / `kind_runner_up` | `FoodKind`, `float`, `FoodKind \| None` | Choice `tipo` |
| `consistency` / `consistency_confidence` | `Consistency`, `float` | Choice `consistencia` |
| `temperature` / `temperature_confidence` | `Temperature`, `float` | Choice `temperatura` |
| `portion` / `portion_confidence` | `float` 0-3, `float` | Score `porcion` |

`FoodKind`: `sopas, plato_fuerte, frituras, ensaladas, salsas, panaderia, frutas,
postres_helados, bebida_fria, bebida_caliente, no_es_comida`.
`Consistency`: `solido, semiliquido, liquido`. `Temperature`: `caliente, fria, ambiente`.

`FoodProfile.metadata()` → dict sin texto para trazas y libro.

## Requirements (`carvajal.food.recommend`)

`use`, `families` (tupla de `Family`), `temperature`, `closure: bool`, `target_oz: float`.

## Recommendation

| Campo | Tipo |
|---|---|
| `status` | `"ok" \| "not_food" \| "ask_kind" \| "ask_temperature" \| "no_match"` |
| `profile` | `FoodProfile` |
| `requirements` | `Requirements \| None` |
| `references` | tupla de `Reference` (≤ 3) |
| `alternative` | `Reference \| None` (solo en `no_match`) |
| `country` | `"CO" \| "MX"` |

## Turno de foto (persistido con el repositorio existente)

- `question`: `"📷 Foto de un alimento"` o `"📷 Foto de un alimento: <texto>"`.
- `answer`: texto de `render_recommendation` (markdown).
- `sources`: `[{"result_type": "commercial", "result": cards}]` con `cards` en el formato de
  `buscar_producto` (`request`, `options`, `discarded`, `discarded_total`, `channel`) cuando hay
  sugerencias.
- `client_turn_id`: el del cliente (idempotencia).

## JudgeCall

`JudgeKind` gana `"food"`; el libro anota `kind: "food"`, estado, latencia, tokens y
`food: {tipo, conf_tipo, temperatura, conf_temperatura, comida}` sin imagen ni texto.
