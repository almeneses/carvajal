# Feature Specification: Tres tareas de LLM: Resumen, FAQ y Q&A

**Feature Branch**: `005-tres-tareas-llm`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-10-29 (FAQ por lotes: 2026-11-14)
**Evidence**: [CONSTITUTION.md §9](../../CONSTITUTION.md) (2026-10-29 y 2026-11-14); PRs #8, #116; [docs/prompts.md](../../docs/prompts.md), [docs/resumen.md](../../docs/resumen.md), [docs/faq.md](../../docs/faq.md); [tests/test_rag.py](../../tests/test_rag.py), [tests/test_faq.py](../../tests/test_faq.py)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preguntar y recibir una respuesta citada o una abstención honesta (Priority: P1)

Una persona interesada escribe una pregunta en lenguaje natural y recibe una respuesta basada solo en el corpus, con la fuente citada y su tipo (oficial o tercero). Si el dato no está en el corpus, el asistente lo dice y no inventa cifras, teléfonos ni direcciones.

**Why this priority**: es el producto mínimo de las tres tareas; sin respuesta citada y abstención no hay asistente defendible.

**Independent Test**: `uv run carvajal ask "¿En qué año y ciudad fue fundada Carvajal?"` responde 1904/Cali citando `historia` y `quienes_somos`; una pregunta por horarios o teléfono recibe abstención. Lo cubre [tests/test_rag.py](../../tests/test_rag.py).

**Acceptance Scenarios**:

1. **Given** el corpus cargado y el índice al día, **When** se pregunta algo cubierto, **Then** la respuesta cita al menos una fuente y distingue si es oficial o de tercero.
2. **Given** una pregunta fuera del corpus, **When** se envía, **Then** el asistente declara que no dispone del dato y no usa conocimiento externo.
3. **Given** una cifra que solo publica la prensa, **When** se responde, **Then** se indica medio y fecha y se aclara que no es comunicación oficial.

---

### User Story 2 - Obtener un resumen ejecutivo con datos ausentes declarados (Priority: P2)

`uv run carvajal summarize` genera `docs/resumen.md` con cinco bloques en orden: qué es la empresa, sectores y empresas del grupo, cifras clave con año y fuente, tres hitos de la historia y presencia geográfica. Cuando un dato no está, escribe «no consta en el corpus» en lugar de omitirlo.

**Why this priority**: el resumen demuestra que el corpus consolidado sirve como memoria completa y es una de las tres tareas exigidas.

**Independent Test**: `docs/resumen.md` declara «no consta en el corpus» para el EBITDA de 2024 y cita la fuente tras cada cifra; [tests/test_rag.py](../../tests/test_rag.py) demuestra que la tarea usa su propio prompt.

**Acceptance Scenarios**:

1. **Given** el corpus completo, **When** se genera el resumen, **Then** cubre los cinco bloques en el orden fijado.
2. **Given** un dato ausente, **When** se redacta, **Then** se escribe «no consta en el corpus» en lugar de dejar el bloque vacío.
3. **Given** una discrepancia entre fuentes (ingresos de 3,9 frente a 3,96 billones; 10 frente a 17 países), **When** aparece, **Then** se declara en vez de elegir una cifra arbitrariamente.

---

### User Story 3 - Generar una FAQ por lotes que declare sus huecos (Priority: P2)

`uv run carvajal faq` produce `docs/faq.md` con 10-12 preguntas de un cliente primerizo, agrupadas por tema y con formato pregunta, respuesta breve y fuente, más una sección obligatoria de «Temas sin información en el corpus». Desde la interfaz se piden lotes de hasta 10 preguntas nuevas, se excluyen las ya mostradas y el lote se puede descargar en Markdown (esta parte pliega la spec 026).

**Why this priority**: completa la tercera tarea y convierte los huecos de cobertura en información visible en vez de respuestas que parecen completas.

**Independent Test**: `docs/faq.md` contiene 12 preguntas con fuente y 5 temas ausentes; [tests/test_faq.py](../../tests/test_faq.py) demuestra el límite de 10, la exclusión de previas y la marca de agotamiento.

**Acceptance Scenarios**:

1. **Given** el corpus completo, **When** se genera la FAQ, **Then** incluye 10-12 preguntas agrupadas por tema, cada una con respuesta breve y una fuente.
2. **Given** temas esperables sin cobertura, **When** termina la generación, **Then** aparecen en la sección «Temas sin información en el corpus».
3. **Given** una FAQ ya mostrada, **When** se pide otra tanda, **Then** se generan hasta 10 preguntas nuevas excluyendo las anteriores.
4. **Given** que el prompt se trunca por longitud o el modelo no aporta preguntas nuevas, **When** termina el lote, **Then** se avisa del truncamiento o del agotamiento sin persistir estado en el servidor.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Ofrecer tres tareas de LLM sobre el mismo corpus consolidado, cada una con su propio prompt y sus reglas.
- **FR-002**: Consolidar en los prompts el corpus núcleo completo (24 documentos) y excluir la capa extendida.
- **FR-003**: En Q&A, responder solo con el corpus, citar la fuente, distinguir oficial de tercero y declarar la ausencia de un dato.
- **FR-004**: En Resumen, usar estructura fija de cinco bloques, citar el slug de la fuente tras cada cifra y escribir «no consta en el corpus» cuando falte.
- **FR-005**: En FAQ, generar 10-12 preguntas agrupadas por tema con respuesta breve, exactamente una fuente por entrada y sección obligatoria de temas ausentes.
- **FR-006**: En FAQ por lotes, devolver hasta 10 preguntas nuevas, excluir las ya mostradas, no persistir el estado y permitir descargar el lote en Markdown.
- **FR-007**: Avisar cuando el corpus se trunca por longitud o se alcanza el máximo de documentos.
- **FR-008**: Generar los entregables `docs/resumen.md` y `docs/faq.md` de forma reproducible por comando.
- **FR-009**: Mantener temperatura 0 y proveedor de LLM configurable (DeepSeek u Ollama en la aplicación web; Claude solo en la interfaz heredada) sin cambiar el contrato de las tres tareas.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: La evaluación del Q&A obtiene 26/30 respuestas completas y correctas, 3 parciales y 1 abstención correcta ([docs/informe.md](../../docs/informe.md)).
- **SC-002**: El resumen entregado cubre los cinco bloques, cita la fuente de cada cifra y declara que el EBITDA de 2024 no consta ([docs/resumen.md](../../docs/resumen.md)).
- **SC-003**: La FAQ entregada contiene 12 preguntas con fuente y 5 temas sin información ([docs/faq.md](../../docs/faq.md)).
- **SC-004**: Cero respuestas con datos de contacto, horarios o sedes inventados en las 30 preguntas de la evaluación.
- **SC-005**: Cada lote de FAQ devuelve como máximo 10 preguntas, no repite las previas y no deja estado persistido.

## Assumptions

- El corpus núcleo es el único que se consolida en el prompt; la capa extendida se recupera aparte.
- El LLM se invoca con temperatura 0 y el proveedor se elige por configuración.
- La evaluación de Resumen y FAQ es cualitativa y la califica el equipo.
