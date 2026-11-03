# Feature Specification: Backend y frontend desplegados en un servidor

**Feature Branch**: `002-api-backend-frontend`
**Created**: 2026-11-03
**Status**: Draft
**Input**: «La aplicación debe correr en un servidor y tener su backend y su frontend.»

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Consumir el asistente por API (Priority: P1)

Un cliente (la interfaz u otro sistema) obtiene respuestas del asistente mediante HTTP, sin
depender de Streamlit ni de que el corpus viva en su máquina.

**Why this priority**: sin API no existe backend; todo lo demás se apoya en ella.

**Independent Test**: levantar el servicio y ejecutar `POST /ask` con una pregunta cubierta;
comprobar que devuelve la respuesta y las fuentes en JSON.

**Acceptance Scenarios**:

1. **Given** el servicio arriba, **When** se llama `GET /health`, **Then** responde 200 con el
   estado del índice y el número de documentos.
2. **Given** una pregunta cubierta, **When** `POST /ask`, **Then** devuelve `answer` y
   `sources[]` con `slug`, `domain`, `source_type` y fragmento.
3. **Given** una pregunta fuera del corpus, **When** `POST /ask`, **Then** la respuesta declara
   que no dispone del dato (nunca inventa).
4. **Given** el servicio de embeddings caído, **When** `POST /ask`, **Then** responde 503 con
   un mensaje accionable, no un *traceback*.

---

### User Story 2 - Interfaz que consume la API (Priority: P2)

El usuario final usa una interfaz web que no ejecuta el RAG en su proceso: solo llama a la API
y muestra resultados.

**Why this priority**: es la separación pedida entre frontend y backend.

**Independent Test**: abrir la interfaz con la API arriba y completar una pregunta; luego
apagar la API y comprobar que la interfaz muestra un error claro.

**Acceptance Scenarios**:

1. **Given** la API disponible, **When** el usuario pregunta, **Then** la interfaz muestra la
   respuesta y las fuentes citadas.
2. **Given** la API caída, **When** el usuario pregunta, **Then** la interfaz informa el
   problema y ofrece reintentar.
3. **Given** una configuración por entorno, **When** cambia `API_URL`, **Then** la interfaz
   apunta al nuevo backend sin cambios de código.

---

### User Story 3 - Despliegue reproducible en servidor (Priority: P3)

El equipo despliega los dos servicios en un servidor con HTTPS, secretos fuera del repositorio
y un procedimiento repetible.

**Why this priority**: cierra el requisito de «correr en un servidor».

**Independent Test**: seguir el procedimiento desde un clon limpio y obtener una URL pública
que responde `GET /health` y una pregunta completa.

**Acceptance Scenarios**:

1. **Given** el repositorio y las credenciales, **When** se sigue el procedimiento, **Then** los
   dos servicios quedan arriba con HTTPS y sin secretos en la imagen.
2. **Given** un reinicio del servidor, **When** vuelve a estar arriba, **Then** el índice está
   disponible sin reconstruirse.

### Edge Cases

- Índice ausente en la imagen: la API debe fallar al arrancar con un mensaje claro, no en la
  primera pregunta.
- Dos solicitudes simultáneas que intenten reconstruir el índice: deben serializarse.
- Capturas largas: la petición no puede bloquear al resto de usuarios.
- Corpus extendido activado: el arranque no puede tardar horas (índice precalculado).
- Costos: el uso de la API del LLM debe poder limitarse por clave.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Exponer una API HTTP con, al menos, `GET /health`, `GET /corpus`, `POST /ask`,
  `POST /summary`, `POST /faq` y `POST /capture`.
- **FR-002**: Documentar el contrato con OpenAPI accesible en `/docs` y esquemas Pydantic.
- **FR-003**: Mantener el retriever y el índice en memoria como servicio único, con exclusión
  mutua durante reconstrucciones y capturas.
- **FR-004**: Devolver errores tipados (4xx/5xx) con mensajes accionables.
- **FR-005**: Consumir la API desde la interfaz mediante `API_URL`, con modo local de respaldo
  para demos sin red.
- **FR-006**: Empaquetar ambos servicios en contenedores con el índice construido en la imagen.
- **FR-007**: Gestionar secretos por entorno (`DEEPSEEK_API_KEY`, token de embeddings) sin
  versionarlos.
- **FR-008**: Permitir elegir el proveedor de embeddings (`ollama` o API) por configuración.
- **FR-009**: Incluir healthchecks y registro de peticiones con duración.
- **FR-010**: Documentar el procedimiento de despliegue y reversión.

### Key Entities

- **Pregunta/Respuesta**: pregunta del usuario, respuesta y lista de fuentes con procedencia.
- **Documento**: metadatos del corpus (`slug`, `section`, `domain`, `source_type`).
- **Trabajo de captura**: alcance, límite, reporte y descartes.
- **Estado del índice**: huella, fecha de construcción y vigencia.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `GET /health` responde en menos de 1 s y refleja si el índice está vigente.
- **SC-002**: Una pregunta con el índice caliente responde en menos de 10 s (p95).
- **SC-003**: 100 % de las respuestas de la API incluyen fuentes citadas.
- **SC-004**: El despliegue desde cero se completa en menos de 30 min siguiendo el documento.
- **SC-005**: La suite de pruebas cubre el contrato de cada endpoint y sigue en verde en CI.
- **SC-006**: Ningún secreto aparece en la imagen ni en el historial de Git.

## Assumptions

- **Frontend**: se mantiene Streamlit como cliente de la API (menor riesgo y 1 día de trabajo);
  React queda como evolución futura sin cambiar el contrato.
- **Destino**: se decidirá entre Cloud Run (con embeddings por API) y una instancia pequeña con
  Docker Compose y Ollama. Ambas opciones usan los mismos contenedores.
- **Embeddings**: si el destino no puede ejecutar Ollama, se usa un proveedor por API y se
  reconstruye el índice en el build.
- **Autenticación**: clave simple por cabecera para la demo; OAuth queda fuera de alcance.
