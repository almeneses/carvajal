# Feature Specification: Arquitectura por capas: dominio, puertos y configuración tipada

**Feature Branch**: `011-arquitectura-capas`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-11-08
**Evidence**: [`CONSTITUTION.md` §9](../../CONSTITUTION.md) (2026-11-08); [ADR-0001](../../docs/decisions/0001-stack-app-chat.md) y [plan de migración](../../docs/plans/0001-migracion-app.md) (ola 0); PRs #33, #35, #36, #37; [`tests/test_ports.py`](../../tests/test_ports.py), [`tests/test_domain.py`](../../tests/test_domain.py), [`tests/test_settings.py`](../../tests/test_settings.py)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Sustituir un adaptador sin tocar los casos de uso (Priority: P1)

Quien mantiene el proyecto quiere cambiar el almacén de documentos, el índice vectorial o el proveedor de LLM sin reescribir la lógica que los usa: implementa un adaptador nuevo y lo valida contra el mismo contrato que ya cumplen los dobles en memoria.

**Why this priority**: es la razón de ser de los puertos; sin un contrato compartido la app volvería a quedar atada a una tecnología concreta.

**Independent Test**: ejecutar las aserciones de contrato de [`tests/test_ports.py`](../../tests/test_ports.py) contra el doble en memoria y contra un adaptador real, y comprobar que ambas pasan sin cambiar las aserciones.

**Acceptance Scenarios**:

1. **Given** un adaptador de documentos, **When** se ejecuta el contrato común, **Then** las mismas comprobaciones de consulta, listado, actualización y versiones valen para él.
2. **Given** un objeto incompleto, **When** se verifica contra el puerto en tiempo de ejecución, **Then** no se acepta como adaptador.

---

### User Story 2 - Derivar metadatos del corpus sin IO (Priority: P1)

La persona que cura el corpus obtiene de cada URL su slug, su dominio, su sección, su huella y su estimación de tokens con reglas puras, de modo que el mismo insumo produzca siempre el mismo metadato en cualquier capa del sistema.

**Why this priority**: el corpus se describe con estas reglas; si dependieran de red o de archivos, no podrían probarse ni reutilizarse.

**Independent Test**: ejecutar [`tests/test_domain.py`](../../tests/test_domain.py), que cubre las reglas sin base de datos, sin red y sin LLM.

**Acceptance Scenarios**:

1. **Given** una URL anidada del sitio oficial, **When** se deriva su slug, **Then** se usa el último segmento, con guiones convertidos en guiones bajos y puntos eliminados; la portada devuelve `home`.
2. **Given** una URL de un medio tercero, **When** se deriva su sección, **Then** la sección es `prensa` y el dominio conserva el medio.
3. **Given** el mismo Markdown dos veces, **When** se calcula su huella, **Then** la huella SHA-256 coincide; un contenido distinto produce otra huella.
4. **Given** una página de menos de 200 caracteres, **When** se decide si duplica, **Then** nunca cuenta como duplicada.

---

### User Story 3 - Configurar el pipeline por entorno sin romper los valores por defecto (Priority: P2)

Quien despliega ajusta rutas, semillas, credenciales y proveedor mediante variables de entorno o un archivo `.env`, y conserva los valores históricos cuando no define nada; un valor inválido se detecta al arrancar, no en mitad de una petición.

**Why this priority**: los entornos local, de pruebas y de producción comparten el mismo código y no deben divergir en valores silenciosos.

**Independent Test**: construir la configuración dos veces, una sin entorno y otra con variables, y comprobar valores, derivaciones y rechazos en [`tests/test_settings.py`](../../tests/test_settings.py).

**Acceptance Scenarios**:

1. **Given** un entorno limpio, **When** se construye la configuración, **Then** las rutas del corpus, el proveedor por defecto y las semillas reproducen las constantes históricas.
2. **Given** una variable de entorno válida, **When** se construye la configuración, **Then** esa variable gana al valor por defecto y la fachada histórica reexporta el mismo valor.
3. **Given** un puerto de base de datos no numérico o fuera de 1..65535, **When** se construye la configuración, **Then** falla en la construcción con un error de validación.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El dominio debe ser puro: sus reglas y entidades no dependen de red, base de datos, archivos ni proveedores de LLM.
- **FR-002**: Las reglas deben derivar slug, dominio, sección oficial o `prensa`, huella SHA-256, estimación de tokens y decisión de duplicado con umbral mínimo de caracteres.
- **FR-003**: Las entidades del corpus deben ser inmutables y compartirse entre capas: página capturada, documento, fragmento y versión de documento.
- **FR-004**: Deben existir contratos de puerto para documentos, fragmentos, índice vectorial, generación de texto, cliente HTTP y extractor.
- **FR-005**: Todo adaptador de infraestructura debe satisfacer su puerto y pasar las mismas aserciones de contrato que el doble en memoria.
- **FR-006**: El contrato de documentos debe fijar id inmutable, slug único, historial de versiones solo ante cambio de huella y listados con `limit` y `offset` no negativos (`limit=0` devuelve vacío).
- **FR-007**: El contrato de índice vectorial debe admitir indexación, búsqueda ordenada de mayor a menor puntuación, filtros por capa y sección, y borrado por documento.
- **FR-008**: El contrato de generación de texto debe ofrecer respuesta completa y respuesta por fragmentos, ambas con mensaje de sistema y prompt.
- **FR-009**: La configuración debe ser tipada, cargarse de entorno y `.env`, conservar los valores por defecto históricos, aceptar tuplas declaradas en JSON y rechazar valores inválidos al construirla.
- **FR-010**: La fachada de configuración histórica debe reexportar los mismos valores que la configuración tipada, sin divergencias.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Las 6 pruebas de contrato de [`tests/test_ports.py`](../../tests/test_ports.py) pasan contra los dobles en memoria, y los adaptadores reales reutilizan las mismas aserciones sin reescribirlas.
- **SC-002**: Las 8 pruebas de dominio de [`tests/test_domain.py`](../../tests/test_domain.py) corren sin base de datos, sin red y sin LLM; sus casos parametrizados cubren 4 URLs de slug, 2 de dominio y 3 de sección.
- **SC-003**: Con el entorno limpio, 0 valores por defecto dejan de coincidir con las constantes históricas; las 3 formas de puerto inválido probadas (`no-es-un-numero`, `0`, `70000`) fallan al construir la configuración.
- **SC-004**: La fachada histórica y la configuración tipada devuelven exactamente las mismas semillas y la misma ruta de índice derivada (`data/gold/index.jsonl`), con 0 divergencias.
- **SC-005**: El núcleo se importa como `carvajal` desde la estructura nueva y `uv build` y `make check` quedan en verde, según la aceptación registrada de la ola 0.

## Assumptions

- La ola 0 cierra los contratos antes de que exista la app web; los adaptadores reales llegan en las olas 1 y 2 y estas pruebas son su referencia.
- El puerto de embeddings se añadió el 2026-11-11 junto al caso de uso de preguntas; no formaba parte del cierre de la ola 0.
- La configuración creció con las olas posteriores (chat, cuota, captura, proveedor local) sin cambiar los valores por defecto históricos ni la fachada.
