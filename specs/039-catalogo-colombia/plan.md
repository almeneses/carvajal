# Implementation Plan: Catálogo de empaques de Colombia con precios de referencia

**Branch**: `039-catalogo-colombia` | **Date**: 2026-11-18 | **Spec**: [`spec.md`](./spec.md)
**Input**: Feature specification from `/specs/039-catalogo-colombia/spec.md`

## Summary

Ampliar el catálogo curado con las referencias foodservice del portafolio de Colombia y sus
precios de referencia en COP. El catálogo deja de tener un precio pegado a la referencia y
pasa a tener **ofertas por fuente**: cada precio conserva quién lo publica, su fecha y su
tratamiento de IVA. Solo se muestran precios publicados para Colombia; si una referencia no
tiene precio colombiano, se declara la ausencia y se remite a los distribuidores. **No hay
conversión ni estimación desde México.** La ruta comercial sigue usando las mismas reglas de
material y el mismo cálculo de cajas.

## Technical Context

| Aspecto | Decisión | Evidencia |
|---|---|---|
| Lenguaje | Python 3.13, `uv` | `pyproject.toml` |
| Catálogo | CSV versionados en `data/catalog/empaques/`, carga en memoria | spec 036, `catalog/loader.py` |
| Modelo | `Reference` + `ReferencePrice` (oferta por fuente); `Catalog.prices` | nuevo |
| Moneda | COP para Colombia, MXN para México; sin conversión entre países | FR-004, FR-006 |
| IVA | `incluido` / `no_incluido` / `no_indicado`; nunca se supone | FR-004, caso borde |
| Sin precio | Se declara y se listan distribuidores; no se estima | FR-006 |
| Regeneración | `carvajal catalog-prices`, idempotente y atómica, con manifiesto | FR-009 |
| UI | Tarjetas Jinja del shell HTMX, una sección por oferta | `apps/api/templates/_productos.html` |
| Evaluación | 20 preguntas nuevas en `data/eval/routing_questions.json` | FR-012 |
| Pruebas | `pytest` con catálogo de fixtures y `Decimal` | `tests/fixtures/catalog/` |

## Constitution Check

*GATE: Must pass before Phase 0. Re-check after Phase 1.*

| Puerta | Cómo se cumple |
|---|---|
| I. La constitución manda | La capa de catálogo ya está en §5; se amplía con la procedencia por precio y la regla de no estimar |
| II. El corpus es el contrato | No se toca `data/gold/`; las referencias CO salen del catálogo PDF colombiano |
| III. Sin fuentes no hay respuesta | Cada precio conserva `source_url`, `source_name` y `captured_at`; sin fuente no se muestra |
| IV. Calidad automatizada | Cada FR y SC tiene prueba; `make check` en cada PR |
| V. Captura responsable | Si se capturan tiendas, se respetan `robots.txt`, `Crawl-delay` y rate limit; el MVP admite curación manual fechada |
| VI. Cambios por PR | Seis PRs; los CSV curados se presupuestan aparte como datos |
| §5 Catálogo | Se mantiene: datos estructurados con fuente y fecha; precios como referencia, nunca oferta |

**Enmienda propuesta**: §5 añade que un precio puede provenir de un tercero identificado y
que **no se estima ni convierte** un precio ausente; §9 registra la decisión del 2026-11-18.

## Project Structure

### Documentation (this feature)

