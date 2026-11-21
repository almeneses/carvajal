# Feature Specification: Corpus: Gold fuera del adaptador SQL y verificación en un solo recorrido

**Feature Branch**: `perf/045-corpus-rendimiento`
**Created**: 2026-11-20
**Status**: Draft
**Input**: User description: "Separa la lectura y fragmentación de Gold del adaptador SQL; reutiliza esa
implementación desde PostgreSQL y Qdrant. No cambies formatos, fragmentos, IDs, huellas, citas ni el
orden de recuperación. Haz que `sync --verify` obtenga huellas e IDs en un solo recorrido de Qdrant
por capa, con el mismo diagnóstico y sin escrituras. Distingue documentos ausentes, incompletos y
modificados. Un cambio solo de metadatos debe seguir sin volver a calcular embeddings. Mide las
relecturas de Gold y el costo de pedir embeddings por fragmento. Optimiza solo lo que justifique la
medición, sin cambiar el modelo ni reindexar producción."

## Contexto

La sincronización del corpus (spec 037) funciona y es incremental, pero su implementación acumula
dos costos que crecen con el corpus:

1. **Dos recorridos del índice en la verificación.** `sync --verify` pide las huellas y luego los
   documentos del índice en dos pasadas completas por capa. Cada pasada es una secuencia de
   *scrolls* sobre todos los puntos de la capa; en la capa extendida de producción son decenas de
   miles de puntos. El diagnóstico ya distingue documento incompleto de documento modificado,
   pero paga dos recorridos por la misma información.
2. **Relecturas de Gold dentro de un mismo `sync`.** El servicio lee Gold para calcular huellas,
   el importador de PostgreSQL lo vuelve a leer para importar y el indexador de Qdrant lo vuelve a
   leer para indexar (y otra vez para podar). Cada relectura abre y parsea los mismos Markdown.

Además, la lectura y la fragmentación de Gold viven en el adaptador SQL. La indexación importa
desde ese adaptador, de modo que una dependencia de infraestructura (SQLAlchemy) queda de facto en
el camino del índice vectorial y el dueño de la definición de documento y fragmento no es el
dominio. La spec 037 ya había movido las constantes de *chunking* al dominio; falta mover la
construcción del documento y del fragmento.

Esta capacidad **no cambia** lo que el corpus es: formatos, fragmentos, IDs de punto, huellas,
payloads, citas y orden de recuperación deben quedar idénticos. Tampoco cambia el modelo de
embeddings ni reindexa producción: es una simplificación y una optimización medida.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Verificar el corpus con un solo recorrido del índice (Priority: P1)

Un operador ejecuta `carvajal sync --verify` para saber si Gold, PostgreSQL y Qdrant coinciden.
La verificación lee el índice una sola vez por capa y aun así informa exactamente lo mismo que
hoy: qué documentos faltan, sobran, están incompletos o tienen otra huella, y los conteos por
capa. No escribe nada en ningún almacén.

**Why this priority**: es el ahorro más directo y el que más crece con el corpus; además el
diagnóstico es la herramienta con la que se decide una poda o una reindexación.

**Independent Test**: ejecutar `sync --verify` sobre el corpus local y comparar la salida con la de
la versión anterior; instrumentar el adaptador para contar los recorridos por capa (deben ser uno)
y confirmar con dobles que no se llama a ninguna operación de escritura.

**Acceptance Scenarios**:

1. **Given** un corpus sincronizado, **When** se ejecuta `sync --verify`, **Then** el índice se
   recorre una sola vez por capa y no se produce ninguna diferencia.
2. **Given** un documento de Gold que no está en Qdrant, **When** se verifica, **Then** informa
   que falta en Qdrant.
3. **Given** un documento en Qdrant cuyos puntos están a medias, **When** se verifica, **Then**
   informa que tiene puntos incompletos y no lo confunde con un documento modificado.
4. **Given** un documento cuya huella de payload difiere de Gold, **When** se verifica, **Then**
   informa que tiene otra huella de sincronización.
5. **Given** un documento que está en un almacén y no en Gold, **When** se verifica, **Then**
   informa que sobra en ese almacén.
6. **Given** PostgreSQL caído, **When** se verifica, **Then** sigue informando el estado de Qdrant
   y devuelve error distinto de cero.

---

### User Story 2 - Una sola definición de documento y fragmento Gold (Priority: P1)

Quien mantiene el corpus encuentra la lectura del front matter y la fragmentación en un módulo de
dominio, compartido por el importador relacional y por el indexador vectorial. Cambiar una regla
de lectura o de *chunking* se hace en un solo sitio y ambos almacenes producen lo mismo.

**Why this priority**: es la deuda estructural que permite que las optimizaciones posteriores no
dupliquen lógica; sin ella, el índice depende del adaptador SQL.

**Independent Test**: importar y verificar que las pruebas de carga y fragmentación se ejecutan
contra el módulo nuevo; comprobar por inspección de imports que ni PostgreSQL ni Qdrant definen
su propia lectura de Gold.

