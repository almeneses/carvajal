# Feature Specification: Persistencia relacional con Postgres y migraciones

**Feature Branch**: `012-persistencia-postgres`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-11-08 → 2026-11-10
**Evidence**: [`CONSTITUTION.md` §9](../../CONSTITUTION.md) (2026-11-10); [plan de migración](../../docs/plans/0001-migracion-app.md) (ola 1); PRs #39, #50, #53, #54, #55, #56; [`tests/test_storage_import.py`](../../tests/test_storage_import.py), [`tests/test_import_corpus.py`](../../tests/test_import_corpus.py), [`tests/test_storage_tables.py`](../../tests/test_storage_tables.py), [`tests/test_storage_repositories.py`](../../tests/test_storage_repositories.py), [`tests/test_storage_migrations.py`](../../tests/test_storage_migrations.py), [`tests/test_migrations_single_head.py`](../../tests/test_migrations_single_head.py), [`tests/test_settings.py`](../../tests/test_settings.py)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Cargar el corpus Gold en Postgres sin duplicar (Priority: P1)

Quien opera el corpus ejecuta `carvajal migrate-gold` sobre el directorio Gold y obtiene una base con los mismos documentos y fragmentos que el corpus en disco; repetir la carga no duplica nada y lo que ya no existe en disco se poda.

**Why this priority**: es el paso que convierte los Markdown en la fuente de verdad consultable de la app web; sin idempotencia, cada recarga ensuciaría la base.

**Independent Test**: importar dos veces el mismo directorio de prueba y comparar los reportes y los identificadores de fragmentos en [`tests/test_storage_import.py`](../../tests/test_storage_import.py).

**Acceptance Scenarios**:

1. **Given** un directorio con dos Markdown, **When** se importa dos veces, **Then** cada corrida reporta 2 documentos, 2 fragmentos y 0 podados, y los fragmentos conservan sus identificadores.
2. **Given** un documento cuyo cuerpo cambió, **When** se reimporta, **Then** el Markdown anterior queda archivado como versión y el documento muestra el nuevo contenido.
3. **Given** un Markdown eliminado del directorio, **When** se reimporta, **Then** el documento y sus fragmentos y versiones desaparecen y el reporte cuenta 1 podado.
4. **Given** un slug con separadores de ruta o dos archivos con el mismo slug, **When** se intenta importar, **Then** la corrida aborta antes de escribir y nombra el archivo culpable.

---

### User Story 2 - Conservar el historial y la integridad de cada documento (Priority: P2)

Quien audita el corpus ve una sola fila por documento, con su slug estable y sus versiones anteriores archivadas; borrar un documento arrastra sus fragmentos y su historial, sin huérfanos.

**Why this priority**: el corpus cambia con el tiempo y la app necesita explicar qué decía antes y qué ya no existe.

**Independent Test**: aplicar la secuencia v1 → v2 → v2 → v3 sobre un documento y comprobar que quedan dos versiones y ninguna fila huérfana, con [`tests/test_storage_repositories.py`](../../tests/test_storage_repositories.py) y [`tests/test_storage_import.py`](../../tests/test_storage_import.py).

**Acceptance Scenarios**:

1. **Given** un documento guardado, **When** se actualiza con la misma huella, **Then** no se archiva ninguna versión nueva, aunque cambie el Markdown.
2. **Given** un documento guardado, **When** se actualiza con otra huella, **Then** se archiva el Markdown, la huella y la fecha de captura anteriores.
3. **Given** un slug ya asignado a un documento, **When** se intenta guardar con otro identificador, **Then** la operación falla y las versiones existentes quedan intactas.
4. **Given** un documento con fragmentos y versiones, **When** se borra, **Then** la cascada de la base elimina ambos y un segundo borrado devuelve falso.

---

### User Story 3 - Crear el esquema desde cero con migraciones reproducibles (Priority: P1)

Quien levanta el entorno local arranca Postgres 16 con soporte vectorial mediante `make db` y aplica las migraciones hasta la cabeza; desde una base vacía queda el esquema del corpus y el descenso deja la base limpia.

**Why this priority**: la app no puede depender de un esquema copiado a mano ni de una base improvisada por entorno.

