# Evidencia de disponibilidad

`evidencia_disponibilidad.csv` es la única fuente que puede convertir una existencia en
`stock_verificado`. Hoy tiene **solo cabecera**: no existe evidencia operativa y, por eso, las
160 ofertas colombianas quedan `por_confirmar` o `desconocidas` y ninguna sede figura con
stock verificado.

## Columnas

| Columna | Regla |
|---------|-------|
| `sku`, `country` | producto del catálogo o de la extensión demo |
| `site_id` | identificador estable de la sede (`CO-SEDE-…`), no `p01…p29` |
| `kind` | `inventario_vendedor`, `confirmacion_vendedor`, `integracion_pos`, `pedido_confirmado` o `simulacion` |
| `declared_status` | `disponible`, `insuficiente`, `agotado` o `desconocido` |
| `units` | unidades disponibles, si la fuente las declara |
| `recorded_by` | persona o rol que registró la evidencia |
| `recorded_at` | fecha de la evidencia |
| `reference` | correo, acta, folio o consulta que permite auditarla |
| `source_url` | URL de respaldo, si existe |
| `valid_until` | fecha hasta la que vale; al vencer degrada a `por_confirmar` |
| `note` | contexto breve |

Solo `inventario_vendedor`, `confirmacion_vendedor` e `integracion_pos` prueban existencias.
`pedido_confirmado` prueba un pedido pasado, no stock actual; `simulacion` nunca asciende a
`verificado`.

El detalle del contrato, los roles que actualizan y los ejemplos están en
[`docs/contratos/comerciales/README.md`](../../docs/contratos/comerciales/README.md).
