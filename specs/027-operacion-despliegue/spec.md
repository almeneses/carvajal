# Feature Specification: Operación: imagen, despliegue y observabilidad

**Feature Branch**: `027-operacion-despliegue`
**Created**: 2026-11-15
**Status**: Implemented (as-built)
**Implemented**: 2026-11-13 → 2026-11-14
**Evidence**: `CONSTITUTION.md` §9 (2026-11-13); PRs #96, #97, #109; `docs/despliegue-ovh.md`; `tests/test_observability.py`, `tests/test_migrations_single_head.py`

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Levantar el sistema completo en una máquina (Priority: P1)

Quien opera parte de una imagen ya construida y un compose que levanta la app, la
base, el índice vectorial y los embeddings. El arranque exige el secreto de sesión y
publica solo el puerto de la app; corpus y cargas persisten en volúmenes.

**Why this priority**: Sin un arranque reproducible no hay demo ni producción posible.

**Independent Test**: Validar el compose con y sin secreto y comprobar puertos, usuario y volúmenes.

**Acceptance Scenarios**:

1. **Given** el compose de producción, **When** falta el secreto de sesión, **Then** el arranque falla con un mensaje claro (evidencia: [`docs/despliegue-ovh.md`](../../docs/despliegue-ovh.md)).
2. **Given** el secreto presente, **When** se levanta el sistema, **Then** solo se publica `APP_PORT` (8080 por defecto), la app corre sin privilegios y el volumen de datos se monta en su directorio de trabajo.
3. **Given** un despliegue o reinicio, **When** el contenedor se recrea, **Then** Bronze, Silver, Gold y las cargas siguen disponibles en el volumen persistente.

### User Story 2 - Desplegar con rollback (Priority: P1)

Al fusionar el PR de release en `main` con CI verde se publica una imagen inmutable
identificada por el commit. El despliegue aplica migraciones, recrea contenedores y
comprueba vitalidad y preparación; si algo falla, vuelve a la imagen anterior.

**Why this priority**: Un despliegue sin retorno automático alarga cualquier caída.

**Independent Test**: Simular un despliegue correcto y uno fallido y comprobar tag, comprobaciones y retorno.

**Acceptance Scenarios**:

1. **Given** un commit en `main` con CI verde, **When** se publica, **Then** la imagen queda en el registro con un tag derivado del commit y `latest`.
2. **Given** una imagen nueva, **When** el despliegue falla, **Then** se restaura el tag anterior y se conserva constancia de la versión (ver [`docs/despliegue-ovh.md`](../../docs/despliegue-ovh.md)).
3. **Given** una migración incompatible, **When** el rollout falla, **Then** el rollback cambia la imagen pero no revierte el esquema; el grafo debe tener una sola cabeza en CI (evidencia: `tests/test_migrations_single_head.py`).

### User Story 3 - Operar, respaldar y diagnosticar (Priority: P2)

El equipo respalda la base a diario, conserva los volúmenes y sigue una petición por
su identificador sin abrir el código. La vitalidad del proceso se separa de la
preparación: que la base caiga no debe reiniciar la app.

**Why this priority**: Sin respaldos ni trazas, un incidente se diagnostica a ciegas.

**Independent Test**: Comprobar el respaldo, la separación de `/live` y `/health` y la
línea de acceso con identificador.

**Acceptance Scenarios**:

1. **Given** una petición cualquiera, **When** se registra el acceso, **Then** queda una línea JSON con identificador de petición, método, ruta, estado y duración (evidencia: `tests/test_observability.py`).
2. **Given** la base caída, **When** se consulta `/live`, **Then** responde `200` sin tocar la base; `/health` responde `503` con los conteos y el estado del índice.
3. **Given** el respaldo diario, **When** se ejecuta, **Then** produce un volcado comprimido de la base y se conserva fuera del servidor; `prod-down` detiene servicios sin borrar volúmenes.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: La imagen debe ser multi-etapa, incluir el runtime web y el extra de captura, excluir el extra heredado y no aplicar migraciones al arrancar.
- **FR-002**: El compose de producción debe exigir `SESSION_SECRET`, publicar solo `APP_PORT` (8080 por defecto), ejecutar la app sin privilegios y con sistema de archivos de solo lectura, y montar un volumen de datos persistente.
- **FR-003**: El despliegue debe dispararse cuando la CI termina en verde en `main` y publicar la imagen en el registro con un tag inmutable derivado del commit; el servidor no construye la imagen ni hace `git pull`.
- **FR-004**: El despliegue debe aplicar migraciones, recrear los contenedores y comprobar `/live` y `/health`; ante fallo debe volver al tag anterior y registrar la versión desplegada.
- **FR-005**: El grafo de migraciones debe tener exactamente una cabeza y esa comprobación debe correr en CI.
- **FR-006**: `/live` debe ser vitalidad del proceso sin dependencia de la base, y `/health` debe ser preparación con conteos de documentos y fragmentos y estado del índice.
- **FR-007**: Cada petición debe generar una línea JSON con `request_id` —respetando el válido que envíe el cliente—, método, ruta, estado y duración; un `500` no debe filtrar detalles internos.
- **FR-008**: El consumo de tokens debe registrarse por visitante cuando el proveedor lo publica, sin inventar cifras cuando no hay uso.
- **FR-009**: El seguimiento externo de errores debe ser opcional por DSN, con datos personales desactivados, muestreo de trazas explícito y el identificador de petición como etiqueta.
- **FR-010**: El corpus no viaja en la imagen; el índice se construye aparte y los respaldos deben conservar base y volúmenes sin usar `docker compose down -v`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: El compose falla sin `SESSION_SECRET`; con él, solo se publica el puerto de la app y el proceso corre sin privilegios sobre sistema de archivos de solo lectura.
- **SC-002**: El despliegue publica un tag por commit y comprueba `/live` y `/health`; ante fallo restaura el tag anterior y no revierte migraciones.
- **SC-003**: `/health` reporta 24 documentos, 125 fragmentos y 125 puntos de 768 dimensiones (ver [`docs/evidence/m2/health.json`](../../docs/evidence/m2/health.json)); `/live` responde `200` aunque la base esté caída.
- **SC-004**: El 100 % de las respuestas lleva `request_id`, y un `500` conserva identificador y cabeceras de seguridad sin exponer el error interno.
- **SC-005**: Sin DSN el proceso no envía nada fuera; con DSN, `send_default_pii` es `False` y el muestreo de trazas queda explícito.

## Assumptions

La demo se publica por IP y HTTP en el puerto 8080, sin dominio ni TLS; el servidor
comparte nginx con otros sitios y no se toca. El corpus y las cargas viven en volúmenes
del servidor. Convención as-built en [el inventario](../README.md) §3; runbook en
[`docs/despliegue-ovh.md`](../../docs/despliegue-ovh.md).
