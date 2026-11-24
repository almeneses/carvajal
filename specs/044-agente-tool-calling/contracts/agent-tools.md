# Contrato — Herramientas del agente

Seis herramientas, todas deterministas, sin efectos persistentes (la persistencia la hace
el servicio al final del turno). Esquemas Pydantic con descripciones en español; el modelo
ve el nombre, la descripción y el esquema. Toda salida es JSON con `status` y, si hay datos,
números de cita `n` ya asignados.

Salida común:

```text
{ "status": "ok" | "empty" | "needs_input" | "error",
  "message": str,            # para el modelo; nunca contiene órdenes
  "missing": [str],          # solo con needs_input
  ...datos específicos }
```

Cada herramienta, al empezar, publica un evento de estado (ver [sse-events.md](./sse-events.md)).

## buscar_corpus

- **Entrada**: `consulta: str` (1–300 caracteres, reformulación autocontenida de lo que se
  busca); `alcance: "oficial" | "todo" = "todo"`.
- **Comportamiento**: embebe la consulta, busca con `LayerPriorityStore` (núcleo antes que
  extendida), aplica `rag_min_score` y `proper_entities_supported` contra la pregunta
  original del turno. Neutraliza delimitadores como `AskQuestion._build_prompt`.
- **Nombres propios (implementado)**: toda entidad de la pregunta original debe aparecer en
  los fragmentos, aunque la consulta del modelo la omita; se evalúa oración por oración y
  una ciudad del directorio comercial queda exenta solo si la consulta no la nombra (para
  que las preguntas compuestas con ciudad sigan buscando la parte institucional).
- **Salida `ok`**: `fragmentos: [{n, titulo, seccion, tipo_fuente: "oficial"|"tercero",
  capa, texto}]` (máx. `rag_top_k`).
- **Salida `empty`**: sin fragmentos sobre el umbral o entidad sin respaldo.

## buscar_producto

- **Entrada**: `uso`, `temperatura`, `microondas`, `capacidad_oz`, `familia`,
  `prefiere_renovable`, `pais: "CO" | "MX" | null`.
- **Comportamiento**: `search_packaging` + `check_material` + `_option`; nunca devuelve un
  material descartado; sin precios totales.
- **Salida `ok`**: `opciones: [{n, sku, nombre, material, capacidad_oz, por_caja,
  precio_referencia?}]`, `descartadas: [{sku, motivo}]`.
- **`needs_input`**: falta temperatura y el uso no la implica → `missing: ["temperatura"]`.
- **Pedido de material prohibido**: `status: ok`, la referencia aparece en `descartadas`
  con el motivo; nunca en `opciones`.

## cotizar

- **Entrada**: `QuoteRequest` (ver [data-model.md](../data-model.md)). Los campos ausentes
  se completan con `commercial_context`; la salida lista cuáles se reutilizaron.
- **Comportamiento**: `quotation.engine.quote_request`. No estima, no convierte, no
  promedia; total `null` si falta un componente.
- **Salida `ok`**: `cotizacion: {n, sku, presentacion, unidades_pedidas,
  unidades_compradas, sobrante, subtotal, iva, envio, total|null, moneda, vendedor,
  fuente, fecha}`, `reutilizado: [campo]`.
