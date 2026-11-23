# Contrato — Eventos SSE del chat (cambios de la 044)

Los eventos existentes (`open`, `trace`, `meta`, `products`, `token`, `fallback`, `done`,
`error`) no cambian de nombre ni de campos obligatorios. Cambios:

## Nuevo: `status`

```text
event: status
data: {"text": "Estoy calculando cuánto te salen 2.000 vasos en Bogotá…", "step": 2}
```

- Uno por herramienta que empieza; `step` es el orden en el turno (1–4).
- `text` sale de plantillas fijas por herramienta (sin nombres internos, sin resultados,
  sin precios ni fuentes). Puede incluir el dato principal aportado por la persona
  (producto, cantidad, ciudad), escapado como texto.
- El cliente lo muestra en la línea de estado (`role=status`, `aria-live=polite`),
  reemplazando «Pensando…», y lo borra con el primer `token`, `fallback` o `error`.
- No se persiste ni se reemite en el replay.

Plantillas (ejemplos; el texto final se revisa con el owner):

| Herramienta | Mensaje |
|---|---|
| `buscar_corpus` | «Estoy buscando en la información de Carvajal…» |
| `buscar_producto` | «Estoy revisando qué {producto} te sirven…» / «Estoy revisando el catálogo…» |
| `cotizar` | «Estoy calculando cuánto te salen {cantidad} {producto} en {ciudad}…» |
| `buscar_sedes` | «Estoy buscando dónde comprar en {ciudad}…» |
| `indicador_financiero` | «Estoy consultando las cifras de Carvajal…» |

## `trace` (solo cambia el contenido)

Añade `mode: "routes" | "tools"` y `path: "shortcut" | "agent" | "fallback"`. En modo
`tools`, `route` vale `"agent"` salvo en el atajo. Sigue sin mostrarse al visitante sin
`?debug=1`.

## `products`

El HTML puede contener varias tarjetas de cotización (una por folio).

## `done`

Añade `quote_ids: [str]` (vacío si no hay cotizaciones). `quote_id` se conserva y vale el
primer folio o `null`.

## `fallback` en modo `tools`

Se usa para la respuesta determinista del respaldo (FR-009): `text` es el aviso fijo más
el resumen determinista; `sources` como hoy.
