# Feature Specification: Separar historial visible, preferencias y estado técnico del agente

**Feature Branch**: `refactor/046-estado-agente`
**Created**: 2026-11-20
**Status**: En curso (fase 4 del goal de simplificación; las fases 042–047 se entregan
en PRs independientes contra `dev`)
**Input**: Fase 4 del goal del owner: «Distingue el historial visible, las preferencias
del visitante y el estado técnico del grafo. Define PostgreSQL como autoridad del
historial visible y retira la copia del checkpoint solo si las pruebas demuestran que no
cumple otra función. Conserva LangGraph, el comportamiento de los seguimientos y las
preferencias. Prueba hilos nuevos, reinicios, reintentos, aislamiento entre visitantes y
turnos incompletos. No borres datos históricos.»

## Contexto

Hoy el chat guarda el mismo turno dos veces. PostgreSQL conserva el historial visible
(`conversations` y `messages`, con `status` y `client_turn_id`), y además el servicio
siembra el grafo con `seed_turns` leídos de PostgreSQL en cada pregunta. En paralelo, el
adaptador del agente escribe el turno otra vez en el checkpointer de LangGraph
(`turns`, `answer` e `incomplete`) mediante `record()`, y el nodo `recall` del grafo cae a
esa copia solo cuando la semilla viene vacía.

Esa duplicación esconde tres cosas distintas bajo la misma palabra «memoria» y hace
imposible saber cuál manda cuando las dos copias discrepan: el historial visible que ve el
visitante, las preferencias declaradas por el visitante y el estado técnico de la
ejecución del grafo. Esta fase separa las tres capas, deja a PostgreSQL como única
autoridad del historial visible y retira la copia del checkpointer **solo si** la
evidencia demuestra que no cumple otra función.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - El historial visible siempre coincide con PostgreSQL (Priority: P1)

Una persona conversa, cierra el navegador, vuelve horas después y encuentra el mismo
hilo. Si la API se reinició mientras tanto —o si el proceso atendió la pregunta con otro
worker—, el historial que ve es el que quedó guardado; el seguimiento sigue funcionando
igual. Un reintento del navegador no duplica el turno y un turno cortado se ve como tal.

**Why this priority**: es la promesa central del chat persistente. Si el historial
dependiera de memoria del proceso o de una copia paralela, un reinicio o un reintento
cambiaría lo que el visitante ve.

**Independent Test**: con PostgreSQL real, abrir un hilo, hacer un turno, simular un
reinicio del runtime (nuevo grafo y nuevo checkpointer) y comprobar que el historial y el
seguimiento salen de PostgreSQL sin depender de la copia del checkpointer.

**Acceptance Scenarios**:

1. **Given** un visitante sin hilo, **When** hace la primera pregunta, **Then** el hilo se
   crea con exactamente un turno usuario→asistente y no hereda ninguna memoria previa.
2. **Given** un hilo con turnos persistidos, **When** el proceso se reinicia y la persona
   hace un seguimiento elíptico, **Then** el router usa el turno anterior leído de
   PostgreSQL y la respuesta llega igual que antes del reinicio.
3. **Given** un turno ya persistido, **When** el navegador reintenta con el mismo
   `client_turn_id`, **Then** se reemite el turno guardado sin llamar de nuevo al agente
   ni al LLM, y el historial no gana mensajes.
4. **Given** un hilo que la base dejó de reconocer como del visitante, **When** llega una
   pregunta, **Then** la petición falla con el error de acceso y no se persiste nada.

---

### User Story 2 - Seguimientos, preferencias e aislamiento no cambian (Priority: P1)

Quien conversa obtiene la misma experiencia: las preguntas elípticas («¿y en qué año fue
eso?») se resuelven con el turno anterior, una preferencia declarada («recuerda que
prefiero respuestas breves») sobrevive al hilo nuevo del mismo visitante y nunca aparece
para otro visitante. Un turno incompleto se conserva marcado y el siguiente turno no lo
confunde con una respuesta buena.

**Why this priority**: el goal prohíbe regresiones. La separación de capas no puede
cambiar ninguna de las tres conductas que hoy dependen de la memoria.

