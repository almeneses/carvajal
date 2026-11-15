# Feature Specification: Agente LangGraph con router, memoria y herramienta de indicadores

**Feature Branch**: `019-agente-langgraph`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-11-13
**Evidence**: [`CONSTITUTION.md` §9](../../CONSTITUTION.md) (2026-11-13); [`tests/test_agent_graph.py`](../../tests/test_agent_graph.py); [`tests/test_agent_router.py`](../../tests/test_agent_router.py); [`data/indicators.json`](../../data/indicators.json); [`docs/aceptacion-m2.md`](../../docs/aceptacion-m2.md) (sección B)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Enrutar cada pregunta por la vía correcta (Priority: P1)

El agente decide antes de responder: un saludo va a respuesta directa, una consulta de indicador con período va a la herramienta, una pregunta documental va a recuperación y un seguimiento elíptico se reescribe con el turno anterior. Si falta el período, pide aclaración en vez de adivinar.

**Why this priority**: mezclar saludos con búsquedas gasta recuperación y mezclar seguimientos sin contexto produce respuestas equivocadas.

**Independent Test**: llamar al router con un saludo, una consulta de EBITDA con período, una pregunta documental y un «¿Y en qué año ocurrió eso?»; comprobar ruta e intención en cada caso (`test_un_saludo_va_a_direct_sin_busqueda`, `test_una_consulta_de_indicador_va_a_tool_con_periodo`).

**Acceptance Scenarios**:

1. **Given** un saludo, **When** se enruta, **Then** la ruta es `direct`, la intención `greeting` y no se consulta el índice (`test_un_saludo_va_a_direct_sin_busqueda`, `test_directo_no_consulta_qdrant`).
2. **Given** una consulta de indicador con período, **When** se enruta, **Then** la ruta es `tool`, con nombre y período `2025-09`; sin período pide aclaración en `direct` (`test_una_consulta_de_indicador_va_a_tool_con_periodo`, `test_indicador_sin_periodo_pide_aclaracion_en_direct`).
3. **Given** una pregunta documental, **When** se enruta, **Then** la ruta es `rag`; un saludo con pregunta documental no se considera directo (`test_una_consulta_documental_va_a_rag`, `test_un_saludo_con_pregunta_documental_no_es_directo`).
4. **Given** un seguimiento elíptico, **When** se enruta con el turno anterior, **Then** la consulta reescrita incluye la pregunta previa (`test_un_seguimiento_eliptico_reescribe_con_el_turno_anterior`).

### User Story 2 - Consultar indicadores con evidencia citable (Priority: P2)

La herramienta de indicadores recibe un nombre y un período tipados, lee el registro verificado y devuelve valor, cita textual y URL, o una ausencia estable cuando no hay dato. Parámetros inválidos fallan antes de leer y un período inexistente no inventa cifras.

**Why this priority**: las cifras financieras son el dato más consultado y el más fácil de inventar; la cita y la ausencia estable son la defensa.

**Independent Test**: pedir ingresos de 2024 y comprobar valor, cita y URL; pedir EBITDA de 2024 y comprobar ausencia; enviar un nombre o período inválido y comprobar que falla la validación (`test_herramienta_devuelve_un_registro_verificable`, `test_herramienta_devuelve_ausencia_estable`).

**Acceptance Scenarios**:

1. **Given** un indicador y período existentes, **When** se invoca la herramienta, **Then** devuelve valor, cita y URL de la fuente (`test_herramienta_devuelve_un_registro_verificable`).
2. **Given** un indicador sin registro para el período, **When** se invoca, **Then** devuelve ausencia estable con «No hay registro», sin valor ni URL (`test_herramienta_devuelve_ausencia_estable`).
3. **Given** un nombre de indicador inventado o un período imposible, **When** se invoca, **Then** la validación tipada rechaza antes de leer datos (`test_parametros_invalidos_fallan_antes_de_leer`).
4. **Given** una consulta de indicador real, **When** el nodo la ejecuta, **Then** la respuesta cita la fuente y conserva la URL verificable (`test_herramienta_ejecuta_el_registro_real_y_cita_la_fuente`).

### User Story 3 - Recordar el hilo y las preferencias sin mezclar visitantes (Priority: P3)