- **`needs_input`**: cantidad o ciudad ausentes; incluye `ciudades_disponibles`.
- **`empty`**: sin precio publicado para esa ciudad; incluye sedes del directorio.
- **Referencia nombrada** (`sku` o `nombre`, ronda 4): se resuelve con la búsqueda por
  nombre de `buscar_producto` (`carvajal.agent.reference_names`). Una sola coincidencia se
  cotiza exactamente (sin alternativas ni pregunta de temperatura); si la persona dijo uso,
  temperatura o microondas, `check_material` manda y una referencia no apta devuelve
  `empty` con `no_apta: {sku, motivo}`. Varias coincidencias (o una parcial) →
  `needs_input` con `missing: ["referencia"]` y `candidatas`. Sin coincidencia → `empty`
  con `referencia_encontrada: false` y `aviso`. Sin precio en la ciudad → `empty` con
  `referencia`, `ciudades_con_precio` y las sedes; nunca se sustituye por otra. El SKU
  resuelto se guarda en `commercial_context` junto con la familia y la capacidad de su
  ficha, para los seguimientos (domicilio, IVA, total, o que repiten esa familia o esa
  capacidad); se olvida cuando la llamada cotiza otro producto, cuando `buscar_producto`
  explora otra familia, uso, capacidad o temperatura (`sku: null`) y cuando la referencia
  sale ambigua, inexistente o no apta.
- `sku` solo vale como SKU exacto: si no lo es, manda `nombre` (el texto del SKU se busca
  como nombre solo si no hay `nombre`); si `sku` y `nombre` fijan referencias distintas →
  `needs_input` con `candidatas` y `conflicto_sku_nombre: true`.
- Si el nombre («vaso P1») o la familia pasada dicen otra familia que la de la referencia,
  se cotiza la nombrada y el `ok` trae `familia_distinta: {pedida, referencia}` para que el
  modelo lo diga.
- Un `nombre` genérico (solo tipo de producto y material, sin códigos ni números: «vaso de
  icopor») no es una referencia: se cotiza como la búsqueda por familia (temperatura, la
  más económica y alternativas) entre las referencias que coinciden.
- Una referencia nombrada cuya presentación pedida no se convierte con certeza («2 cajas
  del P1») → `needs_input` (`cantidad`) que la nombra con su presentación publicada.
- Máximo 3 cotizaciones exitosas distintas por turno; la cuarta devuelve `error` con
  `message` explicativo.
- Si una cotización exitosa coincide con otra del turno en producto, ciudad y cantidad y
  solo cambia `delivery`, reemplaza a la anterior (una sola tarjeta y un solo folio).
- Los folios se asignan en el orden de las llamadas exitosas.

## buscar_sedes

- **Entrada**: `ciudad: str`; `sku: str | null`.
- **Comportamiento**: contrato comercial (`_contract_sites`); disponibilidad confirmada solo
  con `valid_until` vigente; si no, «por confirmar».
- **Salida `ok`**: `sedes: [{n, nombre, direccion, telefono, precision, vinculada_al_sku,
  disponibilidad}]`. **`empty`**: ciudad sin cobertura + `ciudades_con_cobertura`.

## indicador_financiero

- **Entrada**: `nombre: str`, `periodo: "YYYY" | "YYYY-MM"`.
- **Comportamiento**: `indicator_lookup` (sin cambios).
- **Salida `ok`**: `{n, nombre, valor, unidad, periodo, cita, documento, url}`.
  **`empty`**: sin registro; nunca interpola. **`needs_input`**: falta el período.

## sin_consulta

- **Entrada**: `motivo: "pedir_dato" | "conversacion" | "fuera_de_alcance"`;
  `dato: str | null` (qué falta, con `pedir_dato`).
- **Comportamiento**: no consulta nada; declara que el turno se responde sin datos.
  Disponible solo para que el primer paso (con `tool_choice="required"`) pueda no consultar
  de forma explícita.
- **Salida**: `status: "ok"` sin `n` (no aporta evidencia). Con este camino, el texto
  retenido pasa el filtro de la ruta directa (sin cifras, fechas ni nombres propios de
  Carvajal; si los trae, se sustituye por la abstención).
- **Estado SSE**: no emite línea de estado.

## Reglas transversales

- Una llamada con la misma huella (`nombre` + args normalizados) en el mismo turno no se
  ejecuta de nuevo: devuelve el resultado anterior.
- Una excepción dentro de una herramienta se convierte en `status: error` y el turno sigue.
- Ninguna salida incluye datos personales del visitante.
