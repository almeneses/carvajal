<!--
Sync Impact Report (spec 053)
- Versión: 2.10.0 → 2.11.0 (MINOR: estado técnico ampliado; sin principios retirados ni
  redefinidos). Elegir por reglas y confirmar antes de registrar cotizaciones (principio III).
- Plantillas revisadas: sin cambios.

Sync Impact Report (spec 051)
- Versión: 2.9.0 → 2.10.0 (MINOR: estado técnico ampliado; sin principios retirados ni
  redefinidos). Fotos de alimentos con Jev y respuesta determinista (principio III).
- Plantillas revisadas: sin cambios.

Sync Impact Report (spec 046)
- Versión: 2.8.0 → 2.9.0 (MINOR: guía ampliada, sin principios retirados ni redefinidos).
- Enmienda D (restricciones técnicas y estado técnico): gatekeeper del modo `tools` con Jev
  de TypeSafe opcional, minimización de lo que viaja y registros sin texto; `routes` sin
  Jev (T026).
- Plantillas revisadas: `.specify/templates/plan-template.md`, `spec-template.md` y
  `tasks-template.md` no requieren cambios (el *Constitution Check* ya lee esta proyección).
- Enmienda E (principio III): compuerta de afirmaciones con Jev (pregunta Noul, umbral 0,5)
  después de la compuerta determinista, retención completa del camino con evidencia con la
  compuerta activa y *fail-open* a la determinista (T038).
- Pendiente: ninguno.
- Sincronización posterior (sin cambio de versión): estado técnico de la 046 tras la
  medición y sus correcciones (CONSTITUTION.md §9, 2026-11-24).
-->
# Constitución de Carvajal Corpus

> Proyección de la constitución del proyecto para el flujo de Spec-Driven Development.
> **La fuente única de verdad es [`CONSTITUTION.md`](../../CONSTITUTION.md) en la raíz del
> repositorio**: este archivo resume sus principios y su estado técnico para que los comandos
> de Spec Kit (`/speckit.*`) los verifiquen. Si hay discrepancia, manda la raíz.
>
> **Sincronizada**: 2026-11-24, con las decisiones de `CONSTITUTION.md` §9 hasta esa fecha.

## Principios fundamentales

### I. La constitución manda

Todo cambio arquitectónico, tecnológico o de proceso se registra en `CONSTITUTION.md` en el
mismo commit. Las especificaciones y los planes de `specs/` no pueden contradecirla; si lo
hacen, se corrige la spec o se enmienda la constitución, nunca ambas a medias.

### II. El corpus es el contrato

El entregable versionado es `data/gold/` (24 documentos con *front matter*: `url`, `title`,
`section`, `domain`, `source_type`, `captured_at`, `hash`, `chars`) más `index.jsonl`.
Bronze (`data/raw/`) y silver (`data/processed/`) se regeneran y no se versionan.

- Nada del RAG toca la web: se deriva siempre de `data/gold/`.
- Gold es la fuente de verdad del contenido; PostgreSQL y Qdrant se derivan y se reconcilian
  con `sync`. Cambiar el corpus obliga a sincronizar, no a reconstruir a mano.
- Cada documento tiene una identidad estable por fuente; la huella del original deduplica
  antes de escribir capas y la huella del texto decide la versión.
- La capa extendida (`data/external/`) es opcional y solo participa en recuperación y citación,
  con prioridad posterior al núcleo.
- La capa de catálogo (`data/catalog/`, specs 036 y 039) guarda datos estructurados curados con
  fuente y fecha por fila; sus precios son siempre referencias fechadas, una oferta por fuente,
  y un tercero se nombra como tal.
- La capa institucional curada (`data/institucional/`, spec 045) guarda listas pequeñas
  derivadas solo del núcleo, cada fila con su cita literal y su URL de `index.jsonl`,
  verificadas por prueba contra `data/gold/`; no añade hechos que el núcleo no diga.

### III. Sin fuentes no hay respuesta (NO NEGOCIABLE)

Toda afirmación se sustenta en el corpus y se cita. El asistente debe distinguir fuente
oficial (`carvajal.com`) de tercero (prensa) y declarar lo que no sabe.

- Las referencias se conservan en la respuesta almacenada y en la API. La vista del
  chat no muestra el bloque de fuentes ni los marcadores numéricos; la voz usa los
  títulos como metadatos y no se reduce el contexto recuperado.
- Los prompts prohíben explícitamente inventar datos y usar conocimiento externo.
- La temperatura es `0`.
- Cada variante de prompt se mide antes de adoptarla (`docs/prompts.md`,
  `scripts/experimentos_prompts.py`).
