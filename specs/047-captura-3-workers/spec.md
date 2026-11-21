# Feature Specification: Captura web duradera para tres workers

**Feature Branch**: `047-captura-3-workers`
**Created**: 2026-11-20
**Status**: Draft (fase 047a en implementación; la activación de tres workers se
entrega en una tarea posterior, `task-8`)
**Input**: Objetivo del owner: «Comparte el estado de los jobs, el progreso consultable, la
exclusión y el límite por IP mediante el PostgreSQL existente. Mantén una sola captura web
activa globalmente; no una por worker. Cualquier worker debe poder servir el progreso
respetando los permisos actuales. Define cómo detectar trabajos interrumpidos y recuperarlos
desde un punto seguro. Si se repite trabajo, no debe duplicar documentos ni dejar escrituras
inconsistentes. No prometas reanudación exacta por URL si no se implementa y prueba. Revisa
la inicialización concurrente de recursos compartidos, los presupuestos de conexiones y
memoria, y el cierre de los procesos. Conserva `/live` independiente de la base y el
contrato de `/health`.»

## Contexto y problema

La captura web desde Configuración se guarda hoy en la memoria del proceso: el registro de
jobs, el progreso que consume el SSE y el límite por IP viven en un diccionario. El
despliegue usa **un solo worker de uvicorn**, así que ese diseño funciona. Al pasar a tres
workers (tres procesos sobre el mismo contenedor y el mismo volumen de datos), la memoria
deja de ser un lugar válido para ese estado:

- la petición que crea el job y la que consulta su progreso pueden caer en procesos
  distintos, y la segunda responde «la captura no existe o expiró»;
- cada proceso permitiría una captura concurrente, de modo que tres workers podrían escribir
  el corpus a la vez;
- el límite por IP se multiplicaría por la cantidad de procesos;
- si un worker muere a mitad de una captura, el estado queda huérfano y nadie libera la
  capacidad de captura.

El corpus ya tiene una base de datos PostgreSQL como fuente de verdad relacional y una
sincronización idempotente (`carvajal sync`, spec 037). Esta capacidad reutiliza esa base y
esa sincronización en lugar de añadir infraestructura nueva.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Consultar el progreso de una captura desde cualquier worker (Priority: P1)

Una persona lanza una captura desde Configuración. La petición la atiende uno de los tres
workers y la captura corre en ese worker. El navegador abre el stream de progreso y, por
balanceo, puede reconectar contra otro worker; además puede consultar el estado final con el
identificador del job. En ambos casos ve el mismo progreso y el mismo resultado, con los
mismos eventos, códigos y mensajes que hoy.

**Why this priority**: sin esto, con tres workers la captura deja de ser usable: el progreso
desaparece en dos de cada tres peticiones y no se puede distinguir «no existe» de «otro
proceso la tiene».

**Independent Test**: lanzar una captura en un worker, consultar su estado y su stream desde
otra instancia, desconectar y reconectar, y comprobar que el resultado final es el mismo y
que un job inexistente sigue devolviendo 404.

**Acceptance Scenarios**:

1. **Given** una captura en curso iniciada en el worker A, **When** el worker B recibe
   `GET /configuracion/captura/{job_id}`, **Then** responde 200 con el estado vigente y, al
   terminar, con el mismo `outcome` que habría devuelto A.
2. **Given** una captura en curso, **When** el navegador abre o reconecta
   `GET /configuracion/captura/{job_id}/eventos` contra otro worker, **Then** recibe los
   eventos de progreso que le faltan y el evento final, sin duplicar los ya recibidos.
3. **Given** un `job_id` desconocido o expirado, **When** se consulta el estado o el stream,
   **Then** la respuesta es 404 con el mensaje actual «La captura no existe o expiró.».
4. **Given** una captura terminada, **When** se consulta su estado pasada la retención,
   **Then** deja de existir para el usuario igual que hoy (TTL de una hora).
5. **Given** que cualquier persona conoce el `job_id`, **When** consulta el estado o el
   progreso, **Then** accede sin cookie, sesión ni autenticación, y la respuesta no revela
   la IP ni datos del dueño del job (permisos actuales por capacidad).

---