**Independent Test**: ejecutar `make db` y `alembic upgrade head` sobre una base vacía, comprobar el grafo con [`tests/test_migrations_single_head.py`](../../tests/test_migrations_single_head.py) y la ida y vuelta con [`tests/test_storage_migrations.py`](../../tests/test_storage_migrations.py).

**Acceptance Scenarios**:

1. **Given** el repositorio recién clonado, **When** se ejecuta `make db`, **Then** el servicio de Postgres 16 con soporte vectorial arranca y su healthcheck queda en verde.
2. **Given** una base vacía, **When** se ejecuta `alembic upgrade head`, **Then** existen el corpus, sus restricciones e índices y el grafo tiene exactamente una cabeza.
3. **Given** una base migrada, **When** se ejecuta el descenso hasta la base, **Then** no quedan tablas del corpus y una nueva subida reproduce el mismo esquema.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Debe existir un Postgres 16 con soporte vectorial para desarrollo y pruebas, levantado por `make db` con healthcheck y credenciales configurables; la URL de conexión se define por entorno o se deriva de las variables de Postgres con las credenciales codificadas.
- **FR-002**: El esquema debe incluir documentos, fragmentos y versiones de documento, con clave primaria, slug único, una posición única por documento, claves foráneas en cascada e índices de consulta por capa y sección y por historial.
- **FR-003**: Las migraciones deben ser versionadas, aplicables desde cero hasta una única cabeza y reversibles hasta la base sin residuos del corpus.
- **FR-004**: El repositorio de documentos debe ofrecer lectura por identificador y por slug, listado filtrado por capa y sección con `limit` y `offset` no negativos (`limit=0` vacío), conteo y borrado.
- **FR-005**: El guardado de documentos debe mantener el identificador inmutable y el slug único, y archivar la versión anterior solo cuando cambie la huella de contenido.
- **FR-006**: El repositorio de fragmentos debe reemplazar de forma atómica todos los fragmentos de un documento sin acumular filas, devolverlos ordenados por posición y validar pertenencia y posiciones duplicadas.
- **FR-007**: El importador debe leer los Markdown de `data/gold`, respetar el front matter (slug, título, URL, sección, fecha y huella), validar el slug, normalizar fechas a UTC y fragmentar con posiciones contiguas.
- **FR-008**: La importación debe ser idempotente y reportar en cada corrida los documentos importados, los fragmentos importados y los documentos podados.
- **FR-009**: El directorio importado debe ser autoritativo para su capa: lo ausente se poda con su cascada, el directorio vacío solo poda con `--allow-empty` y una ruta inexistente siempre falla.
- **FR-010**: Un corpus inválido debe abortar antes de escribir y un fallo a mitad de la importación debe revertir la transacción completa, sin documentos ni fragmentos parciales.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Reimportar dos veces el mismo corpus produce reportes idénticos (2 documentos, 2 fragmentos, 0 podados) y conserva los mismos identificadores de fragmento, sin filas duplicadas.
- **SC-002**: Tras importar el corpus real, el conteo de documentos de la capa núcleo coincide con las entradas de `index.jsonl` y cada entrada coincide en slug, huella, fecha de captura, sección, título y URL.
- **SC-003**: Con un fallo simulado a mitad de la importación quedan 0 documentos y 0 fragmentos: la transacción revierte por completo.
- **SC-004**: Desde cero, `alembic upgrade head` deja el esquema vigente y exactamente 1 cabeza; el descenso a la base no deja tablas del corpus y una segunda subida las reproduce.
- **SC-005**: La poda de un documento lo elimina junto con sus fragmentos y versiones, y una corrida posterior sin cambios reporta 0 podados y 0 duplicados.

## Assumptions

- La capacidad nació con las tres tablas del corpus; las tablas de chat y de cuota se añadieron después como migraciones nuevas sin alterar ese contrato.
- La columna vectorial de los fragmentos y su índice se añaden en la capacidad del índice vectorial; este importador no calcula embeddings.
- La unidad de trabajo pertenece a quien invoca la importación: la CLI confirma al final y revierte ante error, mientras la importación solo prepara los cambios.
