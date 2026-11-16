# Implementation Plan: Asistente comercial de empaques para foodservice

**Branch**: `036-asistente-empaques` | **Date**: 2026-11-16 | **Spec**: [`spec.md`](./spec.md)
**Input**: Feature specification from `/specs/036-asistente-empaques/spec.md`

## Summary

Añadir al agente LangGraph una cuarta ruta, `commercial`, que convierte una necesidad de
empaque en referencias válidas del catálogo de Carvajal Empaques. La ruta sigue el patrón de
la ruta `tool`: el router extrae datos y pide aclaración si faltan; una secuencia fija de
herramientas deterministas busca, valida contra la guía de materiales, calcula cajas, cotiza
o remite a distribuidores; el LLM solo redacta con los resultados y las fuentes. El catálogo
es un conjunto de CSV curados y versionados, cargados en memoria como `data/indicators.json`.
El router pasa a ser intercambiable para comparar reglas, LLM y un modelo de decisión.

## Technical Context

| Aspecto | Decisión | Evidencia |
|---|---|---|
| Lenguaje | Python 3.13, paquete en `packages/core` (src-layout) | `pyproject.toml` |
| Entorno | `uv` + `uv.lock`; prohibido `pip` | `CONSTITUTION.md` §3 |
| Agente | LangGraph: nodos `recall` → `route` → `direct`/`rag`/`tool`; se añade `commercial` | `packages/core/src/carvajal/agent/graph.py` |
| Router | Reglas deterministas que devuelven `RouteDecision`; se añade la ruta y los datos en `extra` | `packages/core/src/carvajal/agent/router.py`, `state.py` |
| Herramientas | Esquemas Pydantic y `@tool`, como `indicator_lookup` | `packages/core/src/carvajal/agent/tools.py` |
| Catálogo | CSV en `data/catalog/empaques/` + `manifest.json`; carga en memoria con validación Pydantic | patrón de `data/indicators.json` |
| Construcción del catálogo | Orden `catalog-build`: JSON-LD y descripción de cada ficha, solo con biblioteca estándar | `packages/core/src/carvajal/catalog/build.py` |
| Interfaz | Parcial Jinja de tarjetas en el shell HTMX; la SPA lo consume después | `apps/api/templates/_fuentes.html` |
| Evaluación | Conjunto etiquetado en `data/eval/` y orden `eval-router` | `data/eval/retrieval_questions.json` |
| Modelo de decisión | Adaptador opcional tras un extra `decision`; fuera de la imagen de producción | — |
| Pruebas | pytest con LLM falso y catálogo de prueba en `tests/fixtures/` | `tests/test_agent_router.py`, `tests/test_agent_graph.py` |
| Alcance | Sin migraciones, sin pedidos ni pagos, sin captura nueva | `spec.md` §Assumptions |

**Alternativas descartadas**:

- **Tablas en Postgres** (`empaque_producto`, `material_regla`, `distribuidor`). Son unas 230
  filas de solo lectura: una migración, repositorios y un importador no aportan nada que un
  CSV versionado y revisable en el PR no dé. Se reconsidera si el catálogo se actualiza desde
  la web o supera unos miles de filas.
- **Solo RAG sobre las fichas**. La búsqueda por similitud no garantiza «12 oz» ni «apto
  para caliente»; el SC-001 exige un filtro exacto.
- **Que el LLM elija las herramientas**. Una secuencia fija es auditable y cumple el FR-004;
  la flexibilidad no se necesita para un flujo de cinco pasos.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Puerta | Cómo se cumple |
|---|---|
| I. La constitución manda | Requiere enmienda en la misma PR que la primera implementación (ver abajo). |
| II. El corpus es el contrato | `data/gold/` no cambia. El catálogo es una capa nueva, separada y derivada del corpus externo. |
| III. Sin fuentes no hay respuesta | Cada fila del catálogo conserva `source_url` y `captured_at`; cada tarjeta los muestra (FR-007). |
| IV. Calidad automatizada | Cada escenario tiene prueba; `make check` en verde antes de cada PR. |
| V. Captura responsable | No hay captura nueva: se reutiliza el corpus capturado el 2026-10-23. |
| VI. Cambios por PR | Siete PRs a `dev`, cada uno dentro de 300 líneas salvo los datos curados. |
| §3 Stack: sin dependencias nuevas en runtime | Carga con Pydantic; construcción solo con biblioteca estándar; el adaptador del modelo de decisión va en un extra opcional. |
| §5 Regla de la capa extendida | Hoy la capa extendida «solo se recupera y se cita». El catálogo usa datos del corpus externo como **datos estructurados**, lo que exige enmienda. |

