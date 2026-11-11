# Feature Specification: Asistente Q&A con RAG sobre el corpus de Carvajal

**Feature Branch**: `001-asistente-rag-carvajal`
**Created**: 2026-11-03
**Status**: Implemented (línea base documentada a posteriori)
**Input**: Sistema construido durante el Módulo 1: corpus web curado, tres tareas de LLM
(Resumen, FAQ y Q&A) e interfaz de pruebas con el scraping visible.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Consultar el corpus y recibir una respuesta citada (Priority: P1)

Un cliente que contacta a Carvajal por primera vez escribe una pregunta y recibe una
respuesta basada **solo** en el corpus, con la fuente identificada y su tipo
(oficial o tercero). Si el dato no está, el asistente lo dice.

**Why this priority**: es el producto mínimo: sin respuesta citada y sin abstención honesta
no hay asistente defendible.

**Independent Test**: abrir la pestaña *Asistente*, preguntar «¿En qué año y ciudad fue
fundada Carvajal?» y comprobar que responde 1904/Cali citando `historia` y `quienes_somos`;
luego preguntar por el horario de atención y comprobar que responde que no lo sabe.

**Acceptance Scenarios**:

1. **Given** el corpus cargado y el índice al día, **When** se pregunta algo cubierto,
   **Then** la respuesta cita al menos una fuente y distingue si es oficial o de terceros.
2. **Given** una pregunta fuera del corpus, **When** se envía, **Then** el asistente declara
   que no dispone del dato y no inventa cifras ni teléfonos.
3. **Given** una cifra que solo publica la prensa, **When** se responde, **Then** se indica el
   medio y la fecha y se aclara que no es comunicación oficial.

---

### User Story 2 - Ver el proceso de captura por etapas (Priority: P2)

Quien evalúa el sistema ve el scraping en funcionamiento: las siete etapas, la URL en curso,
las métricas del corpus y los descartes con su motivo.

**Why this priority**: la captura es la fuente de verdad del corpus; verla elimina la duda de
«datos copiados a mano».

**Independent Test**: pulsar *Ejecutar captura* con dos URLs y comprobar que el diagrama
ilumina las etapas y que al final aparecen descubiertas, descargadas, escritas, descartadas,
caracteres, tokens y duración.

**Acceptance Scenarios**:

1. **Given** la pestaña *Pipeline*, **When** se ejecuta una captura, **Then** cada etapa se
   marca como pendiente, activa o completada y se registra la URL procesada.
2. **Given** una captura con descartes, **When** termina, **Then** cada descarte aparece con
   su motivo (`robots.txt`, `duplicada por hash`, `error de descarga`).
3. **Given** un corte de red, **When** una URL falla, **Then** la captura continúa y la URL se
   cuenta como descarte en lugar de abortar el proceso.

---

### User Story 3 - Obtener resumen ejecutivo y FAQ del corpus (Priority: P3)

El equipo obtiene dos entregables derivados del mismo corpus: un resumen ejecutivo con cifras
y fuentes, y una FAQ para un cliente primerizo que declara los temas no cubiertos.

**Why this priority**: completa las tres tareas de LLM exigidas y demuestra que el corpus
sirve para más que preguntas sueltas.

**Independent Test**: `uv run carvajal summarize` y `uv run carvajal faq` generan
`docs/resumen.md` y `docs/faq.md` con fuentes y con la sección de temas ausentes.

**Acceptance Scenarios**:

1. **Given** el corpus completo, **When** se genera el resumen, **Then** cubre los cinco
   bloques (empresa, sectores, cifras, hitos, presencia) y escribe «no consta en el corpus»
   cuando falta un dato.
2. **Given** el corpus completo, **When** se genera la FAQ, **Then** incluye 10-12 preguntas
   con respuesta y fuente, agrupadas por tema, más una sección de temas no cubiertos.
3. **Given** una discrepancia entre fuentes, **When** aparece en el resumen, **Then** se
   declara en lugar de elegir una cifra arbitrariamente.

