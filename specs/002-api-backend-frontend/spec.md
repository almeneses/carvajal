# Feature Specification: Aplicación web del asistente, con backend y frontend desplegados

**Feature Branch**: `002-api-backend-frontend`
**Created**: 2026-11-03
**Updated**: 2026-11-15
**Status**: Implemented (as-built; olas 0–3, 5 y 6, pendientes en [`tasks.md`](./tasks.md))
**Implemented**: 2026-11-08 → 2026-11-14
**Evidence**: `CONSTITUTION.md` §9 (2026-11-08 → 2026-11-14); [`docs/aceptacion-m2.md`](../../docs/aceptacion-m2.md); PRs #33–#120
**Input**: «La aplicación debe correr en un servidor y tener su backend y su frontend.»
**Decisiones**: [`ADR-0001`](../../docs/decisions/0001-stack-app-chat.md) elige el stack;
[`plan 0001`](../../docs/plans/0001-migracion-app.md) lo divide en olas.

## Contexto

Hoy el asistente es un proceso Streamlit que carga el corpus desde disco en la máquina de
quien lo ejecuta: no hay sesiones, ni concurrencia, ni trabajos en segundo plano, y el corpus
no se comparte. Esta spec define la aplicación objetivo: **un servicio desplegado en un
servidor**, con backend y frontend separados en responsabilidades, y una base de datos como
fuente de verdad del corpus.

Durante la implementación el frontend se resolvió en dos capas: un **shell renderizado en
servidor con HTMX** para las actualizaciones parciales (puerto 8000) y un **frontend propio
con pestañas** (puerto 8001) que consume un contrato JSON de lectura (`/api/corpus`,
`/api/documents/*`). La captura se ejecuta por CLI, por cron o desde la pantalla de
Configuración. Las olas 0–3, 5 y 6 quedaron entregadas el 2026-11-14; la ola 4 (autenticación
y cuotas) y la 7 (retiro del prototipo) siguen pendientes, y Streamlit continúa disponible
como extra `legacy` hasta esa ola 7.

**Fuera de alcance**: framework de JavaScript con SPA en el cliente, Kubernetes, despliegue
multi-región y aplicación móvil.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Consultar el asistente desde el navegador (Priority: P1)

Una persona entra a la aplicación, escribe una pregunta sobre Carvajal y recibe una respuesta
con las fuentes citadas, sin instalar nada ni descargar el corpus.

**Why this priority**: es el producto. Sin esto no hay app que desplegar.

**Independent Test**: con el servicio arriba y el corpus importado, abrir la página de chat,
preguntar por un tema cubierto y comprobar la respuesta con sus fuentes.

**Acceptance Scenarios**:

1. **Given** el servicio arriba, **When** se abre la página de chat, **Then** se ve el campo
   de pregunta y el estado del corpus (documentos disponibles).
2. **Given** una pregunta cubierta, **When** se envía, **Then** la respuesta llega en streaming
   y termina con `sources[]` que incluyen `slug`, `domain`, `source_type` y el fragmento usado.
3. **Given** una pregunta fuera del corpus, **When** se envía, **Then** la respuesta declara
   que no dispone del dato; no inventa ni usa conocimiento externo.
4. **Given** el servicio de embeddings caído, **When** se envía una pregunta, **Then** se
   muestra un mensaje accionable, sin *traceback*.
5. **Given** una recarga de página, **When** la sesión sigue viva, **Then** se conserva el
   historial de la conversación.

---

### User Story 2 - Explorar el corpus (Priority: P2)

Una persona revisa qué documentos componen el corpus, filtra por sección y capa, y abre un
documento para leer el texto y su procedencia.

**Why this priority**: da transparencia sobre las respuestas y es la base de la confianza en
las citas.

**Independent Test**: abrir el listado, filtrar por sección y abrir un documento; comprobar
metadatos, markdown y enlace a la fuente original.

**Acceptance Scenarios**:

1. **Given** el corpus importado, **When** se abre el listado, **Then** se ven los documentos
   con título, sección, capa y fecha de captura, paginados.
