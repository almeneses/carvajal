# Feature Specification: Línea base y arquitectura para simplificar Carvajal con 3 workers

**Feature Branch**: `docs/042-linea-base-arquitectura`
**Created**: 2026-11-20
**Status**: En curso (fase 0 del goal de simplificación; fases 043–047 son specs independientes)
**Input**: Goal del owner: «Simplificar la arquitectura de Carvajal sin regresiones y
operar con 3 workers». Fase 0: inventario, contratos, mediciones base, verificación de la
brecha `dev`/`main` y ADR de arquitectura.

## Contexto

Antes de borrar código o escalar procesos hay que poder responder, con evidencia y no de
memoria: qué rutas y comandos existen, qué eventos cruzan SSE, cuántos procesos hay hoy,
qué dato manda en cada almacén y qué se está midiendo. `CONSTITUTION.md` describe el
proyecto, pero no es un inventario operativo; los ADR explican decisiones pasadas, pero el
ADR 0001 sigue en estado `propuesto` y no cubre el despliegue con varios workers.

Además, `origin/dev` y `origin/main` ya divergieron: `main` tiene los arreglos de trazas
(`5826114`) y de manos libres (`9e5484f`, `9371268`) que `dev` no conserva. Partir de
`dev` sin cerrar esa brecha arrastraría una regresión de comportamiento al goal.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Inventario verificable de la arquitectura actual (Priority: P1)

Una persona que revisa el goal necesita localizar cada superficie pública de la aplicación
—rutas HTTP, comandos CLI, eventos SSE, procesos y fuentes de verdad— y comprobar que el
inventario coincide con el código de `origin/dev`, sin leer todo el repositorio.

**Why this priority**: sin inventario, cualquier retirada de código o cambio de workers es
una apuesta; todas las fases siguientes dependen de este mapa.

**Independent Test**: tomar el inventario y recorrer el código de `origin/dev`: cada ruta,
comando y evento declarado aparece exactamente una vez, y no falta ninguna superficie
pública que un usuario o cliente pueda invocar.

**Acceptance Scenarios**:

1. **Given** el inventario publicado, **When** se compara con los decoradores de ruta y
   los routers montados en `apps/api/main.py`, **Then** la lista de rutas coincide y
   distingue formato HTML, JSON y SSE.
2. **Given** el inventario de CLI, **When** se ejecuta `carvajal --help` y cada
   subcomando con `--help`, **Then** los comandos, opciones y efectos documentados existen
   y no hay comandos sin documentar.
3. **Given** el inventario de eventos SSE, **When** se revisan los `encode(...)` y las
   ramas `case` de los consumidores, **Then** cada evento tiene productor, consumidor y
   condición de cierre documentados.

### User Story 2 - Paridad entre `dev` y `main` antes de simplificar (Priority: P1)

El owner necesita que el punto de partida del goal conserve los arreglos que ya están en
`main` (traza sin re-unir la respuesta en cada token y manos libres que devuelve la
pregunta al borrador cuando no hay `done`), sin arrastrar los documentos de la spec 040 que
solo viven en `dev`.

**Why this priority**: partir de una rama que perdió arreglos fusionados es una regresión
silenciosa; el goal prohíbe regresiones.

**Independent Test**: en una rama creada desde `origin/dev`, aplicar las tres correcciones
y comprobar con pruebas que (a) la traza acumula el largo sin unir la respuesta completa
por token y conserva el texto parcial al cancelar, y (b) un fallo de manos libres devuelve
siempre la pregunta al borrador. Sin las correcciones, las pruebas fallan.

**Acceptance Scenarios**:

1. **Given** una sesión de chat con `monitoring` activo, **When** se cancelan los tokens,
   **Then** la traza conserva el texto parcial con una sola unión y el largo acumulado.
2. **Given** el chat en manos libres con contenido recibido, **When** el stream termina sin
   `done`, **Then** la pregunta vuelve al borrador una sola vez y el turno no se persiste.
3. **Given** la rama de sincronización, **When** se compara con `origin/main`, **Then** los
   tres commits de corrección están presentes y ningún documento de la spec 040 se importa
   desde `main`.

### User Story 3 - ADR de arquitectura actual y transición a 3 workers (Priority: P1)

