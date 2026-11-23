# Contrato de datos comerciales v1.0

**Estado:** implementado y validado como capa de referencia (`carvajal.commercial`). El chat
general y el Cotizador todavía no se reescribieron para consumirlo; esta capa les ofrece el
mismo contrato y adaptadores de lectura. La adopción por cada consumidor es un cambio
posterior y localizado.

**Alcance inicial:** Empaques Colombia. **Paridad obligatoria:** las 98 referencias de México
que hoy traen precio inline en `referencias.csv`.

## 1. Qué problema resuelve

La auditoría dejó tres datos que se confundían entre sí:

| Dato | Evidencia actual | Qué no prueba |
|------|------------------|---------------|
| 102 referencias colombianas | fichas del catálogo curado | que alguien las venda ni a qué precio |
| 29 precios publicados | tienda de EDEXA, un solo vendedor en Bogotá | existencia, sede ni disponibilidad |
| 29 sedes en 10 ciudades | directorio oficial de distribuidores | que la sede venda un SKU concreto |

Antes de este contrato, la demo usaba `p01…p29` (orden del archivo) como identificador de
sede, la disponibilidad era 100 % simulada y algunas ofertas con precio simulado citaban una
URL real de EDEXA. El contrato separa **producto**, **oferta** y **sede**, y trata la
**disponibilidad** como un dato con evidencia propia.

## 2. Entidades

### Producto

| Campo | Regla |
|-------|-------|
| `sku`, `country`, `name`, `family` | identidad vendible; `(sku, country)` es único |
| `units_per_box` | unidades de la caja de referencia, usadas para normalizar |
| `origin` | `real` (catálogo curado) o `demo` (extensión simulada) |
| `source` | URL, fecha y tipo de la ficha |

Un producto **no** tiene precio ni existencias.

### Vendedor

| Campo | Regla |
|-------|-------|
| `seller_id` | `CO-VEND-…` / `MX-VEND-…`, estable por país y nombre normalizado |
| `name`, `country`, `kind` | distribuidor, tienda en línea o fabricante |
| `source` | de dónde salió el nombre |

Un vendedor puede tener varias sedes y una tienda en línea puede no tener sede física.

### Sede

| Campo | Regla |
|-------|-------|
| `site_id` | estable, `CO-SEDE-…` / `MX-SEDE-…`; no depende del orden del archivo |
| `legacy_provider_id` | `p01…p29` mientras dure la migración de la demo |
| `seller_id`, `country`, `name`, `city` | identidad comercial |
| `address`, `phone` | tal como los publica el directorio; la dirección puede faltar si la fuente no la trae |
| `source` | URL del directorio y fecha de captura |
| `location` | latitud, longitud, `precision`, geocodificador y fecha de geocodificación |

Precisiones admitidas: `direccion`, `via`, `ciudad`, `desconocida`. La sede **no** contiene
precio ni stock; el contacto del directorio tampoco.

### Oferta

| Campo | Regla |
|-------|-------|
| `offer_id` | estable por contenido; varias ofertas pueden compartir SKU |
| `sku`, `country` | deben existir en el producto |
| `seller_id` | vendedor que publica |
| `site_id` | opcional: solo si la oferta se atribuye a una sede concreta |
| `presentation` | presentación que **cobra el vendedor**: `unidad`, `paquete` o `caja` con `units_in_basis` |
| `amount`, `currency` | importe de **esa presentación**; COP en Colombia, MXN en México |
| `tax_status` | `incluido`, `no_incluido` o `no_indicado`; `tax_rate` solo si la fuente declara la tasa |
| `tax_simulated` | `true` en la demo cuando el IVA no lo declara una fuente publicada |
| `source` | nombre, URL, fecha y vigencia de la fuente |
| `normalized` | vista adicional a caja: `{basis: "caja", units_in_basis, amount}`; solo si la caja contiene un número entero de presentaciones |
| `origin` | `publicada` o `demo` |
| `availability` | objeto independiente; ver §5 |
| `delivery` | opcional: plazo mínimo/máximo, costo de envío, `simulated` y fecha de consulta |

`delivery` no forma parte del mínimo producto–oferta–sede, pero el Cotizador ya lo usa. Se
conserva para que la migración no pierda plazo ni envío, siempre marcado como simulado cuando
viene de la demo.