**Enmienda propuesta** (`CONSTITUTION.md` §2, §5 y §9, y su proyección en
`.specify/memory/constitution.md`):

- §2 Alcance: «Asistente comercial de empaques foodservice sobre un catálogo curado (spec 036)».
- §5 Regla nueva: «La capa de catálogo (`data/catalog/`) contiene datos estructurados curados
  a partir del corpus; cada fila conserva su fuente y fecha de captura; los precios se
  presentan siempre como referencia fechada y nunca como oferta».
- §9 Decisión con fecha y motivo.

**Resultado**: pasa con la enmienda. La 035 también enmienda §2, §9 y §10. Para evitar
conflictos, la enmienda de la 036 se aplica después de que la 035 entre a `dev`.

## Project Structure

### Documentation (this feature)

```text
specs/036-asistente-empaques/
├── plan.md              # Este archivo
├── spec.md              # Contrato funcional
├── tasks.md             # Desglose por historia
└── checklists/
    └── requirements.md  # Validación de la spec
```

### Source Code (repository root)

```text
data/
├── catalog/empaques/              # nuevo, versionado
│   ├── referencias.csv            # tienda MX + catálogo PDF curado
│   ├── materiales.csv             # guía de materiales (5 filas, curada a mano)
│   ├── distribuidores.csv         # directorio de Colombia
│   └── manifest.json              # fuente, captura, conteos, versión
└── eval/routing_questions.json    # nuevo: ruta, datos y restricciones esperadas

packages/core/src/carvajal/
├── catalog/                       # nuevo
│   ├── models.py                  # Pydantic: Reference, MaterialRule, Distributor
│   ├── loader.py                  # carga y valida los CSV (cache en memoria)
│   └── build.py                   # construcción desde el corpus externo (biblioteca estándar)
├── agent/
│   ├── state.py                   # RouteName += "commercial"
│   ├── router.py                  # reglas comerciales y extracción de datos
│   ├── commercial.py              # nuevo: herramientas deterministas
│   ├── graph.py                   # nodo "commercial"; router inyectable
│   └── routers/                   # nuevo: llm.py y decision.py (mismo contrato)
└── cli.py                         # órdenes catalog-build y eval-router

apps/api/templates/_productos.html # nuevo: tarjetas de referencia

tests/
├── fixtures/catalog/              # catálogo mínimo para pruebas
├── test_catalog_loader.py
├── test_commercial_tools.py
├── test_agent_router.py           # ampliado
└── test_agent_graph.py            # ampliado
```

## Diseño

### Datos del catálogo

| Archivo | Columnas | Origen |
|---|---|---|
| `referencias.csv` | `sku, name, family, use, capacity_oz, diameter_mm, material, brand, units_per_pack, packs_per_box, units_per_box, temp_min_c, temp_max_c, price, currency, tax_included, price_valid_until, country, source_url, captured_at` | Fichas de la tienda MX (precio y SKU de los datos estructurados de la página); catálogo PDF transcrito y revisado |
| `materiales.csv` | `material, temp_min_c, temp_max_c, microwave, refrigeration, freezing, hot_content, recycling_code, applications, source_url, captured_at` (`applications` usa `uso=recomendable\|aplicable\|no_apto`) | Guía de materiales, «Lineamientos Generales», pág. 3 (PP, EPS, PS, PET, aluminio) |
| `distribuidores.csv` | `name, city, address, phone, source_url, captured_at` | Directorio de distribuidores |

La orden `carvajal catalog-build --corpus <ruta>` regenera `referencias.csv` desde la tienda y
deja el catálogo PDF y la guía como CSV curados a mano: el PDF en columnas mezcla productos
vecinos al extraerse y la guía son cinco filas críticas. El manifiesto registra qué filas son
automáticas y cuáles curadas, las observaciones de cada ficha (contradicciones, combos) y los
materiales sin regla en la guía (`materials_without_rule`: papel y pulpa).