**Independent Test**: con PostgreSQL real y el Store del agente, ejecutar seguimiento
elíptico, preferencia entre hilos, aislamiento entre dos dueños y turno incompleto; todos
los escenarios deben pasar antes y después de cualquier retirada.

**Acceptance Scenarios**:

1. **Given** un hilo con un turno respondido, **When** la persona pregunta «¿y en qué año
   ocurrió eso?», **Then** la ruta es `rag`, la intención `follow_up` y la consulta de
   recuperación incluye la pregunta anterior.
2. **Given** un visitante que declaró una preferencia en el hilo A, **When** abre el hilo B
   y saluda, **Then** la preferencia se aplica en B y se lee del Store, no del checkpoint.
3. **Given** una preferencia guardada para el visitante A, **When** el visitante B abre un
   hilo propio, **Then** no ve ni hereda la preferencia de A, y A no ve los hilos de B.
4. **Given** un turno cortado tras el primer token, **When** la persona reintenta el mismo
   turno, **Then** se reemite lo guardado con el aviso de incompleto; **When** hace un
   turno nuevo, **Then** el turno incompleto se usa solo como contexto histórico y no
   como respuesta completa.

---

### User Story 3 - Una sola copia del turno, con evidencia y reversión (Priority: P2)

Quien mantiene el código puede afirmar, con pruebas, qué dato manda en cada capa y por qué
la copia del checkpointer se retiró (o por qué se conservó). Si algo dependía de ella, el
cambio se limita a separar y documentar las capas; si nada dependía, la copia desaparece y
queda una prueba que lo demuestra.

**Why this priority**: es la razón del refactor. Sin evidencia, retirar la copia sería
arriesgar los seguimientos; sin documento, el siguiente cambio no sabría cuál es la
autoridad.

**Independent Test**: intentar el mismo flujo de seguimientos con el checkpointer sin la
copia de turnos y con la copia envenenada; comprobar que el comportamiento no cambia y que
un turno nuevo no lee el estado técnico como historial.

**Acceptance Scenarios**:

1. **Given** un grafo con checkpointer y semilla de PostgreSQL, **When** se ejecuta un
   seguimiento, **Then** el resultado depende solo de la semilla y no de `turns`,
   `answer` ni `incomplete` persistidos por el checkpointer.
2. **Given** un hilo con copia de turnos en el checkpointer y una semilla vacía, **When**
   se pregunta, **Then** el grafo no reutiliza la copia como historial.
3. **Given** la retirada aplicada, **When** se inspecciona el estado técnico persistido,
   **Then** ya no contiene la respuesta ni la marca de incompleto del turno.
4. **Given** cualquier comportamiento que dependiera de la copia, **When** la prueba lo
   demuestra, **Then** la copia se conserva y el PR documenta por qué con el cambio
   limitado a la separación de capas.
5. **Given** la rama del PR, **When** se quiere revertir, **Then** basta revertir el commit
   del refactor (la spec queda) sin migraciones ni pérdida de datos.

---

### Edge Cases

- Un hilo nuevo con un visitante que ya tiene otro hilo: la preferencia se comparte por
  dueño (Store) y el historial no se mezcla entre hilos.
- El hilo tiene más de 16 mensajes: la semilla usa los últimos y el recorte documentado
  (`agent_history_turns`) no cambia.
- El checkpointer se cae o rechaza `setup()`: el turno no debe romperse por el estado
  técnico; el historial ya está en PostgreSQL.
- La respuesta se corta después del primer token: se guarda `incomplete` y el reintento lo
  declara; un turno nuevo posterior la trata como contexto histórico, no como verdad.
- El turno se persiste en PostgreSQL pero falla el registro del estado técnico: la copia
  no debe ser la única fuente de nada.
- El mismo `client_turn_id` en otro hilo del mismo dueño: el reintento es por hilo, no
  global.
- Un visitante intenta usar el hilo de otro: error de acceso, sin escritura y sin filtrar
  contenido ajeno.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: PostgreSQL MUST ser la única autoridad del historial visible del chat: cada
  pregunta se responde con los turnos leídos de las tablas de conversaciones y mensajes.
- **FR-002**: El Store del agente MUST seguir siendo la autoridad de las preferencias del
  visitante, en un namespace por dueño; una preferencia MUST sobrevivir al hilo nuevo del
  mismo dueño y MUST NOT cruzar a otro.