### User Story 2 - Una sola captura activa y un límite por IP compartido (Priority: P1)

Dos personas —o la misma persona en dos pestañas— envían una captura a la vez. Solo una
arranca; la otra recibe el mismo 409 con «Ya hay una captura en curso. Espera a que
termine.» que hoy, aunque las peticiones caigan en workers distintos. El límite de capturas
por IP de la ventana configurada se cuenta una sola vez para todo el despliegue, no una vez
por worker, y sigue respondiendo 429 con `Retry-After`.

**Why this priority**: la captura escribe Gold y sincroniza PostgreSQL y Qdrant; dos
capturas simultáneas en workers distintos ya no quedan serializadas por la memoria del
proceso y podrían competir por las mismas URLs y por el índice.

**Independent Test**: lanzar dos solicitudes simultáneas a dos instancias y comprobar que
solo una se acepta; agotar el cupo de una IP en una instancia y comprobar que otra instancia
lo rechaza con 429 y el mismo `Retry-After`.

**Acceptance Scenarios**:

1. **Given** una captura en curso, **When** se envía otra desde el mismo worker o desde otro,
   **Then** la segunda recibe 409 con el mensaje actual y no inicia trabajo.
2. **Given** dos envíos simultáneos desde workers distintos, **When** ambos intentan
   arrancar, **Then** exactamente uno es aceptado (200) y el otro recibe 409.
3. **Given** una IP con el cupo agotado en la ventana, **When** envía otra captura a otro
   worker con el anterior ya terminado, **Then** recibe 429 con `Retry-After` igual al
   tiempo que falta para liberar cupo.
4. **Given** una IP con el cupo agotado, **When** otra IP envía una captura, **Then** no se
   ve afectada por el cupo de la primera.
5. **Given** que la captura terminó, **When** la misma IP vuelve a enviar dentro de la
   ventana, **Then** solo se acepta si aún le queda cupo (el límite no se reinicia al
   terminar la captura).

---

### User Story 3 - Detectar y recuperar una captura interrumpida (Priority: P1)

Un worker se cae, se reinicia el contenedor o un despliegue mata el proceso a mitad de una
captura. Al volver a arrancar, el sistema detecta que ese trabajo quedó interrumpido, lo
marca como tal, libera la captura global y permite lanzar una captura nueva. La parte que
llegó a escribirse en Gold se reconcilia con PostgreSQL y Qdrant mediante la sincronización
idempotente, sin duplicar documentos ni dejar escrituras a medias.

**Why this priority**: con un solo worker, morir a mitad de captura dejaba un job en memoria
que desaparecía con el proceso y el estado se perdía en silencio; con estado persistente,
un job «running» eterno bloquearía todas las capturas futuras.

**Independent Test**: simular la caída de un worker con una captura en curso, reiniciar la
lógica de arranque y comprobar que el job queda interrumpido, que se puede lanzar otra
captura y que repetir la captura de las mismas URLs no crea documentos duplicados.

**Acceptance Scenarios**:

1. **Given** un job en estado «running» cuyo worker dejó de dar señales de vida, **When**
   arranca un worker o alguien intenta una captura nueva, **Then** el job pasa a
   «interrumpido», se libera la captura global y la nueva solicitud puede aceptarse.
2. **Given** un job interrumpido, **When** se consulta su estado, **Then** informa que se
   interrumpió y no se queda «running» para siempre; su stream emite el evento final de
   error y se cierra.
3. **Given** un job interrumpido con parte del trabajo escrito en Gold, **When** se ejecuta
   la recuperación, **Then** se sincroniza esa parte con PostgreSQL y Qdrant de forma
   idempotente y repetir la recuperación no añade documentos nuevos.
4. **Given** la misma URL capturada tras una interrupción, **When** se repite la captura y
   la sincronización, **Then** el corpus conserva un solo documento para esa fuente.
5. **Given** una captura interrumpida, **When** la persona vuelve a Configuración, **Then**
   entiende que puede lanzar otra captura y **no** se le promete continuar exactamente por
   la URL donde se cortó.

---

### User Story 4 - Operar tres workers sin regresiones ni desbordes de recursos (Priority: P2)

