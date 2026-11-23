# Implementation Plan: Cotizador conversacional de productos Carvajal

> **Histórico:** la demo y sus archivos fueron retirados por la [spec 043](../043-chat-comercial-unificado/spec.md). Este plan describe la entrega original de la 042; las referencias a `data/demo/cotizador/` ya no existen en el árbol actual.

**Branch**: `main` (integración histórica desde `prototype`) | **Date**: 2026-11-21 | **Updated**: 2026-11-23 | **Spec**: [spec.md](./spec.md)

**Input**: Spec 042 implementada, con aclaraciones cerradas el 2026-11-21 y el
conjunto de demostración de [`docs/plans/0002-cotizador-preliminar.md`](../../docs/plans/0002-cotizador-preliminar.md).

## Summary

Añadir una sección `/cotizador` con la misma conversación del chat, dedicada a cotizar
productos de Carvajal Empaques en Colombia. El motor es determinista: reutiliza la
extracción de intención comercial de la spec 036 y la validación de materiales, y calcula
presentaciones, dinero, existencias, sedes, mapa y ventana de entrega con los datos de
`data/demo/cotizador/`. La respuesta se pinta **inline dentro del hilo**, primero como
resumen en texto y después como tarjetas, lista de sedes con mapa esquemático y entrega.
No hay LLM en la ruta: la cotización es aritmética y las cifras nunca las inventa un
modelo.

## Technical Context

**Language/Version**: Python 3.13 (proyecto con `uv`).

**Primary Dependencies**: FastAPI + Jinja2 + HTMX (ya instalados), SQLAlchemy Core con
PostgreSQL para el historial, `pytest` para pruebas. Sin dependencias nuevas: el mapa es un
iframe de OpenStreetMap y las fechas usan `datetime`/`zoneinfo`.

**Storage**: PostgreSQL para conversaciones y mensajes, reutilizando las tablas actuales
con un espacio de nombres propio del dueño (`cot:<owner_key>`), sin migración. El
conjunto comercial vive en `data/demo/cotizador/*.csv` y se carga una vez por proceso.

**Testing**: `pytest` (patrón AAA). Pruebas del motor (extracción, presentaciones, dinero,
calendario, existencias), del render (parcial HTML) y de la vista (GET/POST, CSRF, cuota,
aislamiento de hilos).

**Target Platform**: la app web única en `:8000` (Docker `runtime`), escritorio y móvil.

**Project Type**: aplicación web con paquete de dominio (`packages/core`) y vistas
(`apps/api`).

**Performance Goals**: una cotización se resuelve en memoria sobre 150 productos y 160
ofertas; no hay red ni LLM en el camino. El cálculo queda por debajo de 50 ms en el VPS.

**Constraints**: CSP `self` con `frame-src` limitado a `https://www.openstreetmap.org`,
CSRF por doble cookie, cuota diaria, sin PII en trazas, fechas en días hábiles con festivos
de Colombia.

**Scale/Scope**: 150 productos CO, 160 ofertas, 29 sedes; un hilo con historial de hasta
50 mensajes como el chat.

## Constitution Check

*GATE: revisado antes de la fase 0 y otra vez al cerrar el diseño.*

| Principio | Cumplimiento |
|---|---|
| Verdad en los datos | Los precios reales se copian de EDEXA; las 98 referencias de México solo documentan el origen de las 48 simuladas y no se cotizan. Lo simulado se marca fila por fila (`simulated`, `origin`) y la sección avisa. No se convierte moneda ni se estima un precio ausente con otro país. |
| Cita y procedencia | Cada oferta enlaza su fuente; las sedes enlazan el directorio oficial y su precisión de geocodificación. |
| Capas separadas | `data/demo/` no entra en Qdrant ni en el corpus; el motor comercial no escribe vectores. |
| Sin LLM donde hay aritmética | La cotización no llama al modelo: cantidades, totales y plazos son deterministas. |
| Seguridad | Se conservan CSRF, cuota, identidad anónima y saneamiento; el mapa no carga recursos externos. |
| Un solo camino de respuesta | El chat general no cambia; el Cotizador es una ruta adicional con su propio espacio de hilos. |
| Constitución al día | §2 y §9 registran la propuesta; al entregar se actualizan con la ruta y el contrato. |