### Edge Cases

- **Página sin texto** (`sostenibilidad`): se conserva el documento con 0 caracteres y el
  asistente no la usa como fuente de datos.
- **Página dinámica** (`/trabaja-con-nosotros/`, `/nuestras-empresas/`): el extractor obtiene
  0 caracteres; se documenta como no cubierto.
- **Corpus modificado**: si cambia el cuerpo de un documento, el índice queda inválido y debe
  reconstruirse antes de responder.
- **Servicio de embeddings caído**: la consulta falla; el sistema debe informar y no devolver
  una respuesta sin recuperación.
- **Datos de contacto**: no existen en el sitio (404 verificados); el asistente declara la
  ausencia y remite a canales oficiales.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Capturar semillas explícitas del sitio oficial y de fuentes de prensa
  respetando `robots.txt` (con `Crawl-delay`), un `User-Agent` identificable, timeout y rate
  limit por host.
- **FR-002**: Escribir tres capas por documento — bronze (`data/raw/*.html`), silver
  (`data/processed/*.md`) y gold (`data/gold/*.md`) — más `data/gold/index.jsonl`.
- **FR-003**: Registrar en el *front matter* `url`, `title`, `section`, `domain`,
  `source_type`, `captured_at`, `hash` y `chars`.
- **FR-004**: Deduplicar por SHA-256 del contenido, sin descartar páginas casi vacías.
- **FR-005**: Responder solo con el corpus, citando fuente y distinguiendo oficial de tercero.
- **FR-006**: Declarar explícitamente la ausencia de un dato en lugar de inferirlo.
- **FR-007**: Generar un resumen ejecutivo con estructura fija y «no consta» para lo que falta.
- **FR-008**: Generar una FAQ de 10-12 entradas con fuente y sección de temas ausentes.
- **FR-009**: Mantener el índice FAISS en disco, reutilizarlo mientras la huella del corpus no
  cambie e invalidarlo cuando cambie.
- **FR-010**: Ofrecer una interfaz con cuatro pestañas (Pipeline, Resumen, FAQ, Asistente) con
  identidad visual de la empresa y fuentes citadas en cada respuesta.
- **FR-011**: Permitir evaluar el sistema con un conjunto versionado de preguntas y un informe
  reproducible.
- **FR-012**: Exponer todo por CLI (`capture`, `ask`, `summarize`, `faq`, `eval`, `status`).

### Key Entities

- **Documento Gold**: página curada con metadatos de procedencia y huella de contenido.
- **Chunk**: fragmento de 1000 caracteres con 200 de solape, con metadatos heredados.
- **Índice**: vectores FAISS + huella SHA-256 del corpus que los produjo.
- **Reporte de captura**: descubiertas, descargadas, escritas, descartes con motivo, caracteres,
  tokens y duración.
- **Respuesta**: texto generado + fuentes recuperadas (dominio, tipo, fragmento).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100 % de las respuestas incluyen al menos una fuente citada y su tipo.
- **SC-002**: 0 respuestas con datos de contacto, horarios o sedes inventados (el corpus no los
  contiene).
- **SC-003**: La evaluación de 30 preguntas arroja ≥ 85 % de respuestas completas y correctas
  (obtenido: 26/30 = 87 %, con 3 parciales y 1 abstención correcta).
- **SC-004**: Una pregunta con el índice caliente responde en menos de 10 s (medido: 3-8 s).
- **SC-005**: La captura completa de 24 URLs termina en menos de 2 min (medido: ~72 s).
- **SC-006**: La suite de pruebas pasa con `make check` (71 pruebas) y el CI está en verde.

## Assumptions

- El corpus se limita a contenido público; PDFs y contenido autenticado quedan fuera.
- Los embeddings se calculan con Ollama en local; el LLM es DeepSeek por API.
- La evaluación es cualitativa y la califica el equipo.
- El sistema es una interfaz de pruebas, no un producto en producción.
