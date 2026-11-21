# Feature Specification: Retiro de código y dependencias sin consumidores

**Feature Branch**: `043-codigo-dependencias`
**Created**: 2026-11-20
**Status**: Implementada (fase 1 del goal 042–047); en revisión por PR #203 el
2026-11-20. Ver [analysis](analysis.md) para la evidencia.
**Input**: User description: "Retira `_safe_rollback`, el embedder sin uso de ChatService
y la configuración no utilizada de GraphAgentRunner. Revisa consumidores antes de quitar
símbolos públicos sin llamadas internas. Valida retirar las declaraciones directas de
langchain (paquete paraguas del extra legacy) y python-dotenv (transitiva). Verifica
instalación base y completa, imagen y CLI. Conserva FAISS, SQLite, Sentry y el soporte de
pgvector que requieren las migraciones históricas mientras sigan respaldando funciones
documentadas."

## Contexto y alcance

La app es una sola (FastAPI + Jinja2/HTMX sobre PostgreSQL y Qdrant) y la CLI histórica se
conserva sobre FAISS y SQLite. Después de varias olas de migración quedaron piezas que
nadie consume: una función privada de *rollback* que no se llama, un embedder que el
servicio de chat guarda pero nunca usa, una configuración que el runner del agente recibe y
no lee, y dos declaraciones directas de dependencias que ya entran por otra vía o que
ningún módulo importa. Esta fase las retira **sin cambiar el comportamiento observable** de
la app ni de la CLI.

El alcance excluye cualquier reescritura: no se renombran rutas, no se cambian contratos
SSE/CSRF, no se toca el corpus, las vistas, las plantillas ni el grafo del agente más allá
de la firma del runner y de la construcción del servicio.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Mantenedor retira plomería muerta del chat sin cambiar su comportamiento (Priority: P1)

Quien mantiene el repositorio abre `apps/api/chat/service.py` y encuentra un *rollback*
defensivo que ninguna llamada usa y un embedder que el servicio guarda en el constructor y
nunca consulta. Al retirarlos, el chat responde, persiste e informa exactamente igual que
antes, y cuesta menos leer el archivo.

**Why this priority**: es el centro del goal de simplificación y la parte con más riesgo de
regresión: toca el único punto que mezcla el grafo, la base de datos y el stream.

**Independent Test**: ejecutar la suite de chat y de stream antes y después con una prueba
de caracterización que fije contrato, eventos, persistencia y comportamiento de
`default_chat_service`; el resultado debe ser idéntico.

**Acceptance Scenarios**:

1. **Given** el árbol en `dev`, **When** se buscan llamadas a `_safe_rollback`, **Then** no
   existe ninguna fuera de su definición y se puede retirar.
2. **Given** un `ChatService` construido con una fábrica de sesiones, un LLM y un runner
   inyectado, **When** la persona envía un turno, **Then** los eventos `open`/`trace`/`meta`/
   `token`/`done`, el historial y la persistencia son los mismos que sin el embedder.
3. **Given** `default_chat_service`, **When** se construye sin preguntar, **Then** sigue sin
   resolver clientes ni conexiones y devuelve la misma instancia cacheada.
4. **Given** un fallo antes del primer token, **When** el stream se cierra, **Then** no se
   guarda un turno parcial y el evento `error` conserva el mensaje actual.

---

### User Story 2 - Mantenedor retira la configuración que el runner no usa (Priority: P1)

El runner real del agente recibe un objeto de configuración que solo se guarda en un
atributo que nadie lee. Al retirarlo, preparar y registrar un turno siguen funcionando
igual, también con el checkpointer de SQLite y con el de PostgreSQL.

**Why this priority**: la firma del runner es el contrato que consume el servicio; si cambia
mal, el chat deja de arrancar en producción.

**Independent Test**: prueba de caracterización que, con un grafo doble, fija el `AgentTurn`
devuelto por `prepare` y lo que `record` añade al checkpoint, además de que un fallo del
checkpointer no rompe la respuesta.

**Acceptance Scenarios**:

1. **Given** un grafo doble, **When** `prepare` recibe dueño, hilo, pregunta y turnos
   sembrados, **Then** el `AgentTurn` conserva ruta, intención, prompt, sistema, citas,
   preferencias, herramienta, motivo y consulta.
2. **Given** un grafo doble y un turno nuevo, **When** `record` se ejecuta, **Then** el turno
   se añade una sola vez al estado del hilo sin duplicar el anterior.
3. **Given** un checkpointer que falla, **When** `record` se ejecuta, **Then** se registra el
   aviso y la respuesta del chat no se interrumpe.
4. **Given** `default_agent_runner`, **When** se pide el runner real, **Then** usa el runtime
   del agente y el grafo compilado como hasta ahora.

