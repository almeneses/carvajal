# Contrato — Herramientas del agente

Cinco herramientas, todas deterministas, sin efectos persistentes (la persistencia la hace
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
- Máximo 3 cotizaciones exitosas distintas por turno; la cuarta devuelve `error` con
  `message` explicativo.

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

## Reglas transversales

- Una llamada con la misma huella (`nombre` + args normalizados) en el mismo turno no se
  ejecuta de nuevo: devuelve el resultado anterior.
- Una excepción dentro de una herramienta se convierte en `status: error` y el turno sigue.
- Ninguna salida incluye datos personales del visitante.