```text
specs/039-catalogo-colombia/
├── spec.md
├── plan.md              # Este archivo
├── tasks.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
data/catalog/empaques/
├── referencias.csv          # + filas CO (mismos campos que MX)
├── precios.csv              # nuevo: una fila por oferta/fuente ya normalizada
├── fuentes/
│   ├── README.md            # qué va aquí y cómo se captura
│   ├── catalogo_co.pdf      # fuente del portafolio CO (no versionado)
│   └── precios_co.csv       # capturas crudas por unidad/paquete/caja
├── materiales.csv           # sin cambios
├── distribuidores.csv       # sin cambios; se valida que no haya datos personales
└── manifest.json            # + secciones CO y precios, con descartes

packages/core/src/carvajal/
├── catalog/
│   ├── models.py            # + ReferencePrice, TaxStatus, PriceSource
│   ├── loader.py            # lee precios.csv; Catalog.prices
│   ├── prices.py            # nuevo: normalización y procedencia (sin conversión)
│   └── build.py             # + escritura atómica de precios y manifiesto
├── agent/
│   ├── commercial.py        # + quote_offer, selección de ofertas
│   ├── commercial_flow.py   # opciones con ofertas y canal CO
│   └── commercial_intent.py # ciudades CO ya existen; validar extracción
└── cli.py                   # + catalog-prices

apps/api/templates/_productos.html  # ofertas, IVA, vigencia y fuente

tests/
├── fixtures/catalog/        # + precios de prueba
├── test_catalog_prices.py   # nuevo
├── test_commercial_quote.py # ampliado
├── test_catalog_data.py     # cobertura CO
├── test_sc_039.py           # criterios de éxito
└── test_routing_eval_set.py # preguntas CO
```

## Diseño

### Datos

**`referencias.csv`** conserva el esquema de la 036. Las filas CO se agregan a mano desde el
catálogo PDF (transcripción revisada) y se marcan en el manifiesto como curadas. Para CO las
columnas de precio quedan vacías: el precio vive en `precios.csv`.

**`precios.csv`** (nuevo) separa el producto de la oferta:

| Columna | Regla |
|---|---|
| `sku`, `country` | Referencia a la que aplica |
| `amount`, `currency` | Precio por caja; COP o MXN |
| `tax_status` | `incluido`, `no_incluido` o `no_indicado` |
| `source_type` | `oficial` o `tercero` |
| `source_name` | Carvajal o el nombre del distribuidor/comercio |
| `source_url`, `captured_at` | Procedencia obligatoria |
| `valid_until` | Vigencia publicada, si existe |

Varias filas por SKU son válidas: se muestran todas y **no se promedian** (FR-006, caso
borde). Un precio por unidad o por paquete se normaliza a caja con `units_per_box`; si no se
puede, no se publica. Una referencia CO sin filas en `precios.csv` no tiene precio: no se
busca un equivalente mexicano ni se convierte moneda.

**`fuentes/`** es el directorio de entrada: el PDF del catálogo colombiano y las capturas
crudas de precios. El PDF no se versiona por tamaño; su ruta, fecha y hash quedan en el
manifiesto. `precios_co.csv` sí se versiona porque es la fuente curada y fechada.

### Modelo y carga

- `ReferencePrice` (Pydantic, inmutable): valida moneda, estado de IVA y procedencia.
- `Catalog.prices: dict[tuple[str, str], tuple[ReferencePrice, ...]]`.
- El loader lee `precios.csv` si existe. Si una referencia tiene a la vez precio inline y
  ofertas, gana `precios.csv` y el manifiesto lo advierte; las pruebas viejas con `Reference`
  inline siguen funcionando.
- `Reference` conserva `price/currency/tax_included/price_valid_until` por compatibilidad con
  fixtures y la ruta MX histórica; el catálogo real migra sus 52 precios MX a `precios.csv`.

### Cotización

- `quote(reference, boxes)` se conserva para fixtures y precios inline; se implementa sobre
  `quote_offer`.
- `quote_offer(price, boxes, today)` devuelve total, moneda, nota de IVA, fuente, fecha,
  vigencia y aviso de referencia. No existe aviso de estimación porque no hay estimaciones.
- `_option` arma `offers`: por cada oferta calcula cajas/total si hay cantidad; si no hay
  ofertas, deja `price_note` con el motivo. `discarded` no cambia.
