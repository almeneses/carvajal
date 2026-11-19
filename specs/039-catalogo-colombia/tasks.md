# Tasks: Catálogo de empaques de Colombia con precios de referencia

**Spec**: [`spec.md`](./spec.md) · **Plan**: [`plan.md`](./plan.md) · **Estado**: pendiente

## Format: `[ID] [P?] [Story] Description`

- **[P]**: puede ejecutarse en paralelo (archivos distintos)
- **[USn]**: historia de usuario de la spec
- **PR n**: agrupación sugerida en Pull Requests a `dev`; los CSV curados se presupuestan
  aparte como datos

## Phase 1: Setup (infraestructura y fuentes)

- [ ] T001 Crear `data/catalog/empaques/fuentes/` con un `README.md` que documente el nombre
      esperado del PDF (`catalogo_co.pdf`), su fecha, su hash y que no se versiona por tamaño;
      dejar `.gitkeep` para que el directorio exista
- [ ] T002 [P] Identificar las tiendas de distribuidores y comercios con precios publicados en
      Colombia, revisar su `robots.txt` y anotar cuáles se pueden capturar y cuáles se curan a
      mano (FR-011)
- [ ] T003 [P] Ampliar `tests/fixtures/catalog/` con dos referencias CO y tres ofertas
      (oficial, tercero y sin IVA indicado)
- [ ] T004 Añadir a `catalog/models.py` `TaxStatus`, `PriceSource` y `ReferencePrice` con
      validación de moneda, IVA y procedencia

## Phase 2: Fundacional (bloquea las historias)

**PR 1 — Modelo de ofertas y carga**

- [ ] T005 `catalog/loader.py`: leer `precios.csv` si existe; construir `Catalog.prices`; una
      referencia sin ofertas conserva su precio inline
- [ ] T006 [P] `tests/test_catalog_prices.py`: una fila inválida detiene la carga con archivo y
      línea; una oferta sin fuente, fecha o IVA se rechaza; un precio negativo se rechaza
- [ ] T007 [P] `tests/test_catalog_loader.py`: si una referencia tiene precio inline y ofertas,
      gana `precios.csv` y el manifiesto lo advierte
- [ ] T008 Añadir al `manifest.json` la sección `precios` con conteos, fuentes, fecha de captura
      y descartes; validar el manifiesto al cargar

**PR 2 — Migración de los precios de México**

- [ ] T009 Generar `data/catalog/empaques/precios.csv` con los 52 precios MX actuales como
      `source_type=oficial`, `source_name=Carvajal Empaques México`, `tax_status` derivado de
      `tax_included` y la misma fecha/vigencia
- [ ] T010 Vaciar las columnas de precio de las filas MX en `referencias.csv` y comprobar que
      `quote`/`_option` siguen dando el mismo total, moneda, aviso y fecha
- [ ] T011 [P] `tests/test_commercial_quote.py`: regresión de los 52 precios MX con cantidades
      de la 036; los totales y avisos no cambian
- [ ] T012 [P] `tests/test_catalog_data.py`: toda referencia MX con precio tiene exactamente una
      oferta oficial y no hay filas huérfanas en `precios.csv`

**Checkpoint**: el catálogo MX funciona igual con el nuevo modelo de ofertas.

## Phase 3: US1 — Referencias que se venden en Colombia (P1) 🎯 MVP

**PR 6 — Datos curados CO (puede empezar en paralelo si hay fuente)**

- [ ] T013 [US1] Transcribir las referencias foodservice del catálogo PDF a `referencias.csv`
      con país CO, `source_url` del documento y `captured_at`; las columnas de precio quedan
      vacías
- [ ] T014 [US1] Convertir capacidades en ml/cc a onzas y validar familia, material y unidades
      por caja; cada fila dudosa se marca como curada a mano en el manifiesto
- [ ] T015 [US1] Verificar que `search_packaging` con `country=CO` solo devuelve referencias CO y
      que `check_material` aplica las mismas reglas que en México
- [ ] T016 [P] [US1] `tests/test_agent_commercial_flow.py`: una solicitud CO nunca devuelve una
      referencia MX; sin referencias CO, el resultado trae `catalog_note` y distribuidores
- [ ] T017 [P] [US1] `tests/test_catalog_data.py`: cobertura del PDF (100 % con destino, ≥90 %
      en catálogo), SKU único por país y material presente en la guía

## Phase 4: US2 — Precio de referencia en COP (P1)

**PR 3 — Regeneración de precios**

- [ ] T018 [US2] `catalog/prices.py`: normalizar precio por unidad, paquete o caja a precio por
      caja con `units_per_box`; si no se puede, descartar con motivo (FR-004)
- [ ] T019 [US2] `carvajal catalog-prices --country CO`: escribir `precios.csv` y el manifiesto
      de forma atómica, idempotente y con diferencias (agregadas, cambiadas, descartadas)
- [ ] T020 [P] [US2] `tests/test_catalog_prices.py`: dos corridas idénticas producen el mismo
      archivo; un precio cambiado deja el valor anterior y el nuevo en el manifiesto
- [ ] T021 [P] [US2] `tests/test_catalog_prices.py`: sin precio colombiano no hay oferta; una
      referencia CO nunca usa un precio MX; varias fuentes se conservan y no se promedian
- [ ] T022 [US2] Incorporar los precios capturados de distribuidores a
      `data/catalog/empaques/fuentes/precios_co.csv` con fuente, fecha, IVA y URL; los que no
      normalizan quedan en el manifiesto

**PR 4 — Cotización y canales**

- [ ] T023 [US2] Refactorizar `quote` sobre `quote_offer(price, boxes, today)` para compartir
      cálculo, IVA, vigencia y avisos entre MX y CO
