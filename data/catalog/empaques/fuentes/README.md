# Fuentes del catálogo de Colombia

Este directorio guarda las fuentes primarias del catálogo de Colombia. El PDF **no se
versiona** por tamaño; su nombre, fecha de captura y hash se registran en
`../manifest.json`.

## Archivos esperados

| Archivo | ¿Se versiona? | Qué es |
|---|---|---|
| `catalogo_co.pdf` | No | Catálogo PDF de Carvajal Empaques Colombia, con las ~180 referencias foodservice |
| `precios_co.csv` | Sí | Capturas de precios de distribuidores y comercios colombianos, una fila por oferta y fuente |

## Regla de precios

- Solo se muestran precios **publicados para Colombia**. No se convierte ni se estima un
  precio desde México.
- Cada fila de `precios_co.csv` debe traer `sku`, `amount`, `currency` (COP), `tax_status`
  (`incluido`, `no_incluido` o `no_indicado`), `source_type` (`oficial` o `tercero`),
  `source_name`, `source_url`, `captured_at` y, si existe, `valid_until`.
- Si un precio viene por unidad o por paquete, se indica en la fuente y se normaliza a caja
  con `units_per_box` al ejecutar `carvajal catalog-prices`. Si no se puede normalizar, no se
  publica.
- Antes de capturar una tienda, revisar su `robots.txt` y respetar `Crawl-delay` y el rate
  limit. Si lo prohíbe, la tienda no se captura y el motivo queda en el manifiesto.

## Cómo se usa

```bash
# 1. Coloca el PDF aquí (sin commitearlo).
# 2. Cura las referencias en ../referencias.csv y los precios en precios_co.csv.
# 3. Regenera la tabla normalizada y el manifiesto.
uv run carvajal catalog-prices --country CO
```

El comando es idempotente y escribe `../precios.csv` y `../manifest.json` de forma atómica.