Sin violaciones que requieran justificación en *Complexity Tracking*.

## Project Structure

### Documentation (feature)

```text
specs/042-cotizador/
├── spec.md              # contrato de usuario
├── plan.md              # este archivo
├── research.md          # decisiones técnicas de la fase 0
├── data-model.md        # entidades y CSV demo
├── quickstart.md        # cómo levantarlo y probarlo
├── contracts/
│   └── cotizador.md     # contrato HTTP/HTML y forma del payload
├── checklists/
│   └── requirements.md
└── tasks.md             # fase 2 (/speckit.tasks)
```

### Source Code

```text
packages/core/src/carvajal/quotation/
├── __init__.py
├── data.py          # carga de data/demo/cotizador/*.csv y aviso de demo
├── calendar.py      # festivos de Colombia y ventana en días hábiles
├── memory.py        # extracción por reglas del perfil del visitante
├── evaluation.py    # casos dorados con totales esperados
└── engine.py        # solicitud → productos → ofertas → cotización

apps/api/views/cotizador.py     # GET /cotizador, POST /cotizador
apps/api/templates/
├── cotizador.html              # shell del chat en modo cotización
├── _cotizador_turno.html       # dos mensajes del turno (historial y respuesta)
├── _cotizacion.html            # tarjetas, sedes, mapa OSM y entrega (inline)
└── base.html                   # pestaña nueva en la navegación
apps/api/static/cotizador.js    # envía el turno y añade el bloque al hilo
packages/core/src/carvajal/adapters/storage/visitor_memory.py  # perfil por visitante
packages/core/src/carvajal/adapters/storage/quotes.py          # cotizaciones emitidas
migrations/versions/0008_visitor_memory.py
migrations/versions/0009_quotes.py
data/demo/cotizador/fichas/                                    # ficha citable por oferta
data/eval/cotizacion_cases.json                                # casos dorados
scripts/evaluar_cotizador.py

tests/test_quotation_engine.py
tests/test_quotation_calendar.py
tests/test_api_cotizador.py
```

## Phase 0 — Research

Ver [research.md](./research.md). Decisiones que fijan el resto del plan:

1. **Motor determinista, sin LLM.** Reutiliza `extract_commercial` y `check_material`;
   el LLM no interviene porque no debe poder alterar cifras.
2. **Mapa incrustado de OpenStreetMap.** `frame-src` permite solo ese origen; el mapa se
   centra en la sede de la oferta y la lista textual queda como respaldo. Sin clave de API
   ni geolocalización del visitante.
3. **Hilos aislados por nombre de dueño.** `cot:<owner_key>` reutiliza tablas y repositorio
   sin migración; el servidor deriva el dueño, no el cliente.
4. **Interacción con SSE.** El compositor del Cotizador envía por SSE, recibe el texto por
   `token` y las tarjetas por `products`; el POST normal queda como respaldo sin streaming.
   El chat general conserva su SSE intacto.
5. **Calendario calculado.** Pascua por algoritmo y los 18 festivos colombianos por año;
   ventana en días hábiles con corte a las 15:00.

## Phase 1 — Design

### Flujo de un turno

```text
POST /cotizador
  └─ identidad + CSRF + cuota (reutilizados del chat)
      └─ extract_commercial(pregunta, turnos del hilo)
          ├─ sin solicitud comercial → aviso de alcance, sin tocar datos
          ├─ falta ciudad → pregunta la ciudad y conserva producto y cantidad
          └─ solicitud completa
              ├─ memoria: perfil vigente, detalles nuevos y olvido explícito
              ├─ productos CO por familia, capacidad y uso
              ├─ validación de material (regla vigente)
              ├─ ofertas con existencia ≠ agotada, ordenadas por total
              ├─ presentación necesaria, sobrantes y totales con Decimal
              ├─ sedes de la oferta + coordenadas + mapa
              ├─ entrega si se pidió domicilio: zona, envío y ventana
              └─ hasta dos alternativas compatibles
  └─ persiste turno usuario + asistente (payload en `sources`)
```

### Reglas de cálculo

- **Presentaciones:** `presentaciones = ceil(solicitadas / unidades_presentación)`;
  unidades comprables = presentaciones × unidades; sobrantes = comprables − solicitadas.