2. **Given** el listado, **When** se filtra por sección o por capa, **Then** el resultado se
   actualiza sin recargar la página completa.
3. **Given** un documento, **When** se abre su detalle, **Then** se ve el markdown renderizado,
   la URL de origen, el tipo de fuente y el hash de contenido.
4. **Given** un documento con más de una captura, **When** se consulta el historial, **Then**
   se puede abrir la versión anterior.

---

### User Story 3 - Operar el servicio (Priority: P3)

Quien administra despliega, vigila y actualiza la aplicación sin intervenir el código.

**Why this priority**: «correr en un servidor» implica operación: arranque, salud, respaldo y
reversión.

**Independent Test**: desde un servidor limpio, seguir el procedimiento y obtener una URL con
HTTPS que responde al chequeo de salud y a una pregunta completa.

**Acceptance Scenarios**:

1. **Given** el servidor y las credenciales, **When** se ejecuta el procedimiento de despliegue,
   **Then** el servicio queda arriba con HTTPS y sin secretos en la imagen.
2. **Given** un reinicio del servidor, **When** vuelve a estar arriba, **Then** el corpus está
   disponible sin reimportarlo.
3. **Given** la base caída, **When** se consulta el chequeo de disponibilidad, **Then** responde
   «no listo» con la causa, sin afectar el chequeo de vida.
4. **Given** una migración pendiente, **When** arranca el servicio, **Then** las migraciones se
   aplican en orden y el servicio no arranca a medias si una falla.

---

### User Story 4 - Mantener el corpus al día (Priority: P4)

Quien administra captura contenido nuevo y lo incorpora a la base sin duplicar ni perder el
historial.

**Why this priority**: el corpus cambia; la app debe reflejarlo sin reconstrucciones manuales.

**Independent Test**: importar `data/gold/` dos veces seguidas y comprobar que los conteos no
cambian y que coinciden con `index.jsonl`.

**Acceptance Scenarios**:

1. **Given** `data/gold/` actualizado, **When** se ejecuta el importador, **Then** los
   documentos nuevos entran, los existentes sin cambios se omiten y las versiones nuevas se
   registran.
2. **Given** una segunda ejecución sin cambios, **When** se importa de nuevo, **Then** el
   número de documentos, fragmentos y versiones no varía.
3. **Given** una captura programada, **When** termina, **Then** la web muestra su estado, la
   fecha y los descartes.

### Edge Cases

- Base de datos caída al arrancar: el chequeo de vida sigue respondiendo; el de disponibilidad
  explica la causa.
- Migración fallida: el servicio no queda en un estado intermedio.
- Dos importaciones simultáneas: se serializan; no hay duplicados.
- Corpus extendido presente: el arranque no reconstruye nada; la capa extendida solo participa
  en recuperación.
- Varias preguntas concurrentes: ninguna bloquea al resto de usuarios.
- Proveedor del LLM sin cuota o sin red: error tipado y accionable, nunca *traceback*.
- Costo: el consumo del LLM se puede acotar por usuario o por clave.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Servir las páginas desde el backend (renderizado en servidor) con actualizaciones
  parciales; el navegador no ejecuta la lógica del RAG.
- **FR-002**: Exponer el contrato HTTP del asistente — consulta con streaming, listado de
  corpus, detalle de documento, salud y disponibilidad — documentado con OpenAPI.
- **FR-003**: Usar la base de datos como fuente de verdad del corpus: documentos, fragmentos y
  versiones. El índice vectorial se resuelve con Qdrant en el M2; pgvector queda como adaptador
  heredado del prototipo.
- **FR-004**: Importar `data/gold/` de forma idempotente, marcando la procedencia de cada
  documento (oficial, tercero, colaborador).
- **FR-005**: Toda respuesta incluye fuentes citadas con enlace al documento de origen.
- **FR-006**: Separar el chequeo de vida del de disponibilidad y devolver la causa cuando algo
  falta.
