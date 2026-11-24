# Contrato — Las 20 herramientas del agente (spec 045)

Amplía [el contrato de la 044](../../044-agente-tool-calling/contracts/agent-tools.md), que
sigue vigente para las seis herramientas originales. Todas las herramientas:

- Se declaran con `@tool(name, description=..., args_schema=<modelo Pydantic>)` de
  `langchain_core.tools` y se registran en `toolagent/tools/registry.py` (ver
  [data-model.md](../data-model.md) *ToolSpec*). El nodo `tools` las ejecuta con `run_tool`,
  así que la deduplicación, el límite, la cancelación, el plazo del turno, la renumeración
  de fuentes y la fusión de tarjetas de la 044 aplican a las 20.
- Devuelven la salida común `{status, message, missing, ...datos}` dentro del bloque
  `<datos>` neutralizado. `status ∈ ok | empty | needs_input | error`.
- Son deterministas y no tienen efectos persistentes, **salvo** `recordar_preferencia` y
  `olvidar_preferencia`, que escriben en el Store en el momento (research R7), y
  `preguntas_frecuentes`, la única que llama a un LLM (inyectado por `ToolDeps.faq`) y que
  verifica cada entrada contra su documento antes de devolverla (research R18).
- Ningún esquema tiene campos de identidad (`owner_key`, `thread_id`, conversación): salen
  del estado del grafo.
- Cada dato devuelto (cifra, año, ciudad, nombre propio) aparece en el `excerpt` de una
  fuente registrada con `n`, salvo en las de memoria, que no registran fuentes. En
  `preguntas_frecuentes` lo garantiza la verificación de cada entrada (R18).
- La descripción de las 14 nuevas sigue el patrón «Qué hace. Úsala para: … No la uses
  para: … (usa X)», con ≤ 300 caracteres y sin cifras, ciudades ni SKU (R10).
- `TOOL_NAMES` se deriva del registro; el `Literal` `ToolName` crece con cada grupo
  registrado y coincide con el registro en cada commit (R9).

Columnas de la tabla resumen: **Rol** = `evidence_role`; **Cupo** = consume
`AGENT_MAX_TOOL_CALLS`.

| # | Herramienta | Módulo | Rol | Cupo | Fuente (`chunk_id`) | Línea de estado |
|---|---|---|---|---|---|---|
| 1 | `buscar_corpus` | `builtin.py` | corpus | sí | `chunk_id` del corpus | «Estoy buscando en la información de Carvajal…» (044) |
| 2 | `buscar_producto` | `builtin.py` | data | sí | `ficha:`/`precio:` (044) | 044 |
| 3 | `cotizar` | `builtin.py` | data | sí | `precio:`, `envio:`, `sede:` | 044 |
| 4 | `buscar_sedes` | `builtin.py` | data | sí | `sede:{site_id}` | 044 |
| 5 | `indicador_financiero` | `builtin.py` | data | sí | `tool:{nombre}:{periodo}` | 044 |
| 6 | `sin_consulta` | `builtin.py` | declaration | no | — | ninguna |
| 7 | `ficha_producto` | `ficha_producto.py` | data | sí | `ficha:{sku}:{pais}`, `precio:{sku}:{site}` | «Estoy revisando la ficha del producto…» |
| 8 | `comparar_referencias` | `comparar_referencias.py` | data | sí | `ficha:`, `precio:` | «Estoy comparando las referencias que me pediste…» |
| 9 | `guia_materiales` | `guia_materiales.py` | data | sí | `guia:{material}` | «Estoy revisando la guía de materiales…» |
| 10 | `convertir_presentacion` | `convertir_presentacion.py` | data | sí | `ficha:`, `precio:` | «Estoy calculando cuántas unidades son…» |
| 11 | `ciudades_con_cobertura` | `ciudades_con_cobertura.py` | data | sí | `directorio:CO`, `precio:` | «Estoy revisando en qué ciudades hay distribuidores…» |
| 12 | `consultar_cotizacion` | `consultar_cotizacion.py` | data | sí | `cotizacion:{folio}` + fuentes guardadas, solo procedencia `real` | «Estoy buscando tu cotización…» |
| 13 | `indicadores_disponibles` | `indicadores_disponibles.py` | data | sí | `tool:indicadores:catalogo` | «Estoy revisando qué cifras financieras tengo…» |
| 14 | `historia_carvajal` | `historia_carvajal.py` | data | sí | `historia:{anio}` | «Estoy buscando en la historia de Carvajal…» |
| 15 | `empresas_del_grupo` | `empresas_del_grupo.py` | data | sí | `empresa:{slug}` | «Estoy revisando las empresas y marcas del grupo…» |
| 16 | `preguntas_frecuentes` | `preguntas_frecuentes.py` | corpus | sí | `faq:{slug}:{huella}` (pasaje literal del núcleo) | «Estoy preparando preguntas frecuentes sobre Carvajal…» |
| 17 | `fecha_actual` | `fecha_actual.py` | data | sí | `reloj:{fecha}` (`source_type="sistema"`) | «Estoy revisando la fecha de hoy…» |
| 18 | `recordar_preferencia` | `recordar_preferencia.py` | memory | sí | — | «Estoy guardando lo que me pediste recordar…» |
| 19 | `listar_memoria` | `listar_memoria.py` | memory | sí | — | «Estoy revisando lo que recuerdo de ti…» |
| 20 | `olvidar_preferencia` | `olvidar_preferencia.py` | memory | sí | — | «Estoy borrando lo que me pediste olvidar…» |