Reglas duras:

- una oferta `publicada` exige URL de fuente;
- una oferta `demo` no puede citar una URL real y no puede afirmar stock verificado;
- la moneda debe ser la del país;
- si hay `normalized`, su importe debe ser exactamente
  `amount × (normalized.units_in_basis / presentation.units_in_basis)` y su base debe ser
  `caja`; la presentación primaria sigue siendo la que cobra el vendedor;
- `offer_id` identifica una observación fechada: cambia si cambia el importe o la fecha, que
  es lo que un consumidor debe distinguir;
- dos ofertas con el mismo contenido son un duplicado; dos ofertas del mismo SKU con distinto
  vendedor, sede, presentación o fecha son válidas.

### Disponibilidad

| `status` | Significado |
|----------|-------------|
| `verificado` | hay evidencia vigente de inventario, confirmación del vendedor o integración POS |
| `por_confirmar` | alguien afirmó existencias pero no hay evidencia admisible (o la evidencia venció) |
| `no_disponible` | hay evidencia de que no hay existencias |
| `desconocido` | la fuente no afirmó nada |

`declared_status` conserva lo que dijo la fuente (`disponible`, `insuficiente`, `agotado`,
`desconocido`). `simulated=True` nunca asciende a `verificado`.

### Registro para consumidores

`CommercialRecord` es el sobre que consumen chat y Cotizador. Los cuatro tipos comparten
**exactamente los mismos campos**:

| `record_type` | `sku` | `offer` | `availability` | Uso |
|---------------|-------|---------|----------------|-----|
| `contacto_directorio` | `null` | `null` | `null` | dónde comprar; no es una oferta |
| `oferta_publicada` | SKU | oferta real | estado del stock | precio publicado, con o sin stock |
| `oferta_demo` | SKU | oferta simulada | `por_confirmar`/`desconocido` | demo explícita, nunca real |
| `stock_verificado` | SKU | oferta opcional | `verificado` | existencia por sede con evidencia |

Campos comunes: `record_id`, `country`, `seller`, `site`, `source`, `captured_at`, `map`.

## 3. Identificadores estables

```text
CO-SEDE-<sha256(país|vendedor|nombre|ciudad|dirección_o_teléfono)[:10]>
CO-VEND-<sha256(país|nombre_normalizado)[:8]>
CO-OFERTA-<sha256(país|sku|vendedor|sede|url|presentación|unidades|importe|fecha|origen)[:12]>
```

- La normalización quita tildes, mayúsculas y espacios repetidos.
- La sede no incluye la fecha de captura, la fuente ni el teléfono en el hash cuando hay
  dirección: recapturar el directorio o cambiar el número de contacto no renombra la sede.
  La dirección es el ancla; si falta, se usa el teléfono. Cambiar dirección, ciudad, nombre o
  vendedor sí cambia el ID, y por eso se conserva `legacy_provider_id`.
- Si dos sedes producen el mismo hash (colisión), se resuelve de forma determinista ordenando
  la identidad, no el orden del archivo; la segunda recibe el sufijo `-2`.
- La oferta incluye el contenido y la fecha: dos ofertas del mismo SKU no colisionan, una fila
  repetida se detecta y una recaptura con otro importe es una observación nueva.

## 4. Consultas compartidas

```python
from carvajal.commercial import load_commercial_book

book = load_commercial_book()

# Directorio por ciudad: no exige precio ni cantidad.
bogota = book.city_directory("Bogotá")
contactos = bogota.of_type("contacto_directorio")  # 7 sedes
publicadas = bogota.of_type("oferta_publicada")  # 29 ofertas EDEXA
demo = bogota.of_type("oferta_demo")  # 29 ofertas simuladas
verificadas = bogota.of_type("stock_verificado")  # 0: aún no hay evidencia real

# Todas las ofertas de un SKU, publicadas y de demo.
ofertas = book.offers_for_sku("CO-CTT-08", "CO")  # 2 ofertas

# Solo sedes vinculadas por oferta o stock; nunca contactos generales.
sedes = book.sites_for_sku("CO-CTT-08", "CO")
```

Conteos reales del 2026-11-22:

| Consulta | Resultado |
|----------|-----------|
| `city_directory("Cali")` | 4 contactos, 0 ofertas publicadas, 16 de demo |
| `city_directory("Neiva")` | 1 contacto, 0 publicadas, 4 de demo |
| `city_directory("Bogotá")` | 7 contactos, 29 publicadas, 29 de demo |
| `cities()` | 10 ciudades |
| `offers_for_sku("CO-CTT-08", "CO")` | 2 ofertas: EDEXA publicada + Sigmaplasticos demo |
| `sites_for_sku("CO-CTT-08", "CO")` | EDEXA y Sigmaplasticos; no Sideral, que solo es contacto |

La consulta de ciudad nunca recibe precio, cantidad ni stock. Si la ciudad no existe,
devuelve `records=()` y la lista de ciudades con sede para ofrecer alternativas.

Estado real de la disponibilidad (2026-11-22): de las 160 ofertas colombianas, **145 quedan
`por_confirmar` y 15 `desconocido`; ninguna `verificado`**. Las 160 evidencias registradas son
`simulacion`, que no habilita stock. Es el resultado correcto: todavía no existe una fuente
operativa de inventario.

## 5. Disponibilidad: qué evidencia permite afirmarla

Una fila de `data/commercial/evidencia_disponibilidad.csv` es la única vía para crear
`stock_verificado`. Su formato:

```csv
sku,country,site_id,kind,declared_status,units,recorded_by,recorded_at,reference,source_url,valid_until,note
CO-CTT-08,CO,CO-SEDE-…,confirmacion_vendedor,disponible,120,Ana Pérez (EDEXA),2026-11-22,correo 2026-11-22 09:15,https://...,2026-11-29,"Confirmó 120 paquetes"
```

| `kind` | ¿Prueba stock? | Quién la registra | Cómo llega | Vigencia sugerida |
|--------|----------------|------------------|------------|-------------------|
| `inventario_vendedor` | sí | vendedor autorizado o curador | exportación de inventario con URL/folio | 7 días |
| `confirmacion_vendedor` | sí | vendedor autorizado o curador | correo, acta o formulario con nombre y fecha | 7 días y `valid_until` |
| `integracion_pos` | sí | operaciones / integración | job autenticado contra POS o ERP | 1 día |
| `pedido_confirmado` | no | operaciones | comprobante de un pedido pasado | no prueba stock actual |
| `simulacion` | no | generador de la demo | nunca se presenta como real | n/a |

Reglas:

1. Sin evidencia admisible, `status` es `por_confirmar` (o `desconocido` si la fuente no
   afirmó nada). Nunca `verificado`.
2. Una simulación se queda en `por_confirmar`; no habilita `stock_verificado`.
3. `verificado` y `no_disponible` exigen al menos una evidencia de las tres primeras filas.
4. La consulta usa la fecha de hoy por defecto (o `as_of` si se indica) y degrada a
   `por_confirmar` si la evidencia tiene más de 7 días o si `valid_until` ya pasó. El dato no
   se borra: se deja de afirmar.
5. El stock es por **sede y SKU**. El inventario de una sede no prueba el de otra, aunque
   compartan vendedor.
6. El chat y el Cotizador solo leen. No escriben evidencia, no la infieren del precio y no
   convierten una oferta publicada en stock.

**Quién actualiza cuando exista la fuente**

- **Curador comercial (hoy):** agrega la fila al CSV y la valida con `make`/pruebas; la fuente
  debe quedar en `reference` y `source_url`. Es el camino para el primer piloto con EDEXA u
  otro distribuidor.
- **Vendedor autorizado (siguiente):** portal o API autenticada que escribe la misma fila con
  `recorded_by` y `valid_until`; el validador es el mismo.
- **Operaciones / POS (siguiente):** job diario que registra `integracion_pos` con folio de
  consulta y unidades.
- **Chat / LLM:** nunca. Su contrato es de lectura.

## 6. Mapa según precisión

| `precision` | `mode` | Zoom | ¿Aproximado? | Qué ve la persona |
|-------------|--------|------|--------------|-------------------|
| `direccion` | `marcador` | 17 | no | punto de la dirección publicada |
| `via` | `marcador` | 15 | sí | vía; el número puede no ser exacto |
| `ciudad` | `zona` | 11 | sí | zona/centroide, nunca la sede exacta |
| `desconocida` | `sin_mapa` | — | — | solo texto: ciudad, dirección y teléfono |

