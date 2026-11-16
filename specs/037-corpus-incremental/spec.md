# Feature Specification: Corpus incremental, deduplicado y sincronizado

**Feature Branch**: `037-corpus-incremental`
**Created**: 2026-11-16
**Status**: Draft
**Input**: User description: "El corpus que se captura del sitio y el que se agrega después por la web o la consola deben convivir en un solo corpus: reimportar no debe duplicar, un archivo modificado debe actualizar su documento, todo lo que está en Gold debe quedar disponible para el chat y el asistente, y la capa extendida debe recuperarse con prioridad sin desplazar al núcleo."

## Contexto

La spec 035 entregó la carga multiformato (PDF, datos estructurados, consola de carpetas) y el
índice concurrente. Esta capacidad resuelve lo que falta para que el corpus crezca sin
degradarse: **identidad estable**, **deduplicación por huella del original**, **actualización de
versiones** y **un único camino de indexación** para todo lo que entra.

Sustituye el supuesto de identidad de la 035 (`nombre + hash`): la identidad pasa a derivarse
de la fuente y la huella del contenido queda como versión. El resto del contrato de la 035
(formatos, límites, consola de carpetas y escritura atómica del índice) sigue vigente.

No incluye el rastreo periódico (cron/crawler): es una capacidad futura que consumirá estos
mismos contratos.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Agregar y reimportar sin duplicar (Priority: P1)

Un operador importa una carpeta de documentos desde la consola, o sube archivos desde
Configuración. Días después repite la importación con la carpeta actualizada: los archivos que
no cambiaron no se vuelven a procesar, los nuevos entran una sola vez, los que ya existían con
otro nombre no se duplican y los modificados actualizan su documento conservando la versión
anterior.

**Why this priority**: es la condición para que el corpus pueda crecer sin inflarse. Sin esto,
cada reimportación duplica documentos, gasta extracción y embeddings y ensucia las citas.

**Independent Test**: importar una carpeta con un archivo, reimportarla sin cambios y comprobar
que no se crea ni se reprocesa nada; renombrar una copia y comprobar que se detecta como
duplicado; modificar un archivo y comprobar que el documento se actualiza y la versión anterior
queda archivada.

**Acceptance Scenarios**:

1. **Given** una carpeta ya importada, **When** se reimporta sin cambios, **Then** no se crea
   ningún documento, no se reescriben las capas y el informe lo declara.
2. **Given** un archivo cuyo contenido exacto ya está en el corpus con otro nombre, **When** se
   importa, **Then** se detecta como duplicado por la huella del original, no se escribe en
   Bronze ni en Silver ni en Gold y se informa qué documento lo contiene.
3. **Given** un archivo ya importado que se modificó en disco, **When** se reimporta, **Then**
   el mismo documento se actualiza con el contenido nuevo y la versión anterior queda
   archivada; no aparece un documento nuevo.
4. **Given** dos archivos con el mismo nombre y contenido distinto, en carpetas distintas,
   **When** se importan en la misma corrida, **Then** cada uno recibe una identidad distinta y
   ninguno sobrescribe al otro.
5. **Given** una página web que no cambió desde la última captura, **When** se vuelve a
   capturar, **Then** no se reescriben sus capas ni se recalculan sus embeddings.
6. **Given** una página web cuyo texto cambió, **When** se vuelve a capturar, **Then** el mismo
   documento se actualiza y su versión anterior queda archivada.

---

### User Story 2 - Todo lo que entra se puede consultar (Priority: P1)

Cualquier camino de entrada —captura web, captura por consola, subida desde Configuración o
importación de una carpeta— deja el documento disponible para el chat y el asistente sin pasos
manuales. Si la base o el índice estaban caídos durante la carga, una sincronización posterior
repara la diferencia sin perder ni duplicar nada.

**Why this priority**: hoy `carvajal capture` escribe Gold pero no indexa, y un fallo de un
almacén deja el corpus en dos estados distintos. El valor del corpus es que se pueda consultar,
no que esté en disco.

**Independent Test**: capturar una URL por consola y preguntar por su contenido en el chat; con
el índice detenido, subir un documento y comprobar que Gold queda escrito, que el chat no lo ve
todavía y que al sincronizar aparece una sola vez.