Las plantillas pasan por `_clean` de `status.py` (recorte a 40 caracteres, sin nombres de
herramientas). Las de memoria y las de catálogo nuevas **nunca** interpolan valores: un SKU o
un nombre escrito por el modelo no puede aparecer en la línea de estado, que solo cita lo
que aportó la persona (regla de la 044; FR-018).

---

## Cambios a las seis herramientas de la 044

- Nombres, esquemas y descripciones visibles para el modelo **sin cambios**, verificados con
  un *golden* de `convert_to_openai_tool`, salvo una frase final «No la uses para…» en
  `buscar_producto`, `cotizar`, `buscar_sedes`, `indicador_financiero` y `buscar_corpus`
  para desambiguar con las nuevas (R10), con un crecimiento máximo de 160 caracteres cada
  una y sin el patrón completo ni el tope de las nuevas (hoy `cotizar` mide 688). El
  *golden* se actualiza en el mismo commit y se declara en la evidencia. `sin_consulta` no
  cambia.
- `cotizar`: si la ciudad no la dijo la persona (regla `_city_said`, vigente en
  `tools.py:1452`) y la memoria tiene `ciudad_habitual`, el `needs_input` con
  `missing: ["ciudad"]` añade `sugerencia_ciudad: <ciudad habitual>`.
- `buscar_sedes`: **cambio declarado sobre la 044**. Hoy usa `args.ciudad` tal cual
  (`tools.py:1833`), así que una ciudad copiada de `<preferencias>` devolvería sedes sin que
  la persona la dijera. Adopta la misma regla `_city_said`: si la ciudad no se dijo,
  devuelve `needs_input` con `missing: ["ciudad"]`, `ciudades_disponibles` y, si hay ciudad
  habitual, `sugerencia_ciudad`. Se revisan los casos de la 044 y los ítems del v3 que la
  llaman con una ciudad tomada de una respuesta anterior (T044).
- En ambas, la ciudad habitual **no** se añade a los textos de `_city_said`: nunca cuenta
  como dicha (FR-013). Si la persona responde «sí» a la sugerencia, la regla existente de
  confirmación (`_confirms` + `last_answer`) la acepta. `ToolContext.history` sigue
  limitado a `agent_history_turns` (la holgura de la semilla no amplía `_city_said`).
- `_today` pasa a usar `_now_bogota(deps)` (hoy usa la fecha sin zona, `tools.py:670`).

