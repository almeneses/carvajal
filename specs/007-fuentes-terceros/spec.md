# Feature Specification: Fuentes de terceros para los indicadores financieros

**Feature Branch**: `007-fuentes-terceros`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-10-29
**Evidence**: [CONSTITUTION.md §9](../../CONSTITUTION.md) (2026-10-29); PR #9 y commit `477e382`; [docs/alcance.md](../../docs/alcance.md) §5, [docs/arquitectura.md](../../docs/arquitectura.md) §2; [tests/test_gold.py](../../tests/test_gold.py), [tests/test_pipeline.py](../../tests/test_pipeline.py), [tests/test_fetching.py](../../tests/test_fetching.py); [data/gold/index.jsonl](../../data/gold/index.jsonl)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Obtener las cifras financieras que el sitio oficial no entrega en HTML (Priority: P1)

Un cliente pregunta por ingresos, EBITDA, utilidad, activos, pasivos, patrimonio o empleo. El sitio oficial pinta parte de esas cifras con contadores dinámicos y publica los estados financieros completos en PDF, fuera del alcance del pipeline. Se incorporaron seis notas de prensa económica que publican las mismas cifras en texto plano; el asistente responde con la cifra y con el medio y la fecha.

**Why this priority**: sin estas fuentes el asistente no puede cubrir la categoría de inversión y cifras de la matriz de alcance; es el hueco que motivó la ampliación del corpus.

**Independent Test**: las preguntas de cifras financieras de [docs/resultados.md](../../docs/resultados.md) responden con cantidades y medio, y `uv run carvajal status` cuenta 18 documentos oficiales y 6 de terceros.

**Acceptance Scenarios**:

1. **Given** una pregunta por los ingresos de 2024, **When** se responde, **Then** se cita la cifra con su medio y fecha y se aclara que la fuente es de terceros.
2. **Given** una pregunta por el EBITDA de 2024, **When** se responde, **Then** se declara que no consta en el corpus y se ofrece el dato disponible de septiembre de 2025 con su fuente.
3. **Given** que las cifras oficiales viven en contadores dinámicos o en PDFs, **When** se define el alcance, **Then** esas fuentes quedan fuera y el motivo se documenta.

---

### User Story 2 - Distinguir lo oficial de lo publicado por terceros (Priority: P1)

Cada documento registra su dominio y su tipo de fuente. El asistente atribuye cada cifra a su medio y fecha, aclara que no es comunicación oficial, y cuando dos fuentes discrepan —10 frente a 17 países de presencia, o 3,9 frente a 3,96 billones de ingresos— declara ambas en lugar de elegir una.

**Why this priority**: la confianza depende de la trazabilidad; una cifra sin medio es indistinguible de una inventada.

**Independent Test**: el índice Gold registra 6 documentos con tipo `tercero` y sección `prensa`; [tests/test_gold.py](../../tests/test_gold.py) demuestra que solo el dominio de Carvajal se considera oficial.

**Acceptance Scenarios**:

1. **Given** un documento de un dominio externo, **When** se enriquece, **Then** queda con tipo de fuente `tercero` y sección `prensa`.
2. **Given** una cifra de tercero en una respuesta, **When** se redacta, **Then** se indica el medio y la fecha y que no es comunicación oficial de la empresa.
3. **Given** dos fuentes con cifras distintas, **When** se responde, **Then** se declaran ambas y se advierte la discrepancia.

---

### User Story 3 - Respetar las reglas particulares de cada medio (Priority: P2)

Los medios de prensa declaran reglas propias en su `robots.txt`: grupos `User-agent` repetidos, combinaciones de `Allow` y `Disallow`, y `Crawl-delay` mayor que el mínimo. Antes de interpretarlo, el archivo se normaliza para conservar las reglas de todos los grupos, aplicar la coincidencia de ruta más larga y usar el `Crawl-delay` declarado.

**Why this priority**: sin normalizar, el segundo grupo del archivo se descarta y podrían pedirse rutas prohibidas; es la condición para que las fuentes de terceros sean legales y estables.

**Independent Test**: [tests/test_fetching.py](../../tests/test_fetching.py) demuestra la fusión de grupos repetidos (caso real de La República), la coincidencia más larga y el uso de un `Crawl-delay` de 3 s.

**Acceptance Scenarios**:

1. **Given** un `robots.txt` con dos grupos `User-agent: *`, **When** se normaliza, **Then** se conservan las reglas de ambos grupos.
2. **Given** `Allow: /empresas/` y `Disallow: /empresas/privado`, **When** se evalúa una ruta, **Then** gana la regla más específica.
3. **Given** un `Crawl-delay: 3` en el archivo, **When** se encadenan peticiones al host, **Then** se esperan 3 s entre ellas.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Mantener semillas de prensa separadas de las oficiales, con seis URLs de La República, El País y Cali Escribe.
- **FR-002**: Clasificar como oficial solo el dominio de Carvajal y sus subdominios; cualquier otro dominio es tercero.
- **FR-003**: Registrar dominio, tipo de fuente y sección `prensa` en el front matter de cada documento de terceros.
- **FR-004**: Atribuir cada cifra de tercero a su medio y fecha y aclarar que no es comunicación oficial.
- **FR-005**: Declarar las discrepancias entre fuentes en lugar de escoger una cifra arbitrariamente.
- **FR-006**: Normalizar `robots.txt` de cada medio —grupos repetidos, `Allow` y `Disallow`, coincidencia más larga— y respetar su `Crawl-delay`.
- **FR-007**: Capturar solo HTML con texto extraíble; PDFs, contadores dinámicos y contenido autenticado quedan fuera.
- **FR-008**: Exponer el conteo de documentos oficiales y de terceros en el estado del corpus.
- **FR-009**: Cubrir ingresos, EBITDA, margen, utilidad, activos, pasivos, patrimonio y empleo con al menos una cifra y su fuente.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: El corpus queda en 24 documentos: 18 oficiales y 6 de terceros ([docs/alcance.md](../../docs/alcance.md) §5).
- **SC-002**: El 100 % de los documentos de terceros registra tipo `tercero` y sección `prensa`.
- **SC-003**: Cero cifras de terceros aparecen sin medio y fecha en las respuestas de la evaluación.
- **SC-004**: La discrepancia de presencia (10 frente a 17 países) y la de ingresos (3,9 frente a 3,96 billones) quedan declaradas.
- **SC-005**: Ninguna petición a los medios vulnera su `robots.txt` y el `Crawl-delay` declarado se respeta en el 100 % de los casos.

## Assumptions

- Los medios publican en texto plano las cifras y sus propias reglas de rastreo.
- Las cifras de prensa son datos de terceros, no comunicación oficial de Carvajal.
- Los PDFs de estados financieros quedan fuera del alcance de esta fase.