- Sin evidencia recuperada, la respuesta documental es la abstención declarada y el modelo no
  redacta afirmaciones del corpus. En el modo `routes`, la ruta `rag` sin contexto no llama
  al LLM. En el modo `tools` (spec 044) el modelo decide qué consultar, pero el servidor
  garantiza lo mismo de forma determinista: si ninguna consulta del turno devolvió
  evidencia, sustituye la redacción por la abstención. El primer paso declara siempre una
  consulta o que no consulta; una respuesta sin consulta no contiene cifras, fechas ni
  nombres propios de Carvajal. Las rutas `direct` (saludos) y `tool` (indicadores
  estructurados) responden sin recuperación y no afirman hechos del corpus sin evidencia.
- El flujo comercial público (spec 043) sirve solo datos publicados. La simulación de la
  demo de la 042 se retiró y no puede reaparecer como fuente oficial, cita ni parte del
  corpus RAG.
- La memoria del dueño y el resumen de la conversación son datos del usuario: aplican como
  contexto y estilo, nunca respaldan datos de Carvajal ni cambian las reglas. Las palabras
  literales de la persona respaldan solo lo que ella dijo. Además de oficial y tercero,
  existe la procedencia `sistema` para datos que calcula el servidor (fecha y calendario de
  festivos de Colombia); se cita como tal (spec 045, enmienda B).
- En el modo `tools`, después de la compuerta determinista, una compuerta de afirmaciones
  (Jev, pregunta Noul, umbral 0,5) sustituye por el texto neutro existente toda respuesta del
  modelo que marca como no respaldada por las consultas del turno. Una valoración presentada
  como hecho sin fuente cuenta como no respaldada; un dato reformateado no. Con esa compuerta
  activa, el camino con evidencia retiene el texto completo antes de emitirlo; si Jev no
  responde, rige la compuerta determinista sola (spec 046, enmienda E).

### IV. Calidad automatizada y reproducible

`uv` es el único gestor de dependencias (prohibido `pip` y `requirements.txt`). Ruff, `ty`,
pytest y pre-commit se ejecutan antes de cada commit; el CI corre lo mismo que en local
(pre-commit, `ty` y pytest) y nada se integra con `make check` en rojo.

### V. Captura responsable

Se respeta `robots.txt` (incluido `Crawl-delay`), se identifica el cliente con un
`User-Agent` propio, se espera un segundo entre peticiones al mismo host y los errores de
red se registran como descartes, no como excepciones.

- La captura desde la interfaz está acotada: máximo de 24 URLs por ejecución y límite por IP.
- Las URLs de usuario pasan por un guard SSRF: solo HTTP/HTTPS en los puertos 80 y 443, sin
  IPs privadas, con redirecciones revalidadas y DNS fijado a la IP validada.

### VI. Cambios por Pull Request

Los cambios grandes van en rama (`feat/…`, `fix/…`, `docs/…`, `chore/…`), con Conventional
Commits, y entran por Pull Request a `main` con descripción y checks verdes. `main` es la
única rama de integración y producción; `dev` se retiró el 2026-11-22. Los agentes abren el
PR y se detienen: no mergean ni aprueban, ni siquiera con `--admin`.

## Estado técnico

- La migración del prototipo a la app web entregó las olas 0–3, 5 y 6 el 2026-11-14; la ola 4
  (autenticación y cuotas por usuario) y la 7 (retiro del prototipo) siguen pendientes. El
  Módulo 2 está aceptado, con evidencia en `docs/aceptacion-m2.md`.
- Un solo frontend y un solo RAG: shell renderizado en servidor con HTMX y la API en el
  mismo proceso (puerto 8000).
- Gold es la fuente de verdad del corpus; PostgreSQL guarda el texto para la FAQ y el
  explorador y Qdrant es el índice vectorial del chat. Ambos se reconcilian con `sync`.
- El prototipo Streamlit se retiró el 2026-11-18; FAISS sigue en el extra `legacy`
  porque la CLI histórica (`ask`, `summarize`, `faq`, `eval`) lo usa.
- Pendientes vivos: autenticación y cuotas por usuario (ola 4), captura programada por cron y
  endurecimiento del despliegue (TLS, dominio, respaldos).
- La spec 035 define la carga local multiformato —PDF con texto por página y datos XML, JSON,
  JSONL, CSV y TSV— para la web y la consola; el OCR de PDF escaneados sigue fuera de alcance.
  El código se implementa después de aprobar el contrato.
- La spec 036 define el asistente comercial de empaques sobre la capa de catálogo, con una
  ruta `commercial` en el agente y un router intercambiable para evaluación comparativa;
  quedó implementada el 2026-11-18 y transfirió su SC-006 a la spec 041 (Jev cloud).