---

## Catálogo

### ficha_producto

- **Entrada** (`FichaProductoArgs`): `sku: str | None` (exacto), `nombre: str | None`
  (1–120), `pais: "CO" | "MX" | None`. Al menos uno de `sku` o `nombre` (validador).
- **Comportamiento**: resuelve con `reference_names.exact_sku` y `resolve_reference`
  (`agent/reference_names.py:235, 299`); lee `Catalog.references`, `Catalog.offers(ref)` y
  `CommercialBook.offers_for_sku`; la regla del material sale de `Catalog.materials`.
- **`ok`**: `ficha: {n, sku, nombre, familia, material, marca, capacidad_oz, diametro_mm,
  unidades_por_paquete, paquetes_por_caja, unidades_por_caja, usos, pais,
  temperatura_ficha: {min_c, max_c} | null, guia_material: {n, microondas, temperatura,
  rotulo: "según la guía de materiales, no la ficha"} | null, precios: [{n, presentacion,
  unidades, importe, moneda, iva_incluido, vendedor, ciudad, fecha, tipo_fuente}]}`. Sin
  totales ni recomendación de uso.
- **`needs_input`**: varias coincidencias → `missing: ["referencia"]`, `candidatas: [{sku,
  nombre}]` (≤ `MAX_TOOL_OPTIONS`).
- **`empty`**: `referencia_encontrada: false`.
- **Cuidados**: las fichas CO no declaran temperatura (0/102): `temperatura_ficha` es `null`
  y solo aparece `guia_material` rotulada. En las 21 fichas CO con «paca/paquete x N» en el
  nombre, la presentación sale del nombre, como en el motor.

### comparar_referencias

- **Entrada** (`CompararReferenciasArgs`): `referencias: list[str]` (2–4 elementos, SKU o
  nombre, 1–120 caracteres cada uno), `pais: "CO" | "MX" | None`.
- **Comportamiento**: resuelve cada referencia como `ficha_producto`. Precio unitario =
  importe ÷ unidades de la base, con `Decimal`: para CO, desde `Offer.units_in_basis` de la
  oferta publicada; para MX, `ReferencePrice.amount / units_per_box` (precio por caja). No
  usa `engine._offer_for`, que opera sobre ofertas CO y exige una cantidad.
- **`ok`**: `comparacion: {filas: [{n, sku, nombre, material, capacidad_oz, microondas,
  temperatura_guia, unidades_por_caja, presentacion_nombre, usos, precio_unitario: {valor,
  moneda, n} | null}], diferencias: [str], mas_economica: sku | null, aviso: str | null}`.
  `unidades_por_caja` y `presentacion_nombre` siguen la regla de `ficha_producto`: una
  ficha «(paquete x N)» o «(paca x N)» sin paquete declarado no dice que sea una caja, así
  que `unidades_por_caja` es `null` y el extracto de `ficha:{sku}:{pais}` dice «paquete de
  N unidades según el nombre» (la fuente se comparte con `ficha_producto`). `mas_economica`
  solo si todas tienen precio y coinciden país, moneda y `tax_status`; si no, `null` y
  `aviso`.
  Caso positivo de prueba: CO-CTT-08 frente a CO-CTT-16 (ambas con precio de EDEXA); las
  CO-VTER-08 y CO-VTER-10 no tienen precio y sirven para el caso sin veredicto.
- **`needs_input`**: alguna ambigua → `missing: ["referencia"]`, `candidatas: {texto: [...]}`.
- **`empty`**: menos de 2 referencias resueltas; lista `no_encontradas`.
- **No** recomienda uso (eso es `buscar_producto`).

### guia_materiales

- **Entrada** (`GuiaMaterialesArgs`): `material: "PP" | "EPS" | "PS" | "PET" | "ALUMINIO" |
  "PAPEL" | "PULPA" | None` (acepta «icopor» → EPS por validador), `aplicacion: str | None`
  (una de las 9 de `materiales.csv`: comida_fria, comida_caliente, sopas, frituras,
  ensaladas, sandwiches, salsas, panaderia, frutas; validador con sinónimos). Al menos uno.
