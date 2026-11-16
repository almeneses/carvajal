# Feature Specification: Evaluación del retrieval separada de la respuesta

**Feature Branch**: `023-evaluacion-retrieval`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-11-13
**Evidence**: `CONSTITUTION.md` §9 (2026-11-13); PR #102; `docs/eval/retrieval-report.md`; `data/eval/retrieval_questions.json`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Medir la recuperación sin el modelo (Priority: P1)

Quien evalúa la entrega lanza la evaluación del retrieval y obtiene, por consulta y
en conjunto, cuánto del documento correcto aparece en el top-10. La métrica mide
solo la búsqueda: no hay llamada al LLM, de modo que un fallo de redacción no se
confunde con un fallo de recuperación.

**Why this priority**: Sin una medida separada no se puede afirmar que el RAG
recupera; solo se puede opinar sobre la respuesta final.

**Independent Test**: Ejecutar la evaluación y comprobar que el informe contiene las
métricas por consulta y que ninguna respuesta fue generada por un modelo.

**Acceptance Scenarios**:

1. **Given** las 31 consultas del conjunto de evaluación, **When** se ejecuta la evaluación, **Then** el informe resume 30 consultas con evidencia etiquetada, Recall@10 = 0.8000 y nDCG@10 = 0.7791 sobre k=10 (evidencia: `docs/eval/retrieval-report.md`).
2. **Given** una corrida de evaluación, **When** se inspecciona el proceso, **Then** no se invoca al LLM y cada fila muestra el top-3 recuperado.

### User Story 2 - Documentar una consulta sin evidencia (Priority: P2)

Una de las consultas del conjunto pregunta por un dato que el corpus no publica.
La evaluación no la fuerza dentro de las métricas: la reporta como abstención y deja
constancia de que el sistema se abstuvo en lugar de inventar una respuesta.

**Why this priority**: La honestidad del corpus se demuestra mejor con una consulta
que no tiene evidencia que con una que sí.

**Independent Test**: Comprobar que la consulta sin evidencia aparece identificada
en el informe y que no aporta puntuación de Recall ni de nDCG.

**Acceptance Scenarios**:

1. **Given** la consulta sin evidencia, **When** se evalúa, **Then** el informe la marca como abstención con `0` fuentes y no la incluye en los promedios.
2. **Given** el detalle por consulta, **When** se revisa una consulta sin evidencia, **Then** se ven las fuentes devueltas y su puntuación para justificar la abstención.

### User Story 3 - Reproducir el informe (Priority: P3)

Cualquier persona del equipo repite la medición con un comando, sobre una colección
declarada y un número de puntos conocido, y obtiene un informe con fecha.

**Why this priority**: Una métrica sin fecha ni condiciones no es evidencia.

**Independent Test**: Regenerar el informe y verificar que conserva la estructura, la
fecha de generación, la colección y el número de puntos indexados.

**Acceptance Scenarios**:

1. **Given** el índice preparado con 24 documentos y 125 fragmentos, **When** se ejecuta la evaluación, **Then** el informe declara colección, `k=10` y 125 puntos.
2. **Given** una corrida previa, **When** se repite, **Then** el informe se regenera con la misma fórmula de métricas y fecha actual.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El conjunto de evaluación debe contener 31 consultas con relevancia etiquetada manualmente a nivel de documento sobre el corpus Gold.
- **FR-002**: Al menos una consulta del conjunto debe estar declarada sin evidencia en el corpus.
- **FR-003**: La evaluación debe medir Recall@10 y nDCG@10 sin invocar al LLM.
- **FR-004**: El informe debe incluir, por consulta, Recall@k, nDCG@k y el top-3 recuperado.
- **FR-005**: El informe debe resumir consultas etiquetadas, Recall@10, nDCG@10 y consultas sin evidencia.
- **FR-006**: La consulta sin evidencia debe registrarse como abstención y quedar fuera de los promedios de Recall y nDCG.
- **FR-007**: El informe debe declarar la fecha de generación, la colección evaluada, el valor de `k` y el número de puntos indexados.
- **FR-008**: El detalle completo con los diez resultados y extractos debe poder regenerarse sin versionarse en Git.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Recall@10 = 0.8000 y nDCG@10 = 0.7791 sobre 30 consultas etiquetadas con k=10.
- **SC-002**: La consulta sin evidencia devuelve abstención con 0 fuentes y no altera los promedios.
- **SC-003**: La corrida de referencia se hizo sobre 24 documentos y 125 puntos de 768 dimensiones en la colección evaluada.
- **SC-004**: Cero llamadas al proveedor de lenguaje durante la evaluación.
- **SC-005**: El informe regenerado reproduce las mismas métricas globales sobre el mismo índice.

## Assumptions

La etiqueta es a nivel de documento, no de fragmento: dos fragmentos del mismo
documento relevante cuentan igual. Las métricas dependen del modelo de embeddings
(`nomic-embed-text`) y del índice con coseno y 768 dimensiones. Convención as-built
en [el inventario](../README.md) §3; contexto en [`docs/aceptacion-m2.md`](../../docs/aceptacion-m2.md).
