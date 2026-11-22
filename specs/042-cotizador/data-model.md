# Data model — Cotizador conversacional

**Fecha**: 2026-11-21 · **Spec**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md)

Los datos comerciales viven en `data/demo/cotizador/` y se generan con
`scripts/generar_demo_cotizador.py`. No se persisten en PostgreSQL: el motor los carga una
vez por proceso y la base solo guarda el historial de la conversación.

## Archivos

| Archivo | Clave | Filas | Notas |
|---|---|---:|---|
| Catálogo curado | `(sku, country)` | 102 CO + 98 MX | Única fuente de productos reales |
| `productos.csv` | `(sku, country)` | 48 | Solo la extensión simulada de Colombia |
| `ofertas.csv` | `(sku, provider_id)` | 258 | 160 CO y 98 MX |
| `proveedores.csv` | `provider_id` | 29 | sedes reales con coordenadas aproximadas |
| `geocode_sedes.json` | `name + city + address` | 29 | caché de Nominatim, regenerable |
| `fichas/*.md` | `sku-provider_id` | 160 | Documento citable por oferta |
| `manifest.json` | — | — | conteos, fuentes, reglas y aviso de demo |

### Cotización emitida (`quotes`)

`id`, `owner_key`, `conversation_id`, `message_id`, `created_at`, `valid_until` (7 días),
`total`, `currency`, `status` (`issued`) y `payload` con lo mostrado. Es el registro de
auditoría; no se recalcula al leer.

### Memoria del visitante (`visitor_memory`)

Una fila por dueño firmado, con caducidad de 180 días y borrado a pedido.

`owner_key`, `name` (≤ 60), `workplace` (≤ 80), `city` (≤ 80), `delivery` (bool),
`preferences` (JSONB, hasta 5 elementos de ≤ 60), `updated_at`.

Regla: no se escribe en `messages.sources` ni en las trazas; solo se aplica al motor.

## Entidades

### Producto (`productos.csv`)

`sku`, `country`, `name`, `family`, `brand`, `material`, `capacity_oz`,
`units_per_pack`, `packs_per_box`, `units_per_box`, `temp_min_c`, `temp_max_c`, `use`,
`origin` (`real` | `simulada`), `source_url`, `captured_at`, `note`.

### Oferta (`ofertas.csv`)

`sku`, `country`, `provider_id`, `provider`, `city`, `basis` (`unidad` | `paquete` |
`caja`), `units_in_basis`, `amount`, `currency`, `tax_status`, `valid_until`,
`stock_units`, `stock_status` (`disponible` | `agotado` | `insuficiente` | `desconocido`),
`lead_days_min`, `lead_days_max`, `ship_cost`, `simulated` (campos ficticios separados por
`|`), `source_url`, `captured_at`.

Regla: `simulated` siempre incluye `stock`, `lead_days` y `ship_cost` en Colombia; incluye
`price` cuando ninguna fuente publica el precio.

### Sede (`proveedores.csv`)

`provider_id`, `name`, `city`, `address`, `phone`, `latitude`, `longitude`, `precision`
(`direccion` | `via` | `ciudad` | `sin_coordenadas`), `source_url`, `captured_at`,
`simulated`.

Regla: el `provider_id` es la clave de unión con la oferta; hay empresas con dos sedes en
la misma ciudad.

### Cotización (en memoria y en `messages.sources`)

```json
{
  "kind": "cotizacion",
  "request": {"family": "vaso", "use": "bebida_caliente", "quantity": 20,
              "city": "Bogotá", "country": "CO", "delivery": true},
  "primary": {
    "sku": "7704918100199", "name": "Vaso rígido traslúcido 9 oz Wau",
    "material": "PP", "brand": "Wau", "capacity_oz": 9.0, "origin": "real",
    "presentation": {"basis": "paquete", "units": 50, "count": 1},
    "units_requested": 20, "units_bought": 50, "units_surplus": 30,
    "unit_price": "75.02", "subtotal": "3751", "currency": "COP",
    "tax_status": "incluido", "tax_included": true,
    "provider": {"id": "p06", "name": "EDEXA", "city": "Bogotá", "phone": "…",
                 "address": "…", "lat": 4.65, "lon": -74.05, "precision": "ciudad"},
    "stock": {"status": "disponible", "units": 350, "simulated": true},
    "price_simulated": false,
    "source_url": "https://www.tiendaedexa.com/…", "captured_at": "2026-11-18"
  },
  "alternatives": [{"sku": "…", "difference": "caja de 1.000 unidades"}],
  "delivery": {"requested": true, "window": "26–27 nov", "days_min": 1, "days_max": 2,
               "ship_cost": "12000", "free_from": "500000", "coverage": true},
  "notice": "Modo demo: existencias y plazos simulados.",
  "pending": []
}
```

`alternatives` repite la forma de `primary` reducida a lo que la tarjeta muestra. El
payload se guarda en `sources[0]` del mensaje del asistente para repintar el historial sin
volver a calcular.

## Estados del turno

| Situación | Respuesta |
|---|---|
| No es una solicitud comercial | Aviso de alcance y enlace al chat general |
| Falta la ciudad | Pregunta la ciudad; no muestra precios ni sedes |
| Producto inexistente o descartado por material | Lo explica y ofrece alternativas compatibles |
| Sin ofertas con existencia | Declara la ausencia y propone el canal de contacto |
| Domicilio sin destino | Pregunta ciudad o zona antes de estimar |
| Fuera de cobertura | Declara el límite y ofrece retiro en sede |
