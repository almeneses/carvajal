# Feature Specification: Interfaz de pruebas con el pipeline visible

**Feature Branch**: `006-interfaz-pipeline`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-10-29
**Evidence**: [CONSTITUTION.md §9](../../CONSTITUTION.md) (2026-10-29); PRs #4, #7; [docs/informe.md](../../docs/informe.md) §4.5; [docs/ui-pipeline.png](../../docs/ui-pipeline.png), [docs/ui-metricas.png](../../docs/ui-metricas.png); [tests/test_ui.py](../../tests/test_ui.py)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ver la captura avanzar por etapas en pantalla (Priority: P1)

Con `make app` abierta, quien evalúa el sistema ejecuta una captura desde la pestaña Pipeline y ve las siete etapas —descubrir, `robots.txt`, descargar, extraer, deduplicar, enriquecer e indexar—, la URL en curso y el avance de cada bloque mientras el proceso corre. Al terminar, la última etapa queda completada.

**Why this priority**: la rúbrica exige ver el scraping en funcionamiento, no solo el Q&A; verlo elimina la duda de «datos copiados a mano».

**Independent Test**: ejecutar una captura de dos URLs desde la pestaña Pipeline y comprobar que cada etapa pasa de pendiente a activa y a completada y que al final aparece el reporte ([tests/test_ui.py](../../tests/test_ui.py)).

**Acceptance Scenarios**:

1. **Given** la pestaña Pipeline recién abierta, **When** se ejecuta la captura, **Then** las siete etapas aparecen numeradas y avanzan en orden.
2. **Given** una captura en curso, **When** se procesa una URL, **Then** se registra la etapa, la URL y su detalle (bytes descargados, caracteres extraídos, tokens estimados).
3. **Given** un evento de una etapa no declarada, **When** llega a la interfaz, **Then** el estado del diagrama no se rompe ni se corrompe.

---

### User Story 2 - Leer las métricas de la corrida y el motivo de cada descarte (Priority: P1)

Al terminar, el panel muestra descubiertas, descargadas, escritas, descartadas, caracteres, tokens estimados y duración. Cada URL descartada aparece con su motivo —`robots.txt`, duplicada por hash o error de descarga— y arriba se resume el estado del corpus: documentos totales, núcleo, extendidos, oficiales y terceros.

**Why this priority**: sin las métricas en pantalla no se puede evaluar la captura; sin el motivo, un descarte es un número mudo.

**Independent Test**: un reporte con 5 descubiertas, 3 escritas y dos descartes muestra «Descartadas 2», «Tokens (est.) 300» y las filas `robots.txt` y `duplicada por hash` ([tests/test_ui.py](../../tests/test_ui.py)).

**Acceptance Scenarios**:

1. **Given** una captura con descartes, **When** termina, **Then** cada descarte aparece con su motivo.
2. **Given** el corpus Gold cargado, **When** se abre la interfaz, **Then** se cuentan documentos, núcleo, extendidos, oficiales y terceros.
3. **Given** una captura sin descartes, **When** termina, **Then** el conteo de descartes es 0 y la duración se muestra en segundos.

---

### User Story 3 - Reconocer la marca y elegir el alcance de la captura (Priority: P2)

La interfaz usa el logo SVG oficial y la paleta de Carvajal, y un diagrama de bloques ilumina la etapa activa: gris para pendiente, color para activa, marca de completado para hecha y rojo para error. Un selector permite capturar solo oficiales, solo terceros o todo el alcance.

**Why this priority**: la identidad institucional y la lectura del proceso sin depender del log completan una interfaz de pruebas presentable.

**Independent Test**: [docs/ui-pipeline.png](../../docs/ui-pipeline.png) y [docs/ui-metricas.png](../../docs/ui-metricas.png) muestran la interfaz con marca, diagrama y métricas; las pruebas de la interfaz demuestran los cuatro estados y la selección de alcance.

**Acceptance Scenarios**:

1. **Given** que la captura avanza, **When** entra en una etapa nueva, **Then** la anterior queda completada y la nueva aparece activa.
2. **Given** una etapa que termina con error, **When** se pinta, **Then** se distingue del resto y no se confunde con completada.
3. **Given** la selección de alcance, **When** se elige oficial, terceros o todo, **Then** se usan 18, 6 o 24 semillas respectivamente.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Ofrecer una pestaña Pipeline que ejecuta la captura desde la interfaz y muestra las siete etapas del proceso.
- **FR-002**: Representar cada etapa en estado pendiente, activa o completada, y en error cuando corresponda.
- **FR-003**: Registrar por URL la etapa, la URL y un detalle legible del progreso.
- **FR-004**: Mostrar descubiertas, descargadas, escritas, descartadas, caracteres, tokens estimados y duración de la corrida.
- **FR-005**: Listar cada descarte con su motivo: `robots.txt`, duplicada por hash o error de descarga.
- **FR-006**: Resumir el corpus en documentos totales, núcleo, extendidos, oficiales y terceros.
- **FR-007**: Permitir elegir el alcance de la captura: solo oficiales, solo terceros o todo.
- **FR-008**: Usar la identidad visual de Carvajal (logo oficial y paleta de marca) y un diagrama de bloques que refleje el avance.
- **FR-009**: Mostrar en la respuesta del asistente las fuentes citadas con dominio, tipo de fuente, capa y categoría.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Las siete etapas numeradas están visibles en toda corrida y la última queda completada al terminar.
- **SC-002**: Cada corrida muestra las siete métricas y una fila por descarte con su motivo.
- **SC-003**: El selector de alcance entrega exactamente 18 semillas oficiales, 6 de terceros o 24 en total.
- **SC-004**: El resumen del corpus informa 24 documentos (18 oficiales y 6 terceros) y 479 documentos extendidos.
- **SC-005**: Una captura de dos URLs desde la interfaz termina con el reporte completo y sin errores de estado en el diagrama.

## Assumptions

- La interfaz es una herramienta de pruebas local, no un producto en producción.
- Consume las mismas métricas del reporte de captura; no calcula cifras propias.
- Se ejecuta una captura a la vez por instancia.