- **Dinero:** `Decimal`, totales en COP sin decimales; nunca se suman monedas distintas;
  sin envío conocido, el subtotal no se rotula como total final. La fecha del precio se
  muestra como referencia y no se rotula como vencida ni se descarta por antigüedad.
- **Existencias:** `agotado` excluye la oferta; `insuficiente` compara con las unidades
  comprables; `desconocido` se muestra como no confirmado.
- **Entrega:** solo si se pidió domicilio; ventana = días hábiles desde el siguiente día
  con corte a las 15:00; no cuentan festivos, fines de semana ni días sin atención del
  proveedor.
- **Alternativas:** hasta dos productos compatibles distintos, con su diferencia de
  capacidad, material o presentación.

### Contratos y datos

- [contracts/cotizador.md](./contracts/cotizador.md): rutas, formulario y payload.
- [data-model.md](./data-model.md): productos, ofertas, sedes y payload del mensaje.

### Riesgos y límites asumidos

- El mapa lo sirve OpenStreetMap: si el visitante no tiene red o el iframe se bloquea,
  quedan la dirección, el teléfono y el enlace de respaldo.
- Las coordenadas son aproximadas y llevan su precisión; un centroide no se presenta como
  puerta de la sede.
- Los 48 productos simulados no existen en el portafolio público de Colombia; la sección
  lo repite donde aplica.
- El texto viaja por SSE con trozos de la respuesta ya calculada, sin pausas artificiales;
  si el navegador no soporta streaming, el POST normal inserta el turno completo.
- La cuota diaria se comparte con el chat: es el mismo visitante.
- La memoria guarda datos personales que la persona escribe: se limita a patrones
  conocidos, caduca a los 180 días y se borra a pedido. En un dispositivo compartido la
  cookie es la misma para todos; queda documentado como riesgo aceptado.


## Traceability (spec → tareas → evidencia)

| Requisito | Tarea | Evidencia |
|---|---|---|
| FR-001 | T028–T035 | `cotizador.html`, navegación; prueba HTTP de la sección |
| FR-002 | T027 | `test_pregunta_ajena_al_catalogo_queda_fuera_de_alcance` |
| FR-003 | T006, T025 | `test_sin_ciudad_pide_la_ciudad_y_no_cotiza`, `test_turno_sin_ciudad_pide_ciudad_y_aisla_el_hilo` |
| FR-004 | T006 | `test_quotation_engine.py` (material y uso) |
| FR-005 | T007 | `test_cotiza_20_vasos_con_presentacion_y_sobrantes` |
| FR-006 | T008 | `test_domicilio_suma_envio_y_ventana_habil`, cifras con `Decimal` |
| FR-007 | T014 | tarjetas con fuente, fecha y aviso de referencia |
| FR-008 | T015 | `test_sin_modo_demo_solo_cotiza_lo_publicado` |
| FR-009 | T013, T014 | sellos de existencia simulada y producto demo |
| FR-010 | T009 | alternativas con `difference` |
| FR-011 | T017, T018 | sedes con `provider_id`, dirección, teléfono, `source_url` y fecha |
| FR-012 | T019, T020, T055 | iframe OSM, un marcador por sede geolocalizada y lista textual |
| FR-013 | T021–T024 | `test_quotation_calendar.py`, `test_domicilio_suma_envio_y_ventana_habil` y día sin atención |
| FR-014 | T013, T014, T054 | sellos por dato en las tarjetas; sin aviso general |
| FR-015 | T015 | `COTIZADOR_DEMO=0` y `allow_demo=False` |
| FR-016 | T031 | `test_el_almacen_sql_usa_un_espacio_propio_para_el_cotizador` |
| FR-017 | T029 | `test_post_sin_csrf_se_rechaza_antes_del_motor`, `test_cuota_agotada_responde_429` |
| FR-018 | T025, T026 | `test_cantidad_seguida_recalcula_presentaciones` |
| FR-019 | T008, T016 | fecha visible como referencia; sin sello de vencimiento |
| FR-020 | T011, T012, T036, T062 | suite completa y regresión explícita de chat/SSE/comercial |
| FR-021 | T032, T034 | `_cotizacion.html` y estilos `.coti*` |
| FR-022 | T047–T052 | `test_quotation_memory.py`, `test_la_memoria_silenciosa_usa_ciudad_domicilio_y_nombre` |
| FR-023 | T056 | `data.py` lee el catálogo; `test_colombia_llega_a_150_con_el_catalogo_y_48_simulados` |
| FR-024 | T057 | `test_la_ficha_de_la_oferta_es_citable`, `test_una_ficha_inexistente_o_invalida_responde_404` |
| FR-025 | T058 | `test_cada_cotizacion_emite_un_registro_auditable`, `test_una_aclaracion_no_emite_cotizacion` |

