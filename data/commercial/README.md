# Datos comerciales curados

## Sedes y geocodificación

`proveedores.csv` conserva las 29 sedes del directorio oficial con su geocodificación
trazable: `provider_id`, nombre, ciudad, dirección, teléfono, coordenadas, precisión,
`source_url` y `captured_at`. La geocodificación se hizo con OpenStreetMap Nominatim el
2026-11-21; las coordenadas son aproximadas y su precisión se conserva: 12 sedes a nivel
`direccion`, 12 a nivel `via` y 5 a nivel `ciudad`. Las 29 tienen coordenadas y ninguna
requiere un marcador exacto. Este archivo, junto con el catálogo curado y los precios
publicados, es la fuente del contrato comercial público.

## Evidencia de disponibilidad

`evidencia_disponibilidad.csv` es la única fuente que puede convertir una existencia en
`stock_verificado`. Hoy tiene **solo cabecera**: no existe evidencia operativa y, por eso, las
29 ofertas colombianas publicadas quedan `por_confirmar` o `desconocidas` y ninguna sede
figura con stock verificado.

## Columnas

| Columna | Regla |
|---------|-------|
| `sku`, `country` | producto del catálogo curado |
| `site_id` | identificador estable de la sede (`CO-SEDE-…`), no `p01…p29` |
| `kind` | `inventario_vendedor`, `confirmacion_vendedor`, `integracion_pos` o `pedido_confirmado` |
| `declared_status` | `disponible`, `insuficiente`, `agotado` o `desconocido` |
| `units` | unidades disponibles, si la fuente las declara |
| `recorded_by` | persona o rol que registró la evidencia |
| `recorded_at` | fecha de la evidencia |
| `reference` | correo, acta, folio o consulta que permite auditarla |
| `source_url` | URL de respaldo, si existe |
| `valid_until` | fecha hasta la que vale; al vencer degrada a `por_confirmar` |
| `note` | contexto breve |

Solo `inventario_vendedor`, `confirmacion_vendedor` e `integracion_pos` prueban existencias.
`pedido_confirmado` prueba un pedido pasado, no stock actual. Además, la evidencia debe traer
`valid_until` no vencido; sin esa vigencia el dato queda «por confirmar».

El detalle del contrato, los roles que actualizan y los ejemplos están en
[`docs/contratos/comerciales/README.md`](../../docs/contratos/comerciales/README.md).
