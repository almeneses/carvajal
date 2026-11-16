# Feature Specification: RAG híbrido ligero: reordenamiento, fichas y expansión de consulta

**Feature Branch**: `020-rag-hibrido`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-11-13
**Evidence**: [`CONSTITUTION.md` §9](../../CONSTITUTION.md) (2026-11-13); [`tests/test_ask.py`](../../tests/test_ask.py); [`tests/test_qdrant_store.py`](../../tests/test_qdrant_store.py); [`data/retrieval_cards.json`](../../data/retrieval_cards.json); [`docs/eval/retrieval-report.md`](../../docs/eval/retrieval-report.md)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Recuperar el documento correcto, no la portada (Priority: P1)

Cada documento del corpus tiene una ficha breve derivada de su texto que se antepone solo para vectorizar. La búsqueda combina similitud vectorial y reordenamiento léxico, y la cita sigue saliendo del fragmento original, no de la ficha. Un candidato con vector bajo se descarta aunque su coincidencia léxica sea alta.

**Why this priority**: el embedding confundía preguntas de fundación con la portada; sin fichas y reordenamiento la fuente citada sería la equivocada.

**Independent Test**: buscar «ciudad fundada Carvajal» con una portada genérica de vector alto y un documento de fundación de vector menor; comprobar que gana el documento relevante (`test_la_busqueda_hibrida_prioriza_la_ficha_relevante`).

**Acceptance Scenarios**:

1. **Given** los 24 documentos del corpus, **When** se generan las fichas, **Then** hay 24 fichas, una por documento, derivadas del texto de `data/gold` ([`data/retrieval_cards.json`](../../data/retrieval_cards.json)).
2. **Given** un candidato genérico de vector alto y un fragmento relevante de vector menor, **When** se aplica el reordenamiento léxico, **Then** el fragmento relevante queda primero (`test_la_busqueda_hibrida_prioriza_la_ficha_relevante`).
3. **Given** un candidato con vector por debajo del umbral, **When** se busca, **Then** se descarta antes del reordenamiento léxico (`test_la_busqueda_hibrida_aplica_el_umbral_vectorial_antes_del_lexico`).
4. **Given** una respuesta con fuentes, **When** se listan, **Then** cada fuente trae `chunk_id`, documento, capa y extracto del fragmento original en orden (`test_respuesta_incluye_fuentes_con_chunk_id_en_orden`).

### User Story 2 - Expandir la consulta con datos del corpus sin inventar (Priority: P2)

Una pregunta de fundación se expande con datos ya declarados en el corpus (1904, Cali, Colombia) para mejorar la recuperación. La expansión respeta límites de palabra: «proceso» no activa el marcador «eso» y «nacional» no activa «nacimiento». Un seguimiento elíptico arrastra la pregunta anterior. La cita sigue saliendo del fragmento, no del texto expandido.

**Why this priority**: la expansión mejora el recall de preguntas cortas, pero sin límites de palabra introduce falsos positivos y citas que no existen.

**Independent Test**: expandir «¿En qué ciudad fue fundada Carvajal?» y comprobar que la consulta reescrita contiene «1904 Cali Colombia»; comprobar que «proceso» y «nacional» no disparan marcadores (`test_la_consulta_de_fundacion_se_expande_con_datos_del_corpus`, `test_la_palabra_proceso_no_activa_el_marcador_eso`).

**Acceptance Scenarios**:

1. **Given** una pregunta de fundación, **When** se expande, **Then** la consulta incluye «1904 Cali Colombia» (`test_la_consulta_de_fundacion_se_expande_con_datos_del_corpus`).
2. **Given** palabras que contienen marcadores como subcadena, **When** se expande, **Then** no se activan: «proceso» no evoca «eso» y «nacional» no evoca nacimiento (`test_la_palabra_proceso_no_activa_el_marcador_eso`, `test_nacional_no_activa_la_expansion_de_nacimiento`).
3. **Given** un seguimiento elíptico, **When** se expande, **Then** la consulta incorpora la pregunta anterior del mismo hilo (`test_un_seguimiento_eliptico_reescribe_con_el_turno_anterior`).
4. **Given** una consulta expandida, **When** se compone el contexto, **Then** el prompt numera los fragmentos originales con su capa y la cita apunta al fragmento, no a la expansión (`test_prompt_incluye_contexto_numerado_y_reglas`).