El owner pasa la app a tres workers de uvicorn en `compose.prod.yml`. La vitalidad
(`/live`) sigue sin depender de la base, la preparación (`/health`) conserva su contrato, y
la inicialización de recursos compartidos (motor de base, pools, clientes) es diferida y
acotada para que tres procesos no agoten las conexiones ni dupliquen trabajo de arranque.
Al apagar un worker, sus conexiones se cierran y sus capturas en curso quedan marcadas para
recuperación.

**Why this priority**: activar los tres workers es la tarea siguiente (`task-8`); esta
entrega debe dejar el terreno preparado y sin regresiones, pero el cambio de `CMD` no se
hace aquí.

**Independent Test**: con la app cableada al estado compartido, comprobar `/live` con la
base caída y el contrato exacto de `/health`; arrancar y apagar una instancia y verificar que
no deja conexiones abiertas ni capturas huérfanas.

**Acceptance Scenarios**:

1. **Given** la base de datos caída, **When** se llama `GET /live`, **Then** responde 200
   sin tocar la base.
2. **Given** la base disponible, **When** se llama `GET /health`, **Then** responde con las
   mismas claves, conteos y códigos 200/503 que hoy.
3. **Given** tres workers configurados, **When** arrancan y atienden tráfico normal,
   **Then** el número de conexiones a la base por proceso está acotado y no crece con las
   peticiones ni con los streams.
4. **Given** un worker que se apaga con una captura en curso, **When** termina el apagado,
   **Then** cierra sus conexiones y deja la captura marcada para que otro worker la detecte
   como interrumpida.
5. **Given** la misma instalación con el registro compartido, **When** se recorren
   `POST /configuracion/captura`, el estado por `job_id` y el stream, **Then** los códigos,
   mensajes y formatos actuales se conservan en todos los workers.

---

### Edge Cases

- **Dos arranques simultáneos en el mismo instante**: la exclusión debe decidirse en una
  sola operación atómica; nunca dos jobs activos ni bloqueo mutuo permanente.
- **Heartbeat retrasado por pausa del proceso (GC, carga)**: el umbral de interrupción debe
  ser holgadamente mayor que el intervalo de latido para no declarar muerto un worker vivo.
- **Trabajo repetido tras una falsa interrupción**: si el worker supuestamente caído sigue
  vivo y termina, su trabajo debe converger sin duplicar documentos porque la escritura de
  Gold es atómica y la sincronización es idempotente.
- **Stream abierto al terminar el job**: debe recibir el evento final y cerrarse; una
  reconexión posterior recibe el mismo resultado.
- **Job expirado mientras hay un stream abierto**: el stream termina sin error de servidor.
- **Base caída al arrancar**: el arranque no falla; la recuperación de interrumpidos se
  reintenta en el siguiente arranque o en el siguiente intento de captura.
- **Base caída durante una captura**: la captura en curso informa el fallo de sincronización
  como hoy y no deja el corpus en un estado inconsistente.
- **Apagado ordenado a mitad de captura**: el job se marca interrumpido; el siguiente
  arranque reconcilia con `sync`.
- **Límite por IP con ventana deslizante**: el `Retry-After` debe ser el tiempo restante
  real, no la ventana completa.
- **Job de otro proceso en un estado terminal**: el TTL y la poda no deben borrar el job
  mientras otro worker está sirviendo su stream.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El estado de cada captura (identificador, estado, petición, resultado, error,
  instante de creación y de cierre) MUST persistirse en el PostgreSQL existente y ser
  legible por cualquier worker.
- **FR-002**: El progreso de la captura MUST persistirse como una secuencia ordenada de
  eventos por job y MUST poder reproducirse desde cualquier worker, incluso si el stream se
  abre después de que la captura terminó.
- **FR-003**: `GET /configuracion/captura/{job_id}` MUST conservar el contrato actual:
  200 con `id`, `status`, `error` y `outcome`; 404 con el mismo mensaje si el job no existe
  o expiró.
- **FR-004**: `GET /configuracion/captura/{job_id}/eventos` MUST conservar el contrato SSE
  actual (`stage`, `done`, `error`, mismos campos y formato) y MAY añadir los identificadores
  estándar de evento para reanudar sin duplicados.
