# Fuentes del catálogo de Colombia (spec 039)

Este directorio guarda las fuentes primarias de la ampliación del catálogo. Todo se capturó
el **2026-11-18** respetando `robots.txt`, con una petición cada 1,5 s.

| Archivo | Qué es | Lo genera |
|---|---|---|
| `precios_co.csv` | Precios de Colombia tal como los publica cada fuente (por unidad, paquete o caja) | `scripts/curar_catalogo_co.py` |
| `descartes_co.json` | Lo que se vio en las fuentes y no entró al catálogo, con su motivo | `scripts/curar_catalogo_co.py` |
| `mx_ampliacion.json` | Las fichas de la tienda de México que no estaban en el corpus: solo el JSON-LD, la descripción corta y la marca de IVA | captura del 2026-11-18 |

## De dónde salen los datos

- **Referencias de Colombia**: las fichas oficiales de `colombia.carvajalempaques.com`
  (familia, tallas, unidades por paquete y por corrugado, material). No publican SKU ni
  precio. El catálogo PDF que mencionaba el plan no hace falta.
- **Precios de Colombia**: la tienda en línea de **EDEXA** (`tiendaedexa.com`), distribuidor
  del directorio oficial en Bogotá. Publica el precio con «Precios ya incluyen impuestos». No
  publica `robots.txt` (404).
- **Referencias nuevas de México**: la tienda `mexico.carvajalempaques.com` publica 102
  productos; el corpus del 2026-10-23 solo tenía 55 fichas.

## Reglas

- Solo se muestran precios **publicados para Colombia**. No se convierte ni se estima un
  precio desde México y no se promedia entre fuentes.
- Si un producto del distribuidor coincide con una ficha oficial, la referencia toma los
  datos de la ficha y el distribuidor aporta el precio. Si no coincide, entra como referencia
  del distribuidor en su presentación de venta (paquete o caja).
- `precios_co.csv` lleva `sku`, `country`, `amount`, `currency` (COP), `basis`
  (`unidad`, `paquete` o `caja`), `units_in_basis`, `tax_status` (`incluido`, `no_incluido` o
  `no_indicado`), `source_type` (`oficial` o `tercero`), `source_name`, `source_city`,
  `source_url`, `captured_at` y, si existe, `valid_until`.
- `carvajal catalog-prices` normaliza a precio por caja. Si la caja no contiene un número
  entero de paquetes, o la caja de la fuente no es la de la referencia, el precio no se
  publica y el motivo queda en el manifiesto.

## Cómo se regenera

```bash
uv run python scripts/curar_catalogo_co.py      # referencias CO y capturas de precios
uv run python scripts/ampliar_catalogo_mx.py    # fichas nuevas de México
uv run carvajal catalog-prices --country CO     # precios.csv y manifiesto
```

Las tres órdenes son idempotentes: con las mismas fuentes producen los mismos archivos.