- `search_packaging` ya filtra por país; se prueba que una solicitud CO **nunca** devuelve
  referencias MX (FR-002). Sin referencias CO, `catalog_note` explica la ausencia.
- `commercial_sources` cita la fuente de cada oferta, no solo la ficha del producto.

### Regeneración

`carvajal catalog-prices --country CO` (nuevo):

- Lee `fuentes/precios_co.csv` (capturas crudas) + `referencias.csv`.
- Normaliza a precio por caja y descarta lo no normalizable con motivo.
- Escribe `precios.csv` y actualiza `manifest.json` de forma atómica, con filas agregadas,
  cambiadas y descartadas (valor anterior y nuevo).
- Es idempotente: dos corridas con las mismas fuentes producen bytes idénticos.
- No captura la web en el MVP: las fuentes se capturan con la CLI existente y se curan; la
  captura automática queda como extensión cuando la fuente lo permita.

### UI

`_productos.html` muestra, por referencia, una lista de ofertas con: precio y moneda, «IVA
incluido / sin IVA / IVA no indicado», fecha, fuente y vigencia vencida. Si no hay ofertas,
el aviso de ausencia y los distribuidores. No hay etiqueta de estimación. Se mantiene el
diseño y la accesibilidad de la 024.

### Evaluación

20 preguntas nuevas en `data/eval/routing_questions.json` (grupo `colombia`) con:
pregunta, ruta/intención, país/ciudad, referencia esperada y total en COP o ausencia
declarada. El conjunto de México no cambia. La corrida de `eval-router` debe mantener 0
violaciones de material y 100 % de coincidencia de totales en las preguntas CO.

## Plan de PRs

| PR | Alcance | Archivos principales | Límite |
|---|---|---|---|
| 1 | Modelo de ofertas, `tax_status` y carga | `catalog/models.py`, `catalog/loader.py`, `tests/test_catalog_prices.py` | ~300 |
| 2 | Migración de precios MX a `precios.csv` y compatibilidad | `data/catalog/empaques/precios.csv`, loader, pruebas de regresión | ~250 |
| 3 | `catalog-prices`: normalización y manifiesto | `catalog/prices.py`, `catalog/build.py`, `cli.py`, pruebas | ~300 |
| 4 | Cotización CO y ofertas múltiples | `agent/commercial.py`, `commercial_flow.py`, pruebas | ~250 |
| 5 | Tarjetas con ofertas e IVA | `_productos.html`, `tests/test_productos.py` | ~200 |
| 6 | Datos curados CO: referencias, precios y manifiesto | CSV + `manifest.json` + `fuentes/` | datos, fuera del presupuesto de código |
| 7 | Preguntas CO de evaluación y evidencia | `routing_questions.json`, `tests/test_sc_039.py`, `evidencia.md` | ~200 |

## Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Transcribir ~180 referencias del PDF | Muestra cruzada, validación Pydantic por fila, manifiesto con descartes y revisión humana |
| Precios de tiendas cambian o desaparecen | Fecha y fuente visibles; aviso >90 días; regeneración reproducible |
| `robots.txt` prohíbe una tienda | No se captura; queda en el manifiesto con el motivo |
| Referencia sin precio | Se declara la ausencia y se listan distribuidores; no se inventa ni se convierte |
| Varias ofertas por referencia | Se muestran todas; no se promedia; la UI no elige una “mejor” sin fuente |
| IVA desconocido | `no_indicado` explícito, sin suponer |
| Regresión de la ruta MX | Pruebas de la 036 intactas y corrida de evaluación MX en cada PR |

## Orden de entrega

1. PR 1 y PR 2: modelo de ofertas y migración de los precios MX, con regresión de la 036.
2. PR 3: regeneración de precios y manifiesto (sin datos CO todavía).
3. PR 4 y PR 5: cotización y UI.
4. PR 6: datos CO curados.
5. PR 7: evaluación, evidencia y cierre.