---

### User Story 3 - Mantenedor retira dos dependencias directas solo con evidencia (Priority: P1)

El archivo de dependencias declara `langchain` (paquete paraguas) en el extra `legacy` y
`python-dotenv` en las dependencias base. Ningún módulo importa `langchain` de forma directa
y `python-dotenv` llega por `pydantic-settings`. Se retiran las dos declaraciones solo si la
instalación completa, la instalación base, la CLI histórica y la imagen demuestran que todo
sigue funcionando.

**Why this priority**: declarar de más oculta dependencias reales y alarga las
instalaciones; declarar de menos rompe la CLI o el contenedor. La evidencia decide, no la
intuición.

**Independent Test**: con el árbol limpio, `uv sync --all-extras` y `uv sync` terminan sin
error; la CLI completa ejecuta `--help` y sus imports (`ask`, `faq`) cargan el módulo
histórico; la imagen construida responde en `/live`.

**Acceptance Scenarios**:

1. **Given** la declaración directa de `langchain` retirada, **When** se ejecuta
   `uv sync --all-extras` y se importa el módulo de RAG histórico, **Then** no falta ningún
   paquete y `carvajal ask`/`faq` conservan su interfaz.
2. **Given** la declaración directa de `python-dotenv` retirada, **When** se ejecuta
   `uv sync` base en un entorno limpio, **Then** `pydantic-settings` sigue leyendo `.env`
   porque la instalación lo resuelve como dependencia transitiva.
3. **Given** cualquiera de las dos retiradas, **When** falla la instalación, el import o la
   CLI, **Then** la declaración se conserva y el fallo queda documentado con el comando y el
   error exactos.
4. **Given** el `uv.lock` actualizado, **When** se construye la imagen Docker de la app web,
   **Then** el build termina y el contenedor responde en `/live`.

---

### User Story 4 - Se conservan las capacidades heredadas que sí tienen consumidores (Priority: P2)

La CLI histórica sigue usando FAISS, el prototipo conserva SQLite para la demo local, el
monitoreo opcional sigue disponible con Sentry y las migraciones históricas siguen
referenciando pgvector. Nada de eso se retira en esta fase.

**Why this priority**: son dependencias y contratos documentados; retirarlas es la spec 030
y exige otra decisión de alcance.

**Independent Test**: el árbol conserva la extra `legacy` con FAISS, la configuración
`agent_checkpointer=sqlite`, la dependencia de Sentry y la migración que menciona pgvector;
las pruebas que las ejercen siguen en verde.

**Acceptance Scenarios**:

1. **Given** el entorno completo, **When** se ejecuta la CLI de consulta sobre FAISS,
   **Then** el extra `legacy` sigue instalándose y el módulo histórico importa.
2. **Given** la configuración de checkpointer SQLite, **When** arranca el agente en modo
   demo, **Then** el archivo local se usa como hasta ahora.
3. **Given** un DSN de Sentry, **When** la observabilidad se configura, **Then** el envío
   opcional sigue disponible.
4. **Given** las migraciones, **When** se revisan sus referencias a pgvector, **Then** no se
   alteran ni se eliminan.

---

### Edge Cases

- Una búsqueda puede dar falsos negativos por código dinámico (`getattr`, import por
  cadena): antes de retirar un símbolo se revisan importaciones, reexports de `__all__` y
  dobles de prueba, no solo llamadas literales.
- `langchain` podría ser necesario por un import indirecto no visible: la prueba real es
  instalar y ejecutar, no el `grep`.
- `python-dotenv` podría dejar de estar instalado si `pydantic-settings` cambia sus
  dependencias: el lock y la instalación base se comprueban juntos y, si no está, se
  conserva la declaración.
- Un entorno con `OLLAMA_MODEL` exportado altera dos pruebas de configuración: la puerta se
  ejecuta con el entorno limpio de esa variable, tal como documenta el contrato de calidad.
- El build de Docker puede fallar por red o por un digest fijado: si falla, se reporta el
  motivo exacto y no se cambia el Dockerfile para sortearlo.
- Un fallo del checkpointer o de la persistencia parcial no debe cambiar de comportamiento al
  retirar el *rollback* no usado: el manejo de errores sigue siendo el mismo (sesiones
  cortas y aviso en el log).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El servicio de chat MUST dejar de definir `_safe_rollback` si ninguna llamada
  lo usa, sin cambiar el manejo de errores del stream ni la persistencia parcial.
- **FR-002**: `ChatService` MUST dejar de recibir y guardar un embedder que no consume; su
  construcción real (`default_chat_service`) MUST seguir siendo perezosa y cacheada.