**Acceptance Scenarios**:

1. **Given** una URL capturada por la consola, **When** termina la captura, **Then** el
   documento aparece en el explorador y el chat puede citarlo, sin comandos adicionales.
2. **Given** una carga mientras el índice no está disponible, **When** la carga termina,
   **Then** Gold conserva el documento y el informe avisa; al ejecutar la sincronización, el
   documento aparece una sola vez.
3. **Given** una sincronización interrumpida a la mitad, **When** se vuelve a ejecutar, **Then**
   converge al mismo estado que si hubiera terminado, sin documentos repetidos ni huérfanos.
4. **Given** un documento eliminado del corpus, **When** se sincroniza con poda, **Then** deja
   de aparecer en el chat, la FAQ y el explorador.

---

### User Story 3 - Reindexar solo lo que cambió (Priority: P2)

Un operador reimporta una carpeta de cientos de archivos después de actualizar unos pocos. El
sistema detecta qué documentos cambiaron y recalcula embeddings solo para esos; los demás
conservan sus vectores y sus puntos. El informe separa nuevos, actualizados, idénticos y
duplicados.

**Why this priority**: el coste real de una reimportación son los embeddings en CPU, no la
extracción. Sin incrementalidad, un corpus grande se vuelve intratable de mantener.

**Independent Test**: cronometrar una segunda importación sin cambios y comprobar que no se
recalcula ningún embedding; modificar un archivo y comprobar que solo ese documento se
reindexa.

**Acceptance Scenarios**:

1. **Given** un corpus indexado, **When** se reimporta sin cambios, **Then** no se calcula
   ningún embedding nuevo y el número de puntos no cambia.
2. **Given** un documento modificado, **When** se reindexa, **Then** sus puntos anteriores se
   reemplazan y no quedan fragmentos de la versión vieja.
3. **Given** una corrida que falla después de actualizar Gold, **When** se repite, **Then** no
   duplica puntos y termina de indexar lo pendiente.
4. **Given** una sincronización completa sobre un corpus sin cambios, **When** termina,
   **Then** el resultado es idéntico al anterior (idempotencia verificable).

---

### User Story 4 - Capa extendida recuperable sin desplazar al núcleo (Priority: P2)

Un operador importa el corpus de un tercero como capa extendida. El asistente puede recuperar y
citar esos documentos con su procedencia, pero las preguntas que el núcleo responde siguen
citando el núcleo; la FAQ sigue construyéndose solo con documentos propios.

**Why this priority**: el corpus donante cubre temas que el sitio no publica, pero mezclarlo en
plano degrada la confianza: una respuesta oficial no puede quedar debajo de un documento de un
colaborador.

**Independent Test**: importar el corpus extendido, hacer una pregunta del núcleo y comprobar
que las citas son del núcleo; hacer una pregunta que solo el extendido responde y comprobar que
la cita lleva su origen.

**Acceptance Scenarios**:

1. **Given** un núcleo y una capa extendida indexados, **When** una pregunta tiene respuesta en
   ambos, **Then** las citas priorizan el núcleo.
2. **Given** una pregunta que solo responde la capa extendida, **When** se consulta, **Then** la
   respuesta cita el documento extendido con su origen y categoría.
3. **Given** la capa extendida indexada, **When** se genera la FAQ, **Then** solo se usan
   documentos del núcleo.
4. **Given** la evaluación de recuperación del núcleo, **When** se compara antes y después de
   indexar la capa extendida, **Then** las métricas del núcleo no empeoran.
5. **Given** un corpus con núcleo y capa extendida, **When** se filtra el explorador por capa
   o procedencia, **Then** solo aparecen los documentos de ese filtro y cada uno muestra su
   origen.

---

### User Story 5 - Un solo índice de respuestas (Priority: P3)

El equipo retira la ruta heredada `/ask` y el adaptador pgvector, que hoy responde sin contexto
porque ningún flujo escribe vectores en Postgres. El chat queda como único camino de respuesta;
Postgres conserva documentos y fragmentos para la FAQ y el explorador.

**Why this priority**: un índice vacío que parece funcionar es deuda silenciosa y confunde a
quien opera el sistema.