Decisiones tomadas al curar los datos (PR 2):

- La temperatura y la vigencia del precio salen de la ficha (`temp_min_c`, `temp_max_c`,
  `price_valid_until`).
- El IVA visible junto al precio prevalece sobre el `valueAddedTaxIncluded: false` que
  WooCommerce pone por defecto en el JSON-LD.
- Si el título y la descripción de una ficha se contradicen, o la temperatura contradice el
  uso declarado, se usa el dato coherente y la contradicción queda en el manifiesto.
- `referencias.csv` se reescribe de forma atómica y valida antes las filas curadas que
  conserva, sin dejar temporales huérfanos. Las columnas se escriben en el orden de la
  tabla anterior, independiente del orden de declaración del modelo. Si el corpus deja
  fuera un SKU ya presente, aborta antes de escribir salvo que se pase `--allow-missing`;
  en ese caso el listado completo de SKU retirados queda en las notas del manifiesto.
- El manifiesto y el CSV se confirman en un único paso: si el reemplazo de uno falla, se
  restaura el otro, de modo que nunca quedan desincronizados; un fallo al restaurar se
  anota y se relanza el error original. La CLI exige que el directorio de `--out` exista
  y, salvo `--allow-missing`, que ya contenga `referencias.csv`, para no crear un catálogo
  vacío que perdería las filas curadas al apuntar a una ruta equivocada.

### Herramientas (`agent/commercial.py`)

| Herramienta | Entrada | Salida | Regla |
|---|---|---|---|
| `search_packaging` | uso, capacidad, país, familia | referencias candidatas | filtro exacto; ml → oz |
| `check_material` | referencias, temperatura, microondas | válidas y descartadas con motivo | FR-003: regla de la guía; papel y pulpa, con la temperatura y el uso de su ficha |
| `boxes_for` | referencia, unidades | cajas, excedente | `ceil`; FR-004 |
| `quote` | referencia, cajas | total, moneda, impuestos, fecha | solo con precio; FR-005 y FR-006 |
| `find_distributors` | ciudad | distribuidores o ciudades disponibles | FR-008 |

### Router y grafo

- `RouteName` incluye `commercial`. La regla comercial se evalúa después de memoria y saludo
  y antes de indicadores; las preguntas institucionales sin intención de compra siguen en
  `rag` (FR-009).
- Los datos extraídos viajan en `RouteDecision.extra`; si falta uno obligatorio, la decisión
  es `direct` con la pregunta de aclaración, como ya ocurre con el periodo en `tool`.
- `build_agent(router=...)` recibe un `Callable[[str, list[Turn]], RouteDecision]`; el valor
  por defecto es `decide_route`. Los adaptadores LLM y de modelo de decisión cumplen el mismo
  contrato (FR-013).
- El nodo `commercial` ejecuta la secuencia fija y entrega al LLM un resultado estructurado y
  las fuentes; el prompt prohíbe añadir referencias, cifras o disponibilidad.

### Evaluación

`data/eval/routing_questions.json` (unas 100 preguntas): las de `retrieval_questions.json` y
las de indicadores con su ruta actual, más preguntas comerciales con datos esperados y
restricciones de la referencia correcta. `carvajal eval-router --router rules|llm|decision`
reporta precisión de ruta, precisión por dato, violaciones de regla de material, latencia y
costo.

## Complexity Tracking

| Decisión | Costo | Mitigación |
|---|---|---|
| Transcribir a mano el catálogo PDF (~180 referencias) | Trabajo manual y riesgo de error | Revisión en el PR; prueba que valida tipos y rangos; muestra cruzada con el PDF |
| Precios congelados al 2026-10-23 | Se desactualizan | Fecha visible siempre; aviso de oferta vencida; actualización fuera de alcance |
| Dos catálogos (MX con precio, CO sin precio) | Respuestas distintas por país | El país es dato obligatorio para cotizar (FR-002) |
| Modelo de decisión externo | Dependencia de un proveedor | Extra opcional; el enrutador por reglas es el valor por defecto |
