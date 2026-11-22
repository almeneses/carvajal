# Feature Specification: Captura responsable del corpus y capas Medallion

**Feature Branch**: `003-captura-responsable`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-10-27 → 2026-10-29
**Evidence**: [CONSTITUTION.md §9](../../CONSTITUTION.md) (2026-10-21, 2026-10-27 y 2026-10-29); PRs #3, #6; [tests/test_fetching.py](../../tests/test_fetching.py), [tests/test_pipeline.py](../../tests/test_pipeline.py), [tests/test_gold.py](../../tests/test_gold.py), [tests/test_extracting.py](../../tests/test_extracting.py)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Capturar solo las semillas declaradas, con cortesía con la web (Priority: P1)

La persona responsable del corpus ejecuta `uv run carvajal capture` y obtiene en una corrida las 24 páginas declaradas —18 del sitio oficial y 6 de prensa— sin rastrear enlaces descubiertos. Antes de cada descarga se consulta `robots.txt` del host, se respeta su `Crawl-delay` y se espera al menos 1 s entre peticiones al mismo host. Una ruta prohibida, un `timeout` o un 404 se registran como descarte y la captura continúa.

**Why this priority**: es el requisito de captura responsable; sin alcance explícito ni cortesía con el servidor, el corpus no es reproducible ni defendible.

**Independent Test**: `uv run carvajal capture` sobre las 24 semillas termina con 24/24 escritas, y [tests/test_fetching.py](../../tests/test_fetching.py) demuestra el bloqueo por `robots.txt`, el uso del `Crawl-delay`, el rate limit por host y la conversión de un timeout en descarte.

**Acceptance Scenarios**:

1. **Given** una lista explícita de semillas, **When** se ejecuta la captura, **Then** solo se piden esas URLs y el reporte cuenta descubiertas, descargadas y escritas.
2. **Given** un `robots.txt` con una regla `Disallow`, **When** la captura intenta una ruta prohibida, **Then** no se descarga y la URL aparece como descarte `robots.txt`.
3. **Given** un host que declara un `Crawl-delay` mayor que 1 s, **When** se encadenan peticiones, **Then** la espera usa el valor declarado, no el mínimo.
4. **Given** una URL que excede el timeout de red, **When** falla, **Then** se registra como error de esa URL y la captura no se aborta.

---

### User Story 2 - Auditar cada documento en las tres capas (Priority: P1)

El equipo puede reconstruir de dónde salió cada documento. Cada captura deja el HTML crudo (Bronze), el Markdown limpio (Silver) y el documento curado con front matter (Gold), más un índice JSONL con URL, slug, sección, hash y tamaño. El mismo contenido no se guarda dos veces y una captura parcial nunca trunca el índice existente.

**Why this priority**: el patrón Medallion era una promesa del informe; materializarlo convierte el corpus en un entregable auditable y permite reprocesar sin volver a la web.

**Independent Test**: una captura de dos semillas produce tres archivos por documento y un índice JSONL con dos líneas; con contenido repetido se escribe una sola vez, y una segunda captura con una semilla conserva el índice ([tests/test_pipeline.py](../../tests/test_pipeline.py)).

**Acceptance Scenarios**:

1. **Given** una página descargada, **When** termina el ciclo, **Then** existen Bronze con el HTML, Silver con el Markdown sin front matter y Gold con front matter.
2. **Given** dos URLs con el mismo contenido escrito, **When** se capturan, **Then** solo se escribe la primera y la segunda se cuenta como duplicada por hash.
3. **Given** una captura posterior con menos semillas, **When** termina, **Then** el índice conserva las entradas anteriores que siguen existiendo en Gold.
4. **Given** una página sin cuerpo extraíble, **When** se escribe, **Then** se conserva con 0 caracteres y sin línea en blanco final.

---

### User Story 3 - Extraer Markdown editorial sin *chrome* (Priority: P2)

El extractor propio convierte el HTML en Markdown útil: conserva encabezados, párrafos, listas, tablas y enlaces, y descarta navegación, encabezado, pie, formularios, modales, menús, scripts y regiones dinámicas. El contenido de contenedores anidados no se duplica.

**Why this priority**: la extracción llevó el corpus de 2.45 MB a ~93 KB de texto limpio. Se documenta aquí porque es inseparable de la captura (pliega la spec 004).

**Independent Test**: [tests/test_extracting.py](../../tests/test_extracting.py) demuestra encabezado y párrafo, ausencia de script y de menú, no duplicación de contenedores y descarte de plantillas de encabezado y pie.

**Acceptance Scenarios**:

1. **Given** un HTML con `nav`, `header`, `footer` y cuerpo, **When** se extrae, **Then** solo permanece el contenido editorial.
2. **Given** contenedores anidados con el mismo párrafo, **When** se extrae, **Then** el párrafo aparece una sola vez.
3. **Given** regiones de plantilla de encabezado o pie, **When** se extrae, **Then** se descartan y el contenido editorial permanece.
4. **Given** un `script` o un bloque de estilos, **When** se extrae, **Then** su texto no llega al Markdown.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Capturar exclusivamente las semillas explícitas declaradas; no rastrear sitemaps ni enlaces descubiertos.
- **FR-002**: Consultar `robots.txt` por host, respetar `Disallow`, `Allow` y `Crawl-delay`, usar un `User-Agent` identificable y un timeout de 30 s.
- **FR-003**: Esperar al menos 1 s entre peticiones al mismo host.
- **FR-004**: Escribir tres capas por documento —HTML crudo, Markdown limpio y Gold con front matter— más un índice JSONL con una entrada por documento.
- **FR-005**: Registrar en el front matter `url`, `title`, `section`, `domain`, `source_type`, `captured_at`, `hash` y `chars`.
- **FR-006**: Deduplicar por SHA-256 del Markdown y no deduplicar páginas de menos de 200 caracteres.
- **FR-007**: Convertir HTML a Markdown conservando encabezados, párrafos, listas, tablas y enlaces, y descartando navegación, encabezado, pie, formularios, modales, menús, scripts y regiones dinámicas, sin duplicar contenedores.
- **FR-008**: Reportar descubiertas, descargadas, escritas, descartes con motivo, caracteres, tokens estimados y duración.
- **FR-009**: Continuar la captura ante un fallo de red o de un guard y registrar la URL como descarte.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Una corrida sobre las 24 semillas declaradas escribe 24/24 documentos con 0 descartes ([docs/alcance.md](../../docs/alcance.md)).
- **SC-002**: El 100 % de los documentos Gold registra los ocho metadatos y un hash de 64 caracteres hexadecimales.
- **SC-003**: El 100 % de las descargas va precedido de la consulta a `robots.txt` y ninguna URL fuera de las semillas declaradas se solicita.
- **SC-004**: El corpus limpio queda en ~93 KB (92 682 caracteres, ≈23 000 tokens) y no contiene menús ni pie institucional.
- **SC-005**: El índice JSONL tiene una línea por documento y conserva las entradas previas tras una captura parcial.

## Assumptions

- El corpus se limita a contenido público en HTML; PDFs, vídeos y contenido autenticado quedan fuera de alcance.
- La red pública está disponible y el sitio no exige autenticación.
- Gold es el entregable versionado; Bronze y Silver se regeneran con cada captura.