- [ ] T024 [US2] `quote_offer` debe producir la nota de IVA (incluido / sin IVA / no indicado),
      el aviso de precio de referencia sin existencias y el aviso de vigencia vencida
      (FR-004, FR-007)
- [ ] T025 [US2] `_option` arma `offers` por referencia (todas las fuentes) y calcula total por
      oferta si hay cantidad; sin ofertas, deja `price_note` con el motivo
- [ ] T026 [US2] `commercial_sources` cita la fuente de cada oferta, con su fecha, no solo la
      ficha del producto
- [ ] T027 [US2] `tests/test_commercial_quote.py`: COP con IVA incluido, sin IVA y no indicado;
      total = cajas × precio; aviso >90 días; sin precio no se estima ni se convierte
- [ ] T028 [P] [US2] `tests/test_sc_039.py`: SC-003, SC-003a y SC-004 con preguntas de Colombia
- [ ] T029 [P] [US2] Prueba explícita de no conversión: referencia CO sin precio y referencia MX
      equivalente con precio; la CO declara ausencia y no muestra el MXN

## Phase 5: US3 — Distribuidores y origen del precio (P2)

- [ ] T030 [US3] Si el precio vino de un distribuidor, incluir su nombre y ciudad en la oferta y
      enlazarlo con el directorio de la ciudad (FR-005, FR-010)
- [ ] T031 [US3] Mantener el canal CO actual: ciudad con distribuidores, ciudades alternativas
      y canal nacional; no exponer nombres de personas ni correos personales
- [ ] T032 [P] [US3] `tests/test_agent_commercial_flow.py`: Medellín lista sus distribuidores;
      Tuluá ofrece las ciudades publicadas; una oferta de tercero nombra al tercero

## Phase 6: US4 — Regeneración reproducible (P3)

- [ ] T033 [US4] Registrar en el manifiesto el origen de cada fila (automática, curada a mano,
      descartada) y el motivo del descarte (FR-009)
- [ ] T034 [US4] Si una tienda prohíbe la captura o no publica precio, no capturarla y anotarlo
      en el manifiesto (FR-011)
- [ ] T035 [P] [US4] `tests/test_catalog_build.py`: idempotencia, diferencia de manifiesto al
      cambiar un precio y descarte por fila incompleta
- [ ] T036 [P] [US4] Prueba de que `catalog-prices` no deja temporales ni escribe si la
      validación falla (escritura atómica)

## Phase 7: UI, evaluación y cierre

**PR 5 — Tarjetas**

- [ ] T037 [US2] `_productos.html`: lista de ofertas por referencia con precio, moneda, IVA,
      fecha, fuente y vigencia; sin ofertas, el aviso de ausencia y distribuidores
- [ ] T038 [P] [US2] `tests/test_productos.py`: render de dos ofertas, IVA no indicado, precio
      vencido, referencia sin precio y ausencia de etiquetas de estimación
- [ ] T039 [US1] Verificar que las tarjetas de una referencia CO no muestran precios MX ni una
      “mejor oferta” sin fuente

**PR 7 — Evaluación y evidencia**

- [ ] T040 [US2] Añadir 20 preguntas CO a `data/eval/routing_questions.json` (grupo `colombia`)
      con país, ciudad, referencia esperada y total en COP o ausencia declarada
- [ ] T041 [US2] `tests/test_routing_eval_set.py`: las preguntas CO conservan ruta comercial,
      no violan material y aciertan el total; las 110 preguntas MX no cambian
- [ ] T042 [P] Ejecutar `carvajal eval-router` y anotar precisión, totales y violaciones en
      `docs/resultados-router.md`
- [ ] T043 Crear `specs/039-catalogo-colombia/evidencia.md` con cada SC y su prueba o corrida
- [ ] T044 Actualizar `CONSTITUTION.md` §5, §9 y `.specify/memory/constitution.md` con la
      procedencia por precio y la regla de no estimar ni convertir
- [ ] T045 Ejecutar `make check` y una prueba manual de una cotización CO con cantidad, IVA y
      distribuidores; registrar el resultado

## Dependencies & Execution Order

- **Fase 1 → Fase 2**: sin el modelo de ofertas y la carga no se puede migrar ni cotizar.
- **PR 2 (MX)** debe cerrar antes de tocar la cotización CO para no mezclar regresiones.
- **US1 (referencias CO)** y **US2 (precios)** pueden avanzar en paralelo: una es datos de
  producto, la otra modelo/cotización; se integran en el PR de datos.
- **US3** depende de US2 (la oferta debe existir para nombrar al tercero).
- **US4** depende de PR 1 y 3; puede ir después de US2.
- **UI y evaluación** van al final, cuando los datos CO estén cargados.

## Parallel Opportunities

- T001, T002 y T003 pueden ir en paralelo con la localización del PDF.
- T013–T017 (datos CO) pueden trabajar en paralelo con T018–T019 (motor de precios) si se
  acuerda el esquema de `precios.csv` antes de empezar.
- T027, T028, T035 y T036 son pruebas de archivos distintos y pueden ir en paralelo.
- T040 y T042 pueden prepararse mientras se cura el último lote de datos.

## Implementation Strategy

1. **MVP de datos**: PR 1 + PR 2 + PR 6 (referencias CO) → el asistente ya recomienda solo CO,
   sin precios; declara la ausencia cuando no hay precio.
2. **Precios**: PR 3 + PR 4 → cotización COP con ofertas publicadas.
3. **Experiencia**: PR 5 → tarjetas.
4. **Cierre**: PR 7 → evaluación, evidencia y constitución.

Cada PR deja `make check` en verde; los PRs de datos se revisan con muestra cruzada contra el
PDF y no cuentan contra el aviso de tamaño del CI.