**Acceptance Scenarios**:

1. **Given** un Markdown Gold, **When** se carga desde el dominio, **Then** produce el mismo
   documento (id, título, sección, huella, procedencia) que antes.
2. **Given** un documento cargado, **When** se fragmenta desde el dominio, **Then** produce los
   mismos fragmentos, posiciones e IDs que antes.
3. **Given** el importador de PostgreSQL y el indexador de Qdrant, **When** se inspeccionan sus
   dependencias, **Then** ambos usan el módulo de dominio y ninguno redefine la lectura.

---

### User Story 3 - Un `sync` completo lee Gold una vez por capa (Priority: P2)

Un operador sincroniza un corpus grande con cambios. El servicio lee los Markdown de cada capa una
vez, calcula sus huellas y entrega ese mismo lote a PostgreSQL y a Qdrant para importar e indexar.
Las relecturas desaparecen sin cambiar los archivos que se escriben ni las huellas.

**Why this priority**: el costo de releer Gold crece con la capa extendida y con la frecuencia de
sincronización; es medible y acotado.

**Independent Test**: instrumentar el acceso a disco durante un `sync` con poda y contar las
lecturas por documento y capa (deben ser una); comparar los conteos de importación e indexación
con los de la versión anterior.

**Acceptance Scenarios**:

1. **Given** una capa con cambios, **When** se sincroniza, **Then** cada Markdown se lee una sola
   vez en esa corrida.
2. **Given** la misma corrida, **When** termina, **Then** PostgreSQL y Qdrant quedan igual que con
   la implementación anterior (mismos documentos, fragmentos, IDs y huellas).
3. **Given** una capa sin cambios, **When** se sincroniza, **Then** no se importa ni se indexa
   nada y el reporte conserva sus conteos.

---

### User Story 4 - El cambio solo de metadatos sigue sin reembeber (Priority: P2)

Un operador corrige la URL o la procedencia de un documento sin tocar su texto. La sincronización
actualiza el payload de Qdrant y no llama al embedder.

