# Feature Specification: Puerto LLMProvider y caso de uso AskQuestion

**Feature Branch**: `014-llm-askquestion`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-11-11
**Evidence**: [`CONSTITUTION.md` §9](../../CONSTITUTION.md) (2026-11-11); [plan de migración](../../docs/plans/0001-migracion-app.md) (W2.2 y W2.3); PRs #65, #66; [`tests/test_llm_adapters.py`](../../tests/test_llm_adapters.py), [`tests/test_ask.py`](../../tests/test_ask.py)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Preguntar y recibir una respuesta con citas (Priority: P1)

Una persona escribe una pregunta sobre Carvajal y recibe una respuesta redactada solo con el contexto recuperado, junto a las fuentes que la sustentan, identificadas por fragmento, documento, sección, capa y puntuación.

**Why this priority**: es el producto mínimo de la app web: respuesta fundada y citable, sin acoplar la generación a una interfaz concreta.

**Independent Test**: invocar el caso de uso con un índice y un proveedor dobles y comparar la respuesta y sus fuentes en [`tests/test_ask.py`](../../tests/test_ask.py).

**Acceptance Scenarios**:

1. **Given** dos fragmentos recuperados, **When** se pregunta, **Then** la respuesta incluye el texto del proveedor y las dos fuentes en orden de relevancia, con identificador, documento, sección, capa, puntuación y extracto.
2. **Given** una búsqueda sin resultados, **When** se pregunta, **Then** se declara la ausencia de información, con 0 fuentes y sin llamar al proveedor.
3. **Given** una pregunta vacía o solo con espacios, **When** se intenta responder, **Then** falla con error de valor antes de recuperar y sin tocar el proveedor.
4. **Given** un proveedor que devuelve una respuesta vacía, **When** se responde, **Then** se informa un error de generación.

---

### User Story 2 - Elegir proveedor y recibir la respuesta completa o por fragmentos (Priority: P1)

Quien opera el asistente elige DeepSeek o un modelo local de Ollama con una variable de configuración, y la interfaz consume la respuesta de dos formas: completa, para conservarla, o por fragmentos, para pintarla mientras llega; el caso de uso solo conoce el puerto.

**Why this priority**: el costo y la disponibilidad del modelo cambian por entorno, y el chat necesita la primera palabra antes de esperar toda la generación, sin reescribir la lógica de preguntas.

**Independent Test**: construir el proveedor con cada configuración y ejecutar el contrato del puerto, completo y por fragmentos, con un chat doble en [`tests/test_llm_adapters.py`](../../tests/test_llm_adapters.py).

**Acceptance Scenarios**:

1. **Given** una configuración que pide DeepSeek u Ollama, **When** se construye el proveedor, **Then** se obtiene el adaptador correspondiente y cumple el puerto.
2. **Given** un nombre de proveedor con espacios o mayúsculas, **When** se construye, **Then** se normaliza, un proveedor desconocido se rechaza y `claude` se rechaza con un mensaje que remite a la interfaz heredada.
3. **Given** una clave, un modelo o una URL de DeepSeek vacíos, o un modelo de Ollama vacío, **When** se construye, **Then** falla con un error que nombra la variable culpable.
4. **Given** un proveedor que emite tres trozos, **When** se consume el modo por fragmentos, **Then** llegan en orden, los trozos vacíos se omiten y la concatenación coincide con la respuesta completa.
5. **Given** una pregunta sin contexto recuperado, **When** se consume el modo por fragmentos, **Then** se emite el mensaje de ausencia sin llamar al proveedor.

---

### User Story 3 - Proteger el prompt frente al contenido del corpus (Priority: P3)

Quien audita el sistema comprueba que el texto recuperado y la pregunta no pueden cerrar ni abrir los bloques del prompt: el contenido se trata como dato y no como instrucción.

**Why this priority**: una inyección desde un fragmento del corpus podría cambiar el comportamiento del modelo o fabricar citas.

**Independent Test**: introducir etiquetas de delimitación dentro de un fragmento y de la pregunta y revisar el prompt enviado en [`tests/test_ask.py`](../../tests/test_ask.py).

**Acceptance Scenarios**:

1. **Given** fragmentos recuperados, **When** se compone el prompt, **Then** cada uno aparece numerado con su documento, sección y capa dentro del bloque de contexto.
2. **Given** un fragmento o una pregunta que contienen `<contexto>` o `<pregunta>`, **When** se compone el prompt, **Then** los delimitadores reales aparecen una sola vez y los del texto quedan escapados.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Debe existir un puerto de generación de texto con respuesta completa y respuesta por fragmentos, ambas con mensaje de sistema y prompt, y todo adaptador debe satisfacerlo.
- **FR-002**: Deben existir adaptadores para DeepSeek (API compatible con OpenAI) y Ollama (modelo local), elegidos por la configuración del proveedor.
- **FR-003**: La fábrica debe normalizar el nombre del proveedor, rechazar los no soportados y remitir `claude` a la interfaz heredada.
- **FR-004**: Cada adaptador debe configurar el cliente con temperatura 0; DeepSeek exige clave, modelo y URL no vacíos, y Ollama exige modelo y una ventana de contexto configurable (32 768 por defecto).
- **FR-005**: Los fallos del proveedor y el contenido no textual deben traducirse a un error de generación conservando la causa original.
- **FR-006**: El caso de uso debe componer un contexto numerado con documento, sección y capa, delimitar contexto y pregunta, normalizar la pregunta y usar un límite de fragmentos por defecto de 8, rechazando límites no positivos.
- **FR-007**: La respuesta debe contener el texto recortado y las fuentes en orden de relevancia, con identificador, documento, sección, capa, puntuación y extracto de hasta 280 caracteres cortado en palabra completa con elipsis.
- **FR-008**: Sin fragmentos no se debe consultar al proveedor: se devuelve un mensaje estable de ausencia; una pregunta vacía falla antes de recuperar y una respuesta vacía del proveedor es un error.
- **FR-009**: El modo por fragmentos debe emitir los trozos en orden, omitir los vacíos y, sin contexto, emitir el mensaje de ausencia sin llamar al proveedor.
- **FR-010**: La neutralización de delimitadores debe impedir que el texto del corpus o la pregunta abran o cierren los bloques del prompt.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: La respuesta de prueba devuelve 2 fuentes en orden (puntuaciones 0.92 y 0.81), con extracto normalizado (`Fundada en 1904`) y el texto exacto del proveedor.
- **SC-002**: Los 3 fallos de proveedor probados (conexión, modelo ausente y error inesperado) se convierten en un error de generación con la causa original y 0 llamadas de red.
- **SC-003**: El prompt contiene los 2 bloques numerados y cada delimitador exactamente 1 vez; un texto con etiquetas incrustadas queda escapado y no añade bloques.
- **SC-004**: Sin contexto recuperado: 0 llamadas al proveedor, 0 fuentes y mensaje de ausencia estable.
- **SC-005**: La fábrica resuelve los 2 proveedores soportados y rechaza los 2 casos no soportados (nombre desconocido y `claude`) sin construir cliente.

## Assumptions

- LangChain sigue detrás de los adaptadores como detalle de implementación; el caso de uso depende del puerto y no del framework, y `claude` queda solo en la interfaz Streamlit heredada.
- El puerto de embeddings y este caso de uso se añadieron el 2026-11-11; el streaming SSE sobre HTTP y la recuperación híbrida llegaron después, en otras capacidades.
- Las pruebas usan un chat doble: no hay red, credenciales ni proveedor real.