El agente conserva el estado por hilo para los seguimientos y guarda las preferencias declaradas en un almacén por dueño. Una preferencia se recupera en otro hilo del mismo visitante y no se hereda a otro. El estado sobrevive a una reconexión del almacén.

**Why this priority**: sin memoria por hilo el seguimiento elíptico se rompe; sin aislamiento por dueño la memoria sería una fuga de datos.

**Independent Test**: declarar «prefiero respuestas breves» en un hilo y abrir otro del mismo dueño; comprobar que la preferencia aparece; repetir con otro dueño y comprobar que no (`test_la_preferencia_se_recupera_en_otro_hilo_del_mismo_dueno`, `test_otro_dueno_no_hereda_la_preferencia`).

**Acceptance Scenarios**:

1. **Given** una preferencia guardada, **When** se abre otro hilo del mismo dueño, **Then** la preferencia aparece en el prompt y en el perfil almacenado (`test_la_preferencia_se_recupera_en_otro_hilo_del_mismo_dueno`).
2. **Given** una preferencia de un dueño, **When** pregunta otro visitante, **Then** no la hereda (`test_otro_dueno_no_hereda_la_preferencia`).
3. **Given** un hilo con turno registrado, **When** se hace un seguimiento, **Then** el estado recupera la pregunta anterior; tras reconectar el almacén, el hilo sigue recuperable y otro hilo no lo hereda (`test_el_checkpointer_recupera_el_turno_para_un_seguimiento`, `test_el_checkpointer_postgres_recupera_el_hilo_tras_reconectar`).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El router MUST decidir en orden `tool`, `rag`, `direct` y exponer ruta e intención observables.
- **FR-002**: La ruta `direct` MUST responder sin consultar el índice vectorial ni ejecutar la herramienta.
- **FR-003**: Una consulta de indicador MUST ir a `tool` solo si trae período; sin período MUST pedir aclaración en `direct`.
- **FR-004**: Los argumentos de la herramienta MUST validarse con un esquema tipado antes de leer el registro; nombre o período inválidos MUST rechazarse.
- **FR-005**: La herramienta MUST devolver valor, cita textual y URL, o una ausencia estable, y MUST NOT inventar cifras.
- **FR-006**: La ruta `rag` MUST recuperar del índice y exponer fuentes con identificador de fragmento y URL.
- **FR-007**: Sin evidencia suficiente, el agente MUST abstenerse y no generar respuesta; un nombre propio ausente en el contexto MUST forzar abstención aunque el puntaje sea alto.
- **FR-008**: Un seguimiento MUST reescribir la consulta con el turno anterior del mismo hilo; los marcadores deben respetar límites de palabra.
- **FR-009**: El estado del hilo MUST persistir en un checkpointer por `thread_id` y MUST recuperarse tras reconectar, sin mezclar hilos.
- **FR-010**: Las preferencias MUST guardarse por dueño, recuperarse en hilos nuevos del mismo dueño, aislarse entre visitantes y limitarse a la frase declarada (máximo 280 caracteres).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Las tres rutas son observables: un saludo produce 0 consultas al índice y una consulta documental produce al menos 1 fuente.
- **SC-002**: El registro verificado contiene 10 indicadores; EBITDA de 2025-09 devuelve valor y URL, y EBITDA de 2024 devuelve ausencia sin valor.
- **SC-003**: Los tres casos de parámetros inválidos (nombre inventado, período `2024-13` e invocación con período no válido) fallan antes de leer.
- **SC-004**: Una preferencia se recupera en otro hilo del mismo dueño y produce 0 preferencias en el hilo de otro visitante.
- **SC-005**: Tras reconstruir el almacén con una conexión nueva, un seguimiento conserva la pregunta anterior y un hilo distinto devuelve 0 turnos; el smoke real supera los 10 chequeos ([`docs/evidence/m2/smoke.json`](../../docs/evidence/m2/smoke.json)).

## Assumptions

- El checkpointer y el almacén de preferencias comparten el Postgres de la app; la variante SQLite es solo para demo local.
- El registro de indicadores es curado y versionado; no se consulta la web en tiempo de pregunta.
- La recuperación documental y el reordenamiento léxico se detallan en la especificación 020.
