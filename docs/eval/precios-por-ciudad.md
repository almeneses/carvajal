# Precios por ciudad en Colombia (medición del 2026-11-26)

Mejora 5 del tercer ensayo: fuera de Bogotá el Cotizador caía al precio de referencia
(convertido desde México) o solo daba las sedes del directorio. Esta nota mide los datos
reales antes de cambiar el motor y deja la regla que se aplicó.

## Qué hay en los datos

Fuente: `load_dataset("2026-11-26")`, que lee `data/catalog/empaques/precios.csv`,
`data/catalog/empaques/distribuidores.csv` y `data/commercial/proveedores.csv`.

| Ciudad | Sedes | Distribuidores del directorio | Refs. con precio publicado | Vendedor del precio | Refs. con precio de referencia | Refs. cotizables antes → después |
|---|---:|---|---:|---|---:|---|
| Barranquilla | 2 | Dispapeles, Kive Andina (distribuidor directo) | 0 | — | 22 | 22 → 51 |
| Bogotá | 7 | Comercializadora de Desechables Sideral (2), EDEXA, Empaques y Soluciones, Megamarcas SAS, PAAK, Super Desechables del Norte S.A.S | 29 | EDEXA | 98 | 127 → 127 |
| Bucaramanga | 3 | Deseplast, Multi Desechables, Plasti Impacto | 0 | — | 22 | 22 → 51 |
| Cali | 4 | Bonniplast, Disdecol, Los Descuentos, Representaciones Lastra | 0 | — | 22 | 22 → 51 |
| Manizales | 2 | Kive Andina, Representaciones Lastra Eje Cafetero | 0 | — | 22 | 22 → 51 |
| Medellín | 6 | Arary, Dispapeles, Kive Andina, Multivasos, PAAK, Sigmaplasticos | 0 | — | 22 | 22 → 51 |
| Neiva | 1 | Representaciones Lastra Neiva | 0 | — | 22 | 22 → 51 |
| Pasto | 1 | Representaciones Lastra Pasto | 0 | — | 22 | 22 → 51 |
| Pereira | 2 | Dispapeles, Kive Eje Cafetero | 0 | — | 21 | 21 → 50 |
| Villavicencio | 1 | Soluciones Institucionales del Llano | 0 | — | 21 | 21 → 50 |

- **Precios publicados:** 29 de las 102 referencias propias de Colombia, todos de EDEXA en
  Bogotá (10 contenedores, 7 platos, 7 vasos, 4 tapas y 1 envase). Ningún otro distribuidor
  publica precio.
- **Precios de referencia:** las 98 copias `CO-MX-…`, todas en Bogotá y cada una en otras 2
  ciudades (decisión del equipo del 2026-11-25), por eso 21 o 22 por ciudad.
- **Cobertura y envío:** ninguna oferta declara condiciones de envío (`delivery` vacío en las
  323 ofertas) ni zonas de cobertura, y `evidencia_disponibilidad.csv` solo tiene cabecera. No
  hay ningún dato capturado que diga que EDEXA u otro vendedor envía a otras ciudades.
- **Curaduría:** no hay precios de otras ciudades sin cargar. `fuentes/precios_co.csv` tiene
  29 filas, todas `EDEXA,Bogotá`, y las 29 llegan a `precios.csv` y al Cotizador;
  `descartes_co.json` solo descarta fichas ambiguas o promociones, ninguna por ciudad. Las
  capturas crudas de EDEXA no se conservaron fuera de `precios_co.csv`.

## Reproducción

| Pedido | Antes | Después |
|---|---|---|
| «Cotízame 500 platos en Cali» | Platos térmicos 6 liso (`CO-MX-2864360031`), precio de referencia, 58.348 COP | Plato espumado pando 12 cm Wau, precio publicado por EDEXA en Bogotá, 43.700 COP; sedes de Cali |
| «1000 vasos de 7 oz en Medellín» | Pregunta la temperatura; con «bebida fría»: «Hoy no hay precios publicados para esta búsqueda en Medellín» | Pregunta la temperatura; con «bebida fría»: Vaso rígido traslúcido 7 oz Wau, precio publicado por EDEXA en Bogotá, 53.060 COP; sedes de Medellín |

## Regla aplicada

1. Si la referencia tiene precio publicado en la ciudad pedida, se usa ese.
2. Si no, y un vendedor publica su precio en otra ciudad de Colombia, se usa ese precio
   publicado. La cotización dice quién lo publica y dónde («Precio publicado por EDEXA en
   Bogotá; en Cali no hay precio publicado de esta referencia»). Sigue siendo de la ciudad
   pedida, y las sedes del directorio de esa ciudad son dónde consultarla.
3. Si tampoco hay precio publicado, se usa el precio de referencia de la ciudad, como antes.
   Nunca se usa un precio de referencia de otra ciudad.
4. Una ciudad sin sedes en el directorio (p. ej. Leticia) no recibe precios de otra.
5. El envío desde la ciudad del precio nunca se confirma, porque ningún dato declara
   cobertura: queda «por confirmar con el vendedor». Según la decisión 4 de la spec 054, la
   nota del envío va en el resumen y no en la línea.

El orden de las opciones es: aptitud para el uso, preferencia renovable, procedencia
(ciudad, otra ciudad, referencia) y precio. `make eval-cotizador` cambia en dos casos, que
eran justo el comportamiento que se quería corregir: «Cali sin precio publicado» y
«chocolate caliente en Pasto» pasan del precio de referencia al precio publicado en Bogotá.
Se añade un caso de cubiertos en Cali que conserva el precio de referencia de Cali, porque
no hay ningún precio publicado.