| Criterio | Estado |
|---|---|
| SC-001, SC-002, SC-003, SC-004, SC-006 | Verificados por pruebas del motor y del calendario |
| SC-005 | Verificado por el iframe en `test_turno_con_ciudad_pinta_tarjetas_y_sede` y las coordenadas del motor |
| SC-007 | Recorrido manual del quickstart |
| SC-008 | Suite completa de `pytest` en verde |
| SC-009 | Pendiente de comprobación manual a 390 y 1440 px (T038) |
| SC-010 | Verificado por la memoria silenciosa y el borrado con «olvida mis datos» |
| SC-011 | Verificado por el registro de cotización y las fichas |
| SC-012 | `tests/test_quotation_eval.py` con 13 casos dorados |

## Verification

1. `uv run pytest -q tests/test_quotation_engine.py tests/test_quotation_calendar.py
   tests/test_api_cotizador.py tests/test_demo_cotizador.py` (incluye los eventos SSE,
   CSRF, cupo y la persistencia única del turno)
2. `make check` (Ruff, `ty`, suite completa).
3. Recorrido manual a 390 px y 1440 px: cotizar, cambiar cantidad, pedir domicilio,
   ver mapa y lista de sedes, alternativas, producto agotado y modo demo.
4. SC-007: una persona completa la cotización de ejemplo en menos de 3 minutos sin ayuda.
5. Regresión explícita del chat general (hilos, SSE y cuota) y del flujo comercial de la
   spec 036.

## Análisis de consistencia (2026-11-21)

Revisión cruzada de `spec.md`, `plan.md`, `tasks.md`, `data-model.md`, `contracts/` y el
checklist después de implementar SSE y la memoria:

| Hallazgo | Resolución |
|---|---|
| FR-001 prometía streaming y el primer corte no lo tenía | SSE implementado; FR-001 vuelve a exigirlo |
| FR-015 (apagar la simulación) y FR-019 (fecha del precio) no tenían código | `COTIZADOR_DEMO=0` y fecha visible sin caducidad ni sello |
| El checklist no mencionaba memoria ni SSE | Secciones «Memory and privacy» y notas actualizadas |
| El contrato no describía el stream | Tabla de eventos `open/token/products/done/error` |
| El aviso general de demo tapaba la sección | Se retira; la procedencia se marca dato por dato |
| El Cotizador leía un CSV con productos duplicados del catálogo | El catálogo curado es la fuente; la demo solo añade 48 simuladas y ofertas |
| No había identidad ni vigencia de cotización | Tabla `quotes`, `#id` y vigencia de 7 días |
| No había evaluación de totales | 13 casos dorados en CI (`SC-012`) |
| El mapa era un esquema abstracto | Iframe de OpenStreetMap y `frame-src` acotado |
| La memoria podía filtrarse al payload | Prueba que comprueba que el perfil no viaja en `sources` |
| `sessionmaker()().begin()` devolvía una transacción | Corregido a `sessionmaker.begin()`; verificado contra Postgres real |
| T040 suponía que la imagen debía llevar la demo | El bootstrap ya copia `./data` al volumen; tarea cerrada con la razón |

### Remediación 2026-11-23

- Estado y rama alineados con la entrega en `main`.
- Conteo único de 13 casos dorados.
- Sin aviso general de demo: solo sellos por dato.
- Mapa definitivo: iframe de OpenStreetMap, no SVG.
- Ventana de entrega con días de atención del proveedor.
- Precio con fecha visible como referencia; sin caducidad ni sello de vencimiento.
- Regresión explícita del chat/SSE y del flujo comercial.
