# Tareas — Chat comercial unificado con datos reales

**Entrada**: [spec](spec.md), [plan](plan.md), [investigación](research.md),
[modelo](data-model.md), [contrato](contracts/chat-comercial.md) y
[validación](quickstart.md).

`[P]` indica tareas que se pueden delegar en paralelo dentro de su fase
una vez satisfechas las dependencias. Los nombres de archivo nuevos
proponen la ubicación de la prueba o componente. Reutilizar el archivo
existente equivalente cuando corresponda.

## Fase 0 · Base entregada y preparación

- [x] T001 Confirmar que #222–#226 pertenecen a la base `42a816c` y registrar las dependencias en `specs/043-chat-comercial-unificado/research.md`. Goals 1 y 3 entregados. FR-012.
- [x] T002 [P] Registrar la referencia de 29 publicaciones CO y la paridad MX sobre la captura auditada. Añadir primero la prueba del cargador sin demo en `tests/test_commercial_contract.py`. Incluir los tres SKU filtrados y la presentación que publicó el vendedor. SC-001.
- [ ] T003 [P] Inventariar consumidores para el retiro y preparar prueba de arranque nuevo y actualización en `docs/despliegue-ovh.md`, `compose.prod.yml` y `tests/browser/`. Registrar las rutas de FAQ, el acceso a históricos y la versión del catálogo esperada. FR-009, FR-010, FR-011.

## Fase 1 · US1. Consultar productos y sedes con datos reales

**Goal 2**. Debe pasar en una instalación sin los archivos de simulación.

- [x] T004 [US1] Curar la geocodificación trazable en `data/commercial/` y documentar la resolución de discrepancias con el directorio. Identificar ubicaciones sin precisión suficiente. FR-003.
- [ ] T005 [US1] Retirar las lecturas y marcas de demo del contrato comercial en `packages/core/src/carvajal/commercial/loader.py`, `packages/core/src/carvajal/commercial/models.py` y `packages/core/src/carvajal/commercial/book.py`. Conservar identificadores, consultas por ciudad, procedencia, presentación original y paridad MX. Depende de T002, T004. FR-001–FR-004, FR-012.
- [ ] T006 [US1] Adoptar el contrato publicado en `packages/core/src/carvajal/quotation/engine.py` y `apps/api/views/cotizador.py`. Mantener la búsqueda de sedes disponible sin precio y usar estados explícitos para los datos que falten. Depende de T005. FR-001, FR-003, FR-004.
- [ ] T007 [US1] Eliminar `data/demo/cotizador/`, `scripts/generar_demo_cotizador.py`, `COTIZADOR_DEMO`, fichas generadas y sus accesos exclusivos. Actualizar `packages/core/src/carvajal/quotation/data.py`, `apps/api/views/cotizador.py`, `tests/test_demo_cotizador.py` y los consumidores de fixtures o ejemplos simulados. Depende de T005–T006. FR-001.
- [ ] T008 [US1] Verificar sobre `tests/test_commercial_contract.py` y `tests/test_quotation_engine.py` la captura CO, la paridad MX, el vendedor de cada oferta, el caso de Cali sin precio y la vigencia `valid_until` de la evidencia. Depende de T005–T007. FR-002–FR-004, FR-012, SC-001, SC-002.

## Fase 2 · US2. Entender cuánto se conoce de una cotización

**Goal 5**. Prueba independiente con envío desconocido, paquetes y solicitud
de dos productos. Inicia tras T006. Una sola persona modifica el motor.