### User Story 3 - Abstenerse y medir el retrieval por separado (Priority: P3)

Si no hay evidencia suficiente, el asistente declara que no dispone del dato y no llama al modelo. El retrieval se evalúa sin el LLM: 31 consultas etiquetadas, con 30 con relevancia y una sin evidencia, y se reportan Recall@10 y nDCG@10.

**Why this priority**: sin abstención el sistema inventa; sin evaluación separada no se puede distinguir un fallo de recuperación de uno de redacción.

**Independent Test**: preguntar por un dato ausente y comprobar la respuesta de ausencia sin llamada al LLM; consultar el reporte de 31 consultas y sus métricas (`test_sin_resultados_declara_ausencia_sin_llamar_al_llm`, [`docs/eval/retrieval-report.md`](../../docs/eval/retrieval-report.md)).

**Acceptance Scenarios**:

1. **Given** cero resultados, **When** se responde, **Then** el texto declara ausencia, no hay fuentes y no se llama al LLM (`test_sin_resultados_declara_ausencia_sin_llamar_al_llm`).
2. **Given** evidencia con puntaje bajo el umbral, **When** se recupera, **Then** se descarta y el prompt queda vacío (`test_evidencia_bajo_el_umbral_se_descarta`).
3. **Given** el dataset de evaluación, **When** se corre la evaluación, **Then** el reporte muestra 30 consultas etiquetadas, 1 sin evidencia y las métricas por consulta ([`docs/eval/retrieval-report.md`](../../docs/eval/retrieval-report.md)).
4. **Given** el prompt con contexto, **When** se compone, **Then** instruye responder solo con el contexto, citar con `[n]`, usar español y abstenerse si falta el dato (`test_prompt_incluye_contexto_numerado_y_reglas`).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Cada documento del corpus MUST tener una ficha de recuperación derivada de su texto; la ficha MUST usarse solo para vectorizar y MUST NOT citarse como fuente.
- **FR-002**: La recuperación MUST combinar similitud vectorial y reordenamiento léxico sobre las fichas y el texto.
- **FR-003**: El umbral vectorial MUST aplicarse antes del reordenamiento léxico; un candidato por debajo MUST descartarse.
- **FR-004**: Toda fuente MUST conservar el identificador del fragmento original, su URL, documento, capa y extracto, en orden de relevancia.
- **FR-005**: La expansión de consulta MUST usar datos ya declarados en el corpus y MUST respetar límites de palabra.
- **FR-006**: Un seguimiento elíptico MUST expandirse con la pregunta anterior del mismo hilo.
- **FR-007**: El prompt MUST numerar el contexto con `[n]`, indicar la capa de cada fragmento y exigir respuesta únicamente con el contexto.
- **FR-008**: El contenido del corpus y la pregunta MUST neutralizarse para que no puedan cerrar los delimitadores del prompt ni inyectar instrucciones.
- **FR-009**: Sin evidencia MUST responderse con una declaración de ausencia y MUST NOT llamarse al LLM.
- **FR-010**: La recuperación MUST evaluarse por separado de la respuesta, con consultas etiquetadas y métricas Recall@k y nDCG@k reproducibles.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Existen 24 fichas, una por documento, y la ficha no aparece como fuente citada en la respuesta.
- **SC-002**: El reporte de evaluación cubre 31 consultas: 30 etiquetadas y 1 sin evidencia, con Recall@10 = 0.8000 y nDCG@10 = 0.7791 ([`docs/eval/retrieval-report.md`](../../docs/eval/retrieval-report.md)).
- **SC-003**: Con `RAG_TOP_K=6` y `RAG_MIN_SCORE=0.30` por defecto, un candidato de puntaje 0.10 se descarta y no llega al prompt.
- **SC-004**: Una pregunta sin evidencia produce 0 fuentes y 0 llamadas al LLM.
- **SC-005**: El reordenamiento léxico hace ganar al documento relevante sobre la portada genérica, sin que el umbral vectorial deje pasar candidatos por debajo de 0.30.

## Assumptions

- Las fichas se derivan del texto de `data/gold` y se regeneran cuando cambia el corpus; no son una fuente de respuesta.
- La expansión usa hechos ya presentes en el corpus y no consulta fuentes externas.
- La recuperación documental del agente se apoya en el índice Qdrant de la especificación 018.