- **FR-005**: `POST /configuracion/captura` MUST conservar códigos y mensajes actuales:
  200 aceptado, 403 CSRF, 409 captura en curso, 422 validación, 429 con `Retry-After` por
  límite de IP.
- **FR-006**: Los permisos MUST mantenerse: el estado y el progreso se consultan con el
  `job_id` como capacidad, sin sesión ni autenticación, y la respuesta no expone la IP ni la
  identidad del dueño. La IP MUST NOT guardarse en claro: el cupo se indexa por un resumen
  criptográfico estable y nunca se devuelve ni se registra en logs.
- **FR-007**: MUST existir una sola captura web activa en todo el despliegue, no una por
  proceso; el segundo intento simultáneo MUST rechazarse con 409.
- **FR-008**: El límite de capturas por IP MUST contarse de forma compartida entre todos los
  workers, respetando la ventana y el cupo configurados, sobre un resumen estable de la IP
  (HMAC con `SESSION_SECRET` cuando existe) que no permita leerla ni enumerarla desde la
  tabla.
- **FR-009**: El sistema MUST detectar capturas cuyo proceso dejó de dar señales de vida
  mediante un latido persistido y un umbral de caducidad, tanto al arrancar como al intentar
  iniciar una captura nueva.
- **FR-010**: Un job detectado como interrumpido MUST quedar marcado como interrumpido con
  un mensaje apto para el usuario, liberar la captura global y permitir una nueva captura.
- **FR-011**: Al recuperar una captura interrumpida, el sistema MUST reconciliar Gold con
  PostgreSQL y Qdrant mediante una sincronización idempotente completa de la capa core
  (`only=None`, sin poda), porque un documento pudo escribirse en Gold sin que llegara su
  evento; la recuperación MUST completarse o liberarse antes de aceptar la siguiente
  captura. MUST NOT prometer ni implementar reanudación exacta por URL en esta entrega.
- **FR-012**: Repetir trabajo por una interrupción o por una nueva captura de las mismas
  URLs MUST NOT duplicar documentos en Gold, PostgreSQL ni Qdrant, ni dejar escrituras
  inconsistentes; la identidad estable por fuente, la escritura atómica y las huellas de
  sincronización son la garantía.
- **FR-013**: La retención y la poda de jobs, eventos y marcas de cupo MUST ser explícitas
  (TTL y límite de registros, ejecutadas de forma oportunista y acotada al arrancar una
  captura) y MUST NOT romper un stream en curso; expirado el TTL, el job deja de existir y
  el 404 conserva el mensaje actual.
- **FR-014**: `GET /live` MUST seguir sin tocar la base de datos ni Qdrant.
- **FR-015**: `GET /health` MUST conservar su contrato (claves, conteos, estado del índice y
  del monitoreo, códigos 200/503) sin consultar las tablas de captura.
- **FR-016**: La inicialización de recursos compartidos (motor, pools, clientes, registro de
  capturas) MUST ser diferida y tolerante a base caída; el ancho por defecto del pool
  SQLAlchemy MUST conservarse (lo comparten chat, FAQ y CLI), el presupuesto de conexiones
  por worker MUST documentarse y medirse, y el apagado MUST cerrar las conexiones del
  proceso y marcar sus capturas en curso como interrumpidas.
- **FR-017**: Los cambios de esquema MUST entregarse en una migración Alembic nueva,
  reversible (subida y bajada), sin editar migraciones históricas.
- **FR-018**: El cambio MUST NOT modificar el `CMD` del Dockerfile ni `compose.prod.yml`;
  la activación de los tres workers pertenece a la tarea posterior.
- **FR-019**: El comportamiento actual del runner, del guard SSRF, del límite de URLs, de
  `robots.txt` y de la sincronización MUST conservarse; esta capacidad solo cambia dónde se
  guarda el estado y cómo se coordina.
- **FR-020**: La documentación de la capacidad MUST declarar de forma honesta qué se
  recupera (sincronización desde Gold) y qué no (reanudación exacta de la captura).

### Key Entities

