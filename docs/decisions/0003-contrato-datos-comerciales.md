# ADR-0003 — Contrato de datos comerciales compartido

- **Fecha:** 2026-11-22
- **Estado:** propuesto (implementación de referencia validada; adopción por chat y Cotizador
  pendiente)
- **Contexto:** chat general, Cotizador, catálogo curado y demo comercial.

## Contexto

La auditoría del 2026-11-22 encontró 102 referencias colombianas, 29 precios publicados por un
solo vendedor en Bogotá y 29 sedes en 10 ciudades. El directorio aporta contactos y
direcciones; los precios publicados aportan evidencia del vendedor; ninguno de los dos prueba
existencia física. En el código, producto, oferta y sede vivían en diccionarios y CSV con
formas distintas:

- `catalog.models.Reference` mezcla la ficha con un precio inline opcional (México).
- `catalog.models.ReferencePrice` normaliza a caja, pero no conserva la presentación publicada.
- `quotation.data.Offer` mezcla precio, stock, envío y proveedor; su `provider_id` es el orden
  del archivo (`p01…p29`).
- El chat usa `Distributor` del directorio, sin precio ni stock.
- La disponibilidad de la demo es 100 % simulada y algunas filas simuladas citaban una URL real
  de EDEXA.

Sin un contrato común, cada consumidor decide por su cuenta qué significa «disponible», qué
identifica una sede y si un contacto del directorio es una oferta.

## Decisión

Adoptar una capa de contrato versionada, `carvajal.commercial`, con:

1. **Producto, oferta y sede separados.** El producto no tiene precio ni stock; la oferta no
   prueba existencia; la sede no tiene precio.
2. **Identificadores estables por contenido** (`CO-SEDE-…`, `CO-VEND-…`, `CO-OFERTA-…`) y
   `legacy_provider_id` para migrar `p01…p29` sin romper la demo.
3. **Presentación explícita** (`unidad`, `paquete`, `caja`, `units_in_basis`): la primaria
   es la que cobra el vendedor y `normalized` conserva la vista a caja cuando es divisible.
4. **Disponibilidad con evidencia.** `verificado` solo con inventario del vendedor,
   confirmación del vendedor o integración POS, vigente; sin evidencia es `por_confirmar`.
   Una simulación nunca asciende a `verificado`.
5. **Registro único** `CommercialRecord` con `record_type`: `contacto_directorio`,
   `oferta_publicada`, `oferta_demo` o `stock_verificado`, con los mismos campos para todos.
6. **Consulta de sedes por ciudad** independiente de precio y cantidad.
7. **Mapa según precisión**: marcador para dirección/vía, zona para ciudad, sin mapa para
   desconocida.
8. **Paridad de México**: la lectura inline de las 98 referencias se conserva y se expone como
   oferta oficial; `mexico_parity()` demuestra cobertura, MXN, caja, importe e IVA antes de
   retirarla.

## Alternativas descartadas

- **Seguir usando los diccionarios de la demo:** no permite que el chat liste sedes sin precio
  ni que el Cotizador distinga contacto de oferta.
- **Migrar todo a `ReferencePrice`:** normaliza a caja y pierde la presentación publicada; no
  tiene vendedor, sede ni disponibilidad.
- **Unificar disponibilidad dentro de `Offer`:** repite el error actual; una oferta publicada
  no prueba stock y un stock verificado puede existir sin precio publicado.
- **Retirar ya el precio inline de México:** no hay comparación de paridad ni migración a
  `precios.csv`; se conserva hasta que `mexico_parity()` esté en verde.

## Consecuencias

- **Positivas:** un solo contrato para chat y Cotizador; errores de unión visibles al cargar;
  sedes consultables sin precio; disponibilidad honesta; IDs estables; México sin regresión.
- **Costos:** una capa nueva que hay que adoptar en cada consumidor; la evidencia real todavía
  no existe (hoy `stock_verificado` es 0); el CSV de evidencia necesita gobierno y validación.
- **Siguiente paso:** conectar `city_directory` al chat y `offers_for_sku`/`sites_for_sku` al
  Cotizador, sin cambiar sus contratos de UI; cargar la primera evidencia real de un vendedor.

## Evidencia

- `docs/contratos/comerciales/README.md` — contrato completo, ejemplos y actualización.
- `docs/contratos/comerciales/contrato.schema.json` — JSON Schema generado.
- `tests/test_commercial_contract.py` — 39 pruebas de aceptación.
- `uv run python -m carvajal.commercial validar` — 248 productos, 29 sedes, 10 ciudades,
  258 ofertas, 0 stock verificado.
- `uv run python -m carvajal.commercial paridad-mx` — `PARIDAD OK` (98/98).