El owner y quien revise el goal necesitan una decisión escrita que describa la arquitectura
actual (una app FastAPI/Jinja2, PostgreSQL, Qdrant, proveedores actuales) y cómo se pasa a
exactamente tres workers de Uvicorn sin añadir servicios, con consecuencias y reversión.

**Why this priority**: el paso a 3 workers cambia el modelo de concurrencia y obliga a
mover estado compartido; sin ADR, la fase 5 se vuelve una decisión implícita en un diff.

**Independent Test**: leer el ADR 0002 y responder sin ambigüedad: qué componentes existen
hoy, cuáles pasan a ser compartidos, dónde vive el estado de captura, cuántos workers hay
por defecto, cómo se vuelve a uno y qué se conserva. El ADR 0001 sigue intacto.

**Acceptance Scenarios**:

1. **Given** el ADR 0002, **When** se buscan las palabras «3 workers» y «UVICORN_WORKERS=1»,
   **Then** aparecen el valor por defecto y la ruta de reversión.
2. **Given** el ADR 0001 aceptado como histórico, **When** se revisa su archivo, **Then**
   no fue editado y el índice de ADR lo marca como conservado.
3. **Given** el ADR 0002, **When** se compara con el inventario y con `compose.prod.yml`,
   **Then** el estado actual descrito (1 worker) y el objetivo (3) son coherentes.

### User Story 4 - Contratos y mediciones de referencia reproducibles (Priority: P2)

Quien vaya a comparar el antes y el después de las fases 043–047 necesita contratos
literales (formatos de respuesta, cookies, errores, eventos) y mediciones que se puedan
repetir en el mismo entorno con los mismos comandos.

**Why this priority**: el cierre exige reportar tiempos y consumo antes/después y no
declarar mejoras sin medirlas; sin línea base no hay comparación honesta.

**Independent Test**: ejecutar los comandos del quickstart en el mismo entorno y obtener
los mismos conteos y el mismo diagnóstico base, con las diferencias esperadas de una
corrida a otra (tiempos) marcadas explícitamente.

**Acceptance Scenarios**:

1. **Given** el documento de baseline, **When** se ejecuta `make check` en el entorno
   registrado, **Then** el resultado y el número de pruebas coinciden con lo anotado.
2. **Given** el baseline, **When** se cuentan documentos/chunks en PostgreSQL y puntos en
   Qdrant, **Then** se obtienen los valores anotados.
3. **Given** el contrato SSE, **When** se inspecciona una respuesta real, **Then** el
   formato de cable coincide con el documentado.

### Edge Cases

- El entorno local filtra `OLLAMA_MODEL` (por ejemplo `qwen3:4b-instruct`): dos pruebas de
  `Settings` fallan por ese valor heredado. El baseline debe registrar el entorno limpio
  usado y repetir con `env -u OLLAMA_MODEL`.
- `origin/dev` no contiene los arreglos de `main`, pero sí documentación de la 040 que
  `main` no tiene: la sincronización no puede ser un merge ni un cherry-pick masivo.
- El baseline de `sync --verify` depende de servicios locales ya poblados; si PostgreSQL o
  Qdrant no están disponibles, se anota la incapacidad y no se inventan tiempos.
- El entorno puede no tener la imagen Docker construida: el arranque de la imagen se mide
  en la fase correspondiente y, en fase 0, se registra como pendiente explícita si aplica.
- La numeración de specs: la 041 está reservada en `CONSTITUTION.md` §9 para el informe de
  enrutadores; las specs de este goal empiezan en 042 para no reutilizar el número.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El inventario MUST documentar cada ruta HTTP con método, ruta, formato de
  respuesta, entrada y efecto observable, verificable contra el código de `origin/dev`.
- **FR-002**: El inventario MUST documentar cada comando CLI con sus opciones, escrituras
  y código de salida relevante.
- **FR-003**: El inventario MUST documentar cada evento SSE (chat, FAQ y captura) con
  productor, consumidor, carga y condición de cierre.
- **FR-004**: El inventario MUST describir los modos de ejecución actuales (CLI, servidor
  local, imagen y Compose) y el número de procesos/workers de cada uno, incluido el estado
  actual de un solo worker.
