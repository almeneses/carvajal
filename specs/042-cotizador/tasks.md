# Tasks: Cotizador conversacional de productos Carvajal

**Input**: [spec.md](./spec.md), [plan.md](./plan.md), [data-model.md](./data-model.md),
[contracts/cotizador.md](./contracts/cotizador.md)

**Tests**: incluidas como parte de cada historia; la spec exige verificación de dinero,
existencias y entrega.

## Phase 1: Setup

- [x] T001 Crear `packages/core/src/carvajal/quotation/` con `__init__`, `data`, `calendar` y `engine`
- [x] T002 Confirmar que no se añaden dependencias: FastAPI, Jinja2, HTMX, SQLAlchemy y `pytest` ya existen

## Phase 2: Foundational

- [x] T003 Cargar `data/demo/cotizador/*.csv` con validación de uniones (`quotation/data.py`)
- [x] T004 Implementar festivos de Colombia y ventana en días hábiles (`quotation/calendar.py`)
- [x] T005 Implementar el motor determinista y sus estados de turno (`quotation/engine.py`)

## Phase 3: US1 — Cotizar (P1) 🎯 MVP

- [x] T006 Reutilizar `extract_commercial` y `check_material` para producto, uso y material
- [x] T007 Calcular presentaciones, unidades comprables y sobrantes con aritmética entera
- [x] T008 Calcular subtotal, precio unitario y total con `Decimal`, mostrando la fecha del precio sin caducidad
- [x] T009 Elegir la oferta más económica por producto y hasta dos alternativas con su diferencia
- [x] T010 Excluir ofertas agotadas y advertir existencia insuficiente o desconocida
- [x] T011 Pruebas del motor en `tests/test_quotation_engine.py`
- [x] T012 Pruebas del calendario en `tests/test_quotation_calendar.py`

## Phase 4: US2 — Datos reales, ausentes y simulados (P1)

- [x] T013 Marcar la procedencia simulada en cada tarjeta, sin aviso general (sustituida por T054)
- [x] T014 Marcar precio simulado, producto de demostración y existencia simulada en las tarjetas
- [x] T015 Declarar ausencia de precio, sede o entrega sin estimar ni convertir
- [x] T016 Pruebas de referencia simulada, sellos por dato y precio antiguo usado como normal (`test_quotation_engine.py`, `test_api_cotizador.py`)
- [x] T054 Retirar el aviso general de demo y conservar los sellos por dato

## Phase 5: US3 — Mapa y sedes (P2)

- [x] T017 Unir oferta y sede con `provider_id`; nunca por nombre
- [x] T018 Listar sedes con dirección, teléfono, precisión de geocodificación, `source_url` y fecha
- [x] T019 Preparar la lista de sedes y los enlaces de respaldo a OpenStreetMap; el iframe se implementa en T055
- [x] T020 Mantener la lista textual cuando no hay coordenadas o el mapa no carga
- [x] T055 Mapa incrustado de OpenStreetMap con `frame-src` acotado y enlace de respaldo

## Phase 6: US4 — Domicilio (P2)

- [x] T021 Detectar la solicitud de domicilio y su retiro en el hilo
- [x] T022 Calcular envío con umbral de gratuidad y ventana con corte a las 15:00, festivos y días de atención del proveedor
- [x] T023 Pedir la ciudad antes de estimar; sin cobertura, ofrecer retiro
- [x] T024 Pruebas de envío, umbral, ventana y día sin atención (`test_quotation_engine.py`)

## Phase 7: US5 — Refinar y comparar (P3)

- [x] T025 Heredar producto, cantidad y ciudad de los turnos anteriores
- [x] T026 Recalcular ante «mejor N», cambio de ciudad y cambio de domicilio
- [x] T027 Derivar al chat general la consulta ajena al catálogo

## Phase 8: US6 — Memoria silenciosa (P2)

- [x] T047 Extracción por reglas del perfil (`quotation/memory.py`)
- [x] T048 Tabla `visitor_memory` y migración 0008
- [x] T049 Repositorio `SqlVisitorMemory` con caducidad y borrado
- [x] T050 Aplicar ciudad, domicilio, nombre y preferencia renovable en el motor
- [x] T051 Memoria silenciosa en las rutas normal y SSE, sin escribirla en el payload
- [x] T052 Pruebas de extracción, aplicación, hilo nuevo, olvido y caducidad

## Phase 9: US7 — Cotización auditable (P2)

- [x] T056 Leer los productos reales del catálogo curado y dejar en la demo solo la extensión simulada
- [x] T057 Generar una ficha Markdown por oferta y servirla en `/cotizador/ficha/<slug>`
- [x] T058 Registrar cada cotización en `quotes` con identificador, vigencia, total y payload
- [x] T059 Casos dorados de cotización con totales esperados (`data/eval/cotizacion_cases.json`), incluido el saludo que no repite
- [x] T060 Mostrar `#id` y vigencia en la tarjeta, y enlazar la ficha desde la oferta

## Phase 10: Web y streaming

- [x] T028 Ruta `GET /cotizador` con hilo y panel de hilos
- [x] T029 Ruta `POST /cotizador` con CSRF, cuota, longitud y errores en HTML
- [x] T030 Ruta `POST /cotizador/nuevo` con redirección al hilo
- [x] T031 Aislar los hilos con `cot:<owner_key>` en `SqlCotizadorStore`
- [x] T032 Plantillas `cotizador.html`, `_cotizador_turno.html` y `_cotizacion.html`
- [x] T033 Script `cotizador.js` para enviar el turno y añadir el bloque al hilo
- [x] T034 Estilos `.coti*` con los tokens del mockup institucional y ajuste móvil
- [x] T035 Pestaña «Cotizador» en la navegación
- [x] T036 Pruebas HTTP en `tests/test_api_cotizador.py`
- [x] T042 Ruta `POST /cotizador/stream` con `open`, `token`, `products`, `done` y `error`
- [x] T043 Render de las tarjetas en el evento `products` y persistencia única antes de `done`
- [x] T044 Cliente SSE en `cotizador.js` con respaldo al POST normal
- [x] T045 Pruebas del stream: eventos, CSRF, cupo y persistencia
- [x] T046 Stream interrumpido: `test_el_stream_avisa_si_la_persistencia_falla_a_mitad`

## Phase 11: Cierre

- [x] T037 Actualizar `specs/README.md` y la constitución con la ruta y el contrato
- [ ] T038 Probar a 390 px y 1440 px, con teclado y con un lector de pantalla
- [x] T040 El conjunto demo viaja por el volumen `appdata` (`compose.prod.yml` monta `./data:/seed` y el bootstrap lo copia); la imagen no lo necesita
- [x] T041 Regresión de la navegación del chat: `test_el_chat_conserva_su_navegacion_y_suma_el_cotizador`
- [ ] T053 Documentar con el owner la retención vigente de 180 días y el texto de borrado
- [ ] T061 Verificar SC-007: una persona completa la cotización de ejemplo en menos de 3 minutos, sin instrucciones externas
- [ ] T062 Regresión explícita del chat general (SSE, hilos y cuota) y del flujo comercial de la spec 036

## Phase 12: Diferido a una spec posterior

- [ ] T039 Reutilizar el dictado por voz en `/cotizador`; no forma parte de la entrega 042