- La spec 037 define el corpus incremental: identidad estable por fuente, deduplicación por
  huella del original antes de Bronze, actualización con versiones, sincronización única con
  PostgreSQL y Qdrant, capa extendida con prioridad y retiro de `/ask` y pgvector.
  `carvajal sync --adopt` completa la identidad de los índices anteriores sin renombrar
  slugs ni reindexar.
- El monitoreo del agente con LangSmith (spec 038) quedó **entregado el 2026-11-19**: traza
  raíz por turno, apagado por defecto, muestreo 0.2 y contenido completo solo en desarrollo
  y evaluación. La evidencia de SC-001…SC-005 está en `docs/evidence/m2/monitoring.md`.
  La raíz puede incluir `agent_id` y `agent_environment` como metadatos opcionales.
- La spec 039 (catálogo de Colombia con precios en COP) está implementada. La 040 (chat
  por voz) está implementada y validada en Chrome 157/Linux (SC-001–SC-005) y Firefox
  159/Linux (FR-009); alcance acordado: Chrome y Firefox de escritorio. SC-001 = 36/40
  (90,0 %), SC-002 p95 = 231 ms, SC-003 p95 = 36 ms, SC-004/005 cumplidos con micrófono
  y dos voces.
- La spec 042 (Cotizador) quedó sustituida por la 043: el compositor separado se retiró y
  su histórico real se conserva en lectura autorizada. La demo simulada ya no se sirve.
- Las capacidades ya entregadas están documentadas como *as-built* en `specs/`; el índice y la
  convención viven en `specs/README.md`. Lo no entregado se especifica antes de implementar.
- La 043 está implementada: un solo hilo comercial con datos publicados sobre la
  protección de ingesta de #222 y el contrato de #223. Retiró la demo y el compositor
  separado; conserva el histórico real autorizado y el contrato `POST /api/faq`.
- La 044 (agente con herramientas) está implementada y `AGENT_MODE=tools` es el valor por
  defecto desde el 2026-11-23, por decisión del owner con riesgos aceptados (SC-001,
  SC-005, SC-006a y SC-004 según la lectura). `routes` queda como interruptor de
  emergencia, atajo y respaldo. El informe comparativo
  (`specs/044-agente-tool-calling/evidencia.md`) da 89,5 % de ítems resueltos frente a 54,4 %
  de `routes`; el modo `tools` solo arranca con `LLM_PROVIDER=deepseek`. La observación dura
  dos semanas desde que producción sirve `mode=tools` (prevista hasta el 2026-12-07) y, si
  se cumple, abre el retiro (T055). El índice de
  Qdrant creció a ~40 800 puntos con el corpus ampliado y el retrieval cayó a
  Recall@10 = 0,578 frente a la línea base: pendiente de una spec aparte.
- La 046 (router y System One) añade al modo `tools` un gatekeeper explícito: reglas
  primero y, si no deciden y Jev está encendido, una pregunta Choice de Jev de TypeSafe que
  restringe las consultas del turno; sin Jev, reglas + LLM como en la 045 (enmienda D).
  Medida el 2026-11-24 con `jev-1.14.0` (gatekeeper 20/20, compuerta 10/10 y 0/6, v3 28/40
  con Jev frente a 29/40 sin Jev); umbrales 0,6 / 0,8 / 0,5 sin cambio. Un `rag` de Jev con
  señal comercial en la pregunta se trata como `mixta` (todas las consultas, sin cautela).
  Jev sigue apagado por defecto (`JEV_ENABLED=false`); activarlo en producción es decisión
  del owner tras revisar el DPA de TypeSafe.
- La 051 (fotos de alimentos) clasifica la foto con Jev (preguntas cerradas, cliente propio
  e independiente de `JEV_ENABLED`, encendida en producción por decisión del owner) y elige
  una recomendada y hasta cuatro alternativas (spec 053) del catálogo con una tabla
  determinista y `check_material`. La respuesta no pasa por el LLM, cita la ficha de cada
  referencia y queda en el hilo; la foto original no se guarda y viaja sin metadatos (en la
  web queda una miniatura de 320 px con el mensaje).
- La 053 resuelve con reglas la elección («dame esa», «la 2») y el «sí»: ninguna cotización
  se registra sin confirmación; antes es una propuesta sin folio, fila ni PDF.

## Restricciones técnicas