- **Comportamiento**: lee `Catalog.materials` (`MaterialRule`) y
  `manifest.json["materials_without_rule"]`.
- **`ok`**: `reglas: [{n, material, temp_min_c, temp_max_c, microondas, refrigeracion,
  refrigeracion_deducida: bool, congelacion, contenido_caliente, codigo_reciclaje,
  nivel: "recomendable" | "aplicable" | "no_apto" | null}]`, `aviso: "Guía de materiales de
  Mi Tiendita Centroamérica (Lineamientos Generales, pág. 3), transcrita a mano"`. Con solo
  `aplicacion`, devuelve los materiales ordenados por nivel.
- **`empty`**: PAPEL o PULPA → `sin_regla: true`, `message: "la guía no cubre este material;
  cada ficha declara su uso"`.

### convertir_presentacion

- **Entrada** (`ConvertirPresentacionArgs`): `sku | nombre` (como `ficha_producto`),
  `cantidad: int` (1–1 000 000), `unidad: "unidad" | "paquete" | "caja" | "paca"`.
- **Comportamiento**: reutiliza **sin duplicar** las funciones del motor que hoy son privadas
  (`engine._declared_sizes`, `_conversion`, `_published_presentations`, `engine.py:247, 287,
  888`), que se exponen como `declared_sizes`, `conversion` y
  `published_presentations(product, dataset, city=None)`: la versión privada exige `city`, y
  la herramienta no recibe ciudad, así que con `None` devuelve las presentaciones de todas
  las ofertas publicadas. El `_published_presentations(dataset, skus)` de `builtin.py`
  (`tools.py:1748`, firma distinta) no se reutiliza ni se renombra. Para MX usa
  `units_per_pack`/`units_per_box` del catálogo (el `Dataset` es solo CO).
- **Ejemplos**: 3 cajas del CO-VTER-04 = 3000 unidades. El portacomidas J1 («… (paca x
  200)», sin `units_per_pack`, oferta «caja de 200») da `no_convertible`: `declared_sizes`
  devuelve `{}` a propósito.
- **`ok`**: `conversion: {n, texto: "3 cajas × 1000 = 3000 unidades", unidades: int, origen:
  "catalogo" | "contrato" | "ambos"}`.
- **`empty`**: `no_convertible: true`, `presentaciones_publicadas: [str]`.
- **`needs_input`**: referencia ambigua (`candidatas`).
- **`empty`** con `referencia_encontrada: false`: la referencia no existe; un `sku` con forma
  de SKU que no está en el catálogo no se reinterpreta como nombre (igual que
  `ciudades_con_cobertura`).

### ciudades_con_cobertura

- **Entrada** (`CiudadesConCoberturaArgs`): `sku: str | None`, `nombre: str | None`.
- **Comportamiento**: `CommercialBook.cities()` y `city_directory` (`commercial/book.py:107,
  112`); con referencia, `engine._priced_cities` y `book.sites_for_sku`.
- **`ok`**: `ciudades: [{ciudad, sedes}]` (10 ciudades, 29 sedes) y, con referencia,
  `con_precio: [ciudad]`, `con_sede: [ciudad]`; `aviso: "una sede no confirma existencias"`.
  Fuente `directorio:CO` (URL oficial del directorio de distribuidores); precios con su
  `precio:`.
- **`needs_input`**: referencia ambigua.
- **`empty`**: referencia que no existe, con `referencia_encontrada: false`. Un `sku` con
  forma de SKU (prefijo de país y guion, o diez o más dígitos) es exacto o no es, como en
  `ficha_producto`: «CO-NOEXISTE-1» no se reinterpreta como nombre. Solo un código corto
  sin esa forma («J2», «P1») se busca como nombre.
- **No** pide ciudad (eso es `buscar_sedes`).

---

## Institucional, preguntas frecuentes, cotizaciones y fecha