- **FR-005**: El inventario MUST nombrar la fuente de verdad de cada dato persistente o
  compartido (corpus Gold, PostgreSQL, Qdrant, catálogo, configuración, checkpoints y
  estado en memoria de captura).
- **FR-006**: El sistema MUST verificar la brecha `dev`/`main` de los arreglos de trazas y
  manos libres y cerrarla en un PR independiente contra `dev`, con pruebas que demuestren
  el comportamiento restaurado.
- **FR-007**: El ADR 0002 MUST describir la arquitectura actual, la decisión de operar con
  exactamente 3 workers de Uvicorn, sus consecuencias, sus disparadores de revisión y la
  ruta de reversión a 1 worker; el ADR 0001 MUST permanecer sin editar.
- **FR-008**: El baseline MUST registrar comandos, versiones, entorno, resultados y tiempos
  reproducibles, incluidos `make check`, las pruebas de Node, los conteos de corpus y el
  diagnóstico de `sync --verify` cuando los servicios estén disponibles.
- **FR-009**: Los contratos MUST incluir ejemplos literales del formato SSE, del cuerpo de
  `/health`, de las cookies de identidad y CSRF, y de los códigos de error compartidos por
  el formulario, el stream web y el stream de API.
- **FR-010**: El inventario MUST listar los candidatos de retirada y duplicación que
  abordan las specs 043–047, sin implementarlos en esta spec.
- **FR-011**: La spec MUST fijar la relación y el orden de las specs 042–047, dejando la
  041 reservada y sin reutilizar su número.
- **FR-012**: La spec NO MUST tocar `main`, el VPS ni producción, y NO MUST fusionar ni
  aprobar PRs; todo el trabajo vive en ramas y PRs contra `dev`.

### Key Entities

- **Inventario**: mapa de rutas, comandos, eventos, modos y fuentes de verdad con ruta de
  código y contrato observable.
- **Brecha de paridad**: conjunto de commits de `main` que `dev` no contiene y su prueba
  de comportamiento.
- **Contrato**: formato de cable o interfaz que no puede cambiar en las fases siguientes
  (SSE, HTML, JSON, cookies, códigos de error, códigos de salida de CLI).
- **Medición base**: comando reproducible, entorno, resultado y tiempo de una métrica
  (suite de pruebas, conteos de corpus, recorridos de Qdrant, embeddings).

## Success Criteria *(mandatory)*

- **SC-001**: El 100 % de las rutas, comandos y eventos SSE invocables desde la
  aplicación de `origin/dev` aparecen en el inventario y coinciden con el código.
- **SC-002**: En el entorno limpio registrado, `make check` termina en verde y
  `node --test tests/js/test_chat_voice.cjs` pasa con 68 pruebas.
- **SC-003**: La brecha `dev`/`main` queda cerrada en un PR con pruebas que fallan sin las
  correcciones y pasan con ellas.
- **SC-004**: Un revisor puede decidir el paso a 3 workers leyendo el ADR 0002 y volver a 1
  worker con un único cambio documentado, sin perder estado persistido.
- **SC-005**: El baseline se repite con los comandos del quickstart y devuelve los mismos
  conteos; los tiempos se reportan como observaciones del entorno, no como garantías.
- **SC-006**: La spec 042 no cambia comportamiento funcional salvo el PR de paridad con
  `main`; la suite existente sigue en verde.

## Assumptions

- El entorno local dispone de PostgreSQL y Qdrant ya levantados (conteos de referencia
  documentados) y de `uv`, Docker y Node.
- El goal se entrega como seis specs pequeñas: 042 (esta), 043 (código y dependencias), 044
  (chat, SSE e interfaz), 045 (corpus y rendimiento), 046 (estado del agente) y 047
  (captura duradera y 3 workers). Cada una abre su propio PR contra `dev`.
- Ningún agente fusiona ni aprueba PRs; el owner decide.
- `main` es la referencia de los arreglos ya fusionados; `dev` es la base de integración.

## History

- 2026-11-20: spec inicial de la fase 0. Descubrimiento: `origin/dev` no conserva los
  arreglos de trazas ni de manos libres de `origin/main`; se separa su sincronización en un
  PR propio bajo esta spec.
