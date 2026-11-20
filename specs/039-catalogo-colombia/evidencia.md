# Evidencia de los criterios de éxito — spec 039 (T043)

Estado al 2026-11-18. Fuentes capturadas ese día: fichas oficiales de
`colombia.carvajalempaques.com`, la tienda de EDEXA (`tiendaedexa.com`) y la tienda de
`mexico.carvajalempaques.com`.

| Criterio | Estado | Evidencia |
|---|---|---|
| **SC-001** 100 % de las referencias con destino; ≥ 90 % en el catálogo | ⚠️ 100 % con destino, **86 %** en el catálogo | Las 47 fichas oficiales suman 109 tallas. Entran 92 con los datos de la ficha y 2 (vasos de 3,5 y 7 oz) como presentación de EDEXA; quedan fuera 15, cada una con su motivo en [`fuentes/descartes_co.json`](../../data/catalog/empaques/fuentes/descartes_co.json): unidades ambiguas en la ficha («960/1000», «50/32»), material mixto o datos incoherentes. No se adivinó ningún dato para subir el porcentaje. Además entran 7 productos de EDEXA que no están en las fichas (contenedores con tapa, tapas PET, vaso Cañatek, copa salsera). `tests/test_sc_039.py::test_sc001_*`. |
| **SC-002** 0 % de recomendaciones para Colombia con referencias de otro país o que violen una regla de material | ✅ Cumple | `tests/test_sc_039.py::test_sc004_*` comprueba que todas las opciones de las 16 preguntas cotizables son de Colombia y que no aparece MXN; `tests/test_routing_eval_set.py::test_ninguna_recomendacion_viola_la_regla_de_material` corre la secuencia real sobre el grupo `colombia`; `eval-router`: 0 violaciones en 130 preguntas. |
| **SC-003** 100 % de los precios de Colombia con COP, IVA, fecha y fuente | ✅ Cumple | `tests/test_sc_039.py::test_sc003_*` recorre las 29 ofertas reales; `ReferencePrice` rechaza un precio de Colombia que no esté en COP o sin fuente (`tests/test_catalog_prices.py`). Las tarjetas muestran fuente, ciudad, etiqueta «Tercero» e IVA (`tests/test_productos.py`). |
| **SC-003a** 0 precios estimados o convertidos | ✅ Cumple | No hay código de conversión. `test_sin_precio_colombiano_no_se_convierte_el_de_mexico` (T029) y las 6 preguntas sin precio del grupo `colombia` declaran la ausencia y no muestran MXN. |
| **SC-004** 100 % de totales correctos en ≥ 20 preguntas de Colombia | ✅ Cumple | 20 preguntas nuevas en `data/eval/routing_questions.json` (grupo `colombia`): 10 con total en COP, 6 con ausencia de precio y 4 aclaraciones. Ejemplo: 5.000 vasos de 9 oz → 2 cajas de 2.750 × $206.305 = **$412.610 COP** (EDEXA, IVA incluido). `tests/test_sc_039.py::test_sc004_*`. |
| **SC-005** Sin regresión en México, catálogo y enrutador | ✅ Cumple | Las 52 referencias de México previas no cambian (148 líneas agregadas y 0 borradas en `referencias.csv`); su precio sigue inline y se expone como oferta oficial (`test_cada_referencia_de_mexico_con_precio_tiene_una_oferta_oficial`). Las 110 preguntas previas conservan ruta e intención. Suite completa sin Docker: unas 1.490 pruebas en verde. |

## Ampliación de México

La tienda de México publica 102 productos; el corpus de la 036 tenía 55 fichas. Con
`scripts/ampliar_catalogo_mx.py`, que usa el mismo `parse_mx_product` de `catalog-build`,
entran **46 referencias nuevas** (de 52 a 98). Quedan fuera 2 combos, 1 plato sin unidades por
caja y 1 plato sin material reconocible. «Cartón» se añadió como sinónimo de papel en el
parser.

## Corridas

```bash
uv run python scripts/curar_catalogo_co.py     # 102 referencias CO · 29 precios · 16 descartes
uv run python scripts/ampliar_catalogo_mx.py   # 46 referencias nuevas de México
uv run carvajal catalog-prices --country CO    # 29 publicables · 0 descartados
uv run carvajal eval-router --router rules     # 130 preguntas · 100 % ruta · 0 violaciones
```

Las tres primeras órdenes son idempotentes: dos corridas seguidas dejan los mismos bytes.

## Pendientes

- **SC-001**: subir del 86 % exige que Carvajal aclare las fichas ambiguas o publique el
  catálogo PDF de Colombia; con el PDF se completan los 15 descartes.
- **Más fuentes de precio**: solo EDEXA, del directorio oficial, publica precios en línea.
  Otras tiendas que venden Carvajal (Fonina, Papyser) no están en el directorio; incluirlas es
  una decisión del equipo.
- **Docker**: las pruebas con Postgres (testcontainers) corren en el CI.