Distribución real: 12 sedes `direccion`, 12 `via`, 5 `ciudad`. Ejemplo: EDEXA está en
`ciudad`; su mapa es una zona y la nota dice «no la sede exacta». El mapa no reemplaza
dirección ni teléfono.

Calidad de la geocodificación (auditoría del 2026-11-22):

- 29 sedes comparten **26 coordenadas únicas**; hay tres grupos con la misma coordenada:
  `p16`/`p19` (centroide de Medellín), `p24`/`p25` (misma galería en Cali) y `p27`/`p28`
  (misma dirección y teléfono en Manizales). El contrato conserva la misma `MapView`; agrupar
  marcadores es responsabilidad del consumidor, no se inventan desplazamientos.
- 7 sedes tienen un `display` de Nominatim que no coincide con la dirección publicada
  (`p01`, `p03`, `p04`, `p14`, `p17`, `p21`, `p22`). La precisión del contrato es la
  **declarada** por el geocodificador; no certifica que la coordenada sea correcta. Antes de
  usar el mapa en producción hay que revisar esas 7 sedes.
- `p19` (PAAK Medellín) no tiene dirección; su punto es el centroide de la ciudad y por eso
  el mapa es `zona`, nunca un marcador exacto.
- La tabla completa de anomalías y la recomendación de `location_id` están en la auditoría
  de la sesión; el contrato deja `legacy_provider_id` para no perder la correspondencia.

## 7. Paridad de México

La lectura inline de `referencias.csv` **no se retiró**. El contrato la expone como oferta
oficial por caja:

- 98/98 referencias auditadas con precio;
- moneda MXN en las 98;
- presentación `caja` con las mismas `units_per_box`;
- importe idéntico;
- IVA declarado por la ficha (`incluido` en las 98).

`mexico_parity()` compara cobertura, moneda, presentación, importe, IVA, URL de fuente, fecha
de captura y vigencia; también falla si hay dos ofertas MX para el mismo SKU. Solo si devuelve
`ok=True` se puede retirar la lectura inline y migrar esos precios a `precios.csv`.

```bash
uv run python -m carvajal.commercial paridad-mx
# PARIDAD OK
# audited_references=98, inline_offers=98, contract_offers=98, sin diferencias
```

## 8. Uso por consumidor

### Chat general

- Para «dónde comprar» usa `contacto_directorio` de `city_directory`; funciona sin precio.
- Para precio usa solo `oferta_publicada`; muestra fuente y fecha.
- Para existencia usa `stock_verificado` **solo** si `availability.status == "verificado"`;
  en cualquier otro caso dice «por confirmar» o «no se conoce».
- No presenta `oferta_demo` como real.

### Cotizador

- `city_directory(ciudad)` resuelve sedes sin exigir producto ni cantidad.
- `offers_for_sku(sku)` trae todas las ofertas; el Cotizador elige por presentación, no
  promedia precios ni suma monedas.
- `sites_for_sku(sku)` separa una sede que vende el SKU de un contacto general.
- La disponibilidad se muestra con `availability.status`; una oferta publicada sin evidencia
  se cotiza como «por confirmar», no como inventario confirmado.

## 9. Ejemplos para Goals 2, 4 y 5

Los archivos están en [`ejemplos/`](./ejemplos/):

| Archivo | Goal | Entrada | Salida |
|---------|------|---------|--------|
| `goal2_ofertas_co-ctt-08.json` | 2 | `{"sku":"CO-CTT-08","country":"CO"}` | 2 ofertas: una publicada y una demo, con presentación, importe, IVA, fuente y disponibilidad |
| `goal4_directorio_neiva.json` | 4 | `{"city":"Neiva"}` | 1 contacto y 4 ofertas demo; el contacto existe aunque no haya precio publicado |
| `goal4_sede_aproximada.json` | 4 | sede EDEXA | `sites`, `offers` y su registro con `map.mode="zona"` y `approximate=true` |
| `goal5_paridad_mx.json` | 5 | `mexico_parity()` | 98/98, MXN, caja, sin diferencias |
| `goal5_oferta_mx.json` | 5 | `{"sku":"2864108000","country":"MX"}` | oferta oficial por caja, MXN, IVA incluido |

Todos los ejemplos son instancias reales de `ContractDocument` y validan contra
`contrato.schema.json`; el campo opcional `query` documenta la consulta que los produjo.
Ejemplo mínimo de una oferta publicada:

```json
{
  "record_type": "oferta_publicada",
  "sku": "CO-CTT-08",
  "seller": {"seller_id": "CO-VEND-…", "name": "EDEXA"},
  "site": {"site_id": "CO-SEDE-…", "city": "Bogotá"},
  "offer": {
    "presentation": {"basis": "paquete", "units_in_basis": 20},
    "amount": "17642",
    "normalized": {"basis": "caja", "units_in_basis": 20, "amount": "17642"},
    "currency": "COP",
    "tax_status": "incluido",
    "source": {"name": "EDEXA", "captured_at": "2026-11-18"}
  },
  "availability": {"status": "por_confirmar", "declared_status": "disponible", "simulated": true}
}
```

Ejemplo mínimo de contacto del directorio:

```json
{
  "record_type": "contacto_directorio",
  "sku": null,
  "offer": null,
  "availability": null,
  "site": {"site_id": "CO-SEDE-…", "city": "Cali", "phone": "…"}
}
```

## 10. Validación y archivos

| Archivo | Rol |
|---------|-----|
| `packages/core/src/carvajal/commercial/models.py` | entidades, registros, validadores |
| `packages/core/src/carvajal/commercial/book.py` | uniones, consultas, error de huérfanos |
| `packages/core/src/carvajal/commercial/loader.py` | carga del catálogo, demo, IDs y evidencia |
| `packages/core/src/carvajal/commercial/parity.py` | paridad MX |
| `packages/core/src/carvajal/commercial/__main__.py` | consulta CLI y JSON Schema |
| `data/commercial/evidencia_disponibilidad.csv` | evidencia versionada (hoy solo cabecera) |
| `docs/contratos/comerciales/contrato.schema.json` | JSON Schema generado |
| `tests/test_commercial_contract.py` | 36 pruebas de aceptación |

```bash
uv run pytest tests/test_commercial_contract.py -q     # 36 passed
uv run ruff check packages/core/src/carvajal/commercial tests/test_commercial_contract.py
uv run python -m carvajal.commercial validar
uv run python -m carvajal.commercial sedes --ciudad "Bogotá"
uv run python -m carvajal.commercial ofertas --sku CO-CTT-08 --json
uv run python -m carvajal.commercial paridad-mx
```

## 11. Límites conocidos (no son aceptación silenciosa)

- `seller_id` agrupa por nombre exacto: 24 nombres colombianos + 1 vendedor oficial de México
  = 25 vendedores. Las variantes «Kive Andina», «Kive Eje Cafetero», «Kive Andina (distribuidor
  directo)» y las cuatro «Representaciones Lastra…» quedan separadas hasta que exista una
  tabla de alias confirmada por el negocio. No se fusionan por heurística.
- `p27`/`p28` comparten dirección y teléfono; `p24`/`p25` comparten galería; `p16`/`p19`
  comparten centroide. El contrato conserva un `site_id` por sede y la misma `MapView`; agrupar
  marcadores o introducir un `location_id` es trabajo del consumidor/UI.
- 7 sedes tienen un `display` de geocodificación que no coincide con la dirección publicada
  (`p01`, `p03`, `p04`, `p14`, `p17`, `p21`, `p22`). La precisión es la declarada por el
  geocodificador, no una certificación; hay que revisarlas antes de producción.
- `p19` (PAAK Medellín) no tiene dirección; su mapa es una zona de ciudad, no un marcador.
- La lectura de México sigue inline en `referencias.csv`; no se retira hasta que
  `mexico_parity()` esté en verde en dos corridas y el negocio apruebe la migración.
- La evidencia real está vacía. `stock_verificado` = 0 es el estado correcto, no un fallo de
  carga.

## 12. Coordinación con otros agentes

Esta capa se agregó como **archivos nuevos** para no interferir con las sesiones paralelas
(`(G1)` y las demás del 2026-11-22). No se modificaron `CONSTITUTION.md`, `AGENTS.md`,
`Makefile`, `pyproject.toml`, el chat ni el Cotizador. Los únicos archivos compartidos que se
tocaron son nuevos: el paquete `carvajal.commercial`, sus datos, sus pruebas y esta
documentación. La integración con cada consumidor queda como cambio localizado de ese
consumidor.
