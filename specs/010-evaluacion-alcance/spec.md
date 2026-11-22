# Feature Specification: Evaluación del Q&A y declaración de alcance

**Feature Branch**: `010-evaluacion-alcance`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-10-30
**Evidence**: [CONSTITUTION.md §9](../../CONSTITUTION.md) (2026-10-30); PRs #9, #13; [docs/resultados.md](../../docs/resultados.md), [docs/alcance.md](../../docs/alcance.md), [docs/informe.md](../../docs/informe.md) §5; [data/questions.json](../../data/questions.json); [tests/test_rag.py](../../tests/test_rag.py)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Evaluar el asistente con preguntas versionadas y un informe reproducible (Priority: P1)

El equipo ejecuta `make eval` (o `uv run carvajal eval`) y cada pregunta de un conjunto versionado de 30 se lanza contra el sistema completo. El informe recoge pregunta y respuesta, y el equipo clasifica cada una como correcta, parcial o abstención correcta. La corrida usa DeepSeek con temperatura 0, embeddings locales y `k=5`.

**Why this priority**: sin una evaluación reproducible no se puede afirmar la calidad del asistente ni demostrar que se abstiene cuando no sabe.

**Independent Test**: `data/questions.json` contiene 30 preguntas y `docs/resultados.md` contiene sus 30 respuestas; [tests/test_rag.py](../../tests/test_rag.py) demuestra la carga, la ejecución por pregunta y el renderizado del informe.

**Acceptance Scenarios**:

1. **Given** las 30 preguntas versionadas, **When** se ejecuta la evaluación, **Then** cada respuesta queda registrada con su pregunta en el informe.
2. **Given** una pregunta fuera del alcance, **When** se evalúa, **Then** la respuesta correcta es la abstención y no una invención.
3. **Given** el informe generado, **When** se revisa, **Then** las 30 preguntas aparecen numeradas y con su respuesta completa.

---

### User Story 2 - Declarar qué cubre el corpus y qué no, con evidencia (Priority: P1)

La matriz de alcance clasifica nueve categorías de pregunta: siete están cubiertas, contacto/sedes/horarios no están disponibles y los canales de venta quedan fuera. La ausencia se verificó antes de declararla: las rutas de contacto responden 404, el barrido de 26 sub-sitemaps no encontró URLs de contacto y la portada no contiene correos, teléfonos ni direcciones.

**Why this priority**: no inventar datos es el criterio de no-alucinación; el alcance debe ser explícito para que el asistente pueda declarar sus huecos.

**Independent Test**: [docs/alcance.md](../../docs/alcance.md) registra las nueve categorías y las comprobaciones de ausencia; [docs/faq.md](../../docs/faq.md) y [docs/resumen.md](../../docs/resumen.md) incluyen sus secciones de temas sin información.

**Acceptance Scenarios**:

1. **Given** el corpus capturado, **When** se intenta capturar contacto, sedes u horarios, **Then** las rutas responden 404 o dejan 0 caracteres y el resultado se documenta.
2. **Given** una pregunta por teléfono, horario o sede, **When** se responde, **Then** se declara que el dato no está en el corpus y se remite a los canales oficiales.
3. **Given** la FAQ y el resumen entregados, **When** se generan, **Then** incluyen una sección de temas sin información.

---

### User Story 3 - Leer los resultados de las tres tareas y sus limitaciones (Priority: P2)

El informe resume la corrida: 26 de 30 respuestas son completas y correctas, 3 son parciales porque señalan información faltante y 1 es una abstención correcta sobre datos de contacto. Además de la exactitud, se evalúan el Resumen y la FAQ con criterios propios y se declaran las limitaciones del prototipo.

**Why this priority**: la rúbrica exige las tres tareas y una lectura honesta de resultados, no solo el conteo del Q&A.

**Independent Test**: [docs/informe.md](../../docs/informe.md) §5.2 resume los 26/30 con el desglose; [docs/resultados.md](../../docs/resultados.md) contiene el detalle y [docs/prompts.md](../../docs/prompts.md) la experimentación que justifica las reglas.

**Acceptance Scenarios**:

1. **Given** las 30 respuestas, **When** se clasifican, **Then** el informe reporta 26 completas y correctas, 3 parciales y 1 abstención correcta.
2. **Given** una respuesta con cifras discrepantes, **When** se evalúa, **Then** se valora que declare la discrepancia (10 frente a 17 países; 3,9 frente a 3,96 billones de ingresos).
3. **Given** el cierre del prototipo, **When** se documenta, **Then** se declaran limitaciones y trabajo futuro: PDFs, evaluación automática y capa extendida con índice precalculado.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Mantener un conjunto versionado de 30 preguntas que cubra empresa, sectores, productos, historia, sostenibilidad, inversión, presencia geográfica y preguntas fuera de alcance.
- **FR-002**: Ejecutar cada pregunta contra el sistema completo y guardar su respuesta junto a la pregunta.
- **FR-003**: Regenerar el informe de resultados con un comando reproducible.
- **FR-004**: Clasificar cada respuesta como correcta, parcial o abstención y resumir el resultado en el informe.
- **FR-005**: Documentar la cobertura por categoría de pregunta y los documentos que la sustentan.
- **FR-006**: Declarar lo que el corpus no contiene y la evidencia de la búsqueda (404, barrido de sub-sitemaps, portada sin datos de contacto).
- **FR-007**: No inventar datos ausentes: declarar la ausencia y remitir a los canales oficiales.
- **FR-008**: Evaluar también el Resumen y la FAQ con criterios propios: cinco bloques con datos ausentes declarados y 10-12 preguntas con temas no cubiertos.
- **FR-009**: Declarar las discrepancias entre fuentes en las respuestas.
- **FR-010**: Documentar las limitaciones del prototipo y el trabajo futuro en el informe.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: La evaluación obtiene 26/30 respuestas completas y correctas (87 %), 3 parciales y 1 abstención correcta ([docs/informe.md](../../docs/informe.md) §5.2).
- **SC-002**: Cero respuestas con teléfonos, direcciones u horarios inventados en las 30 preguntas.
- **SC-003**: El conjunto versionado tiene 30 preguntas y el informe contiene las 30 respuestas.
- **SC-004**: La matriz de alcance clasifica las nueve categorías: siete completas, una no disponible y una fuera de alcance.
- **SC-005**: El resumen cita la fuente de cada cifra y declara ausente el EBITDA de 2024; la FAQ entrega 12 preguntas y 5 temas sin información.

## Assumptions

- La evaluación es cualitativa y la califica el equipo sobre las respuestas generadas.
- Las 30 preguntas cubren las categorías de primer contacto y algunos casos fuera de alcance.
- Los resultados corresponden a la corrida del 2026-10-30 con el corpus de 24 documentos.