- **FR-007**: Versionar el esquema con migraciones aplicables desde cero y reversibles.
- **FR-008**: Leer secretos y configuración del entorno; nada se versiona ni se hornea en la
  imagen.
- **FR-009**: Empaquetar el servicio en contenedores y desplegarlo con un procedimiento
  reproducible que incluya HTTPS, respaldo y reversión.
- **FR-010**: Dejar preparada la autenticación y las cuotas por usuario, que se activan en una
  ola posterior sin cambiar el contrato de las páginas.
- **FR-011**: Retirar Streamlit al final de la migración; el repositorio no mantiene dos
  interfaces en producción.
- **FR-012**: Registrar cada petición con duración y resultado, sin datos sensibles.

### Key Entities

- **Documento**: unidad del corpus con `slug`, `url`, `title`, `section`, `layer`,
  `source_type`, markdown, hash de contenido y fecha de captura.
- **Fragmento**: porción de un documento, con posición, sección, capa y su vector.
- **Versión de documento**: captura anterior de un documento, identificada por hash.
- **Pregunta/Respuesta**: pregunta, respuesta y fuentes citadas.
- **Ejecución de captura**: alcance, resultado, descartes y fecha.
- **Usuario** (ola 4): identidad, cuota y consumo.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: El primer byte de una página responde en menos de 500 ms (p95).
- **SC-002**: La primera pieza de la respuesta llega en menos de 3 s con el índice caliente
  (p95).
- **SC-003**: El 100 % de las respuestas incluye al menos una fuente citada.
- **SC-004**: `alembic upgrade head` desde una base vacía tarda menos de 2 minutos.
- **SC-005**: Importar el corpus dos veces no cambia los conteos y estos coinciden con
  `index.jsonl`.
- **SC-006**: El despliegue desde cero se completa en menos de 30 minutos siguiendo el
  documento.
- **SC-007**: Ningún secreto aparece en la imagen ni en el historial de Git.
- **SC-008**: La suite de pruebas sigue verde en CI en cada PR.

## Assumptions

- **Infraestructura**: un solo servidor con Docker Compose; las 100 K cuentas proyectadas no
  exigen clúster ni colas externas.
- **Embeddings**: `nomic-embed-text` local (768 dimensiones); si el destino no puede ejecutar
  Ollama, se usa un proveedor por API y se recalcula.
- **Autenticación**: llega en la ola 4. El gate con clave de la demo estaba previsto y se
  eliminó el 2026-11-14: la captura y la carga de documentos son públicas y su defensa son el
  límite por IP, el guard SSRF y los límites de tamaño.
- **Corpus**: `data/gold/` sigue siendo el entregable versionado; la base es derivada y
  reconstruible desde él.
- **Entrega por olas**: los criterios de aceptación de cada ola están en
  [`docs/plans/0001-migracion-app.md`](../../docs/plans/0001-migracion-app.md).

## Estado de implementación (as-built)

| Historia | Estado | Evidencia |
|---|---|---|
| US1 — Consultar el asistente | ✅ Entregada | `docs/aceptacion-m2.md`; `tests/test_api_chat.py`, `tests/test_api_stream.py` |
| US2 — Explorar el corpus | ✅ Entregada | `tests/test_api_corpus.py`, `tests/test_api_documents.py`, `tests/test_api_read.py` |
| US3 — Operar el servicio | 🟡 Parcial: despliegue por IP y HTTP, sin dominio ni TLS | `docs/despliegue-ovh.md`; `tests/test_api.py` |
| US4 — Mantener el corpus al día | 🟡 Parcial: importación idempotente y captura desde Configuración; falta la captura programada | `tests/test_import_corpus.py`, `tests/test_capture_jobs.py` |

Quedan pendientes de esta spec la autenticación y las cuotas por usuario (ola 4), el retiro de
Streamlit y FAISS (ola 7) y el endurecimiento del despliegue con TLS y respaldos. El detalle
está en [`tasks.md`](./tasks.md) y el cómo se resolvió, en [`plan.md`](./plan.md).