- [ ] T009 [P] [US2] Especificar y probar lectura compatible de importes históricos y del resultado versionado en `tests/test_cotizador_store.py`. Usar folio y dueño cuando la relación con el mensaje falte. FR-005, FR-009.
- [ ] T010 [P] [US2] Definir en `tests/test_quotation_engine.py` y `tests/test_commercial_quote.py` casos de 300 y 301 vasos, envío con costo cero respaldado, envío sin precio, precio por presentación y vigencia ausente. FR-005.
- [ ] T011 [US2] Calcular y ordenar con Decimal en `packages/core/src/carvajal/quotation/engine.py`. Separar costo por presentación, subtotal, IVA declarado, envío, total y vigencias. Depende de T010. FR-005.
- [ ] T012 [US2] Persistir y leer el contrato versionado en `packages/core/src/carvajal/adapters/storage/quotes.py` y `packages/core/src/carvajal/adapters/storage/tables.py`, con migración Alembic si corresponde. Comprobar lectura y escritura durante la transición. Depende de T009, T011. FR-005, FR-009.
- [ ] T013 [US2] Renderizar los campos correctos y tratar solicitud de varias líneas desde `apps/api/templates/_cotizacion.html` y el motor. Conservar las otras líneas como pendientes. Depende de T011–T012. FR-005, FR-006.

## Fase 3 · US3. Continuar la compra en el mismo chat

**Goal 4**. La interfaz y los casos de navegador pueden adelantarse tras acordar
el contrato. La conexión completa depende del catálogo y la cotización reales.

- [ ] T014 [P] [US3] Preparar ficha, sedes y controles de ciudad, cantidad y contacto en `apps/api/templates/_productos.html` y `apps/api/templates/chat.html` a partir de `specs/043-chat-comercial-unificado/contracts/chat-comercial.md`. FR-003, FR-007.
- [ ] T015 [P] [US3] Preparar pruebas de concurrencia, primer envío sin hilo, reenvío HTML/SSE, recarga y aislamiento en `tests/test_chat_service.py` y `tests/test_api_chat.py`. FR-008, SC-004.
- [ ] T016 [US3] Resolver el turno mixto y persistir el contexto comercial del hilo desde `packages/core/src/carvajal/agent/graph.py`, `packages/core/src/carvajal/agent/commercial_flow.py` y `apps/api/chat/agent.py`. Conectar citas documentales, oferta, sede y aclaración por líneas. Depende de T005–T013. FR-006, FR-007.
- [ ] T017 [US3] Unificar propagación de `client_turn_id` en `apps/api/views/chat.py`, `apps/api/views/stream.py` y `apps/api/views/api.py`, y resolver la concurrencia del primer hilo en `apps/api/chat/service.py` y `packages/core/src/carvajal/adapters/storage/chat.py`. Confirmar mensajes, resultado y cotización juntos. Depende de T012, T015. FR-008, SC-004.
- [ ] T018 [US3] Restaurar ficha y folio al reintentar o recargar desde `apps/api/templates/_chat_thread.html`, `apps/api/static/chat-stream.js` y `apps/api/chat/service.py`, conservando los eventos SSE y de voz. Depende de T014, T016–T017. FR-007, FR-008, SC-003.

## Fase 4 · US4. Validar la transición y retirar el recorrido separado

**Goals 6 y 7**. Ensayar primero con ambos accesos en un entorno aislado. La
retirada pública se realiza después de la validación integrada.

- [ ] T019 [P] [US4] Implementar la lectura autorizada del historial en `apps/api/views/cotizador.py` y `packages/core/src/carvajal/adapters/storage/quotes.py` usando el contrato de T012. Aplicar la matriz real/simulado/ambiguo de `data-model.md`, excluir texto, tarjetas y contexto contaminados y probar cada rama con dos identidades en `tests/test_api_cotizador.py`. FR-009, SC-004.
- [ ] T020 [P] [US4] Separar el aprovisionamiento comercial de la siembra de Gold en `Dockerfile`, `compose.prod.yml` y `scripts/deploy.sh`. Verificar versión y compatibilidad de rollback, y distinguir fallo de catálogo de un índice inicial ausente. Depende de T007 y T012. FR-010, SC-005.
- [ ] T021 [US4] Ejecutar `specs/043-chat-comercial-unificado/quickstart.md` en arranque limpio y actualización. Registrar versiones, calidad, precisión comercial, conservación de datos y mediciones de primer contenido útil y cierre. Depende de T018–T020. SC-001–SC-005.
- [ ] T022 [US4] Tras validar T021, retirar el segundo compositor y su escritura pública de `apps/api/views/cotizador.py`, `apps/api/templates/` y `apps/api/static/`. Conservar el acceso histórico autorizado y enlaces válidos o redirigidos. Aplicar el inventario de consumidores de T003. FR-009, FR-011.
- [ ] T023 [US4] Sustituir la generación pública de FAQ desde la navegación en `apps/api/views/faq.py` y `apps/api/templates/base.html`. Preservar fuentes y consumidores vigentes de la API. Depende de T021. FR-011.
- [ ] T024 [US4] Repetir las pruebas del piloto tras T022–T023 y registrar evidencia en `specs/043-chat-comercial-unificado/evidencia.md`. Actualizar `README.md`, `CONSTITUTION.md` y el índice de specs al estado efectivamente verificado. FR-010–FR-012, SC-001–SC-005.