- **FR-003**: El checkpointer de LangGraph MUST conservar solo estado técnico de la
  ejecución del grafo; MUST NOT ser autoridad del historial visible ni de las
  preferencias.
- **FR-004**: Cada turno MUST sembrar el grafo con el historial persistido antes de
  decidir la ruta, con el mismo recorte (`agent_history_turns`) y el mismo formato de
  turno de hoy.
- **FR-005**: Un hilo nuevo MUST empezar sin turnos heredados; la primera pregunta no
  MUST leer estado técnico de otros hilos ni de otros dueños.
- **FR-006**: Tras un reinicio del proceso MUST conservarse el historial visible, los
  seguimientos y las preferencias, sin depender de la memoria del proceso.
- **FR-007**: Un reintento con el mismo `client_turn_id` MUST reemitir el turno ya
  persistido sin volver a invocar al agente ni al LLM, y MUST NOT duplicar mensajes.
- **FR-008**: Un turno interrumpido MUST persistirse con estado `incomplete`; el reintento
  MUST declararlo y el hilo MUST seguir funcionando.
- **FR-009**: Ningún visitante MUST poder leer ni escribir en el hilo de otro; el intento
  MUST devolver el error de acceso y MUST NOT persistir datos.
- **FR-010**: La copia de turnos del checkpointer (`turns`, `answer`, `incomplete` escritos
  al cerrar el turno) MUST retirarse solo si la caracterización con PostgreSQL real
  demuestra que ningún comportamiento depende de ella; si depende, MUST conservarse y
  documentarse la razón.
- **FR-011**: La retirada MUST NOT borrar datos históricos ni exigir migraciones
  destructivas: las tablas y columnas existentes de mensajes y del checkpointer se
  conservan.
- **FR-012**: Las rutas del router, las citas, los eventos SSE y el comportamiento de
  seguimientos MUST quedar idénticos para el visitante.

### Key Entities

- **Historial visible**: conversación (dueño anónimo) y mensajes (rol, contenido, fuentes,
  estado `complete`/`incomplete`, `client_turn_id`). Autoridad: PostgreSQL.
- **Preferencias del visitante**: lista corta de declaraciones por dueño, con límite y sin
  duplicados. Autoridad: Store del agente, namespace por dueño.
- **Estado técnico del grafo**: decisión de ruta, intención, consulta, prompt, fuentes y
  contexto sembrado de la ejecución del hilo. Autoridad: ninguno para el producto; el
  checkpointer lo conserva como traza técnica y no se lee como historial.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: El 100 % de los escenarios de caracterización (hilo nuevo, seguimiento,
  preferencia entre hilos, reinicio, reintento idempotente, aislamiento entre visitantes y
  turno incompleto/replay) pasa con PostgreSQL real antes y después del refactor.
- **SC-002**: Ninguno de los escenarios anteriores deja de pasar cuando el checkpointer
  se crea vacío o con la copia de turnos envenenada.
- **SC-003**: Tras la retirada, el estado técnico del turno no contiene la respuesta ni la
  marca de incompleto, y ninguna prueba existente cambia de resultado.
- **SC-004**: El historial y los mensajes previos siguen presentes; no se ejecuta ninguna
  migración destructiva ni se borra ninguna fila histórica.
- **SC-005**: `make check`, `make test-integration` y las pruebas de voz del chat pasan en
  la rama.
- **SC-006**: La PR explica las tres capas, la decisión sobre la copia y la ruta de
  reversión en una sola lectura.

## Assumptions

- El chat sigue siendo el único camino de respuesta y PostgreSQL conserva conversaciones y
  mensajes como hoy; no se añaden servicios ni se cambia el stack.
- Las preferencias siguen en el Store de LangGraph, que ya es Postgres en producción; un
  Store caído no rompe el turno, como hoy.
- La copia del checkpointer se evalúa contra el comportamiento real del grafo y del
  servicio; las pruebas del adaptador son parte de la evidencia.
- El hilo se identifica con el id de la conversación y el dueño con la cookie ya existente;
  la identidad no cambia en esta fase.
- La retirada no toca migraciones ni el esquema: si el checkpointer conserva tablas, se
  quedan.