### consultar_cotizacion

- **Entrada** (`ConsultarCotizacionArgs`): `folio: str | None` (12 caracteres hexadecimales,
  validador que normaliza a minúsculas y quita espacios y «#»).
- **Comportamiento**: `deps.quote_lookup.get(folio, context.owner_key)` o, sin folio,
  `for_conversation(context.thread_id, context.owner_key)` recortado a las 5 últimas en
  Python (`[-5:]`; el repositorio no acepta `limit`) (envuelve `SqlQuoteRepository.get` y
  `for_conversation`, `adapters/storage/quotes.py:287, 303`). **Solo** se aceptan las de
  `quote.provenance == "real"` (`Provenance = real|simulado|ambiguo`, `quotes.py:24`), como
  la lectura autorizada (`apps/api/views/cotizador.py:264`). Vigencia comparada con
  `_now_bogota(deps)`. No recotiza.
- **`ok`**: `cotizaciones: [{n, folio, emitida, referencia, presentacion, unidades,
  subtotal, iva, envio, total | null, moneda, vendedor, vigencia_comercial, vigente:
  bool}]` (sin `procedencia`: todas son reales). Fuentes: `cotizacion:{folio}` (excerpt con
  `fallback.quote_text`) más las fuentes guardadas en el payload, solo de cotizaciones
  reales.
- **`empty`**: «no encontré esa cotización» — idéntico, sin fuentes ni valores, para folio
  ajeno, inexistente, simulado o ambiguo, o conversación sin cotizaciones reales (FR-026,
  principio III: la simulación de la 042 nunca vuelve como fuente, cita ni evidencia).
- **`error`**: sin `quote_lookup` (modo sin base) o falla de la base; el turno sigue.

### indicadores_disponibles

- **Entrada** (`IndicadoresDisponiblesArgs`): sin campos.
- **Comportamiento**: `agent/tools.py` `_load` y `known_names` sobre `data/indicators.json`.
- **`ok`**: `indicadores: [{nombre, alias, unidad, periodos}]` (6 indicadores, 10 registros);
  sin valores (los da `indicador_financiero`). Una fuente `tool:indicadores:catalogo` cuyo
  excerpt enumera nombres y períodos, con los documentos de origen (tercero).

### historia_carvajal

- **Entrada** (`HistoriaCarvajalArgs`): `anio: int | None` (1900–2100), `desde: int | None`,
  `hasta: int | None`. Al menos `anio` o `desde`; `hasta ≥ desde`.
- **Comportamiento**: hitos de `data/gold/historia.md` partidos por `## AAAA` (68 encabezados, 67
  con texto: 1962 está vacío en la captura y se trata como año sin hito;
  1904–2024); URL de `index.jsonl`.
- **`ok`**: `hitos: [{n, anio, texto}]` (texto literal, ≤ 10 hitos; `hay_mas: bool`).
- **`empty`**: año sin hito → `anios_cercanos: [int]` (anterior y siguiente con hito); nunca
  interpola.
- **`error`**: `historia.md` ausente (Gold sin sembrar) → error declarado.

### empresas_del_grupo

- **Entrada** (`EmpresasDelGrupoArgs`): `nombre: str | None` (1–80).
- **Comportamiento**: `data/institucional/empresas.json` (research R12).
- **`ok`**: `unidades: [{n, nombre, url}]`, `marcas: [{n, nombre}]`; con `nombre`, solo la
  coincidencia, buscada también en `alias` (por ejemplo, «Propal» → Carvajal Pulpa y Papel,
  con la cita de `pulpa_y_papel.md`).
- **`empty`**: nombre no listado ni como alias (por ejemplo, «Alpina») → `message: "no tengo
  ese dato en las fuentes de Carvajal"`; nunca afirma que no pertenece. También cuando la
  consulta contiene un nombre listado junto a palabras distintivas ajenas («Empaques
  Flexa», «Servicios Nutresa»): solo se acepta lo que sobra si es relleno («Editorial
  Norma», «la empresa Carvajal Empaques») o parte de otro nombre listado.
- **`error`**: `empresas.json` ausente (despliegue incompleto) → error declarado, nunca una
  excepción.

### preguntas_frecuentes

Sustituye a `listar_familias` por pedido del owner (research R18). Es la única herramienta
generativa: el texto lo escribe un LLM y la herramienta lo verifica antes de devolverlo.

- **Entrada** (`PreguntasFrecuentesArgs`): `tema: str | None` (1–80 caracteres, espacios
  colapsados; un área como «sostenibilidad», «historia» o «empaques»), `cantidad: int`
  (1–5, por defecto 5). Sin campos de identidad.
- **Dependencia**: `ToolDeps.faq: FaqSource | None` (`corpus`, `llm`, `timeout_s`,
  `max_prompt_chars`, `cache`; data-model). Sin `FaqSource` → `error` («no puedo generar
  preguntas frecuentes en este momento»). La herramienta nunca construye un proveedor.
- **Comportamiento**:
  1. Caché: si hay un lote verificado del mismo (tema normalizado, cantidad) de menos de
     24 h, se devuelve sin llamar al LLM (`desde_cache: true`).
  2. Documentos: con `tema`, la misma búsqueda de `buscar_corpus` con `alcance="oficial"`
     (`_retrieve` y `_entities_supported` de `builtin.py`) da los slugs de los fragmentos
     recuperados, y el prompt se compone solo con esos documentos núcleo; si no recupera
     nada → `empty`. Sin `tema`, todos los documentos núcleo con el tope
     `max_prompt_chars`.
  3. Generación: `GenerateFaq(corpus, llm, max_prompt_chars=...).prepare(previous=(),
     topic=tema, max_questions=cantidad, slugs=...)` y `.complete(...)`, en un hilo con plazo
     `timeout_s` (`AGENT_FAQ_TIMEOUT_S`). `build_faq_prompt` gana `topic` y `max_questions`
     opcionales; con los valores por defecto el prompt de `POST /api/faq` no cambia (prueba
     byte a byte). `FAQ_INSTRUCTIONS` no cambia; el tema va en la petición como
     `<tema>…</tema>` neutralizado.
  4. Verificación de cada `FaqEntry`: el slug de `source` (antes de « (oficial|tercero)»)
     debe ser un documento núcleo de los que alimentaron el prompt; se toma como `fragmento`
     el pasaje literal del documento (párrafos contiguos, ≤ 1 500 caracteres) que contiene
     las cifras, años, fechas y nombres propios de `question` y `answer` (criterio de
     `proper_entities_supported` y de las cifras de `filter_unsupported`); si alguno no
     aparece en ese pasaje, la entrada se descarta. Se conservan como mucho `cantidad`.
  5. Registro: una fuente por entrada, `chunk_id = "faq:{slug}:{huella}"` (huella de 8
     caracteres del pasaje), `slug`, `url`, `title` y `section` del documento,
     `layer="core"`, `source_type` del documento y `excerpt` = `fragmento`.
- **`ok`**: `preguntas: [{n, tema, pregunta, respuesta, fragmento}]`, `descartadas: int`,
  `desde_cache: bool`, `aviso: "preguntas generadas con las páginas oficiales de Carvajal;
  cada una cita su fuente"`.
- **`empty`**: el tema no recupera fragmentos del núcleo o ninguna entrada pasa la
  verificación. Con `evidence_role="corpus"`, un turno que solo tiene esto se abstiene como
  `buscar_corpus` (`NO_CONTEXT_ANSWER`).
- **`error`**: sin `FaqSource`, proveedor caído, plazo vencido o `FAQError`, **sin** lote en
  caché para ese tema. Con lote en caché (aunque haya caducado), se devuelve `ok` con
  `desde_cache: true` (respaldo).
- **Compuerta**: `_tool_backing` de `apps/api/chat/agent.py` y de `ta/evaluation.py` solo
  leen `fragmento` de sus entradas, nunca `pregunta` ni `respuesta`: el texto generado no se
  respalda a sí mismo.
- **Latencia**: única consulta con un LLM dentro del turno. Plazo propio de 25 s (5–40,
  menor que `AGENT_TURN_TIMEOUT_S`); con `tema`, prompt reducido a los documentos
  recuperados; `cantidad` ≤ 5; caché de 24 h. La línea de estado sale antes de generar. El
  primer texto de un turno con FAQ queda fuera del p95 de SC-002 y se declara en la
  evidencia.
- **Visualización**: web y WhatsApp muestran el texto final con citas [n] y la lista de
  fuentes, como cualquier respuesta documental. La FAQ existente (`/faq` → `/chat`,
  `faq.html`, `faq.js`, `faq.css`, `POST /api/faq`) no cambia.
- **No** responde una pregunta concreta (eso es `buscar_corpus`, `historia_carvajal` o
  `empresas_del_grupo`).

### fecha_actual

- **Entrada** (`FechaActualArgs`): sin campos.
- **Comportamiento**: `_now_bogota(deps)` (el mismo reloj que `_today` de `cotizar` y que
  `consultar_cotizacion`: con `deps.now` a las 04:30 UTC, la fecha es la del día anterior en
  Bogotá); `quotation/calendar.py` (`is_business_day`, `next_business_day`,
  `colombian_holidays`).
- **`ok`**: `{n, fecha: "AAAA-MM-DD", dia_semana, es_habil, es_festivo,
  proximo_dia_habil, zona: "America/Bogota"}`. Fuente `reloj:{fecha}` con
  `source_type="sistema"` y excerpt con todos los valores.

---

## Memoria

Comunes: el dueño sale de `ToolContext.owner_key`; con un dueño vacío o sin prefijo válido
devuelven `error` con `reason: "sin_dueno"` sin tocar el Store; sin `ToolDeps.memory`
devuelven `error` con `reason: "store_no_disponible"`; no registran fuentes; `_outcome`
guarda solo `{"categoria"}` como argumentos; las que escriben devuelven un `state_update`
(`memory`, `preferences` y, con `todo`, `conversation_summary`) e invalidan la huella de
`listar_memoria`; sus valores respaldan texto según el camino de la compuerta (FR-017,
[memory.md](./memory.md) §4). Detalle de almacenamiento en [memory.md](./memory.md).

### recordar_preferencia

- **Entrada** (`RecordarPreferenciaArgs`): `categoria: "nombre" | "empresa" |
  "ciudad_habitual" | "formato_preferido" | "nota"`, `valor: str` (1–120, espacios
  colapsados). Con `formato_preferido`, el valor se normaliza a la lista cerrada (`corto`,
  `detallado`, `tablas`, `viñetas`, `tuteo`, `usted`); si no encaja, se guarda como `nota`.
- **Regla «dicho en la pregunta»** (defensa ante inyección indirecta, R7): solo guarda si
  las palabras significativas de `valor` aparecen en `context.question`.
- **`ok`**: `{guardado: true, categoria, reemplazo: bool}`. No devuelve otros valores.
- **`empty`**: `{guardado: false, motivo: "dato_sensible" | "vacio" | "limite_notas" |
  "demasiado_largo" | "no_dicho"}`; con `dato_sensible`, `message` dice qué tipo de dato no
  se guarda, sin repetirlo.

### listar_memoria

- **Entrada** (`ListarMemoriaArgs`): sin campos.
- **`ok`**: `memoria: {nombre?, empresa?, ciudad_habitual?, formato_preferido?, notas: [str]}`
  más `aviso: "la ciudad habitual solo se usa si la confirmas"` cuando hay ciudad.
- **`empty`**: nada guardado.

### olvidar_preferencia

- **Entrada** (`OlvidarPreferenciaArgs`): `categoria: "nombre" | "empresa" |
  "ciudad_habitual" | "formato_preferido" | "nota" | "todo"`, `valor: str | None` (con
  `nota`, el texto o un fragmento de la nota a olvidar; sin `valor`, todas las notas).
- **Regla «pedido en la pregunta»**: solo actúa si la pregunta contiene un verbo de olvido
  de una lista cerrada, con límites de palabra exactos («olvida», «olvídate», «olvídame»,
  «olvides», «borra», «bórrame», «borres», «no recuerdes» y sus formas con «lo/la»), y no
  negado («no olvides», «nunca me olvides», «no lo borres» no piden olvidar). «Borrador»,
  «borrosa» o «eliminan» no cuentan; «elimin*» no es un verbo de olvido. Con `todo`,
  además, la pregunta debe cumplir `forget_all_requested` (el mismo patrón del atajo:
  «olvida todo lo que sabes de mí», «olvídate de mí», «borra mi memoria»…), de modo que
  una inyección indirecta no borra toda la memoria con un «olvida mi nombre».
- **`ok`**: `{olvidado: [categoria]}`. Con `todo`: memoria nueva, formato anterior, todos los
  borradores de resumen del dueño, lápida de olvido y marcador en el canal del hilo actual
  (R16).
- **`empty`**: `{olvidado: [], motivo: "nada_que_olvidar" | "no_pedido"}`.

---

## Reglas transversales nuevas

- Al cerrar la fase 6, `ToolName` contiene exactamente los 20 nombres; en cada commit
  coincide con el registro (prueba).
- Ninguna salida incluye datos personales del visitante, salvo `listar_memoria`, que solo
  devuelve lo que ese mismo dueño guardó.
- El texto final de un turno no puede afirmar datos de Carvajal respaldados solo por la
  memoria o por el resumen (FR-006, FR-017).

---

## Ajustes al implementar (2026-11-24)

Cambios sobre lo escrito arriba, elegidos por ser la opción más segura; las pruebas de cada
herramienta los fijan.

- **Fuentes compartidas**: `ficha:{sku}:{pais}` y `precio:{sku}:{sede}` las registran
  varias herramientas con campos distintos. `_Sources.add` añade al extracto de la fuente
  ya registrada el texto que trae la nueva, así cada cifra queda en el extracto de su `[n]`.
- **`comparar_referencias`**: `needs_input` devuelve `candidatas: [{posicion, opciones,
  total_coincidencias}]` y `no_encontradas` como posiciones, sin repetir el texto del
  modelo (la compuerta lee los datos de la herramienta como respaldo). Registra además
  `guia:{material}` para microondas y temperatura. Una coincidencia parcial cuenta como
  ambigua y un empate de precio no da `mas_economica`.
- **`convertir_presentacion`**: `texto` es el del motor, con separador de miles
  («3 cajas × 1.000 = 3.000 unidades»). `unidad="unidad"` devuelve `needs_input` con las
  presentaciones declaradas. En México, una ficha sin `units_per_pack` no se convierte.
- **`empresas_del_grupo`**: el `chunk_id` es `empresa:{clave}` con el nombre normalizado
  (siete filas comparten la página `quienes_somos`); el `slug` de la fuente sigue siendo la
  página de Gold.
- **`consultar_cotizacion`**: `vigente` puede ser `null` cuando el vendedor no publicó
  vigencia (hoy, todas); nunca usa la retención de 7 días como vigencia. Las fuentes
  guardadas son `cotizacion:{folio}:precio` y `cotizacion:{folio}:envio`. El recorte a 5
  se hace en la herramienta después de filtrar la procedencia: el `QuoteLookup` devuelve el
  hilo completo.
- **`historia_carvajal`**: `anio` junto con `desde`/`hasta` es un error de validación; un
  año sin hito se declara con los años cercanos (`tool_misses`).
- **`preguntas_frecuentes`**: en la compuerta solo respalda `fragmento`
  (`evidence.backing_data`), nunca la pregunta ni la respuesta generadas.
- **Descripciones**: todas las nuevas respetan el tope de 300 caracteres de R10.