- **FR-003**: El contrato de `ChatService` MUST conservar `history`, `conversations`, `ask`,
  `stream` y `new_conversation`, los nombres de los eventos y la persistencia exacta una sola
  vez.
- **FR-004**: El runner real del agente MUST dejar de recibir una configuración que no lee;
  `prepare` y `record` MUST conservar su comportamiento y su contrato de `AgentTurn`.
- **FR-005**: `default_agent_runner` MUST seguir devolviendo el runner real sobre el runtime
  del agente sin abrir conexiones al importar.
- **FR-006**: La declaración directa de `langchain` en el extra `legacy` MUST retirarse solo
  si la instalación completa, el import del módulo histórico de RAG y la CLI demuestran que
  no hace falta; si falla, MUST conservarse y documentarse el fallo.
- **FR-007**: La declaración directa de `python-dotenv` MUST retirarse solo si la instalación
  base y la carga de configuración desde `.env` demuestran que llega como dependencia
  transitiva; si falla, MUST conservarse y documentarse el fallo.
- **FR-008**: MUST conservarse FAISS y el extra `legacy` que usa la CLI histórica, SQLite
  como checkpointer de la demo local, Sentry como observabilidad opcional y las referencias a
  pgvector de las migraciones históricas.
- **FR-009**: El cambio MUST NOT alterar rutas HTTP, contratos SSE, CSRF, cuota, esquema de
  base, migraciones ni el comportamiento de la CLI.
- **FR-010**: La imagen Docker de la app web MUST construirse con el lock actualizado y, si
  el build funciona, el contenedor MUST responder en `/live`.
- **FR-011**: Las pruebas de caracterización MUST pasar en el árbol anterior al cambio y
  seguir pasando después; la puerta completa (`make check` y las pruebas JS de voz) MUST
  quedar en verde.
- **FR-012**: Cada retirada MUST tener evidencia reproducible (comandos y salida) y una ruta
  de reversión documentada (revertir el commit o restaurar la dependencia).

### Key Entities *(include if feature involves data)*

- **Símbolo retirado**: función, parámetro o atributo sin consumidores; su evidencia es la
  búsqueda en el árbol y las pruebas que lo rodean.
- **Dependencia declarada**: entrada de `pyproject.toml` con su justificación; su evidencia
  es la instalación, el import, la CLI y la imagen.
- **Contrato de caracterización**: comportamiento observable que no puede cambiar (eventos
  del chat, `AgentTurn`, persistencia, CLI), fijado por pruebas antes del cambio.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 0 consumidores de `_safe_rollback`, del embedder de `ChatService` y de la
  configuración de `GraphAgentRunner` en `apps/`, `packages/`, `tests/` y `scripts/` al
  terminar la fase.
- **SC-002**: `make check` termina en verde y la suite completa mantiene al menos las 1.643
  pruebas que pasan hoy, sin pruebas nuevas en rojo.
- **SC-003**: `uv sync --all-extras` y `uv sync` base terminan con código 0 en un entorno
  limpio; `import carvajal.rag` y `uv run carvajal --help` funcionan con la CLI completa.
- **SC-004**: `docker build -t carvajal-api:043 .` termina con código 0 y el contenedor
  responde 200 en `/live`; si el build no es posible, queda el motivo exacto documentado.
- **SC-005**: `pyproject.toml` reduce sus declaraciones directas en las dos entradas
  identificadas (`langchain` del extra `legacy` y `python-dotenv` de base) y el `uv.lock`
  refleja la resolución nueva.
- **SC-006**: 0 archivos funcionales fuera del alcance modificados; el diff se limita al
  servicio de chat, el runner, la declaración de dependencias, el lock, las pruebas y la
  spec.

## Assumptions

- La app única (FastAPI/Jinja2 + PostgreSQL + Qdrant) y la CLI histórica se mantienen; no se
  introduce ningún servicio, framework ni gestor de dependencias distinto de `uv`.
- `langchain` (paquete paraguas) no lo importa ningún módulo del repositorio; lo que se usa
  son `langchain-core`, `langchain-community`, `langchain-anthropic`, `langchain-ollama`,
  `langchain-openai` y `langchain-text-splitters`, declarados por separado.
- `python-dotenv` sigue instalándose porque `pydantic-settings` (dependencia base) y
  `testcontainers` (grupo de desarrollo) lo declaran; `Settings` sigue leyendo `.env`.
- El worktree de la fase ya tiene el entorno sincronizado y los servicios locales (Postgres,
  Qdrant, Ollama) disponibles; el build de Docker depende de la red local de la máquina.
- La puerta de calidad se ejecuta con `OLLAMA_MODEL` sin exportar, como exige el contrato
  del proyecto, para no alterar las pruebas de configuración.
