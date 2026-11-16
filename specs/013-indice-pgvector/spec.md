# Feature Specification: Índice vectorial pgvector

**Feature Branch**: `013-indice-pgvector`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-11-11
**Evidence**: [`CONSTITUTION.md` §9](../../CONSTITUTION.md) (2026-11-11); [plan de migración](../../docs/plans/0001-migracion-app.md) (W2.1); PR #64; [`tests/test_vector_store.py`](../../tests/test_vector_store.py), [`tests/test_storage_tables.py`](../../tests/test_storage_tables.py), [`tests/test_storage_migrations.py`](../../tests/test_storage_migrations.py)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Recuperar los fragmentos más parecidos a una consulta (Priority: P1)

El asistente necesita contexto del corpus: dado un vector de consulta, obtiene los fragmentos más similares ordenados de mayor a menor puntuación, opcionalmente acotados por capa y sección, sin depender de un índice en disco.

**Why this priority**: sin recuperación semántica no hay respuesta fundada en el corpus; es la pieza que separa una búsqueda por texto de un RAG.

**Independent Test**: indexar tres fragmentos con vectores conocidos y comprobar orden, filtros y límites en [`tests/test_vector_store.py`](../../tests/test_vector_store.py).

**Acceptance Scenarios**:

1. **Given** tres fragmentos indexados con vectores conocidos, **When** se busca con un vector afín a uno de ellos, **Then** los resultados llegan de mayor a menor similitud y la puntuación equivale a uno menos la distancia coseno.
2. **Given** fragmentos de dos capas y dos secciones, **When** se busca filtrando por capa o por sección, **Then** solo se devuelven los que cumplen el filtro; la combinación sin coincidencias devuelve vacío.
3. **Given** un índice sin vectores, **When** se busca, **Then** la respuesta es vacía; `limit=0` también devuelve vacío y un límite negativo falla.
4. **Given** un fragmento sin vector, **When** se busca, **Then** no participa en los resultados.

---

### User Story 2 - Indexar vectores de forma idempotente (Priority: P1)

Quien indexa el corpus carga los vectores en lotes; si el fragmento ya existe solo se reescribe su vector, y un identificador ya usado por otro documento se rechaza antes de escribir nada.

**Why this priority**: el corpus se reindexa tras cada captura; una carga no idempotente duplicaría filas o pisaría fragmentos ajenos.

**Independent Test**: indexar un lote, reindexarlo con vectores distintos y comprobar que no aparecen filas nuevas ni dueños cambiados en [`tests/test_vector_store.py`](../../tests/test_vector_store.py).

**Acceptance Scenarios**:

1. **Given** fragmentos ya guardados con sus vectores, **When** se cargan de nuevo con vectores nuevos, **Then** solo cambia el vector y el número de filas del documento se mantiene.
2. **Given** un lote con un identificador que pertenece a otro documento, **When** se intenta indexar, **Then** la operación falla antes de escribir y el índice queda como estaba.
3. **Given** un vector vacío, con dimensión distinta de 768, con valores no finitos o un lote con cantidades desparejadas, **When** se intenta indexar, **Then** la operación se rechaza con un error de valor.

---

### User Story 3 - Mantener el índice y su esquema entre entornos (Priority: P2)

Quien administra el corpus retira del índice todos los vectores de un documento sin borrar sus filas, y quien levanta una base nueva aplica las migraciones y obtiene la extensión vectorial, la columna de 768 dimensiones y su índice de similitud coseno sin pasos manuales.

**Why this priority**: la propiedad de las filas debe quedar en un solo componente y el índice debe existir en cualquier entorno sin recetas aparte.

**Independent Test**: borrar un documento y comprobar que la búsqueda ya no lo devuelve pero las filas siguen ahí, y aplicar las migraciones sobre una base vacía, en [`tests/test_vector_store.py`](../../tests/test_vector_store.py) y [`tests/test_storage_migrations.py`](../../tests/test_storage_migrations.py).

**Acceptance Scenarios**:

1. **Given** un documento con dos fragmentos indexados, **When** se borra del índice, **Then** la búsqueda deja de devolverlos y el repositorio conserva sus dos filas.
2. **Given** un documento sin vectores, **When** se borra del índice, **Then** la operación termina sin error.
3. **Given** una base vacía, **When** se aplican las migraciones hasta la cabeza, **Then** la extensión vectorial, la columna de 768 dimensiones en los fragmentos y su índice HNSW coseno quedan creados.
4. **Given** una base migrada, **When** se desciende la migración, **Then** se eliminan el índice y la columna vectorial y la extensión compartida se conserva.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Cada fragmento del corpus debe poder llevar un vector de 768 dimensiones, almacenado en Postgres; los fragmentos sin vector no participan de la búsqueda.
- **FR-002**: El esquema debe crear la extensión vectorial, la columna de 768 dimensiones y un índice HNSW con operatoria coseno, y el descenso debe retirar índice y columna sin borrar la extensión compartida.
- **FR-003**: La búsqueda debe devolver los fragmentos más similares ordenados por puntuación descendente, con puntuación igual a uno menos la distancia coseno.
- **FR-004**: La búsqueda debe aceptar filtros combinables por capa y por sección, `limit=0` como vacío, rechazar límites negativos y devolver vacío sobre un índice vacío.
- **FR-005**: La indexación por lotes debe insertar el fragmento ausente completo y actualizar solo el vector del existente, sin duplicar filas.
- **FR-006**: La indexación debe validar dimensión exacta, valores finitos, vector no vacío y un vector por fragmento antes de escribir; un lote inválido no deja cambios.
- **FR-007**: El identificador de fragmento debe ser inmutable: reutilizarlo desde otro documento debe fallar antes de escribir.
- **FR-008**: La búsqueda no debe recuperar el vector de 768 dimensiones: la proyección se limita a los campos del fragmento y a la distancia.
- **FR-009**: El borrado por documento debe anular el vector y conservar las filas del fragmento, que pertenecen al repositorio de fragmentos.
- **FR-010**: El índice no debe generar embeddings: recibe fragmentos y vectores ya calculados por quien lo invoca.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Con 3 fragmentos de vectores conocidos, la búsqueda devuelve `c1`, `c2`, `c3` en ese orden; al invertir el vector de `c1`, el orden pasa a `c2`, `c3`, `c1` y el documento sigue teniendo 2 filas.
- **SC-002**: Las 5 validaciones inválidas (vector vacío, `NaN`, infinito, dimensión 767 y lote desparejado) se rechazan con error de valor sin escribir en el índice.
- **SC-003**: Un lote que mezcla una fila válida y un identificador ajeno no deja cambios pendientes en la sesión y deja el índice idéntico.
- **SC-004**: Los filtros por capa extendida devuelven 1 fragmento, los de sección `home` devuelven 2 y la combinación de capa núcleo con sección `prensa` devuelve 0.
- **SC-005**: Tras borrar un documento del índice, la búsqueda no devuelve ninguno de sus fragmentos y el repositorio conserva sus 2 filas.

## Assumptions

- El 2026-11-13 el índice evaluado del M2 pasó a Qdrant; la columna de 768 dimensiones, el índice HNSW y esta implementación se conservan soportados, pero la web no los usa como índice principal.
- Los embeddings los produce un adaptador de Ollama; este índice solo almacena y busca vectores.
- Crear la extensión vectorial exige privilegios en la base; en un Postgres gestionado se provisiona antes de migrar.