**Independent Test**: comprobar que `/ask` ya no existe y que el chat, la FAQ y el explorador
siguen funcionando igual.

**Acceptance Scenarios**:

1. **Given** la aplicación desplegada, **When** se visita `/ask`, **Then** responde que no
   existe.
2. **Given** un corpus sincronizado, **When** se consulta la FAQ y el explorador, **Then**
   siguen funcionando con los documentos de Postgres.
3. **Given** la base de datos, **When** se inspeccionan los fragmentos, **Then** no queda una
   columna de vectores sin uso.

---

### Edge Cases

- Copia exacta del mismo archivo con otro nombre en la misma corrida: el segundo se reporta
  como duplicado del primero; no se procesa.
- Mismo contenido re-codificado (PDF re-guardado): la huella del original difiere, la huella del
  texto coincide; se detecta después de extraer y se evita la indexación duplicada.
- Página web con bytes distintos pero texto idéntico: se considera sin cambios.
- Página web eliminada o con error: no se borra el documento existente sin poda explícita.
- Archivo movido de carpeta: al cambiar su ruta cambia su identidad; la importación lo trata
  como nuevo y la poda elimina el anterior sólo si se pide.
- Fallo de Postgres o Qdrant a mitad de una sincronización: Gold queda intacto y la siguiente
  corrida repara.
- Dos sincronizaciones simultáneas: la segunda espera o se rechaza; el corpus nunca queda a
  medio escribir.
- Documentos anteriores a esta capacidad, con identidad derivada del hash: se adoptan sin
  renombrar ni perder contenido.
- Corpus núcleo por encima del tope de la FAQ: se avisa y se selecciona de forma determinista;
  nunca se trunca en silencio.
- Archivo vacío, ilegible o con formato no admitido: se reporta y el lote continúa.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Cada documento MUST tener una identidad estable derivada de su fuente canónica:
  URL normalizada para capturas, ruta relativa para importaciones de consola y nombre de
  archivo para subidas web. La huella del contenido MUST NOT formar parte de una identidad
  nueva.
- **FR-002**: El sistema MUST conservar un registro persistente que asocie fuente, identidad,
  huella del original, huella del texto, capa y fecha de actualización, y MUST mantenerlo
  consistente con el índice del corpus.
- **FR-003**: Los archivos locales MUST deduplicarse por la huella SHA-256 de los bytes
  originales antes de escribir Bronze. Un duplicado MUST omitirse y reportarse sin gastar
  extracción ni indexación.
- **FR-004**: Si una fuente ya conocida llega con contenido distinto, el sistema MUST actualizar
  el mismo documento y archivar la versión anterior; MUST NOT crear un documento nuevo.
- **FR-005**: Tras la extracción, la huella del texto normalizado MUST compararse con la versión
  vigente. Si no cambió, el sistema MUST NOT reescribir capas ni recalcular embeddings.
- **FR-006**: Las capturas web MUST usar la URL normalizada como identidad y la huella del texto
  para detectar cambios, porque los bytes del HTML cambian sin que cambie el contenido.
- **FR-007**: Todo camino de entrada —subida web, captura web, captura por consola e importación
  de carpeta— MUST terminar sincronizando los documentos afectados con PostgreSQL y con el
  índice vectorial del chat.
- **FR-008**: El sistema MUST ofrecer una sincronización explícita que reconcilie el corpus en
  disco con ambos almacenes, de forma incremental, idempotente y repetible tras un fallo.
- **FR-009**: La sincronización MUST NOT recalcular embeddings de documentos sin cambios; para
  los cambiados MUST reemplazar sus fragmentos y puntos sin dejar residuos de la versión
  anterior.
- **FR-010**: Un fallo de un almacén durante la sincronización MUST NOT perder el documento en
  Gold; la siguiente sincronización MUST reparar la diferencia.
- **FR-011**: La capa extendida MUST ser recuperable y citable por el chat con su procedencia
  (`origin`, `category`, `entity`), y la FAQ MUST seguir leyendo solo el núcleo.
- **FR-012**: Cuando núcleo y capa extendida respondan a la misma pregunta, la recuperación MUST
  priorizar el núcleo; la capa extendida MUST completar solo cuando el núcleo no alcance.