- Python 3.13+, monorepo `uv` con el paquete en `packages/core/src/carvajal` (src-layout).
- API FastAPI + Jinja2 + HTMX; sin framework de JavaScript en el cliente.
- Persistencia Postgres 16 con SQLAlchemy Core y migraciones Alembic (0001–0011).
- Vectores en Qdrant: 768 dimensiones, métrica coseno e IDs UUID5 estables.
- Agente LangGraph con `PostgresSaver` y `PostgresStore`, en dos modos elegidos con
  `AGENT_MODE`: `tools` (spec 044, valor por defecto: el modelo encadena consultas tipadas y
  deterministas) y `routes` (router `direct`/`tool`/`rag`/`commercial`, interruptor de
  emergencia). El router por reglas atiende en ambos el atajo de saludos y el respaldo
  cuando el proveedor falla, y todos los turnos si `tools` no arranca (`LLM_PROVIDER`
  distinto de `deepseek` o sin `DEEPSEEK_API_KEY`).
- El modo `tools` tiene un gatekeeper en el nodo `shortcut`: reglas primero (saludos,
  despedidas, «olvida todo», preferencias y seguimiento comercial, sin búsquedas ni
  servicios externos) y, si no deciden, Jev de TypeSafe (System One, `jev-1.14.0` fijado,
  pregunta Choice) clasifica el turno en herramienta estructurada, RAG, mixta o respuesta
  directa y restringe sus consultas; con confianza baja, cautela con todas las consultas.
  Jev es opcional en tiempo de ejecución y se enciende de forma explícita: sin él, reglas +
  LLM. A TypeSafe solo viajan la pregunta redactada y una línea de contexto (y, en la
  compuerta, la respuesta sin datos de la persona y la evidencia mínima); nunca identidad,
  memoria ni historial; los registros guardan solo decisión, probabilidades, latencia,
  modelo y tokens. El modo `routes` no usa Jev (spec 046, enmienda D).
- El estado del hilo guarda un resumen acumulado (`conversation_summary`) calculado en
  segundo plano; el Store guarda la memoria del dueño por categorías, sin datos sensibles y
  con caducidad de 180 días. El modo `tools` dispone de consultas registradas en
  `toolagent/tools/registry.py` (20 al cerrar la spec 045); solo las de memoria escriben
  (spec 045, enmienda C).
- Observabilidad: logs JSON con `request_id` y Sentry opcional. El monitoreo del agente con
  LangSmith (spec 038) es opcional, sin PII y en producción solo con metadatos.
- Precios del catálogo: referencias fechadas con moneda, una oferta por fuente. Los de
  Colombia van en COP y no se estiman (spec 039). Única conversión admitida desde México: las
  copias `CO-MX-…`, con procedencia `sistema`, solo en Bogotá y presentadas como «precio de
  referencia del catálogo», nunca atribuidas a un distribuidor (decisión del 2026-11-25).
- Cotizador (spec 042): motor determinista sin LLM; el catálogo curado es la única fuente
  de productos reales y la demo solo añade una extensión simulada marcada por campo.
- Voz en el chat (spec 040): dictado y lectura con las capacidades del navegador; el audio
  no llega al servidor ni se guarda. `microphone=(self)` se declara solo en el documento
  HTML de `/chat` (el SSE y el resto del sitio siguen con `microphone=()`); el dictado
  requiere contexto seguro, `localhost` o HTTPS.
- LLM configurable: DeepSeek (por defecto) u Ollama; embeddings locales con
  `nomic-embed-text` (768 dimensiones).
- Streaming por SSE; CSRF por doble cookie, CSP sin `unsafe-inline` y cookies `HttpOnly`.
- Sin secretos en el repositorio: `.env` está en `.gitignore`.

## Flujo de desarrollo

1. `/speckit.specify` → `specs/NNN-*/spec.md` (qué y para quién, sin tecnología).
2. `/speckit.clarify` → resuelve las ambigüedades antes de planear.
3. `/speckit.plan` → `plan.md` (cómo, con esta constitución como filtro).
4. `/speckit.tasks` → `tasks.md` (tareas accionables por historia de usuario).
5. `/speckit.analyze` → coherencia entre spec, plan y tasks antes de implementar.
6. Implementación en rama, `make check`, commit convencional y PR.

- Una capacidad ya entregada sin spec se documenta como *as-built*: fecha honesta, requisito
  observable y evidencia por escenario, sin `plan` ni `tasks` retroactivos.
- Toda spec nueva se numera en `specs/` y se registra en el índice `specs/README.md`.

## Gobernanza

- Enmiendas: PR que modifique `CONSTITUTION.md` y, si aplica, esta proyección **en el mismo
  commit**.
- Cumplimiento: el plan de cada feature incluye una sección *Constitution Check*; toda
  violación debe justificarse o eliminarse antes de implementar.
- Versión: 2.11.0 - 2026-11-25, sincronizada con `CONSTITUTION.md` del repositorio.