## Trabajo con subagentes

| Tanda | Reparto propuesto |
| --- | --- |
| Preparación | Un subagente verifica datos y paridad (T002). Otro prepara validación e inventaría consumidores (T003). |
| Datos e importes | Un agente toma datos y cargador (T004–T005). El responsable del motor toma T006 y, tras el corte publicado, T011 y T013. Las pruebas T009–T010 se pueden delegar con archivos disjuntos. |
| Chat | Un subagente prepara la interfaz (T014). Otro prepara pruebas (T015). El agente principal integra router, persistencia y replay o asigna esas tareas por archivos, en el orden de sus dependencias. |
| Validación | Subagentes distintos preparan historial y despliegue (T019–T020). El principal valida el piloto completo, revisa la compatibilidad y decide el corte según la evidencia. |

Cada subagente debe reportar archivos, comandos, resultados y bloqueos.
El principal conserva un único dueño por archivo compartido y verifica la
integración. Antes de iniciar los goals de implementación, usa los IDs de esta
lista como criterios de cierre. La depuración irreversible de una base real
lleva un inventario respaldado y autorización por separado. FAISS y ZIP
pertenecen a otra tarea de mantenimiento.

## Goals de ejecución

| Goal | Alcance y cierre dentro de la spec 043 |
| --- | --- |
| 1 · Protección de ingesta | Entregado en #222. T001 registra la base y T024 verifica regresiones. |
| 2 · Eliminar la demo | T002 y T004–T008 cierran el corte de datos publicados. T019 cubre históricos y su inventario. |
| 3 · Contrato de datos | Entregado en #223. T001 registra la base y T005–T006 la adoptan una vez retirada su rama simulada. |
| 4 · Unificar el chat | T014–T018 cierran ficha, turno mixto, contexto y reenvío. |
| 5 · Cotización | T009–T013 cierran cálculo, persistencia y visualización. |
| 6 · Piloto y despliegue | T003 y T019–T021 preparan y ejecutan la validación. |
| 7 · Retirar duplicados | T022–T024 sustituyen los accesos y repiten la validación. |

## Phase 5: Convergence

- [ ] T025 Actualizar `.specify/memory/constitution.md` para retirar la excepción de datos simulados de la 042 en el mismo cierre documental de T024, sin dejar la proyección desalineada con la raíz (partial, `Constitution III` / `plan: T024`)
- [ ] T026 Añadir regresión explícita de dictado y lectura por voz, y de consulta institucional, dentro del hilo unificado; no basta con conservar los eventos SSE (missing, `FR-012` / `US3`)
- [ ] T027 Probar el contrato de `/api/faq` y sus consumidores antes de retirar la generación pública de FAQ, además de la navegación (partial, `FR-011` / `plan: T023`)
- [ ] T028 Cubrir con pruebas la transición de `/cotizador/stream`, `/cotizador/nuevo` y `/cotizador/ficha/{slug}`: redirección, lectura histórica autorizada o 404 explícito según corresponda (partial, `FR-009` / `plan: T022`)
- [ ] T029 Definir y registrar el umbral de aceptación de SC-005 (mediana y p95 de primer contenido útil y cierre) y la evidencia de la muestra; hoy solo se pide medir, no aprobar (partial, `SC-005`)