- **FR-013**: El explorador de corpus MUST mostrar capa y procedencia y MUST permitir filtrar
  por ambas.
- **FR-014**: La ruta heredada `/ask` y el adaptador de vectores en PostgreSQL MUST retirarse; el
  chat MUST quedar como único camino de respuesta. Documentos y fragmentos MUST conservarse en
  PostgreSQL para la FAQ y el explorador.
- **FR-015**: Una sincronización con poda MUST eliminar de ambos almacenes los documentos que ya
  no existen en el corpus; sin poda, MUST conservarlos.
- **FR-016**: Cada corrida MUST informar nuevos, actualizados, sin cambios, duplicados y
  errores, identificando el archivo o la URL que causó cada descarte.
- **FR-017**: Las escrituras del registro y del índice MUST ser atómicas y MUST excluirse
  mutuamente entre procesos; dos escrituras simultáneas MUST NOT perder entradas ni dejar el
  archivo truncado.
- **FR-018**: El corpus existente MUST adoptarse sin pérdida de contenido ni renombres: conserva
  su identidad actual y registra su fuente y sus huellas cuando estén disponibles.
- **FR-019**: Dos sincronizaciones simultáneas MUST serializarse o rechazarse de forma explícita;
  el corpus MUST NOT quedar en un estado intermedio.
- **FR-020**: Los límites de la carga (100 archivos y 5 MB en la web; 100 MB por archivo en la
  consola) MUST mantenerse.

### Key Entities

- **Documento**: unidad del corpus con identidad estable, título, sección, capa y procedencia.
- **Fuente**: de dónde vino el documento (URL, ruta relativa o archivo subido) y con qué tipo de
  procedencia (`oficial`, `tercero`, `colaborador`).
- **Versión**: contenido vigente y anteriores de un documento, identificados por la huella del
  texto y la fecha de captura.
- **Registro del corpus**: asociación persistente entre fuente, identidad, huellas, capa y
  estado de sincronización.
- **Informe de corrida**: conteos de nuevos, actualizados, sin cambios, duplicados y errores,
  con el motivo por elemento.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Reimportar dos veces la misma carpeta produce cero documentos nuevos, cero
  escrituras en capas y cero puntos nuevos en la segunda corrida.
- **SC-002**: Una carpeta con N archivos y M copias exactas termina con N−M documentos y reporta
  las M como duplicadas.
- **SC-003**: Modificar un archivo y reimportarlo actualiza un único documento y archiva una
  única versión; el conteo total no cambia.
- **SC-004**: El 100 % de los documentos presentes en Gold es recuperable por el chat después de
  cualquier camino de entrada, verificado con una muestra de preguntas.
- **SC-005**: Una sincronización sin cambios recalcula cero embeddings y deja el mismo número de
  puntos.
- **SC-006**: Las métricas de recuperación del núcleo (Recall@10 y nDCG@10) no empeoran al
  indexar la capa extendida.
- **SC-007**: Una interrupción durante la sincronización deja el índice legible y la siguiente
  corrida converge sin duplicados.
- **SC-008**: Una pregunta con respuesta en ambas capas cita el núcleo; una que solo responde la
  capa extendida cita su origen.
- **SC-009**: La aplicación no expone `/ask` y no conserva un índice vectorial vacío en
  producción.

## Assumptions

- La spec 035 (formatos, consola y escritura atómica del índice) está implementada y es la base.
- El rastreo periódico (cron/crawler) queda fuera; esta capacidad entrega los contratos que
  necesitará.
- Las subidas web se identifican por nombre de archivo porque el navegador no envía la ruta
  relativa; un choque de nombres se resuelve renombrando el archivo.
- La capa extendida conserva su origen, su taxonomía y su carácter de solo recuperación.
- La columna de vectores de PostgreSQL está vacía en producción, por lo que retirarla no pierde
  datos.
- El OCR de PDF escaneados sigue fuera de alcance.
- El tope de 500 documentos de la FAQ se mantiene; si el núcleo lo supera, se avisa y la
  selección es determinista.
- No se introducen dependencias nuevas de Python ni servicios nuevos.
- Las banderas de solo extracción (`--solo-extraer`, `--solo-gold`) existen para pruebas y
  trabajo sin servicios; no son el camino por defecto de una carga real.