**Why this priority**: es la garantía de la entrega anterior (issue #185) y esta refactorización no
puede romperla: un re-embedding accidental multiplicaría el costo por fragmento.

**Independent Test**: ejecutar la prueba existente de cambio solo de metadatos y comprobar que las
llamadas al embedder no aumentan y que el payload se actualiza.

**Acceptance Scenarios**:

1. **Given** un documento indexado, **When** solo cambia un metadato del payload, **Then** se
   reescribe el payload y no se llama al embedder.
2. **Given** el mismo documento, **When** cambia el texto, **Then** solo ese documento se
   reembebe.

---

### User Story 5 - Medir antes de optimizar (Priority: P3)

El equipo dispone de una medición reproducible —mismo comando y mismo entorno antes y después—
con tiempos, recorridos del índice, lecturas de Gold y llamadas al embedder por fragmento. Solo
las mejoras que la medición respalda se implementan; el resto queda documentado como costo
aceptado.

**Why this priority**: sin números, un cambio de rendimiento es una opinión; además, evita tocar
piezas que no aportan (modelo de embeddings, formato del payload).

**Independent Test**: ejecutar el comando de medición con el corpus local antes y después de la
implementación y comparar ambos informes.

**Acceptance Scenarios**:

1. **Given** el corpus local y los servicios locales, **When** se ejecuta la medición, **Then**
   produce tiempos, recorridos, lecturas y llamadas al embedder.
2. **Given** los informes antes y después, **When** se comparan, **Then** las diferencias de
   conteos son explicables y ninguna mejora declarada carece de medición.
3. **Given** el costo por fragmento de pedir embeddings, **When** se mide, **Then** queda
   documentado si se optimiza o si se acepta y por qué.

---

### Edge Cases

- El índice no existe todavía (primera sincronización): la verificación lo trata como índice
  vacío y no falla.
- Un documento con puntos en Qdrant pero sin huella publicada (puntos incompletos) se informa
  como incompleto, nunca como modificado.
- Un documento ausente en ambos almacenes se informa una sola vez por almacén con el mismo
  mensaje que hoy.
- Una capa extendida sin fuentes es una capa vacía: la verificación no la confunde con un error.
- Gold ilegible o con slugs duplicados: el error por capa se reporta y no se toca ningún almacén.
- PostgreSQL caído durante la verificación: el diagnóstico de Qdrant sigue saliendo.
- Qdrant caído durante una sincronización: PostgreSQL se repara y la siguiente corrida repara el
  índice.
- Un documento sin texto no genera fragmentos y no entra al atajo de payload.
- Una corrida con `--only` no toca los documentos no pedidos.
- Dos corridas simultáneas siguen serializándose por el bloqueo del corpus.
- La salida textual de `sync` y `sync --verify` conserva sus líneas y su código de salida.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `sync --verify` MUST obtener de Qdrant las huellas y los IDs de cada capa en un
  único recorrido por capa —una sola pasada lógica sobre los puntos de la capa, con las páginas
  que el cliente necesite—; MUST NOT hacer dos recorridos completos de la misma capa.
- **FR-002**: La verificación MUST conservar el diagnóstico observable de hoy: diferencia por
  documento ausente, sobrante, con puntos incompletos o con otra huella, y diferencia de conteo
  por capa, con el mismo texto y el mismo código de salida.
- **FR-003**: La verificación MUST NOT escribir en PostgreSQL ni en Qdrant, ni cambiar el estado
  del corpus en disco.
- **FR-004**: La lectura de un Markdown Gold y su fragmentación MUST vivir en un módulo de dominio
  fuera de los adaptadores; PostgreSQL y el indexado vectorial MUST reutilizar ese módulo y MUST
  NOT duplicar su lógica.
- **FR-005**: Los documentos, fragmentos, IDs de punto, huellas de sincronización y payloads
  producidos MUST ser idénticos a los de la versión anterior para el mismo corpus.
- **FR-006**: Un `sync` MUST recorrer los Markdown de cada capa una sola vez —una pasada completa
  sobre el conjunto de archivos, con la lectura de cada archivo que esa pasada implique— y
  reutilizar ese lote (documentos y huellas) en el cálculo, la importación y la indexación de esa
  capa.
- **FR-007**: Un cambio que solo afecta metadatos del payload MUST seguir actualizando el payload
  sin llamar al embedder.
- **FR-008**: La sincronización MUST conservar su comportamiento ante fallos parciales: un almacén
  caído no pierde Gold, el otro almacén se repara y una corrida posterior converge.
- **FR-009**: El costo MUST ser medible: relecturas de Gold por capa, recorridos del índice por
  capa en la verificación y llamadas al embedder por fragmento, con un comando reproducible y el
  mismo entorno antes y después.
- **FR-010**: Toda mejora de rendimiento declarada MUST estar respaldada por la medición; lo que
  no se justifique MUST quedar documentado como costo aceptado.
- **FR-011**: La capacidad MUST NOT cambiar el modelo de embeddings, MUST NOT reindexar producción
  y MUST NOT introducir migraciones de base de datos ni dependencias nuevas.
- **FR-012**: Los contratos de la CLI (`sync`, `sync --verify`), de la sincronización usada por la
  web y del puerto del índice MUST conservar su comportamiento observable, salvo la lectura única
  que esta capacidad introduce.

### Key Entities

- **Lote de capa Gold**: una lectura de los Markdown de una capa con sus documentos y sus dos
  huellas (base para PostgreSQL e índice). Es la unidad que se reutiliza dentro de una corrida.
- **Instantánea del índice**: huellas publicadas y documentos presentes en el índice por capa,
  obtenidos en un solo recorrido. Distingue documento ausente, incompleto, modificado y sobrante.
- **Documento y fragmento Gold**: entidades del dominio que se construyen en un único módulo y
  alimentan ambos almacenes.
- **Reporte de sincronización**: conteos y diferencias que la CLI imprime y traduce en código de
  salida.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: En una verificación, las pasadas completas sobre el índice (una invocación del
  recorrido por capa, sin contar las páginas internas del cliente) bajan de dos a una por capa,
  con cero escrituras.
- **SC-002**: En un `sync` completo con poda, las pasadas completas sobre los Markdown de Gold por
  capa bajan de tres o más a una (conteo instrumentado), sin aumentar las lecturas de archivo
  necesarias dentro de esa pasada.
- **SC-003**: El mismo corpus produce exactamente los mismos documentos, fragmentos, IDs de punto,
  huellas, payloads y citas que antes (pruebas de caracterización sin cambios en sus aserciones).
- **SC-004**: Un cambio solo de metadatos produce cero llamadas al embedder y cero puntos nuevos.
- **SC-005**: Con el corpus local y el mismo comando, el tiempo de `sync --verify` no empeora y el
  número de peticiones al índice se reduce a la mitad; el informe antes/después queda en
  `specs/045-corpus-rendimiento/mediciones.md`.
- **SC-006**: El costo por fragmento de pedir embeddings queda medido y la decisión —optimizar o
  aceptar— queda justificada en el mismo informe.
- **SC-007**: `make check`, `make test-integration` y las pruebas de JavaScript siguen en verde.

## Assumptions

- El corpus local reproducible es el núcleo (24 documentos, 125 fragmentos) con PostgreSQL y
  Qdrant locales; la capa extendida de producción no está disponible en el entorno de desarrollo y
  cualquier extrapolación se declara como tal en la medición.
- La colección y la base locales son de desarrollo; la medición no toca producción ni reindexa el
  VPS.
- El cliente de Qdrant permite obtener payload y ID de cada punto en un único *scroll*, lo que
  basta para huellas y documentos.
- Los dobles de prueba pueden actualizarse a los puertos nuevos; el comportamiento observable de
  la CLI no cambia.
- El atajo de metadatos (spec 037, issue #185) sigue siendo correcto y no se modifica.
- La entrega no incluye la optimización de embeddings por lote: se mide su costo y se decide con
  los números.