- **Job de captura**: unidad de trabajo creada por el formulario. Identificador opaco;
  estado (`pending`, `running`, `done`, `error`, `interrupted`); petición normalizada
  (URLs, alcance, respetar `robots.txt`, límite); resultado resumido; error; dueño para el
  límite por IP; worker que lo atiende; latido y marcas de tiempo.
- **Evento de progreso**: etapa emitida por el pipeline (`descubrir`, `robots`,
  `descargar`, `extraer`, `deduplicar`, `enriquecer`, `indice`) con URL y detalle, más el
  evento final (`done` con el resultado o `error` con el mensaje). Ordenado por secuencia
  dentro del job.
- **Captura activa global**: registro único que indica qué job tiene la captura, qué worker
  la posee, su testigo y desde cuándo no se sabe de su worker; es la exclusión mutua de todo
  el despliegue y también el marcador de una recuperación en curso.
- **Cupo por IP**: marcas de capturas aceptadas, indexadas por un resumen criptográfico
  estable de la IP, dentro de la ventana deslizante configurada y compartidas entre workers.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: El 100 % de las consultas de estado y de progreso de un job existente se
  atienden correctamente desde una instancia distinta de la que lo inició, incluidas las
  reconexiones, sin eventos duplicados.
- **SC-002**: En N envíos simultáneos (N ≥ 2) de captura contra instancias distintas se
  acepta exactamente 1 y el 100 % de los rechazos recibe 409.
- **SC-003**: El cupo por IP es único para todo el despliegue: agotado el cupo en una
  instancia, otra instancia responde 429 con `Retry-After` positivo; otra IP conserva su
  cupo.
- **SC-004**: Tras una caída simulada, el 100 % de los jobs con latido caducado quedan
  interrumpidos antes de que pase una ventana de latido adicional, y una captura nueva se
  acepta sin reiniciar el contenedor.
- **SC-005**: Repetir la recuperación y la captura de las mismas URLs deja exactamente 1
  documento por fuente en Gold y huellas de sincronización iguales en PostgreSQL y Qdrant.
- **SC-006**: `/live` responde 200 con la base caída; `/health` conserva claves y códigos;
  la suite existente de captura, seguridad, configuración, vitalidad y preparación queda en
  verde sin cambios de contrato.
- **SC-007**: La migración sube desde cero, baja un paso y vuelve a subir sin residuos ni
  pérdida de datos del corpus.

## Assumptions

- Los tres workers son procesos del mismo contenedor y comparten el volumen `/app/data`; el
  despliegue sigue siendo una sola máquina y una sola base PostgreSQL.
- El `job_id` sigue siendo un identificador opaco que actúa como capacidad; no hay
  autenticación en esta fase (decisión vigente del proyecto).
- La sincronización `sync` de la spec 037 ya es idempotente y su identidad por fuente
  (`url:`, `file:`, `upload:`) evita duplicados; esta capacidad la reutiliza sin cambiarla.
- La retención de una hora y el tope de 20 jobs del registro actual se conservan como
  valores operativos razonables; la limpieza es oportunista y acotada para que las tablas no
  crezcan sin límite.
- El resumen de la IP usa HMAC con `SESSION_SECRET` cuando está definido y, en desarrollo,
  un prefijo fijo: seudonimiza para el cupo, no es un secreto ni permite recuperar la IP.
- El latido y el umbral de interrupción son parámetros del servidor con valores por defecto
  conservadores (latido frecuente, interrupción tras varias ausencias).
- El límite por IP se mantiene en la ventana deslizante actual; no cambia su política.
- La activación de exactamente tres workers en `compose.prod.yml` la hace el Lead en la
  tarea siguiente; esta spec solo deja la app preparada y probada para ese escenario.

## Out of Scope

- Autenticación o cuotas por usuario, Redis, Celery, microservicios o frameworks nuevos.
- Reanudación exacta de una captura por URL o por lote desde el punto de corte.
- Captura distribuida entre máquinas o volúmenes de datos separados.
- Cambios visuales, de plantillas o de JS del progreso; los contratos HTML/SSE se conservan.
- Cambios en `compose.prod.yml`, en el `CMD` del Dockerfile o en la política de captura
  (`robots.txt`, SSRF, máximo de URLs, timeouts).
