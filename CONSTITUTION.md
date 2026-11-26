# CONSTITUTION — Carvajal Corpus

> **Verdad central del proyecto.** Este documento define el propósito, el stack, la
> estructura, las convenciones y el flujo de trabajo de `Carvajal Corpus`. Si algo aquí
> contradice al código, el código manda **y este archivo se actualiza**.

> ⚠️ **Directiva de mantenimiento:** cada vez que se realicen cambios significativos
> (nuevas funcionalidades, cambios arquitectónicos, adición de clases/módulos o ajustes
> de tecnologías), **se debe actualizar este archivo en el mismo commit** para mantener
> la verdad central del proyecto bien documentada.

---

## 0. Identidad del proyecto

| Campo | Valor |
|-------|-------|
| **Nombre** | Carvajal Corpus |
| **Descripción** | Pipeline de captura y curaduría del corpus web de Carvajal para RAG. |
| **Repositorio** | https://github.com/markodavid/carvajal |
| **Autoría** | Marko David Garcia — markodavidgm@gmail.com |
| **Licencia** | MIT |
| **Rama principal** | `main` |
| **Estado** | Captura inicial en curso 🚧 |

## 1. Propósito y no-propósito

### Propósito

Convertir páginas públicas del sitio web de **Carvajal** en un corpus Markdown
citable, reproducible y listo para un pipeline de RAG, respetando `robots.txt` y
sin depender de una API del sitio.

### No-propósito

No es un *crawler* masivo del dominio completo, ni un motor de búsqueda, ni un
sistema que atraviese formularios, sesiones autenticadas o datos personales. Tampoco
es un generador de respuestas: el consumo del corpus (embeddings, recuperación,
agente) forma parte del proyecto del curso y se incorpora aquí en su fase de pruebas
con asistente RAG.

## 2. Alcance

### Dentro del alcance

- Capturar las landing y las páginas de contenido que de ella dependen: misión,
  historia, sostenibilidad, especialidades (empaques, educación, tecnología,
  comunicación, espacios), propiedades e inversiones, inversores, equipo y notas
  institucionales.
- Capturar **fuentes de terceros** (prensa económica y análisis) para cubrir los
  indicadores financieros que el sitio oficial solo publica dentro de PDFs. Cada
  documento de terceros se marca en el *front matter* con `domain` y
  `source_type: tercero`, y el asistente está obligado a distinguirlos de las fuentes
  oficiales.
- Respetar `robots.txt` (incluido `Crawl-delay`) y aplicar *rate limit* por host antes
  de cada petición.
- Mantener tres capas materializadas en disco: **bronze** (`data/raw/`, HTML crudo),
  **silver** (`data/processed/`, Markdown limpio) y **gold** (`data/gold/`, Markdown +
  *front matter* + `index.jsonl`).
- Extraer el cuerpo editorial descartando menús, formularios, modales y pie de página.
- Deduplicar por huella SHA-256 y enriquecer cada documento con sección, hash y tamaño.
- Versionar `data/gold/` como entregable del corpus (bronze y silver se regeneran).
- Importar corpus externos de terceros a una **capa extendida** (`data/external/`),
  solo para recuperación y citación, con manifiesto, deduplicación por URL/hash
  (incluidas las URLs del núcleo) y procedencia explícita (`origin`, `category`,
  `entity`). Se importa solo HTML y esa capa nunca se consolida en el prompt.
- Cargar archivos locales desde Configuración o desde la consola (spec 035), solo para
  operadores autorizados. La carga web exige `OPERATOR_TOKEN` y CSRF, y deja el lote en
  Gold para revisión; la consola publica con `carvajal sync`. Conserva el original en
  Bronze, el texto en Silver y la ficha en Gold, y admite PDF con texto por página y
  datos XML, JSON, JSONL, CSV y TSV, además de HTML, Markdown y TXT. La lectura del
  chat, el corpus y las citas sigue siendo pública.
- Mantener un corpus incremental (spec 037): cada documento conserva una identidad
  estable derivada de su fuente (URL, ruta relativa o nombre de archivo), se deduplica
  por la huella del original antes de escribir Bronze, se actualiza en sitio con
  versiones y se sincroniza con PostgreSQL y Qdrant por un único camino. La capa
  extendida se recupera y se cita con prioridad posterior al núcleo; la FAQ sigue
  leyendo solo el núcleo. El chat es el único camino de respuesta y Qdrant el único
  índice vectorial.
- Recomendar empaques de foodservice desde un **catálogo curado** (`data/catalog/`,
  spec 036): referencias, reglas de material y distribuidores derivados del corpus
  externo, con validación técnica por material, cálculo de cajas y precio de referencia
  fechado donde exista. No se procesan pedidos ni pagos.
- Monitorear el agente con **LangSmith** (spec 038, entregada el 2026-11-19): una traza por turno con
  ruta, enrutador, pasos, tokens, latencia e identidad opcional (`agent_id`,
  `agent_environment`) para distinguir orígenes dentro del mismo proyecto. Desactivado por
  defecto y sin PII; en producción solo metadatos, con el texto completo reservado a
  desarrollo y evaluación. Si el servicio falla, el chat no se afecta. Los experimentos de
  evaluación son opcionales.
- Ampliar el catálogo a **Colombia** (spec 039): referencias del portafolio CO tomadas de las
  fichas oficiales y cruzadas con la tienda en línea de un distribuidor del directorio, y
  precios de referencia en COP con IVA, fecha y fuente. Cada precio es una oferta por fuente
  (`precios.csv`); si nadie publica precio en Colombia, se declara la ausencia: no se estima
  y no se promedia entre fuentes. Desde el 2026-11-25 el catálogo suma **copias de las 98
  referencias de México** (`CO-MX-…`, con foto) y su precio **convertido a COP** con una tasa
  fechada, con procedencia `sistema`: es una referencia sin vendedor, sede ni existencias, se
  cotiza en Bogotá y en 2 ciudades más del directorio (reparto por familia: cada ciudad tiene
  todas las familias) y va después de cualquier precio publicado. Las cotizaciones lo muestran
  como «precio de referencia del catálogo», sin nombrar el origen; el origen queda en los datos,
  las fuentes y el manifiesto para sustentarlo. Las 102 referencias propias no reciben precio
  convertido.
- Ofrecer **chat por voz** (spec 040, implementada y validada en Chrome 157/Linux con SC-001–SC-005 y en Firefox 159/Linux con FR-009 el 2026-11-20; alcance Chrome y Firefox de escritorio): dictado
  revisable antes de enviar y lectura de respuestas y tarjetas con las capacidades del
  navegador. SC-001–SC-005 medidos con dos voces y micrófono real. Con la voz del navegador
  el audio no llega al servidor del proyecto ni se guarda; sin soporte del navegador, el chat
  sigue por escrito. Desde la spec 047, con `VOICE_PROVIDER=openai` el dictado y la lectura
  pasan por el servidor hacia OpenAI (el audio no se guarda) y la voz del navegador queda
  como respaldo ante cualquier fallo ([guía](docs/voz-web.md)).
  El permiso `microphone=(self)` se declara **solo** en el documento HTML de `/chat`; el
  resto de rutas, los POST y el SSE conservan `microphone=()`. Dictar exige contexto
  seguro: `localhost` en desarrollo y HTTPS en despliegue.
- Sugerir **empaque a partir de una foto del alimento** (spec 051) en el chat web y en
  WhatsApp: Jev (TypeSafe) clasifica la foto con preguntas cerradas (es comida, tipo,
  consistencia, temperatura, porción) y una tabla determinista elige una referencia
  recomendada y hasta cuatro alternativas del catálogo del país con la misma regla de
  materiales del flujo comercial (spec 053). La respuesta se redacta sin LLM, cita la ficha
  de cada referencia y queda en el hilo; con confianza baja pregunta, y si no es comida o no
  hay empaque apto lo dice. La foto original no se guarda: se endereza, se escala y se le
  quitan los metadatos antes de enviarla; en la web queda solo una miniatura de 320 px con
  el mensaje. Encendida en producción por decisión del owner (2026-11-24), independiente de
  `JEV_ENABLED`.
- **Elegir y confirmar** (spec 053): «dame esa», «la alternativa 1» o «la 2», por texto o por
  voz, se resuelven con reglas sobre la recomendación de foto más reciente y el agente cotiza
  esa referencia. Ninguna cotización se registra sin confirmación: una cotización completa
  queda como **propuesta** sin folio y solo con un «sí» (o el botón «Confirmar cotización»)
  recibe folio, fila en `quotes` y PDF.
- Ofrecer una CLI y un Makefile como interfaz única del proyecto.

**Chat comercial unificado (spec 043, 2026-11-23):** [la especificación](specs/043-chat-comercial-unificado/spec.md)
sustituye el Cotizador separado de la 042 por un único hilo para consulta,
cotización referencial y búsqueda de sedes. El flujo público usa solo datos
publicados: catálogo curado, 29 precios CO con vendedor, presentación, fuente y
fecha, 29 sedes en 10 ciudades y paridad de 98 referencias MX. La demo, sus CSV,
fichas y banderas se retiraron. La disponibilidad solo se presenta como
confirmada con evidencia vigente (`source_url`, `observed_at`, `valid_until` no
vencido); sin ella queda «por confirmar». Cada cotización versionada separa
presentación, unidades, subtotal, IVA, envío y total; si falta un componente, el
total se conserva ausente. Se guardan mensajes, resultado comercial y
cotización en una sola transacción, con folio y replay idempotente. `/cotizador`
redirige al chat y conserva la lectura autorizada de históricos reales;
`/faq` redirige al chat y `POST /api/faq` mantiene su contrato para
consumidores. La memoria silenciosa (`visitor_memory`, 180 días, borrado con
«olvida mis datos») se conserva. No se afirma inventario ni plazo sin la
evidencia indicada.

### Fuera del alcance (por ahora)

- Rastrear el `sitemap.xml` completo del dominio (podcast, noticias, especialistas).
- Vídeos, OCR de PDF escaneados y transcripciones.
- Páginas tras autenticación o cualquier contenido no público.
- Benchmarking de modelos de lenguaje como producto; la evaluación del enrutador, del
  retrieval y de los experimentos del monitoreo se documenta en `specs/` y `docs/`.

### Fases

| Fase | Estado | Entregable |
|------|--------|------------|
| 1. Captura inicial (landing + secciones) | ✅ Hecha | 15 documentos limpios en `data/gold/` (~64.5 KB) |
| 2. Ampliación de semillas | ✅ Hecha | 6 fuentes de terceros con indicadores financieros |
| 3. Enriquecimiento (PDFs, vídeo) | ⬜ Pendiente | Nuevas semillas y extractores |
| 4. Chunking y embeddings | ✅ Hecha | Chunking 1000/200 + FAISS con embeddings Ollama |
| 5. Asistente RAG (LLM + prompt + UI) | 🚧 En curso | LangChain + Qdrant/Ollama + interfaz única Jinja2/HTMX |
| 6. Corpus externo (capa extendida) | ✅ Hecha | `carvajal import` + manifiesto + recuperación citada |

## 3. Stack técnico

| Categoría | Tecnología |
|-----------|------------|
| **Lenguaje** | Python 3.13+ |
| **Gestor de paquetes y entorno** | [`uv`](https://docs.astral.sh/uv/) |
| **Build backend** | `uv_build` (src-layout) |
| **Paquete Python** | `carvajal` |
| **HTTP** | `requests` (sesión única, `User-Agent` identificable, `timeout`) |
| **Parseo HTML** | `beautifulsoup4` + `lxml` |
| **RAG** | LangChain + FAISS + embeddings |
| **LLM** | DeepSeek (`deepseek-chat`), Anthropic Claude u Ollama (`llama3.2`) |
| **Juez externo (opcional)** | Jev de TypeSafe (System One, `jev-1.14.0` fijado), apagado salvo `JEV_ENABLED=true` (spec 046) |
| **Fotos de alimentos** | Jev con imagen (`TypeSafe-Danger-Zone-Images`) y Pillow para preparar la foto; activas con `TYPESAFE_API_KEY` salvo `FOOD_PHOTO_ENABLED=false` (spec 051) |
| **Voz del chat (opcional)** | OpenAI `gpt-4o-mini-transcribe` y `gpt-4o-mini-tts` por `httpx` (sin SDK), apagada salvo `VOICE_PROVIDER=openai` (spec 047); respaldo en Web Speech |
| **Interfaz web** | FastAPI + Jinja2 + HTMX (interfaz única en `:8000`) |
| **Tipado estático** | [`ty`](https://github.com/astral-sh/ty) |
| **Testing** | [`pytest`](https://docs.pytest.org/) |
| **Linting / Formato** | [Ruff](https://docs.astral.sh/ruff/) |
| **Pre-commit** | [pre-commit](https://pre-commit.com) |
| **CI/CD** | [GitHub Actions](.github/workflows/ci.yml) |
| **Dependencias automáticas** | [Dependabot](.github/dependabot.yml) |

> ⚠️ **Regla dura:** toda gestión de dependencias y entornos pasa por `uv`. Está
> **prohibido** `pip` y `requirements.txt`.

> 📝 **Decisión sobre los extractores:** se evaluó y se descartó `trafilatura` como
> extractor principal. Es excelente con artículos de prensa, pero descarta las tarjetas
> y acordeones de las *landings* institucionales —justo el contenido que el RAG necesita—.
> El extractor propio recorre el DOM y conserva encabezados, listas, tablas y enlaces.

### 3.1 Stack objetivo de la app web (propuesto)

El stack anterior describe el prototipo actual. Para la app de chat en producción
se propone un stack distinto, documentado en
[`docs/decisions/0001-stack-app-chat.md`](docs/decisions/0001-stack-app-chat.md)
(estado: **propuesto**). Resumen:

| Capa | Elección |
|------|----------|
| Web + API | FastAPI + Jinja2 + HTMX + Tailwind (un solo runtime) |
| Persistencia | Postgres 16 + SQLAlchemy 2.0 + Alembic |
| Vectores | Qdrant (único índice vectorial del chat y de la recuperación); pgvector retirado con la spec 037 |
| Autenticación | Authlib (OIDC) + sesiones de servidor en Postgres |
| Streaming | SSE (`hx-ext="sse"`) |
| LLM | Puerto `LLMProvider`; LangChain como adaptador |
| Ingesta | CLI, consola y web (Configuración, solo operador); el rastreo periódico por cron es futuro |

> ⚠️ **Implementación parcial (2026-11-11)**: W2.1 (índice pgvector, retirado en la spec 037), W2.2
> (adaptadores de LLM), W2.3 (caso de uso `AskQuestion`), W2.4 (shell FastAPI) y
> W2.5 (layout y tema) están en revisión. El resto del stack sigue vigente hasta
> que el ADR pase a `aceptado` y la migración avance fase por fase.

## 4. Estructura del repositorio

```text
carvajal/
├── apps/
│   ├── api/                   # API FastAPI: /live, /health, /api, chat y pantallas
│   │   ├── main.py            # create_app, /live y /health
│   │   ├── agent_runtime.py   # checkpointer, Store, Qdrant y grafo compilado
│   │   ├── runtime.py         # templates, sesiones y clientes diferidos
│   │   ├── views/             # routers: corpus, documentos, chat, stream, api, webhooks, voz y PDF de cotización
│   │   ├── voice.py           # voz del servidor: runtime, límite por sesión, caché y texto hablado de un mensaje
│   │   ├── quote_view.py      # vista de lectura de una cotización (WhatsApp y PDF)
│   │   ├── quote_pdf.py       # PDF de la cotización con fpdf2 (recursos en pdf/)
│   │   ├── quote_links.py     # enlaces firmados al PDF
│   │   ├── chat/              # identidad, servicio, adaptador del agente y streaming
│   │   ├── static/            # CSS y JS del chat
│   │   └── templates/         # Jinja2: base, chat, pantallas y parciales
│   ├── mcp/                   # servidor MCP para WhatsApp (OpenClaw): consultar_carvajal
├── packages/
│   └── core/
│       └── src/carvajal/    # código del paquete (src-layout, módulo uv_build)
│           ├── domain/        # entidades y reglas puras (sin IO)
│           │   ├── entities.py  # CapturedPage, Document, Chunk
│           │   ├── rules.py     # slug, sección, dominio, hash, dedupe
│           │   ├── frontmatter.py # lectura y parseo del front matter
│           │   └── chunking.py  # división de documentos en chunks
│           ├── application/   # casos de uso y puertos (Protocol)
│           │   ├── ask.py       # caso de uso AskQuestion con citas y stream
│           │   ├── retrieval.py # prioridad núcleo → capa extendida (spec 037)
│           │   ├── sync.py      # reconciliación Gold → PostgreSQL → Qdrant (037)
│           │   ├── errors.py    # errores de aplicación (LLMError)
│           │   └── ports.py     # repositorios, vector store, LLM, embedder, fetcher
│           ├── agent/         # router, herramienta, memoria y grafo LangGraph
│           │   ├── gatekeeper/  # gatekeeper del modo tools: reglas → Jev → LLM, privacidad y compuerta de afirmaciones (spec 046)
│           │   ├── memory/      # resumen acumulado, memoria por categorías y filtro de sensibles (spec 045)
│           │   └── toolagent/   # modo tools: grafo, compuerta de evidencia y paquete tools/ con el registro (044, 045)
│           ├── catalog/       # catálogo curado de empaques: modelos, carga y construcción (spec 036)
│           ├── institutional.py # empresas curadas e hitos de historia.md, sin excepciones (spec 045)
│           ├── speech.py      # texto apto para voz (TTS) y enlaces aparte: WhatsApp con voz=true
│           ├── indexing/      # indexación Gold → Qdrant
│           ├── adapters/      # adaptadores de infraestructura
│           │   ├── storage/     # SQLAlchemy Core
│           │   │   ├── database.py      # motor y sesiones
│           │   │   ├── tables.py        # esquema declarativo
│           │   │   ├── repositories.py  # repositorios SQL
│           │   │   ├── chat.py          # conversaciones y mensajes del chat
│           │   │   ├── drafts.py        # borrador de cotización por conversación (spec 054)
│           │   │   └── import_corpus.py # importador Gold idempotente
│           │   ├── embeddings/  # Embedder: Ollama (nomic-embed-text)
│           │   ├── judge/       # juez externo opcional: Jev de TypeSafe (Choice y Noul, spec 046; fotos, spec 051)
│           │   ├── speech/      # voz del chat por el servidor: OpenAI STT y TTS con tope y circuito (spec 047)
│           │   ├── llm/         # LLMProvider: DeepSeek y Ollama sobre LangChain
│           │   └── vector/      # QdrantVectorStore (M2; pgvector retirado en 037)
│           ├── infrastructure/ # adaptadores y configuración tipada
│           │   └── settings.py  # pydantic-settings (.env + entorno)
│           ├── food/          # foto de alimento → empaque (spec 051): preparación, perfil, tabla y texto
│           ├── quotation/     # cotizador: motor, datos, borrador (draft.py), flujo (flow.py) y plantillas (render.py) de la spec 054
│           ├── numbers.py     # números dichos con palabras → cifras (voz y respuestas cortas, spec 054)
│           ├── prompts.py     # fuente única de identidad y prompts por ruta
│           ├── config.py      # fachada de compatibilidad de settings
│           ├── fetching.py    # cliente HTTP educado (implementa Fetcher)
│           ├── extracting.py  # HTML → Markdown
│           ├── gold.py        # front matter + índice JSONL
│           ├── pipeline.py    # orquestación de las tres capas
│           ├── models.py      # reexporta el dominio (compatibilidad)
│           ├── importer.py    # importación del corpus externo (capa extendida)
│           ├── rag.py         # recuperación, prompt y generación (RAG, solo CLI)
│           └── cli.py         # capture / sync / import / ask / status / list
├── data/                    # capas Medallion (raw y processed ignoradas)
│   ├── raw/                 # bronze — HTML crudo
│   ├── processed/           # silver — Markdown limpio
│   ├── gold/                # gold — entregable versionado (núcleo)
│   ├── external/            # capa extendida importada (solo manifiesto en Git)
│   ├── eval/                # consultas etiquetadas para Recall@10 / nDCG@10
│   ├── indicators.json      # herramienta estructurada del M2
│   ├── catalog/empaques/    # catálogo curado versionado (CSV + manifest.json, spec 036)
│   ├── institucional/       # capa institucional curada con cita literal de Gold (spec 045)
│   ├── retrieval_cards.json # fichas de recuperación (no son fuente de respuesta)
│   └── index/               # índice FAISS legado (regenerable, ignorado por Git)
├── migrations/              # migraciones Alembic (esquema relacional)
├── alembic.ini              # configuración de Alembic
├── specs/                   # especificaciones (Spec Kit: spec / plan / tasks)
├── .specify/                # plantillas y constitución del flujo (Spec Kit)
├── tests/                   # pruebas (pytest, patrón AAA)
├── docs/                    # documentación, ADRs e investigación
│   ├── decisions/           # ADRs: contexto, opciones y consecuencias
│   ├── arquitectura.json    # fuente tipada del diagrama (Archify)
│   ├── arquitectura.html    # diagrama interactivo autocontenido
│   └── arquitectura.md      # versión Mermaid del mismo pipeline
├── .github/                 # gobernanza (PR/issue templates, CI, Dependabot, labels)
├── .opencode/rules/         # reglas para agentes (tgrep)
├── .claude/skills/          # skills de agentes: abrir-pr y evaluar-agente
├── AGENTS.md                # contexto para agentes LLM
├── CONSTITUTION.md          # este archivo — verdad central
├── README.md                # puerta de entrada pública
├── CONTRIBUTING.md          # guía de contribución
├── CODE_OF_CONDUCT.md       # código de conducta
├── Makefile                 # automatización de tareas
├── Dockerfile               # imagen multi-etapa: `dev` (recarga) y `runtime` (web)
├── compose.yml              # local: db + qdrant; perfil `full` añade app, ollama y corpus
├── compose.prod.yml         # app + Postgres + Qdrant + Ollama; perfil `bootstrap` prepara el servidor
├── scripts/deploy.sh        # despliegue/rollback que invoca GitHub Actions
├── .dockerignore            # contexto mínimo de la imagen
├── pyproject.toml           # metadatos y dependencias (uv)
├── uv.lock                  # lockfile determinista (versionado)
├── .python-version          # versión de Python fijada
└── .pre-commit-config.yaml  # hooks de calidad
```

## 5. Convenciones de código

- **Estilo:** [PEP 8](https://peps.python.org/pep-0008/) y [PEP 257](https://peps.python.org/pep-0257/),
  impuesto por **Ruff** (`line-length = 100`, comillas dobles).
- **Tipado:** anotaciones en funciones y firmas públicas.
- **Nomenclatura:** `snake_case` (funciones/variables), `PascalCase` (clases),
  `UPPER_CASE` (constantes).
- **Docstrings:** en módulos, clases y funciones públicas.
- **Pruebas:** `pytest` con el patrón **AAA** (*Arrange · Act · Assert*).
- **Idioma:** UI, documentación y commits en español; identificadores de código en inglés.
- **Reglas propias:**
  - La extracción **nunca** depende de la red: `extracting` recibe HTML ya descargado.
  - El *front matter* es la única fuente de metadatos; no se duplica en el cuerpo.
  - `data/gold/` es el contrato con el proyecto consumidor: cambiarlo es un cambio
    incompatible y se documenta en esta constitución.
  - La capa extendida (`data/external/`) **solo** se recupera y se cita; nunca se
    consolida completa en el prompt ni se presenta como fuente oficial.
  - Toda importación deja `manifest.json` con su fuente, captura donante y conteos.
  - La capa de catálogo (`data/catalog/`) contiene datos estructurados curados a partir
    del corpus; cada fila conserva `source_url` y `captured_at`, y los precios se
    presentan siempre como referencia fechada, nunca como oferta ni con disponibilidad.
  - La capa institucional curada (`data/institucional/`, spec 045) guarda listas pequeñas
    derivadas solo del núcleo (empresas y marcas del grupo), cada fila con su cita literal
    y su URL de `index.jsonl`, verificadas por prueba contra `data/gold/`; no añade hechos
    que el núcleo no diga. Viaja en la imagen y en la siembra de producción.
  - Un precio del catálogo puede venir de un tercero identificado (un distribuidor):
    se muestra con su nombre, su página y su fecha, y nunca se presenta como precio de
    Carvajal. Un precio ausente no se estima (spec 039). La única conversión admitida es la
    de las copias de México (`CO-MX-…`), con procedencia `sistema`, tasa versionada en
    `fuentes/tasa_mxn_cop.json`, IVA `no_indicado`, en Bogotá y 2 ciudades más (decisión del 2026-11-25). Los campos vacíos
    de las copias solo se completan con lo que declara la ficha de México (`mx_atributos.json`).
    Se presenta como «precio de referencia del catálogo» y nunca se atribuye a un distribuidor.
  - La memoria del dueño y el resumen de la conversación (spec 045) son datos del
    usuario: aplican como contexto y estilo, nunca respaldan datos de Carvajal ni cambian
    las reglas. Las palabras literales de la persona respaldan solo lo que ella dijo.
    Además de oficial y tercero existe la procedencia `sistema` para datos que calcula el
    servidor (fecha en `America/Bogota` y calendario de festivos de Colombia); se cita como
    tal y nunca se presenta como dato oficial de Carvajal.
  - En el modo `tools`, después de la compuerta determinista, la compuerta de afirmaciones
    (Jev, pregunta Noul, umbral 0,5; spec 046) sustituye por el texto neutro existente toda
    respuesta que marca como no respaldada. Una valoración presentada como hecho sin fuente
    («la más usada») cuenta como no respaldada; un dato reformateado (un teléfono con el
    indicativo «(601)») no. Con la compuerta activa, el camino con evidencia retiene el texto
    completo; si Jev no responde, rige la compuerta determinista sola.

## 6. Calidad y automatización

| Herramienta | Rol | Comando |
|-------------|-----|---------|
| `uv` | Entornos y dependencias | `uv sync` |
| Ruff | Lint + formato | `make lint` / `make format` |
| `ty` | Verificación de tipos | `make typecheck` |
| `pytest` | Pruebas | `make test` |
| `pre-commit` | Hooks antes de cada commit | `make pre-commit` |
| GitHub Actions | CI (pre-commit + ty + pytest) | `.github/workflows/ci.yml` |

- Ningún commit se salta los hooks (`--no-verify` está prohibido salvo causa justificada y documentada).
- La **CI debe estar en verde** antes de fusionar un Pull Request.
- El presupuesto de cambios suma adiciones y borrados. CI **solo advierte** al superar
  3.000 líneas en PRs con base distinta de `main`; el aviso no bloquea el merge. El tamaño
  se usa para planear la revisión, no como puerta de calidad.
- Dippy usa DeepSeek para publicar revisiones de GitHub con hallazgos P0/P1.
  Corre automáticamente en PRs internos y se puede pedir con `@dippy review`
  desde una cuenta colaboradora una vez que el workflow llegue a `main`.
- Toda dependencia nueva entra con `uv add` / `uv add --dev` (nunca editando `uv.lock` a mano).

### 6.1 Imagen y despliegue

- `make image` construye la imagen multi-etapa (`Dockerfile`) con las
  dependencias base y el extra `capture` (la captura web desde Configuración
  usa `requests`, `beautifulsoup4` y `lxml` en runtime); `legacy`
  (FAISS para la CLI histórica) se queda fuera de la imagen y se instala en
  desarrollo con `uv sync --all-extras`. La etapa de runtime instala
  `poppler-utils` porque
  `pdftotext` extrae el texto de los PDF que llegan por la carga local.
- `make prod-up` levanta app, Postgres, Qdrant y Ollama con
  `compose.prod.yml`; `make prod-down` los detiene conservando los volúmenes.
  El compose exige `SESSION_SECRET` (falla si falta), publica la app en
  `0.0.0.0:APP_PORT` (8080 por defecto), corre sin privilegios, mantiene el
  sistema de archivos de solo lectura y monta el volumen `appdata` en
  `/app/data` para conservar Bronze, Silver, Gold y el índice de las cargas.
- El stack local tiene dos niveles en `compose.yml`. Sin perfiles quedan `db` y
  `qdrant` para el flujo nativo (`make db`, `make qdrant`, `make api`); el perfil
  `full` añade `ollama`, la app en modo desarrollo (etapa `dev` del Dockerfile,
  recarga y directorios del repo montados) y un `bootstrap` de una sola ejecución
  que aplica migraciones, importa Gold e indexa. `make up`/`make down` son la
  puerta a ese perfil y `LOCAL_UID`/`LOCAL_GID` evitan archivos de root en
  `./data`. Ningún servicio publica el puerto de Ollama para no chocar con un
  Ollama instalado en el host.
- En producción el perfil `bootstrap` (`make prod-bootstrap`) descarga
  `nomic-embed-text`, copia `./data` al volumen `appdata` solo si aún no tiene
  Gold, aplica migraciones, importa Gold y reindexa. Es idempotente y no pisa
  capturas ni cargas más nuevas; el volumen sigue siendo la fuente de verdad.
- El despliegue de producción es automático: al fusionar el PR de release en
  `main`, CI debe terminar en verde; `.github/workflows/deploy.yml` publica la
  imagen en GHCR con un tag `sha-<commit>` y `scripts/deploy.sh` la descarga en
  el VPS, siembra en el volumen el aprovisionamiento comercial que falte (desde
  `/app/seed-data` de la imagen, sin pisar archivos) y lo valida contra
  `EXPECTED_COMMERCIAL_VERSION`, aplica migraciones, recrea los contenedores y
  comprueba `/live` y `/health`. Si la siembra o la validación fallan, se
  detiene antes de migrar; si falla después, vuelve al tag anterior. El runbook completo —VPS,
  secretos, primer despliegue, respaldos y rollback— está en
  [`docs/despliegue-ovh.md`](docs/despliegue-ovh.md).
- La demo se publica por IP y HTTP en `APP_PORT` (8080), sin dominio ni TLS.
  Para un dominio, se pone nginx/Caddy delante, se publica la app solo en
  `127.0.0.1:8000` y se ajusta `FORWARDED_ALLOW_IPS` a la IP desde la que el
  contenedor ve llegar al proxy: con nginx en el host, la puerta de enlace de la
  red de Docker del compose (p. ej. `172.18.0.1`), no `127.0.0.1`. Solo así
  `request.url.scheme` es `https` (cookies `Secure`, HSTS y redirecciones);
  procedimiento en `docs/despliegue-ovh.md` §10.
- Los embeddings se calculan con Ollama (`nomic-embed-text`), configurable con
  `OLLAMA_BASE_URL`; el LLM del chat es DeepSeek por defecto (`LLM_PROVIDER=deepseek`
  en `Settings` y en ambos compose) y Ollama no atiende el chat salvo que se fije
  `LLM_PROVIDER=ollama`. Sin `DEEPSEEK_API_KEY` la app arranca, registra
  `llm.provider_unconfigured` y el chat degrada a `routes`. El servicio `ollama` de
  `compose.prod.yml` cubre los embeddings.
- `GET /live` es la vitalidad del proceso y no toca la base; `GET /health` es
  el readiness y publica los conteos del corpus. El embedder y el LLM se crean
  en la primera pregunta, nunca al arrancar.
- El CLI completo requiere `carvajal[capture,legacy]`; la imagen web incluye
  el extra `capture` para la captura desde Configuración, pero no `legacy`.
  Las migraciones se aplican aparte con `alembic upgrade head`.

## 7. Repositorio y Git

- **Remoto (SSH):** `git@github.com:almeneses/carvajal.git`
- **Rama principal:** `main` — única rama de integración y de producción; recibe
  todo el trabajo por Pull Request.
- **Estrategia de ramas:** `feat/...`, `fix/...`, `docs/...`, `refactor/...`,
  `test/...`, `chore/...`; toda PR apunta a `main`. La rama `dev` deja de usarse
  desde el 2026-11-22.
- **Método de fusión:** los PRs hacia `main` se fusionan con **squash**, un
  commit por PR. El release se marca con un tag anotado `spec-NNN` sobre el
  commit de `main` (por ejemplo `spec-042`); el despliegue de producción se
  dispara al pasar CI en `main`.
- **Protección de `main`:** ruleset con el check `Calidad` y la rama al día
  obligatorios, sin borrado ni force push y sin aprobaciones requeridas (desde el
  2026-11-25). Un agente fusiona su PR en cuanto la rama está al día y `Calidad`
  en verde, sin `--admin`
  ([`AGENTS.md`](./AGENTS.md) § Regla de merge).
- **Commits:** [Conventional Commits](https://www.conventionalcommits.org/) —
  `feat:`, `fix:`, `docs:`, `refactor:`, `style:`, `test:`, `chore:`.
- **Etiquetas de PR:** toda Pull Request debe llevar al menos una etiqueta del
  catálogo existente (`gh label list`), aplicada con
  `gh pr edit <número> --add-label`. En particular, ningún agente da una PR por
  terminada sin etiquetas; si el cambio abarca varias áreas, combina las
  etiquetas que correspondan. Solo se crea una etiqueta nueva si ninguna
  existente encaja y el hueco es estructural, justificándolo en la PR. El
  procedimiento y los ejemplos están en
  [`AGENTS.md` § Etiquetas de PR](AGENTS.md#etiquetas-de-pr-obligatorio-para-agentes).
- **Assignee de PR:** toda Pull Request debe incluir como assignee a quien la
  abre. Por defecto el autor se asigna a sí mismo
  (`gh pr edit <número> --add-assignee @me`); no se asigna a otras personas sin
  pedirlo. Ver
  [`AGENTS.md` § Assignee de PR](AGENTS.md#assignee-de-pr-obligatorio-para-agentes).
- **Identidad git:** Marko David Garcia <markodavidgm@gmail.com> (ver §11).

## 8. Flujo diario (commit + push)

```bash
# 1. Ver cambios
git status

# 2. Verificación completa (lint + formato + tipos + pruebas)
make check

# 3. Agregar y commitear (los hooks se ejecutan solos)
git add -A
git commit -m "tipo: mensaje descriptivo"

# 4. Subir a GitHub
git push
```

## 9. Historial de decisiones técnicas

| Fecha | Decisión | Motivo |
|-------|----------|--------|
| 2026-10-21 | Adoptar `uv` como gestor único de entornos y dependencias | Reproducibilidad; prohibido `pip`/`requirements.txt` |
| 2026-10-21 | Adoptar Ruff (lint + formato) y pre-commit | Calidad automatizada antes de cada commit |
| 2026-10-21 | Adoptar Conventional Commits | Historial legible y automatizable |
| 2026-10-21 | Adoptar licencia MIT y `CONSTITUTION.md` como fuente única de verdad | Claridad legal y operativa |
| 2026-10-21 | Patrón Medallion (bronze/silver/gold) con `data/gold/` versionado | Permite reprocesar sin volver a la web; el entregable queda en Git |
| 2026-10-21 | Descartar `trafilatura` como extractor principal | Pierde tarjetas y acordeones de las *landings* institucionales |
| 2026-10-21 | Extractor propio basado en DOM | Control explícito de qué es contenido y qué es *chrome* |
| 2026-10-21 | Eliminar el pie de página institucional del corpus | Repite el mismo bloque en todas las páginas y contamina la recuperación |
| 2026-10-21 | Semillas explícitas en `config.SEEDS` en lugar de rastrear el sitemap | Alcance acotado y reproducible (ampliado el 2026-10-29 con `EXTERNAL_SEEDS`) |
| 2026-10-27 | Capturar las landing y secciones públicas de Carvajal (`config.SEEDS`) | Punto de partida verificable y reproducible del corpus |
| 2026-10-28 | Incorporar RAG con LangChain + FAISS y chunking | Recuperación de contexto sobre `data/gold/` antes de generar respuestas |
| 2026-10-28 | Añadir asistente RAG con LLM configurable (Ollama / Claude API) | El curso define dos opciones de modelo; se soportan ambas vía `LLM_PROVIDER` |
| 2026-10-28 | Corregir el extractor: eliminar duplicación de contenedores y *chrome* | El corpus pasó de 2.45 MB a 64.5 KB de texto editorial; mejora la recuperación y permite consolidarlo en el prompt |
| 2026-10-28 | Consolidar el corpus limpio en el prompt de sistema | El prompt es la "memoria" completa del asistente en esta fase |
| 2026-10-28 | Añadir DeepSeek como proveedor principal vía API compatible con OpenAI | El curso usa la API de DeepSeek; Ollama y Claude quedan como alternativas vía `LLM_PROVIDER` |
| 2026-10-28 | Persistir el índice FAISS en `data/index/` con huella del corpus | Los embeddings en CPU tardan minutos por arranque; con caché la app abre en segundos y se invalida sola al cambiar `data/gold/` |
| 2026-10-29 | Incorporar fuentes de terceros (`EXTERNAL_SEEDS`) al corpus | Los indicadores financieros no están en el HTML oficial (contadores JS y PDFs); la prensa económica los publica en texto y cada documento se marca con `source_type: tercero` |
| 2026-10-29 | Adoptar Archify para el diagrama de arquitectura (`docs/arquitectura.json` → `.html`) | Documentación viva validada: el JSON tipado es la fuente, el HTML autocontenido es el artefacto que se comparte y se enlaza desde el README |
| 2026-10-29 | Materializar las capas Bronze y Silver y añadir `index.jsonl` | El pipeline escribía solo Gold: el patrón Medallion era una promesa del informe, no del código. Ahora cada capa queda en disco y el índice JSONL permite auditar URL, sección, hash y tamaño |
| 2026-10-29 | Respetar `robots.txt` + `Crawl-delay` y aplicar rate limit de 1 s por host | El README lo afirmaba sin implementarlo; es requisito de captura responsable y de la rúbrica |
| 2026-10-29 | Deduplicar por SHA-256 y enriquecer con `section` y `hash` | Evita documentos repetidos al ampliar semillas y da trazabilidad por documento |
| 2026-10-29 | Interfaz Streamlit de dos pestañas con el pipeline visible | La evaluación exige ver las etapas y las métricas del scraping en pantalla, no solo el Q&A. El `CaptureReport` y los eventos de progreso son la fuente única de esas métricas |
| 2026-10-29 | Identidad visual de Carvajal (logo SVG oficial + paleta de marca) y diagrama de bloques en vivo | El diagrama ilumina cada etapa al avanzar la captura, lo que hace visible el proceso sin depender del log; la paleta se toma de los tokens del sitio oficial, no se inventa |
| 2026-10-29 | Tres tareas de LLM sobre el mismo corpus: Resumen, FAQ y Q&A | La rúbrica del Módulo 1 exige las tres tareas; comparten el bloque de corpus consolidado y cada una tiene su propio prompt, con citas de fuente y reglas anti-alucinación |
| 2026-10-30 | Alcance documentado en `docs/alcance.md`; el sitio no publica contacto ni sedes | Se verificaron 404 en `/contacto/`, `/sedes/`, `/oficinas/`, barrido de 26 sub-sitemaps y portada sin teléfonos ni direcciones. No se inventan datos: el asistente declara la ausencia y remite a los canales oficiales |
| 2026-10-30 | Experimentación de prompts documentada en `docs/prompts.md` | Cada tarea se probó con 3-4 variantes sobre el corpus real; las métricas (fuentes citadas, cifras, declaración de ausencia) justifican las reglas actuales y quedan reproducibles con `scripts/experimentos_prompts.py` |
| 2026-10-30 | Importar el corpus de un compañero como **capa extendida** (`data/external/`) | Su corpus aporta cientos de fuentes que el núcleo no cubre. Se importa solo HTML, se re-extrae con el extractor propio, se deduplica contra el núcleo y se carga siempre si existe; no se consolidan en el prompt porque 1,4 M de caracteres desbordarían el contexto |
| 2026-11-03 | Adoptar **Spec Kit** para el desarrollo guiado por especificaciones | `specs/` pasa a ser el registro de qué se construye y cómo (`spec` → `plan` → `tasks`); `.specify/memory/constitution.md` es una proyección de esta constitución, que sigue siendo la fuente única de verdad |
| 2026-11-08 | Registrar las decisiones en `docs/decisions/` (formato ADR) | `CONSTITUTION.md` dice *qué* es el proyecto; los ADR dicen *por qué* y qué se descartó, para no re-litigar decisiones ni perder el contexto ante agentes nuevos |
| 2026-11-08 | ADR-0001 (**propuesto**): app web en un solo runtime Python (FastAPI + Jinja2 + HTMX) con Postgres/pgvector; captura por CLI/cron | Streamlit no da sesiones, concurrencia ni trabajos en segundo plano. Las 100 K cuentas proyectadas no exigen Kafka ni Kubernetes: el costo dominante son los tokens, no los servidores |
| 2026-11-10 | Persistencia relacional de la Ola 1: Postgres 16 + pgvector, Alembic, repositorios SQLAlchemy Core e importador Gold idempotente con poda autoritativa | El corpus necesita historial de versiones y recargas seguras; el importador recalcula el estado desde `data/gold/` y elimina lo que ya no existe, sin duplicar en ejecuciones repetidas |
| 2026-11-11 | Índice vectorial pgvector: `embedding` de 768 dimensiones en `chunks`, índice HNSW coseno y adaptador `PgVectorStore` | La recuperación semántica vive en Postgres (filtros SQL + ANN) en lugar de FAISS en disco |
| 2026-11-11 | Puerto `LLMProvider` con adaptadores de DeepSeek y Ollama y fábrica `build_llm_provider` | La app web nueva depende del puerto, no de LangChain; `claude` queda solo en Streamlit, que conserva `rag.build_llm` intacto |
| 2026-11-11 | Caso de uso `AskQuestion` con puerto `Embedder`, contexto numerado, citas y neutralización de delimitadores | Une recuperación y generación sin acoplar capas; sin contexto no se llama al LLM y el texto del corpus no puede inyectar instrucciones |
| 2026-11-11 | Shell web FastAPI con handlers diferidos, `/health`, portada HTMX y `/ask` con citas | Importar el módulo no abre conexiones; los fallos de proveedor se traducen a 503 sin filtrar detalles internos |
| 2026-11-13 | Pantallas de corpus y documento: un `APIRouter` por pantalla en `views/`, filtros/paginación y Markdown saneado con `nh3` | Las pantallas viven fuera de `main.py` para que las pistas de la ola 3 no compitan por el mismo archivo; el HTML se sanea con allowlist y la URL de origen se valida contra `javascript:` |
| 2026-11-14 | Identidad del shell HTMX: hash de 256 bits en `localStorage`, cookie `carvajal_visitante` y dueño derivado con HMAC | El navegador genera el hash una sola vez y lo conserva; el servidor no usa el valor crudo como clave y mantiene la cookie firmada como respaldo si el visitante no envía el hash. No es autenticación |
| 2026-11-13 | Chat persistente: cookie firmada con `owner_key` opaco y tablas `conversations`/`messages` (migración 0003) | La cookie sola no guarda historial (4 KB, manipulable); Postgres es la fuente de verdad y la identidad no es autenticación: solo separa conversaciones anónimas |
| 2026-11-13 | Cuota diaria por visitante anónimo en `usage_day` (migración 0004), reserva atómica y 429 con `Retry-After`; medianoche en `QUOTA_TIMEZONE` | El cupo usa la misma cookie firmada del chat y se evalúa antes del LLM; un fallo de BD al consultarlo responde 503. W4.1 añadirá `user_id` de forma aditiva |
| 2026-11-13 | Stream SSE del chat con degradación: `open/meta/token/done/fallback/error` y persistencia exacta una vez | El primer token no debe esperar al LLM completo; si el stream falla antes de emitir, se completa por la vía normal y se persiste una sola vez |
| 2026-11-13 | Imagen Docker multi-etapa; `capture` y `legacy` pasan a extras y `/live` se separa de `/health` | El contenedor instala solo el runtime web, arranca sin credenciales del LLM y usa `/live` para no marcar enfermo al proceso cuando Postgres está caído |
| 2026-11-13 | Logs JSON con `request_id` por petición, consumo de tokens por visitante y Sentry opcional por `SENTRY_DSN` | Una petición se sigue de punta a punta sin abrir el código; sin DSN no sale nada del proceso, `send_default_pii` queda en `False` y el muestreo de trazas es explícito |
| 2026-11-13 | M2: Qdrant como índice vectorial evaluado; Postgres conserva documentos, chat, checkpoints y Store | El índice se prepara con `carvajal index-qdrant` (UUID5 estable, upsert idempotente y poda); la web arranca con el índice ya construido y `/live` no depende de Qdrant |
| 2026-11-13 | Agente LangGraph con router `direct`/`tool`/`rag`, `PostgresSaver` y `PostgresStore` | El seguimiento elíptico necesita estado por hilo; las preferencias sobreviven a un hilo nuevo y se aíslan por dueño. El checkpointer se inicializa fuera de la transacción de la petición |
| 2026-11-13 | Herramienta `indicator_lookup` con `args_schema` Pydantic sobre `data/indicators.json` | El nodo `tool` invoca `indicator_lookup.invoke` con argumentos tipados y devuelve valor, cita y URL o ausencia estable |
| 2026-11-13 | RAG híbrido ligero: ANN + reordenamiento léxico, fichas de recuperación y expansión de consulta | `nomic-embed-text` confunde preguntas de fundación con la portada; las fichas y la expansión usan datos ya declarados en el corpus y la cita sigue saliendo del fragmento original |
| 2026-11-13 | Frontend propio en `apps/web` (:8001) y API (:8000) con CORS explícito y cookie/CSRF compartidos | Cubre la lectura estricta de backend/frontend desacoplados sin duplicar el RAG; el shell Jinja2 se conserva como compatibilidad |
| 2026-11-13 | Migración 0005: `status` de mensaje e idempotencia `client_turn_id` (índice parcial por asistente) | Un reintento del navegador no duplica el turno y una respuesta cortada queda `incomplete`; el usuario puede distinguirla |
| 2026-11-13 | Evaluación de retrieval separada de la respuesta: 31 consultas, Recall@10=0.8000, nDCG@10=0.7791 | Permite inspeccionar el top-k y documentar una consulta sin evidencia sin depender del LLM |
| 2026-11-13 | CSRF por doble cookie, cabeceras de seguridad con CSP sin `unsafe-inline`, HSTS solo sobre TLS y Swagger fuera | El POST sin token se rechaza con 403 antes de la cuota y del caso de uso; el script del formulario pasa a `/static/app.js` para que la CSP sea estricta; las cookies son `HttpOnly` y `SameSite=Lax` y el token se compara en bytes |
| 2026-11-13 | Interfaz institucional con pestañas superiores en el frontend del M2: rutas SPA `/`, `/corpus`, `/documents` y `/documents/{id}`, y contrato JSON de lectura `/api/corpus` y `/api/documents/*` | Las tres pantallas comparten header, navegación y estado sin recargar el chat; el frontend consume JSON y reutiliza los proveedores y el Markdown saneado con `nh3` de las vistas HTML, que se conservan como compatibilidad |
| 2026-11-14 | Chat HTMX con compositor inferior y panel lateral de hilos; pestaña Configuración para cargas | El hilo seleccionado se resuelve por `?thread=` y el compositor envía ese id al stream; la carga reutiliza Bronze → Silver → Gold, `migrate_gold` y `index_gold` para no duplicar el pipeline (sustituido por `sync` en la spec 037) |
| 2026-11-14 | El shell HTMX de compatibilidad (`apps/api`, :8000) adopta el sistema visual institucional del M2, fuentes plegables y un panel lateral de lectura | Cabecera, paleta y componentes unificados; el texto generado se formatea en servidor con Markdown saneado y en cliente para el stream, las fuentes quedan contraídas por defecto y el panel resuelve el documento con `/api/documents/by-slug` y resalta el fragmento citado, sin cambiar rutas ni contratos SSE/CSRF |
| 2026-11-14 | Captura web pública desde Configuración: semillas + URLs, máximo de 24 por ejecución, guard SSRF, job en segundo plano y progreso SSE | La petición solo valida y crea el job; el cierre reutiliza `importar_gold` e `indexar_gold` con `only` y sin poda (sustituido por `sync` en la spec 037). Sin autenticación en esta fase: el límite por IP (5 capturas/hora), el máximo de 24 URLs, `robots.txt`, el timeout y el guard SSRF (IPs privadas, puertos 80/443, redirecciones revalidadas y DNS fijado a la IP validada) son la defensa; `CAPTURE_ALLOWED_DOMAINS` permite restringir dominios por entorno |
| 2026-11-14 | Shell HTMX con escala accesible para adulto mayor: 18px base, controles de 54px, contenedores de 1360/1680px y navegación móvil 2×2 | Mejora lectura y precisión de clic sin cambiar contratos; solo CSS del shell `apps/api`. La SPA `apps/web` queda fuera |
| 2026-11-14 | Carga de documentos pública en Configuración: se elimina `CONFIG_TOKEN` y el gate de loopback | La pestaña queda abierta a cualquier visitante, igual que la captura web; se conservan el CSRF, los límites de 100 archivos/5 MB y el pipeline Bronze → Silver → Gold → PostgreSQL → Qdrant. El riesgo de abuso se acepta por decisión del owner |
| 2026-11-14 | FAQ en la SPA y el shell HTMX (`/faq`): lotes de hasta 10 preguntas, botón «Generar otras 10» y descarga Markdown | El caso de uso lee los documentos `core` de Postgres, excluye las preguntas ya mostradas y no persiste: el estado acumulado vive en la pestaña. Comparte CSRF y cuota diaria con el chat, genera por SSE sobre el corpus completo y advierte si el bloque se truncó por longitud; con Ollama se fija `num_ctx` (32 768) para que el prompt no se recorte en el servidor |
| 2026-11-14 | Se oculta la pestaña Asistente en el shell HTMX y en la SPA; `/` redirige a `/chat` en `apps/api` y a `/corpus` en `apps/web` | El chat concentra la entrada al asistente y la portada heredada deja de competir en la navegación. La ruta `/ask` y la vista `#vista-asistente` se conservan por compatibilidad, pero no se enlazan; la marca lleva al chat en el shell y al corpus en la SPA |
| 2026-11-15 | Documentar *as-built* las 23 capacidades ya entregadas (`specs/003`–`specs/027`) y reconvertir la 002 | El proyecto adoptó Spec Kit el 2026-11-03, pero el Módulo 1 y la migración del M2 se registraron en PRs y en esta tabla: `specs/` tenía dos entradas y la 002 describía otra aplicación. La convención as-built exige fecha honesta, requisito observable y evidencia por escenario, y prohíbe `plan`/`tasks` retroactivos, salvo la excepción documentada de la 002 (§3.5 del inventario); el índice y el estado de cada spec viven en `specs/README.md` |
| 2026-11-16 | Ampliar el alcance a la carga local multiformato: PDF con texto por página y datos XML, JSON, JSONL, CSV y TSV | Se especifica antes de implementar en `specs/035-ingesta-multiformato` con el flujo de Spec Kit; la web conserva 100 archivos y 5 MB por archivo y la consola admite 100 MB por archivo. El OCR de PDF escaneados sigue fuera de alcance y se avisa al usuario en lugar de convertirlo |
| 2026-11-16 | Añadir la capa de catálogo curado (`data/catalog/`) y la ruta comercial de empaques (spec 036) | Recomendar empaques exige filtros exactos (capacidad, material, temperatura) que la recuperación por similitud no garantiza. Son unas 230 filas de solo lectura: se versionan como CSV y se cargan en memoria, como `data/indicators.json`, sin migración. La capa extendida sigue siendo solo de recuperación; el catálogo es una capa distinta, derivada y revisada en PR |
| 2026-11-16 | Corpus incremental: identidad estable por fuente, doble huella y sincronización única (spec 037) | El slug con hash convertía cada archivo modificado en un documento nuevo y cada copia en un duplicado. La identidad pasa a derivarse de la fuente (URL, ruta relativa o nombre), la huella del original deduplica antes de Bronze y la huella del texto decide actualización y reindexación. El registro vive en los `index.jsonl` para funcionar sin base; PostgreSQL y Qdrant se reconcilian con un solo `sync` idempotente y poda explícita |
| 2026-11-17 | Recuperación con prioridad de capas y procedencia en la cita (spec 037, US4) | `LayerPriorityStore` vive en `application/retrieval.py` y no en el adaptador: la prioridad es política de producto. El chat busca primero `layer=core` y solo completa con `layer=extended` si el núcleo no llena el cupo o no supera `RAG_MIN_SCORE`. `origin`, `category` y `entity` viajan del front matter al payload de Qdrant y a la cita; no se persisten en PostgreSQL |
| 2026-11-17 | Identidad estable, deduplicación por dos huellas y adopción del registro legado (spec 037, US1) | Cada documento se identifica por su fuente (`url:`, `file:` o `upload:`), el `raw_hash` corta la extracción de duplicados exactos y el `content_hash` detecta re-codificaciones. `carvajal sync --adopt` completa `source_key` y `raw_hash` en los índices anteriores sin renombrar slugs; si dos versiones legadas resuelven la misma subida, la más reciente conserva la fuente y el resto queda en `legacy:<slug>` |
| 2026-11-17 | Un solo índice de respuestas: retirada de `/ask` y de pgvector (spec 037, US5) | Ningún flujo escribía vectores en PostgreSQL, así que el adaptador `PgVectorStore` y la columna `chunks.embedding` eran un índice vacío que aparentaba funcionar. Se eliminan la ruta, el adaptador y la dependencia, con la migración reversible `0006`; el chat es el único camino de respuesta y la FAQ y el explorador siguen leyendo PostgreSQL |
| 2026-11-16 | Indexar la capa extendida con prioridad y retirar `/ask` y pgvector (spec 037) | La capa extendida suma cobertura si no desplaza al núcleo: se indexa en la misma colección con `layer` en el payload y el chat busca primero el núcleo. La FAQ sigue leyendo solo `core`. Ningún flujo escribe vectores en PostgreSQL, así que `/ask` respondía sin contexto y el adaptador pgvector era un índice vacío; el chat queda como único camino de respuesta y Qdrant como único índice vectorial |
| 2026-11-17 | Sincronización única del corpus (spec 037, US2): `carvajal sync --layer core|extended|all [--prune] [--verify]` reconcilia Gold con PostgreSQL y Qdrant | Un solo camino incremental compara huellas y solo importa e indexa lo nuevo o cambiado; `--verify` no escribe y devuelve código distinto de cero si hay diferencias. La captura web y la carga desde Configuración cierran con la misma sincronización, que necesita PostgreSQL y Qdrant en marcha; `carvajal capture --solo-gold` conserva la corrida limitada a disco |
| 2026-11-17 | Abrir en borrador las specs 038 (monitoreo con LangSmith), 039 (catálogo de Colombia con precios) y 040 (chat por voz) | Se especifican antes de implementar, con el flujo normal de Spec Kit. Decisiones tomadas en la clarificación: LangSmith en producción solo con metadatos; precios de Colombia desde las tiendas en línea de los distribuidores y, donde no haya, estimados por conversión desde México con tasa fechada y rotulados como estimación; la voz se procesa en el navegador. El alcance de §2 se enmienda en la misma PR, con las tres capacidades declaradas en borrador |
| 2026-11-18 | Interfaz única: se retiran la SPA `apps/web` (:8001) y el prototipo Streamlit; quedan el shell Jinja2/HTMX y la API en un solo proceso (:8000) | Dos implementaciones completas de chat, FAQ y corpus sobre los mismos endpoints ya habían divergido: el chat quedó sin enlace en la SPA tras #120. El shell era un superconjunto funcional (incluye Configuración y captura), así que la convergencia es de borrado, no de reescritura. Desaparecen CORS, `FRONTEND_ORIGINS`, el extra `streamlit` y `app.py`; `rag.py` y el extra `legacy` se conservan porque la CLI (`ask`, `summarize`, `faq`, `eval`) sigue usándolos |
| 2026-11-18 | Cerrar la spec 036 como *as-built* y transferir su SC-006 (informe de los tres enrutadores) a la spec 041 | El asistente comercial se entregó y funciona con el enrutador por reglas, que es su valor por defecto; el modelo de decisión es opcional en su alcance. La elección de Jev cloud añade una dependencia externa, costo por llamada y una medición que depende del catálogo de Colombia (spec 039), así que se especifica aparte en la 041. La 036 cierra sin exigir un modelo de decisión y la 041 hereda el criterio como FR-013 y SC-005. T043 se ejecutó el 2026-11-18 (corrida por API registrada en `docs/demo-036.md`) |
| 2026-11-18 | Implementar la spec 039: catálogo de Colombia con ofertas por fuente y ampliación del de México | Las fichas oficiales de Colombia publican medidas, material y unidades, pero no SKU ni precio; la tienda de EDEXA (distribuidor del directorio) publica precio con IVA. Se cruzan en `scripts/curar_catalogo_co.py` (102 referencias, 29 con precio) y `carvajal catalog-prices` normaliza a caja sin estimar ni convertir. El precio inline de México sigue en `referencias.csv` y se expone como oferta oficial, en lugar de migrarlo a `precios.csv`, para no tocar la regresión de la 036. La tienda de México tenía 102 productos y el corpus solo 55 fichas: `scripts/ampliar_catalogo_mx.py` suma 46 con el mismo `parse_mx_product` |
| 2026-11-19 | Monitoreo del agente con LangSmith **entregado** (spec 038): traza raíz por turno, apagado por defecto, muestreo 0.2 y contenido completo solo en desarrollo y evaluación | Diagnosticar una respuesta exigía reproducirla a mano sobre logs JSON dispersos. La traza se envía a LangSmith desde `infrastructure/monitoring.py`, con lista blanca de metadatos en producción (sin pregunta, respuesta ni fragmentos) y cliente perezoso: sin `MONITORING_ENABLED` no se importa el SDK ni sale un byte, `LANGSMITH_TRACING`/`LANGCHAIN_TRACING_V2` se fuerzan a `false` al arrancar y cualquier fallo del servicio queda en los logs sin tocar la respuesta, la persistencia ni la cuota. El muestreo de 0.2 mantiene el plan de 5.000 trazas/mes. Cerrada como entregada el 2026-11-19 con la evidencia de SC-001…SC-005 en `docs/evidence/m2/monitoring.md` (muestreo 0,2, producción solo metadatos) |
| 2026-11-20 | Índice Qdrant: un cambio que no altera el texto vectorizado reescribe el payload y **no** reembebe (issue #185) | El ID del punto cubre título, sección, ficha y texto, que es todo lo que se vectoriza; cambiar la URL, la procedencia o el tipo de fuente marcaba el documento como cambiado y re-embebía todos sus fragmentos. En el corpus extendido (77.588 puntos) eso costaba unas 28 horas de CPU por pasada en el VPS compartido, que además compite con Odoo y nginx. `IndexReport.updated` cuenta los puntos actualizados sin embedding y el evento `qdrant.document_payload_updated` lo deja visible; un cambio real de texto sigue re-embebiendo solo ese documento. Cada fragmento registra en el payload el modelo de embeddings que produjo su vector y un índice con puntos de otro modelo se detiene antes de escribir, pidiendo `--recreate`: mezclar modelos recupera mal aunque las dimensiones coincidan; los índices anteriores al registro adoptan el modelo configurado una sola vez, sin reembeber |
| 2026-11-21 | Política única de ausencia y registro de huecos del corpus | La falta de información se respondía con seis textos distintos y sin medición. `ABSENCE_POLICY` define cinco casos (sin contexto, parcial, entidad sin respaldo, dato estructurado, fuera de alcance) con qué se dice y qué se ofrece; `NO_CONTEXT_ANSWER` y el Cotizador la heredan. El grafo registra `rag.no_context` con motivo y tema normalizado (sin correos, URLs, teléfonos ni identificadores) para priorizar el corpus. |
| 2026-11-21 | Prompts unificados: identidad única, tres sistemas por ruta y datos delimitados | Había cuatro «Eres el asistente…» que divergían y el system del chat exigía «solo CONTEXTO» también en la ruta comercial, donde no existe. `carvajal/prompts.py` centraliza identidad, estilo, evidencia, guiones de borde, router con ejemplos y FAQ; el historial y las preferencias se delimitan como datos y nunca como instrucciones. El Cotizador adopta el mismo tono en sus plantillas. Los contratos se prueban en `tests/test_prompts.py`; la calidad de redacción se sigue midiendo con experimentos y revisión humana. |
| 2026-11-21 | Cotizador defendible: catálogo único, ficha citable, cotización registrada y casos dorados | El prototipo tenía dos verdades (catálogo y CSV demo), no permitía auditar una cifra ni detectar regresiones de totales. El motor lee el catálogo curado; la demo solo añade la extensión simulada y las condiciones. Cada oferta publica una ficha Markdown con procedencia y campos simulados, enlazada desde la tarjeta. Cada cotización completa se guarda en `quotes` con `id`, vigencia de 7 días, total y payload mostrado; las aclaraciones no emiten registro. Trece casos dorados con totales esperados corren en CI (`SC-012`). |
| 2026-11-21 | Cotizador: mapa incrustado y sin aviso general de demo | El esquema SVG no mostraba calles y el aviso general ocupaba la cabecera. Se incrusta OpenStreetMap con `frame-src` limitado a ese origen y se conserva la lista textual como respaldo; la simulación se marca en cada dato (precio, existencia, producto y entrega) y no con un banner. OpenStreetMap ve la IP del visitante pero no la conversación ni el perfil. |
| 2026-11-22 | Rama única `main`: toda PR apunta a `main` y el release es un tag sobre `main` | `dev` duplicaba la integración y obligaba a un PR de release con merge commit para que `main` no quedara desincronizado; además, el retargeteo manual de los PRs de Dependabot añadía trabajo y errores. Desde esta fecha el trabajo entra por PR a `main` con squash, el despliegue se dispara al pasar CI en `main`, el release se marca con un tag anotado `spec-NNN` y Dependabot apunta a `main`. La rama `dev` queda retirada. |
| 2026-11-21 | Prototipo del Cotizador en la rama `prototype`: motor determinista, cotización inline y demo marcada | El chat comercial respondía recomendaciones, pero no una cotización con presentaciones, totales, sedes y entrega. El motor reutiliza la extracción y la validación de la spec 036 y no llama al LLM: cantidades, `Decimal`, existencias y plazos son deterministas. La sección vive en `/cotizador`, con hilos aislados por `cot:<owner_key>` sin migración, streaming SSE del texto y las tarjetas, mapa incrustado de OpenStreetMap (`frame-src` acotado) e importes con su procedencia marcada dato por dato. La demo usa 150 productos de Colombia (102 reales y 48 simulados a partir de fichas de México, que no se cotizan), con precio real de EDEXA cuando existe y simulado cuando no; existencias, plazos y envíos son simulados y se marcan fila por fila. La memoria silenciosa guarda solo detalles con patrón conocido, no viaja en el payload ni en las trazas y caduca a los 180 días. Pendientes: dictado por voz (T039), verificación visual a 390 y 1440 px (T038) y revisión de la retención con el owner (T053). |
| 2026-11-21 | Stack reproducible en Docker con perfiles: `full` en local y `bootstrap` en producción | Clonar el repo en otra máquina exigía `uv`, Ollama y cinco comandos manuales (migraciones, `migrate-gold`, `index-qdrant`, `ollama pull`) antes de la primera pregunta. `compose.yml` conserva `db` y `qdrant` sin perfil para el flujo nativo y el perfil `full` añade la app en la etapa `dev` (recarga y directorios montados), Ollama y un `bootstrap` de una sola ejecución; `make up` es un solo comando. En producción el perfil `bootstrap` automatiza lo que el runbook hacía a mano y copia `./data` al volumen solo cuando no hay Gold, para no pisar capturas. Sin perfil nada cambia: `make db`, `make qdrant`, CI y `deploy.sh` siguen igual. Dockerizar `uv`/Ruff/pytest/Node queda fuera a propósito: el ciclo de desarrollo es más rápido en el host |
| 2026-11-21 | Simplificar la vista del chat: retirar el bloque y los marcadores visibles de fuentes, y formatear antes de pintar | El chat muestra bloques de Markdown ya formateados durante el stream, sin repintar los bloques previos. No cambia la fundamentación en el corpus ni el contrato de fuentes del SSE, del historial o de la API. La lectura en voz conserva los títulos como metadatos y las tarjetas mantienen la procedencia comercial. |
| 2026-11-21 | Hacer progresiva la lectura del bloque activo y simplificar la conversación | El cliente pinta el párrafo activo en lotes cortos de SSE, sella los bloques anteriores y sigue el final solo cuando el lector está cerca. El compositor multilínea queda fuera del área de scroll y permite detener la recepción, conservando el parcial en pantalla sin añadir un contrato nuevo de persistencia. Cabecera compacta, diagnóstico visual optativo (`/chat?debug=1`) y fichas comerciales desplegables. |
| 2026-11-21 | Abrir en borrador la spec 042 (Cotizador conversacional) con un plan preliminar que audita el catálogo | La sección pide producto, cantidad, precio, total, proveedores, mapa, entrega y alternativas en un solo turno, y el catálogo actual no puede sostenerlo: 29 de 102 referencias CO tienen precio, todas de EDEXA, y no existen inventario, coordenadas ni plazos. El plan deja por escrito la brecha —incluido que el flujo vigente redondea a cajas de miles de unidades mientras las capturas conservan precios por paquete— y propone una demo con datos simulados explícitos, separados del catálogo y etiquetados donde aparecen. Se decide el mercado inicial (Colombia, empaques, COP), el alcance exclusivamente comercial de la sección y la prohibición de presentar stock o plazos ficticios como reales. La implementación espera `/speckit.clarify` y `/speckit.plan`. |
| 2026-11-22 | Restringir la escritura del corpus a operadores: `OPERATOR_TOKEN` en la web, CLI para publicar y `Configuración` fuera de la navegación pública | La auditoría de `a21425c` confirmó que un visitante con el CSRF de su propia sesión podía cargar archivos y capturar web, escribiendo el núcleo que alimenta al chat. La autorización se evalúa en servidor antes de leer archivos o crear el job, con CSRF como control separado; el token es un secreto de despliegue, no un sistema de cuentas, y `CONFIG_TOKEN` queda como alias. La carga y la captura web solo escriben Gold: publicar a PostgreSQL y Qdrant sigue siendo el paso de curaduría `carvajal sync`. Sin token, la web queda cerrada y la consola conserva la operación. El procedimiento y las rutas cubiertas están en [`docs/seguridad-operacion-corpus.md`](docs/seguridad-operacion-corpus.md); la decisión, en [ADR-0003](docs/decisions/0003-escritura-corpus-operadores.md). |
| 2026-11-22 | Abrir la spec 043 del chat comercial unificado | Planificar la retirada de la simulación, la integración en un solo hilo y el despliegue con catálogo verificable. #222 y #223 son la base entregada. |
| 2026-11-23 | Cotizador integrado a `main`: precios con fecha visible pero sin caducidad ni sello de vencimiento, y sin aviso general de demo | La spec 042 se alinea con la entrega real en `main`: el mapa definitivo es OpenStreetMap, la ventana de entrega incluye los días de atención del proveedor y la regresión cubre chat/SSE y flujo comercial. Los precios antiguos se usan como precios de referencia normales, con su fecha, sin rotularlos como vencidos. Los datos simulados se marcan por campo; no hay aviso general. T039 (dictado por voz) queda diferida. |
| 2026-11-23 | Chat comercial unificado con datos publicados (spec 043) | El Cotizador separado y la demo simulada se retiraron: un solo hilo atiende información, oferta, sedes y cotización referencial con 29 precios CO y paridad MX 98. El envío y el stock solo se confirman con evidencia vigente; la cotización versionada separa importes y admite total ausente. `/cotizador` y `/faq` redirigen al chat; la lectura autorizada de históricos y `POST /api/faq` se conservan. |
| 2026-11-23 | Enmienda para el agente con herramientas (spec 044): la abstención sin evidencia la garantiza el servidor y conviven los modos `routes` y `tools` | En el modo `tools` el modelo decide qué consultar antes de saber si hay evidencia, así que «sin contexto no se llama al LLM» deja de ser aplicable a la letra. Se conserva la garantía: una compuerta determinista sustituye la redacción por la abstención cuando ninguna consulta devolvió evidencia, y el primer paso declara siempre si consulta. `AGENT_MODE=routes` sigue por defecto hasta aprobar el informe comparativo; el router por reglas queda como atajo y respaldo. Proyección de la constitución: versión 2.6.0 |
| 2026-11-23 | Agente con herramientas entregado detrás de `AGENT_MODE=tools` (spec 044, #231 y siguientes) | DeepSeek encadena seis consultas deterministas con compuerta de evidencia, atajo sin decisión del modelo, respaldo sin LLM y un folio por cotización mostrada. `routes` sigue por defecto hasta que el owner apruebe el informe `specs/044-agente-tool-calling/evidencia.md` (89,5 % frente a 54,4 % de ítems resueltos; faltan SC-001, SC-005 y SC-006a). En el agente, `deepseek-flash` se usa sin razonamiento porque en ese modo la API rechaza `tool_choice="required"`. Proyección 2.6.1 |
| 2026-11-23 | `AGENT_MODE=tools` pasa a ser el valor por defecto del chat (spec 044, decisión 1 de `evidencia.md` §8); `routes` queda como interruptor de emergencia, atajo y respaldo | Decisión del owner con riesgos aceptados: el informe no dejó SC-001…SC-008 en verde (SC-001 con 2 casos, SC-005 con 1 dudoso, SC-006a con 1,48 s y SC-004 según la lectura), pero `tools` supera a `routes` en todas las garantías, resuelve 89,5 % frente a 54,4 % y elimina los hallazgos graves de `routes` en producción. Cambian `Settings`, `.env.example` (también `DEEPSEEK_MODEL=deepseek-flash`, el modelo evaluado), `compose.yml` y `compose.prod.yml`. El modo `tools` usa DeepSeek y solo arranca con `LLM_PROVIDER=deepseek` y DeepSeek completo: con `ollama` o `claude` el chat no envía turnos a DeepSeek aunque haya clave; si no arranca, los turnos siguen por `routes` con un aviso `agent.tools_mode_unavailable` y `/health` lo publica en el bloque `agent`. Observación de dos semanas desde el día en que producción registra `agent.runtime_ready` con `mode=tools` (prevista del 2026-11-23 al 2026-12-07): cero violaciones de material o datos sin fuente en trazas revisadas, respaldo < 2 % de los turnos y sin aumento de «No útil»; al cumplirse se abre T055 (retiro). Ver `specs/044-agente-tool-calling/evidencia.md` §10. Proyección de la constitución: versión 2.7.0 |
| 2026-11-23 | Canal WhatsApp por OpenClaw con un servidor MCP (`apps/mcp`) que expone el turno completo del chat como una sola herramienta, `consultar_carvajal` | Se reutiliza `ChatService` en modo `tools` en lugar de exponer las seis herramientas sueltas: así WhatsApp conserva la compuerta de evidencia, la abstención, el respaldo sin LLM, el tope y los folios, y responde igual que la web. El número del chat no se guarda: el `owner_key` es `wa_` + HMAC con `SESSION_SECRET`, con hilo y cupo propios. Las cotizaciones se leen de `quotes` por folio y se redactan como texto; el Markdown se convierte a formato de WhatsApp sin LLM. El servicio `mcp` va en el perfil `whatsapp` de `compose.prod.yml`, publicado solo en `127.0.0.1:8765`, con bearer `MCP_TOKEN` obligatorio y protección contra DNS rebinding; OpenClaw llega por un túnel SSH restringido a ese puerto. Ver [`docs/whatsapp-openclaw.md`](docs/whatsapp-openclaw.md). |
| 2026-11-24 | `deploy.sh` siembra y valida el aprovisionamiento comercial antes de migrar | Desde la 043, `/health` exige un catálogo comercial válido, pero el volumen `appdata` tapa el `/app/data` de la imagen y solo el perfil `bootstrap` sembraba los archivos nuevos. Siete despliegues seguidos fallaron con `catalog: invalid` (faltaba `data/catalog/empaques/referencias.csv`) y volvieron al tag anterior; el runbook pedía `deploy.sh` y luego el bootstrap, un orden imposible porque el rollback llega antes. Ahora `deploy.sh` copia solo los archivos ausentes de `/app/seed-data`, comprueba la versión y valida antes de migrar, sin reindexar. Si no valida, se detiene sin tocar la versión en marcha. |
| 2026-11-23 | Cotización en PDF y formato de WhatsApp para el canal MCP | El bloque de cotización de WhatsApp sale de una vista común (`apps/api/quote_view.py`) que lee el payload de `quotes` sin calcular nada, y ofrece el PDF. La palabra «PDF» devuelve, sin LLM pero con el mismo cupo diario, un enlace firmado (HMAC con `SESSION_SECRET` sobre folio, dueño y vencimiento, máximo 7 días y 5 minutos de tolerancia de reloj) a la última cotización del hilo activo en `GET /cotizaciones/{folio}.pdf`, que verifica la firma contra el `owner_key` guardado y responde 404 ante cualquier fallo. El PDF se genera al vuelo con fpdf2 (Python puro, compatible con el contenedor de solo lectura) con el logo de Carvajal y DM Sans (OFL) en TTF. Se activa con `PUBLIC_BASE_URL`. |
| 2026-11-24 | Fotos del catálogo para cotizaciones (PDF y WhatsApp) | Una foto por SKU, versionada en `data/catalog/empaques/imagenes/` con su procedencia en `imagenes.csv`, curada con `scripts/curar_imagenes_co.py` desde las fichas oficiales de Carvajal Empaques (`og:image`, respetando `robots.txt`). Las de terceros solo con `--incluir-terceros` y permiso del dueño. No se enlaza en vivo a sitios externos: el PDF incrusta la foto y WhatsApp enlaza `/catalogo/imagenes/{sku}.jpg`, que solo sirve SKU del manifiesto. Las fotos son opcionales: sin foto, la cotización sale igual. |
| 2026-11-24 | Enmienda A de la spec 045: capa institucional curada (`data/institucional/empresas.json`) | `empresas_del_grupo` necesita una lista estructurada que Gold no tiene. Se deriva solo del núcleo («Nuestros Sectores» de `quienes_somos.md`, las páginas de unidad y `home.md`), con cita literal y URL de `index.jsonl` por fila y alias citados (PROPAL → Carvajal Pulpa y Papel); una prueba verifica cada cita contra `data/gold/`. Leer `home.md` con expresiones era frágil y `buscar_corpus` no da una lista completa. El archivo viaja en la imagen y en la siembra (`.dockerignore`, `Dockerfile`, `compose.prod.yml`, `scripts/deploy.sh`); si falta, la herramienta devuelve `error` declarado. Proyección de la constitución: versión 2.8.0 |
| 2026-11-24 | Enmienda C de la spec 045: resumen en el estado del hilo, memoria del dueño por categorías en el Store y herramientas de memoria que escriben | Agente LangGraph con `PostgresSaver` y `PostgresStore`; el estado del hilo guarda un resumen acumulado (`conversation_summary`) que calcula en segundo plano un `SummaryWorker` acotado (cola de 32, idempotente, con su propia sesión y su propio Store) y que el grafo adopta en `recall` con relleno determinista del hueco. El Store guarda la memoria del dueño por categorías (`nombre`, `empresa`, `ciudad_habitual`, `formato_preferido`, `notas`), sin datos sensibles (filtro determinista por forma) y con caducidad de 180 días (`AGENT_MEMORY_DAYS`); el formato anterior se migra una vez. El modo `tools` dispone de consultas registradas en `toolagent/tools/registry.py` (20 al terminar la spec); solo `recordar_preferencia` y `olvidar_preferencia` escriben, en el momento, como excepción explícita al contrato de la 044 «herramientas sin efectos persistentes»: diferir la escritura al cierre del turno obligaba a superponer operaciones pendientes sin beneficio. `recordar_preferencia` solo guarda lo dicho en la pregunta del turno y `olvidar_preferencia` exige un verbo de olvido (defensa ante inyección indirecta); `_outcome` reduce sus argumentos a la categoría, así que trazas, monitoreo y evaluación nunca ven el valor. «Olvida todo» lo atiende el atajo en ambos modos, borra categorías, formato anterior y borradores de resumen del dueño y deja una lápida. La ciudad habitual solo se sugiere: `cotizar` y `buscar_sedes` nunca la usan sin que la persona la diga o la confirme. Proyección de la constitución: versión 2.8.0 |
| 2026-11-24 | Enmienda B de la spec 045: memoria y resumen como datos del usuario y procedencia `sistema` | Sin declarar su papel, la compuerta de evidencia trataría la memoria como evidencia o la bloquearía. La memoria del dueño y el resumen de la conversación aplican como contexto y estilo, nunca respaldan datos de Carvajal ni cambian las reglas; solo las palabras literales de la persona (sus preguntas y las `user_quotes` del resumen) respaldan lo que ella dijo, y las notas nunca respaldan texto. `fecha_actual` cita el reloj del servidor y el calendario de festivos con `source_type="sistema"`: no registrar fuente hacía que la compuerta abstuviera y marcarla como oficial era falso. La interfaz solo distingue `tercero`, así que `sistema` no cambia lo visible. Las 11 herramientas nuevas registran una fuente por dato; `preguntas_frecuentes` es la única que llama a un LLM y descarta toda entrada cuyo pasaje literal del núcleo no contenga sus cifras y nombres propios (solo ese pasaje respalda texto). Proyección de la constitución: versión 2.8.0 |
| 2026-11-24 | El LLM del chat usa DeepSeek por defecto (`LLM_PROVIDER=deepseek` en `Settings`, `compose.yml` y `compose.prod.yml`); Ollama queda solo para embeddings | Con `AGENT_MODE=tools` por defecto, el valor `ollama` dejaba el modo `tools` sin arrancar en cualquier despliegue que no fijara el proveedor a mano. `make up` ya no descarga el modelo de chat de Ollama salvo con `LLM_PROVIDER=ollama`. Sin `DEEPSEEK_API_KEY` la app no falla: registra `llm.provider_unconfigured` al arrancar, `/health` publica `missing_deepseek_api_key` en `agent` y el chat degrada a `routes`; la CLI histórica falla con un mensaje que nombra la variable. |
| 2026-11-24 | Webhook propio en FastAPI para mensajería (`POST /api/webhooks/whatsapp`) que reutiliza el turno del servidor MCP (`WhatsAppBridge`) | Cubre el requisito «Backend/API con webhooks y persistencia» sin un segundo camino de respuesta: mismo `owner_key` HMAC, formato de WhatsApp, cotizaciones en texto con la oferta del PDF (el pedido «PDF» devuelve el mismo enlace firmado que por MCP) y mensajes legibles ante fallos. Seguridad: apagado sin `WEBHOOK_SECRET` (404); HMAC-SHA256 de `<timestamp>.<cuerpo crudo>` en `X-Carvajal-Signature`, comparado en tiempo constante, con `X-Carvajal-Timestamp` en ±5 minutos (el timestamp va firmado para que la ventana impida repeticiones); cuerpo ≤ 16 KiB; allowlist E.164 `WEBHOOK_ALLOWED_NUMBERS` (vacía rechaza todo, 403 sin procesar; `*` es el modo abierto para un bot público: la firma sigue siendo obligatoria y el cupo es por número). `message_id` se convierte en el `client_turn_id` y se reserva de forma atómica en `webhook_deliveries` (migración 0011) antes de abrir hilo, cobrar cupo o llamar al LLM: un reintento con el primer intento en curso recibe 409 con `Retry-After` sin efectos, y uno posterior (o la misma petición firmada reenviada) reemite el turno guardado o la respuesta corta (incluido el enlace al PDF) sin cupo, hilo ni folios nuevos; un fallo (o no poder generar el PDF) libera la reserva. El número nunca se registra en claro y los errores de esquema no hacen eco. Ver [`docs/webhooks.md`](docs/webhooks.md). |
| 2026-11-24 | Enmienda D de la spec 046: gatekeeper del modo `tools` con Jev de TypeSafe opcional | El requisito de la entrega pide decidir entre herramienta estructurada, RAG o respuesta directa con reglas, LLM o el gatekeeper System One (Jev) si está disponible. El nodo `shortcut` decide primero por reglas (saludos, despedidas, «olvida todo», preferencias y seguimiento comercial, sin búsquedas ni servicios externos); si no deciden, Jev (pregunta Choice, `jev-1.14.0` fijado) clasifica el turno en `herramienta_estructurada`, `rag`, `mixta` o `respuesta_directa` y restringe sus consultas al grupo declarado en el registro (`route_group`); con confianza baja o desacuerdo con una señal estructurada fuerte de las reglas, cautela con las 20 consultas y el bloque `<cautela>`. Jev es opcional en tiempo de ejecución y se enciende de forma explícita (`JEV_ENABLED=false` por defecto aunque haya clave): sin él, reglas + LLM como en la 045. Cliente httpx con plazo total de 0,8 s, sin reintentos, tope diario por proceso y circuito. A TypeSafe solo viajan la pregunta redactada (filtro de la 045 y enmascarado de nombre, empresa y notas) y una línea de contexto; nunca identidad, memoria, resumen ni historial. Logs, traza, LangSmith y el libro de llamadas guardan solo decisión, probabilidades, latencia, modelo y tokens. El modo `routes` no usa Jev. Proyección de la constitución: versión 2.9.0 |
| 2026-11-24 | Enmienda E de la spec 046: compuerta de afirmaciones con Jev tras la compuerta determinista | En el camino con evidencia el texto del modelo salía sin filtro y una valoración sin fuente («la de 16 oz es la más usada para sopas») llegaba a la persona. Decisión del owner: después de la compuerta determinista, una pregunta Noul de Jev sobre la respuesta y los extractos mínimos de evidencia; con `p < 0,5` (`JEV_CLAIMS_THRESHOLD`) el texto se sustituye por el mismo mensaje neutro que usa la compuerta determinista (`_replacement()` o la abstención ajustada), que es lo que se emite, se persiste y se devuelve por WhatsApp. Una valoración presentada como hecho sin fuente cuenta como no respaldada; reformatear un dato (teléfono con «(601)») no. Con la compuerta activa (`JEV_CLAIMS_MODE=enforce`, por defecto cuando Jev está encendido) el camino con evidencia retiene el texto completo y muestra una línea de estado mientras se revisa; `shadow` juzga y registra sin sustituir y `off` devuelve la retención de 48 caracteres. Si Jev falla, vence o no queda plazo del turno, rige la compuerta determinista sola (*fail-open*). No se juzgan la respuesta directa, las abstenciones, los reemplazos, los turnos solo de memoria ni los textos que solo preguntan. A la compuerta no viaja la pregunta: la respuesta sin datos de la persona (conserva los del directorio), la evidencia mínima y los valores estructurados que la persona dio (`dicho_por_la_persona`). Proyección de la constitución: versión 2.9.0 |
| 2026-11-24 | Medición de la spec 046 y correcciones posteriores; Jev sigue apagado por defecto | Medición única con `jev-1.14.0` (116 de 120 llamadas, ≈ 0,005 USD): gatekeeper 20/20 (reglas 14/20, LLM 18/20), compuerta 10/10 detectadas y 0/6 falsas alarmas, v3 en modo `tools` 28/40 con Jev frente a 29/40 sin Jev, p95 por llamada 231 ms. Umbrales sin cambio (`JEV_ROUTER_MIN_CONFIDENCE=0.6`, `JEV_ROUTER_DIRECT_MIN_CONFIDENCE=0.8`, `JEV_CLAIMS_THRESHOLD=0.5`): la rejilla no separa y el 0,8 contuvo seguimientos comerciales que Jev mandaba a la respuesta directa. Score queda fuera de producción. Tras la medición, sin llamar a Jev: el adaptador acepta el Score real que devuelve la API (D1); el informe del gatekeeper conserva el umbral directo vigente si la rejilla no separa (D2); y un `rag` de Jev con señal comercial en la pregunta (producto del catálogo, precio, cantidad, capacidad o dónde comprar) se trata como `mixta` con todas las consultas y sin cautela (`producto_reglas`), porque el alcance `rag` quitaba `buscar_producto` en una compuesta (v3-003). Siguen documentados sin corregir: seguimientos comerciales clasificados como directa (los contiene la cautela), texto neutro solo en español, sustituciones de la compuerta que empobrecen respuestas completas y un dato sin fuente que pasa con p = 0,52. `.env.example` fija `JEV_MODEL=jev-1.14.0`, no `jev-latest`. Activar Jev en producción (T060) es decisión del owner, tras revisar el DPA de TypeSafe (T063). Ver [`specs/046-jev-router/evidencia.md`](specs/046-jev-router/evidencia.md) y research R16 |
| 2026-11-24 | Reserva del webhook con token de intento y efectos anotados (migración 0012); mismo E.164 canónico para el `owner_key` del webhook y del MCP | Un intento viejo que terminaba tras perder su reserva caducada podía liberar la del intento siguiente y dejar entrar un tercero; un fallo tras cobrar el cupo o abrir el hilo hacía que el reintento los repitiera; y el MCP con JID o sin `+` caía en otro hilo que el webhook. Ahora `claim` devuelve un token (`attempt`) que exigen `finish` y `release`; la liberación anota `quota_charged`/`thread_opened` y el reintento no los repite; el PDF sin URL pública es respuesta definitiva; y `owner_key_for` reduce número o JID a E.164 antes del HMAC (un chat MCP que antes enviaba JID o dígitos sin `+` cambia de hilo una vez). Ver [`docs/webhooks.md`](docs/webhooks.md). |
| 2026-11-24 | Respuestas por voz en WhatsApp: parámetro opcional `voz` (por defecto `false`) en `consultar_carvajal` y módulo puro `carvajal.speech` | OpenClaw transcribe las notas de voz y responde con TTS; el texto con citas, Markdown, URL y abreviaturas se leía mal. Con `voz=true` la herramienta sigue devolviendo un solo `str` (mismo esquema de salida): primero el texto hablado, generado sin LLM desde el mismo turno (sin citas, Markdown, emojis ni URL; fuentes en una frase; listas en frases; unidades, abreviaturas y fechas en palabras; importes con sus dígitos y «pesos»; folio deletreado por grupos; cotización en frases con las cifras del payload), y, si hay enlaces (PDF, foto, fuentes), una línea `LINKS_HEADER` y un `rótulo: url` por línea para que la pasarela los envíe como texto. Se eligió un texto con separador en lugar de un resultado estructurado para no cambiar el tipo ni el esquema de la herramienta: con `voz=false` la respuesta es idéntica byte a byte. Las reglas siguen las de la lectura en voz de la web (`limpiarTextoHablado`), que no se modifica. STT, TTS y sus llaves viven en el servidor de OpenClaw, nunca en este repositorio. Ver [`docs/whatsapp-openclaw.md`](docs/whatsapp-openclaw.md#mensajes-de-voz). |
| 2026-11-24 | Voz de WhatsApp más corta: cotización hablada de unos 15 s, folio por sus últimos cuatro caracteres y pedido «PDF» tolerante al STT | La cotización en frases duraba más de un minuto en nota de voz. `spoken_quote` dice solo presentaciones, unidades, producto, ciudad, total con IVA y envío, con las cifras exactas del payload (≤ 45 palabras con la oferta «Si quieres el detalle, respóndeme PDF y te lo mando por escrito.»); presentación con precio, subtotal, vendedor, fechas y folio quedan en el texto y en el PDF. Del folio solo se dice el final («tu cotización terminada en B 7 E 4», `speak_folio_tail`), también dentro de `to_speech`. `is_pdf_request` normaliza tildes, mayúsculas, puntuación, espacios entre letras y homoglifos cirílicos; con `voz=true` (`WhatsAppBridge.respond(voice=True)`) acepta además «pe de efe», «impedefe» o «MPDF» (solo p, d y f en orden con «e» opcionales, forma que no tiene ninguna palabra común). El mensaje sigue debiendo ser corto y de relleno: «¿el PDF trae el IVA?» va al agente. El webhook no marca voz y conserva el reconocimiento por escrito. |
| 2026-11-24 | Voz del chat web por el servidor con OpenAI (spec 047), opcional y con respaldo en el navegador | La voz del navegador varía entre navegadores y transcribe peor el español comercial. Con `VOICE_PROVIDER=openai` y `OPENAI_API_KEY` (por defecto `browser`), `POST /api/voz/transcribir` recibe el audio de `MediaRecorder` (`audio/webm`, `audio/ogg` o `audio/mp4`; tamaño por `Content-Length` antes de leer el cuerpo, firma del contenedor; no se guarda ni se registra) y lo transcribe con `gpt-4o-mini-transcribe` (`language=es`); la lectura usa `gpt-4o-mini-tts` (voz `coral`, estilo de asesora comercial colombiana). Adaptador propio sobre `httpx` (`adapters/speech/openai.py`, sin SDK) con el estilo de Jev: plazo por petición, sin reintentos, tope diario por proceso (`VOICE_MAX_CALLS_PER_DAY=500`, medianoche de Bogotá), circuito (3 fallos → 60 s; 401/403 o `insufficient_quota` → 15 min) y logs sin texto ni audio. Las rutas exigen CSRF (en `/transcribir`, solo por cabecera y antes de leer el cuerpo) y la identidad del chat, y tres límites con ventana deslizante y memoria acotada (LRU de 4096 claves): por sesión (`VOICE_RATE_LIMIT=20` cada `VOICE_RATE_WINDOW_S=600`), por IP (`VOICE_IP_RATE_LIMIT=60`) y global (`VOICE_GLOBAL_RATE_PER_MIN=30`), porque la cookie de visitante la elige el cliente y rotarla no debe agotar el tope diario. El servidor rechaza el audio cuya cabecera (Ogg, WebM, MP4) declara más de `VOICE_MAX_AUDIO_SECONDS` y el tope de bytes se deriva de la duración a 64 kbps. Un 4xx de OpenAI por la entrada es `rejected` (422): no abre el circuito de los demás ni gasta el tope; los motivos internos salen al navegador como `unavailable` y `calls_today`/`last_error` de `/health` solo los ve el operador. Cualquier no-200 (503 con `reason` si está apagada, sin clave, con tope o circuito abierto) hace que el navegador vuelva a Web Speech el resto de la sesión. El aviso previo al dictado cambia de texto y se acepta aparte. La CSP de las páginas del chat (`/chat` con sus POST y `/chat/new`, cuya página de error también ofrece «Escuchar») añade `media-src 'self' blob:`; el resto del sitio no cambia. `/health` publica el bloque `voice` sin secretos y no cambia el estado global. Ver [`docs/voz-web.md`](docs/voz-web.md). |
| 2026-11-24 | La síntesis de voz del servidor solo lee respuestas guardadas, por referencia (`message_id`), nunca texto libre | Un endpoint que sintetiza el texto que le envían sería un TTS público pagado por el proyecto. `POST /api/voz/hablar` recibe solo `message_id` (lo exponen `data-voz-mensaje` en el hilo y `message_id` en el evento `done` del stream), carga el mensaje con `SqlChatRepository.owned_message` (join con el hilo del dueño) y exige `role=assistant`; un id ajeno, del usuario o inexistente es 404 sin llamar a OpenAI. El texto se prepara en el servidor con `carvajal.speech.to_speech` (cotizaciones cortas, fuentes en una frase) y se recorta a `VOICE_TTS_MAX_CHARS` en un fin de frase. Se descartó un token firmado por mensaje: la referencia al mensaje guardado ya ata el audio a la conversación sin estado extra. El audio de una respuesta ya leída se cachea en memoria (32 entradas por proceso). |
| 2026-11-24 | `FORWARDED_ALLOW_IPS` detrás de nginx es la puerta de enlace de la red de Docker | La guía decía `127.0.0.1`, pero nginx en el host llega al puerto publicado y el contenedor ve la conexión desde la puerta de enlace del compose (p. ej. `172.18.0.1`): uvicorn ignoraba `X-Forwarded-Proto`, la app veía `http`, las cookies salían sin `Secure` y no había HSTS. `compose.prod.yml` ya pasa la variable (por defecto `127.0.0.1`, sin proxy); `docs/despliegue-ovh.md` §10 explica cómo obtener la IP y comprobar HSTS. El dictado (con o sin voz del servidor) exige HTTPS. |
| 2026-11-24 | Resumen hablado extractivo, sin LLM (spec 048): la voz lee un resumen y el detalle queda en pantalla o por escrito | En producción la lectura en voz (web «Escuchar», manos libres y notas de voz de WhatsApp) decía la respuesta entera (solo la cotización se acortaba). `carvajal.speech.spoken_summary` arma un resumen de unas 55 palabras (`SUMMARY_WORDS`, 15-20 s) solo con frases completas y cifras de la propia respuesta, ya pasadas por `to_speech`: primera frase del primer párrafo (siempre), pregunta final al cliente, primera lista en una frase (hasta cuatro ítems cortos por su cabeza, «Te muestro tres opciones en pantalla: A, B y C.» / «Te dejo por escrito…»; si no, cuántas hay; una lista numerada son «pasos»; no se nombran cabezas que corten una cifra, un rango o una enumeración, repetidas ni de tipo «clave: valor») y más frases mientras quepan; fuentes en una frase; «El detalle completo está en pantalla.» / «Te dejo el detalle por escrito.» si se omitió algo más que el detalle de la lista, haya lista o no. Una respuesta que ya cabe (fuentes incluidas) se lee completa. El presupuesto cuenta todo lo dicho: solo la primera frase, la frase corta de la lista, la de fuentes y el aviso entran aunque no quepan; ninguna cifra dicha es distinta de la de la respuesta (probado con respuestas generadas, por tokens numéricos completos y cabezas que son prefijos de su ítem). Con tarjetas de referencias sin cotización, la voz web las nombra o las cuenta (o avisa de la pantalla), porque no están en el texto. Se descartó un resumen con LLM: podría inventar o redondear cifras y añade latencia y coste a cada lectura. Web: `spoken_message` usa el canal `web`; el mismo texto llega al navegador en `data-voz-resumen` (hilo) y `voz_resumen` (evento `done`, de la respuesta guardada), y `construirSegmentos` lo prefiere para que la voz de respaldo diga lo mismo. WhatsApp (`voz=true`): si el resumen omitió algo o hay cotización, tras la línea `TEXT_HEADER` («--- TEXTO (enviar como mensaje escrito, no leer en voz alta) ---») va la respuesta de `voz=false` completa, que ya trae los enlaces (la sección `LINKS_HEADER` solo aparece con enlaces que no estén en ella); sin omisiones el contrato es el de antes. `parse_voice_reply` devuelve las tres partes y `split_voice_reply` descarta la escrita; ningún código de producción las usa: el consumidor real es el agente de OpenClaw, cuyo prompt vive en su servidor. Con `voz=true` la forma cambia (resumen + `TEXTO`), así que su sección «Mensajes de voz» se actualiza antes de desplegar esta versión del MCP o en el mismo momento; con el prompt anterior leería en voz alta la sección escrita. `normalize_transcript` pasa los miles de EE. UU. de las transcripciones a estilo colombiano («1,800» → «1.800», «2,500.50» → «2.500,50»; no toca «1,5», «10:30» ni «2, 3 y 4»; decisión explícita: «1,500 kg» también se toma como miles, porque así escribe el STT; un decimal dictado de tres cifras saldría mil veces mayor y el dictado web se revisa) en `/api/voz/transcribir` y en el mensaje de `consultar_carvajal` con `voz=true`. Ver [`docs/voz-web.md`](docs/voz-web.md#resumen-hablado) y [`docs/whatsapp-openclaw.md`](docs/whatsapp-openclaw.md#mensajes-de-voz). |
| 2026-11-24 | Rediseño 049 del chat web con el sistema «Asistente Carvajal» de Stitch: inicio con Carva, conversación en tarjetas, movimiento y confirmación plegable de la cotización | La interfaz se sentía básica y sin personalidad. Se adoptan los tokens del proyecto de Stitch (fondo mate `#F3F6F8`, bordes `#DCE5EA`, radios de 24 px, sombras en dos niveles, texto secundario AAA) en `app.css`; el resto es CSS y dos ganchos de JS sin contratos nuevos. Carva vive en SVG en línea (`_carva.html`) porque la CSP (`style-src 'self'`) no deja animar un SVG cargado como imagen. El inicio disuelve historial y bienvenida con `display: contents` para centrar el compositor entre el saludo y los temas, y al primer envío el compositor baja con FLIP (WAAPI, sin estilos en línea). La cotización emitida en el turno celebra y se minimiza sola a los 10 s (`chat-motion.js`): la cuenta se pausa al mirarla, enfocarla o escucharla, se anima la altura real con `grid-template-rows` y el foco y un aviso `role=status` acompañan el cambio; sin JavaScript queda abierta. Se descarta la View Transition del prototipo dentro del hilo desplazable porque sus capas ignoran el recorte del scroll. Los estados de voz se estilan solo por atributos que expone `chat-voice.js` (contrato con la rama de voz): `data-voz-grabando` con `--voz-progreso`, `-transcribiendo`, `-sintetizando`, `-respaldo` y `aria-pressed`/`aria-busy` por turno. No se usan el teléfono, el perfil ni la línea directa del diseño, que no existen en la app. |
| 2026-11-24 | Respuestas del chat web como notas de voz (spec 050): suenan solas solo si la persona habló, sin síntesis proactiva y sin cambiar de voz por un fallo de reproducción | En producción la lectura acababa en la voz del navegador: una sola llamada a `/api/voz/hablar` (17 s), un `play()` bloqueado por la política de autoplay (`NotAllowedError`, lejos del último gesto) y `servidorActivo.lectura = false` para toda la sesión. Ahora cada respuesta guardada trae una nota de voz (`.turno-nota`, creada por `chat-voice.js` tras `.burbuja` también para el historial; estado en `data-nota-estado` = `lista|cargando|sonando|pausada|bloqueada|error` y avance en `--nota-avance`, contrato con el rediseño 049 en `docs/voz-web.md`). Suena sola solo si la pregunta se dictó (borrador dictado enviado o manos libres) o con la lectura automática encendida; escrita, no se pide el audio hasta pulsar ▶. Un único `HTMLAudioElement` se desbloquea con 0,1 s de silencio (WAV por `blob:`, la CSP no admite `data:`) en el gesto de dictar o de enviar lo dictado, y lo reutilizan todas las notas; no se desbloquea al enviar texto sin lectura automática porque en iOS pausaría la música. Un autoplay bloqueado deja la nota `bloqueada` con el audio en caché (un toque la reproduce); un 404, 422, 429, la red o un audio que no decodifica dejan la nota en `error` con reintento. Ninguno cambia de voz ni apaga la del servidor. Solo un 503 o un 401/403 la dejan en espera 60 s (`data-voz-respaldo`), con la voz del navegador si existe o «La voz no está disponible ahora». Precarga al completar el turno solo cuando va a sonar, una síntesis en vuelo por mensaje, LRU de 6 audios, una sola URL `blob:` viva y aborto en `pagehide`. Se descartó sintetizar en el servidor al guardar cada respuesta: pagaría el TTS de respuestas que nadie escucha y gastaría el tope diario y el límite por sesión; el servidor no cambia. Ver [`docs/voz-web.md`](docs/voz-web.md#notas-de-voz-por-respuesta). |
| 2026-11-24 | Fotos de alimentos → empaque sugerido (spec 051): Jev clasifica, una tabla determinista elige en el catálogo y la respuesta no pasa por el LLM | El owner quiere que se pueda enviar la foto de un alimento y recibir el empaque apropiado, en la web y en WhatsApp, encendido en producción con aviso del servicio externo. Una prueba previa con 11 fotos de Wikimedia acertó el tipo en 11/11 (mediana 216 ms, ~2.900 tokens, ~0,00013 USD por foto). Una sola petición de Jev con la foto en `assets` hace cinco preguntas cerradas (Noul `es_comida`, Choice `tipo` de 11 opciones, `consistencia` y `temperatura`, Score `porcion`); ningún texto de la imagen llega a quien redacta. «¿Se derrama?» y Box se midieron y quedaron fuera. Umbrales: comida ≥ 0,5; tipo y temperatura con confianza ≥ 0,6 (`FOOD_PHOTO_MIN_CONFIDENCE`) o se pregunta; el texto que acompaña la foto fija la temperatura y el país por palabras clave. La tabla `KIND_RULES` da uso, familias, temperatura por defecto y cierre; la aptitud la decide `check_material` (la guía manda; contenido caliente exige `hot_content`), se descartan capacidades menores que la mitad de la porción y se eligen hasta tres, con variedad de materiales solo dentro del mismo nivel de aptitud. No se curó el catálogo: las referencias de Colombia sin temperatura se juzgan por su material. La respuesta (descripción, referencias con `ref.` y ficha, razón de la guía, tapa si se derrama, oferta de cotizar) y las tarjetas de `buscar_producto` se guardan como turno («📷 Foto de un alimento»), así el agente cotiza después. `ChatService.photo_stream` reemite un reintento sin clasificar ni cobrar y cobra el cupo solo si el clasificador respondió. Cliente de Jev propio (`FOOD_PHOTO_TIMEOUT_S=4`, `FOOD_PHOTO_MAX_CALLS_PER_DAY=500`, circuito, mismo libro), sin depender de `JEV_ENABLED`, porque encender Jev activaría también el gatekeeper de la 046. Web: `POST /chat/foto` (multipart, CSRF por cabecera antes de leer, ≤ 10 MB) responde el mismo SSE; botón y aviso solo con clasificador disponible, sin vista previa (la CSP no cambia). WhatsApp: `consultar_carvajal(foto=...)` recibe la referencia `media://inbound/<archivo>` que OpenClaw muestra al agente; el puente lee solo desde `FOOD_PHOTO_MEDIA_DIR` (montaje de solo lectura de los adjuntos, `O_NOFOLLOW`, nombre sin rutas) y OpenClaw apaga su propia interpretación de imágenes. Ver [`specs/051-fotos-alimentos/`](specs/051-fotos-alimentos/spec.md) y [`docs/whatsapp-openclaw.md`](docs/whatsapp-openclaw.md#fotos-de-alimentos-spec-051). |
| 2026-11-24 | Rediseño 051 de las tarjetas comerciales del chat con la pantalla «Asistente de Empaques» de Stitch | Las tarjetas de producto seguían con el estilo anterior al lado del chat rediseñado (049). `_productos.html` reordena la presentación sin tocar los datos: «Tu solicitud» en tarjetas con ícono y un botón «Cambiar» que deja la frase empezada en el compositor (reutiliza `data-chat-sugerencia`; `chat-stream.js` los muestra y sin JavaScript quedan ocultos); cada referencia con la foto del catálogo cuando existe (la ruta pública `/catalogo/imagenes/{sku}.jpg` que ya usaba WhatsApp, vía el filtro `foto_catalogo`) o el ícono de su familia, sus cifras en un recuadro y la ficha como botón; las sedes y las otras ciudades van después de las referencias. Se conservan los `data-campo` y clases que leen `chat-voice.js` y las pruebas. De paso se corrigen tres detalles: la fecha de emisión de la cotización se muestra en hora de Bogotá (filtro `fecha_local`; antes cortaba el ISO en UTC y de noche salía el día siguiente), la presentación concuerda en número («60 paquetes») y `base.html` apaga los estilos en línea de htmx (`includeIndicatorStyles: false`), que la CSP bloqueaba con errores en consola. |
| 2026-11-25 | Rediseño 052: modo de voz del chat (pantalla «Modo de Voz» de Stitch) y Corpus, ficha de documento y Configuración con el sistema de Stitch | El modo de voz se monta sobre la voz existente, sin cambiar su lógica: `chat-voice.js` solo suma dos marcas al contrato de `docs/voz-web.md`, `data-voz-leyendo` en `<html>` y `data-voz-hablado` en el formulario al enviar un dictado. Con ellas, `chat.html` muestra una escena decorativa (`aria-hidden`) sobre el compositor, con Carva que escucha (el SVG de Stitch, en línea por la CSP), transcribe o responde, y ondas animadas. La cabecera lleva un chip «Voz lista en español / Escuchando / Manos libres», y el turno dictado se marca «por voz». El botón «Más despacio» de Stitch queda fuera por decisión del owner. Corpus muestra las secciones legibles («Canal clientes»; el filtro sigue usando el slug), un resumen con chips de los filtros activos, un ícono por tipo de fuente y fichas compactas en móvil. La ficha del documento ordena los metadatos en fichas, con «Volver» como píldora. Configuración cambia el aviso con estilo de error por una barra de sesión del operador y tres pasos (carga → Gold → `carvajal sync --layer core`), y el selector de archivos sigue la marca. La página de FAQ no se rediseña: `/faq` redirige al chat desde que se retiró la generación pública. |
| 2026-11-24 | Negativa fija para temas fuera de alcance | Una opinión sobre famosos o un programa en Python pasaban el filtro de `no_query` (los nombres vienen en la pregunta y el código no trae cifras) y el agente los respondía. Ahora el prompt pide `sin_consulta(motivo="fuera_de_alcance")` para temas ajenos a Carvajal y, si el turno solo declaró eso, la respuesta es `OUT_OF_SCOPE_ANSWER` (fija, sin la redacción del modelo). Un turno que además consultó datos no se toca. |
| 2026-11-25 | Ampliar el catálogo de Colombia con las 98 referencias de México y su precio convertido a COP | Pedido del equipo, acordado con Alejandro según quien lo pidió, para uso académico: solo 29 de 102 referencias CO tenían precio, todas en Bogotá, y el Cotizador no podía responder en las otras nueve ciudades. Enmienda la regla de la spec 039 de no convertir desde México. `scripts/adaptar_catalogo_mx.py` copia las referencias como `CO-MX-<sku>` y convierte con 1 MXN = 182,3365 COP (TRM 3.592,03 del 2026-11-25 entre 19,7 MXN por USD del BCE), al peso. No se atribuyen a ningún distribuidor: procedencia `sistema`, sin sede ni existencias, IVA `no_indicado` (el precio de origen incluye el IVA de México), cotizadas solo en Bogotá. Un precio publicado siempre va antes. Por decisión del equipo, la cotización (chat, PDF y WhatsApp) las muestra como «precio de referencia del catálogo» sin nombrar el origen; el origen queda en `precios.csv`, en el manifiesto y en la fuente citada, y el agente lo explica solo si alguien lo pregunta. Las fotos salen de las fichas oficiales de México respetando `robots.txt`. `EXPECTED_COMMERCIAL_VERSION` pasa a 2026-11-25. |
| 2026-11-25 | Repartir los precios de referencia entre las ciudades y completar datos de las copias de México | Pedido del equipo: el precio de referencia solo en Bogotá dejaba sin cotización a las otras nueve ciudades. Cada copia `CO-MX-…` se cotiza en Bogotá y en 2 ciudades más, rotando dentro de cada familia para que todas las ciudades tengan todas las familias (294 precios de referencia). `fuentes/mx_atributos.json` completa en 15 copias la marca, la temperatura o el paquete con lo que declara la ficha de México; se omiten 5 datos que la ficha contradice (vasos de papel para bebida caliente «hasta 40 °C», cajas AxB que no suman). No se inventan datos: las referencias propias de Colombia sin paquete declarado siguen así (EDEXA no lo publica y el vaso térmico de 6 oz declara 20 por paquete y 1.125 por corrugado, que no cuadra), y la capacidad en onzas no aplica a platos, bandejas ni cubiertos. |
| 2026-11-25 | El material que pide la persona filtra la cotización | Reporte por WhatsApp: «1000 vasos de papel para bebida caliente en Bogotá» cotizaba el vaso rígido de PP de 9 oz de EDEXA y lo agregaba al borrador. El motor no filtraba por material: «papel» solo activaba `prefers_renewable`, y el orden por aptitud y por precio publicado lo deshacía. Ahora `QuoteRequest.material` y el argumento `material` de `cotizar` (papel, pulpa, icopor, plastico, pet, pp, ps) filtran las referencias. Si el modelo no lo pasa, la herramienta lo toma de la pregunta, como el microondas. Si no hay referencias de ese material, la respuesta lo dice y no cotiza otro (`material_sin_resultados`); la prueba de reglas duras del icopor para caliente pasa a exigir eso. Con `prefers_renewable`, las de papel o pulpa van antes que el precio publicado. Una referencia nombrada no se filtra. |
| 2026-11-25 | El despliegue sincroniza `catalog/` desde la imagen, con respaldo y restauración | Producción cotizaba «sin precio» los vasos de papel en Bogotá y Cali. `deploy.sh` solo sembraba los archivos ausentes, y las PRs #256 a #258 comparten la versión `2026-11-25`: el volumen conservó el `precios.csv` de la #256 (sin ciudad) bajo el código de la #258, que exige ciudad, y la validación pasó. Ahora `catalog/`, dato curado en el repositorio, se sincroniza desde la imagen: siembra lo que falta y reemplaza lo que difiere, con respaldo en `data/respaldos/catalogo-<fecha>-<tag>/`. Si la versión o la validación fallan, se restaura en el mismo paso; si el despliegue falla después, `rollback` lo restaura antes de volver a la imagen anterior. `commercial/` e `institucional/` siguen sembrando solo lo ausente, porque la evidencia de disponibilidad se registra en el servidor. El perfil `bootstrap` no cambia. |
| 2026-11-25 | `/new` y «olvida todo» reinician de verdad el pedido en WhatsApp | Pruebas por WhatsApp: tras `/new` u «olvida todo», «cotiza 100 vasos en Bogotá» seguía cotizando el vaso de papel de 8 oz del pedido anterior. `/new` solo abría hilo si el agente de OpenClaw pasaba `nueva=true`, y «olvida todo» lo atrapaba el flujo de borradores de la spec 054: cancelaba el borrador sin pasar por el grafo, donde vive el `commercial_context`. Ahora el puente reconoce `/new`, `/nuevo`, `/reset`, «nueva conversación» y «empecemos de nuevo» (solo si son todo el mensaje) y abre el hilo él mismo. Cuando el flujo cancela todo, el servicio llama a `ToolAgentRunner.clear_commercial_context`. «Olvida todo lo que sabes de mí», que sí pasa por el grafo, también vacía el `commercial_context`. |
| 2026-11-25 | Las cotizaciones de WhatsApp vuelven a llevar la foto del producto | Desde la spec 054 la cotización por WhatsApp sale por las plantillas del flujo (`_render_flow`), que no añadían la foto que traía `whatsapp_quote` («📷 Foto: …»). Ahora `render.Message.photos` lleva los SKU que presenta el mensaje (la recomendada de unas opciones y lo recién agregado), el payload del flujo los pasa como `photos` y el puente añade hasta tres fotos del catálogo, por escrito y como enlace aparte de la nota de voz. El resumen y el registro no llevan foto: el PDF ya la incrusta. |
| 2026-11-25 | PDF de varios productos con foto y fuente por producto, y sin abstención delante de la plantilla | Prueba por WhatsApp: el PDF de una cotización de dos productos no traía fotos y citaba la fuente del primero para ambos (el vaso de referencia y el portacomidas de EDEXA). La tabla de productos ahora lleva una miniatura por fila (`line_photos`, SKU → foto del catálogo) y la sección «Vendedor y fuente de cada producto» lista la fuente de cada línea con su fecha y enlace. Además, «No tengo un dato verificado…» salía antes de la plantilla del flujo cuando la compuerta descartaba el comentario del modelo. Si el texto del agente es solo una abstención fija y hay plantilla, el servicio reemplaza la burbuja por la plantilla (evento `fallback`), y el puente trata `fallback` como reemplazo, igual que la web. |
| 2026-11-25 | Fotos oficiales para las referencias propias de Colombia | De 102 referencias propias, solo 19 tenían foto (las que tenían precio y ficha oficial). `scripts/curar_imagenes_co.py --todas` baja la foto de las fichas oficiales de `colombia.carvajalempaques.com`, respetando `robots.txt`: 77 fotos nuevas, revisadas a la vista. Las referencias que solo publica EDEXA toman la foto de la ficha oficial de su misma línea (`LINE_PHOTOS`) solo si es el mismo producto: los contenedores térmicos con tapa de 8, 12 y 16 oz y el vaso rígido traslúcido de 7 oz. Seis quedan sin foto porque no hay ficha oficial equivalente y no se usan fotos de terceros sin permiso. No cambian referencias, precios ni manifiesto. |
| 2026-11-26 | Foto → cotización sin cantidad heredada, con precio primero y con la fuente de sedes bien rotulada | Prueba por WhatsApp con la foto de unas empanadas. (1) `cotizar` reutilizaba la cantidad del contexto aunque se nombrara otra referencia: la elegida de la foto se cotizó con 2.000 unidades que la persona nunca dijo. Con otra referencia nombrada, la cantidad ya no se hereda y se pregunta; la ciudad sí se conserva. Un seguimiento sin referencia nombrada (familia y capacidad), con el mismo código en `sku` o `nombre`, o con un nombre que el catálogo resuelve exactamente a la referencia anterior (acotado por su familia y capacidad) sigue reutilizándola; un nombre que no se resuelve a ella pregunta la cantidad. (2) `recommend` no miraba el precio y la recomendada no tenía precio en ninguna ciudad. Dentro de la misma aptitud, familia y escalón de tamaño para la porción (entre ~0,7 y ~1,4 veces lo esperado), ahora van primero las referencias con precio (publicado o de referencia); la aptitud y el tamaño siguen mandando, así que un 8 oz con precio no pasa por delante de un 32 oz cuando se esperan 32. (3) WhatsApp listaba una sola vez las fuentes que comparten URL, y las sedes del directorio tomaban el nombre de la primera (EDEXA citada como «Sede … Sideral»). Lo resolvió la selección de fuentes por cita de #273: cada sede conserva su número y su nombre; esta PR solo deja la prueba de regresión. Queda sin cambiar el criterio de Jev que describe las frituras con «empanadas», por eso una empanada horneada se lee como fritura. |
| 2026-11-25 | Workspace del agente «ventas» de OpenClaw versionado en `openclaw/workspace/ventas/` | En la VPS de OpenClaw solo `AGENTS.md` tenía contenido: `SOUL.md`, `IDENTITY.md` y `USER.md` eran las plantillas, y no había `MEMORY.md` ni bitácora diaria. El repositorio pasa a ser la fuente de verdad de esos archivos (persona Carva, límites y defensa ante instrucciones dentro de los mensajes, directivas para todos los clientes, decisiones fechadas y `memory/AAAA-MM-DD.md`). No se versionan los resúmenes del hook `session-memory`, porque traen números y mensajes de clientes, ni la configuración ni las claves del gateway. El despliegue es copiar los archivos con respaldo; el workspace se lee al iniciar cada sesión, sin reiniciar el gateway. |
| 2026-11-25 | De la foto a la cotización (spec 053): recomendada + 4 alternativas, elección por reglas, propuesta sin folio hasta el «sí» y miniatura de la foto | El owner quiere ver la foto como en WhatsApp, elegir entre una recomendada y hasta cuatro alternativas (cuadrícula de 2×2 en la web) «por voz o texto» y seguir a la cotización con confirmación en **todas** las cotizaciones. La elección (`carvajal.agent.followup.resolve_choice`) y el «sí» (`is_confirmation`) se resuelven sin el modelo, porque deciden qué se cotiza y si algo se registra: los números y ordinales nombran alternativas («la primera» = alternativa 1) y los deícticos («esa», «dame esa») la recomendada; una pregunta, una cantidad («2 cajas») o un cambio («la 2 pero en 12 oz») no eligen. El agente recibe la elección resuelta a su SKU, pero el hilo guarda lo que escribió la persona. `cotizar` sigue calculando igual; el servicio marca la cotización como `status: "propuesta"` (sin folio ni fila) y añade «¿Confirmo la cotización?» si el texto no lo pregunta. El «sí» a la propuesta del turno inmediatamente anterior la registra sin LLM, con folio derivado de esa propuesta (confirmar dos veces no emite otro); una propuesta vencida se recalcula. WhatsApp muestra la propuesta sin folio ni PDF. La miniatura (JPEG de 320 px sin metadatos) viaja como `data:` en las fuentes del mensaje de la persona; la vista previa en el compositor y en la burbuja usa `blob:`, que la CSP del chat admite en `img-src`. Diseño de la tarjeta de foto y del estado «Mirando tu foto…» en Stitch («Flujo Foto a Cotización»). Ver [`specs/053-foto-a-cotizacion/`](specs/053-foto-a-cotizacion/spec.md). |
| 2026-11-25 | Flujos de cotización (spec 054): borrador por conversación con varias líneas, respuestas cortas por reglas y Jev, plantillas únicas para web, WhatsApp y voz, y un folio por cotización | El owner pidió ocho flujos (tendero directo, foto, comparar cantidades, recomendada, alternativa, solo mirar, varios productos con total parcial y cambio de producto) que terminen siempre en resumen, «¿La confirmo?», folio y PDF, por voz o texto. Cada conversación tiene un borrador (`quote_drafts`, migración 0013, siete días) con las líneas sin folio, lo último que se mostró (recomendada + hasta 4 alternativas o comparativo de hasta 4 cantidades) y la pregunta pendiente (`awaiting`); se guarda en la misma transacción del turno. `carvajal.quotation.flow` atiende sin LLM las respuestas a esa pregunta con las reglas de `agent/followup.py` («la 2», «con mil», «eso es todo», «no, confírmala», «cancélalos», «quita los vasos», «retomar», «lo pienso», «está caro») y, solo si ninguna decide, con Jev (Choice de 8 clases, umbral 0,7, cliente propio que solo exige la clave como las fotos, `QUOTE_FLOW_JEV_*`); a Jev solo viajan la pregunta fija del asistente y la respuesta corta. Las cifras las escriben plantillas (`quotation/render.py`) y no el modelo: el prompt le pide una frase sin cifras y el servicio agrega el bloque; WhatsApp filtra lo que el agente repita (`quote_remarks`) y usa la versión hablada de la plantilla. Al confirmar, todas las líneas pasan a una sola fila de `quotes` con `lines` y un folio (el PDF trae la tabla de productos); en WhatsApp el PDF se adjunta solo y «sí, mándame el PDF» confirma la pendiente. Decisiones del owner aplicadas: recomendada = la más apta para el uso y, entre iguales, la más económica (`MAX_ALTERNATIVES` 2 → 4); producto inequívoco (SKU, nombre o sin alternativas) se agrega directo; una ciudad por cotización; envío solo en el resumen, uno por vendedor; 10 líneas; eventos anónimos del embudo en los logs. Preguntas de otro tema a mitad del flujo: se responden como siempre y se agrega «↩️ Seguimos con tu cotización…» con la pregunta pendiente en los dos primeros turnos. El rechazo por precio con insulto («muy caro, ladrones») responde la frase fija que pidió el owner. Se retiraron la regla «la propuesta solo vale en el turno siguiente», `PROPOSAL_QUESTION`, las propuestas del `done` (ahora `flow`), `_issue_quote(s)`, los alias de la 044 del motor y la extracción por reglas de `quotation/memory.py`. Ver [`specs/054-flujos-cotizacion/`](specs/054-flujos-cotizacion/spec.md). Proyección de la constitución: versión 2.10.0 |
| 2026-11-25 | Chat web: tarjetas solo de las opciones que nombra la respuesta, audio solo si la persona habló, sin «Tu solicitud» y foto de la tarjeta en su propia fila centrada | Pedido del owner: con «necesito 2000 platos desechables» el texto presentaba 4 opciones y salían 8 tarjetas, una nota de voz en una pregunta escrita y la sección «Tu solicitud». **Tarjetas:** `buscar_producto` sigue entregando hasta 8 opciones al modelo (`MAX_TOOL_OPTIONS`, para que no niegue referencias que existen), pero `ChatService` filtra la carga con `presented_options` (`apps/api/chat/products.py`) cuando el texto ya está completo y antes de guardarla, pintarla y enviarla en `done`: se queda solo con las opciones que la respuesta nombra por SKU o por su nombre exacto (sin tildes ni markdown, por palabra completa). Entre gemelas de igual nombre quedan las que traen su SKU y, de las demás, tantas como líneas las nombren aparte. Si no nombra ninguna, quedan las 3 primeras (`commercial_flow.MAX_OPTIONS`). Se presentan **como mucho 4 opciones** (`MAX_PRESENTED_OPTIONS`, como las alternativas de una cotización): lo piden `AGENT_SYSTEM` y el mensaje de `buscar_producto`, y el filtro recorta las tarjetas a las 4 primeras que nombra el texto, en su orden. Probado en local con DeepSeek: platos, vasos y contenedores salen con 4 opciones en el texto y las mismas 4 tarjetas. No se toca la ficha ni la recomendación de una foto. `AGENT_SYSTEM` pide nombrar cada opción tal como la trae la herramienta, con «(SKU <código>)», formato que la voz del servidor y la del navegador omiten. Se acepta que una opción parafraseada se quede sin tarjeta. El modo `routes` no cambia: ya da 3 al modelo y 3 tarjetas. **Voz:** se enmienda la spec 050 y el FR-006 de la spec 040. Una respuesta trae nota de voz (o la franja «Escuchar» con la voz del navegador) solo si la pregunta se dictó o llegó en manos libres. El formulario manda `voz=1` y la pregunta se guarda con la marca `{"result_type": "voice"}` en `sources`, sin migración, como la marca de foto; el historial solo crea la nota tras una pregunta marcada. Se retira la casilla «Leer respuestas en voz alta» (`autoLectura`). Las preguntas escritas no tienen audio, tampoco la foto con el pie dictado (`/chat/foto` no guarda la marca); la respuesta cortada a una pregunta hablada conserva la marca. **Tarjetas, presentación:** se retira «Tu solicitud» (bloque, CSS y el «Cambiar» de `chat-stream.js`; «Elegir» y los botones del flujo siguen con `data-chat-enviar`). La foto ocupa su propia fila arriba del cuerpo, centrada (`--visual-ancho`), en escritorio y móvil. Ver [`docs/voz-web.md`](docs/voz-web.md). |
| 2026-11-25 | Los agentes fusionan sus PRs en `main` con `Calidad` en verde, sin aprobación | Decisión del owner: esperar una aprobación humana frenaba las entregas de la semana del curso. El ruleset de `main` pasa a 0 aprobaciones y deja de exigir la rama al día, el último push aprobado y los hilos resueltos; conserva el check `Calidad`, la prohibición de borrar `main` y la de force push. El agente fusiona con squash en cuanto `Calidad` está en verde, nunca con `--admin` ni con el check en rojo o pendiente, y sigue sin aprobar PRs ajenas. Como fusionar en `main` despliega a producción, solo se fusiona trabajo verificado con `make check`. Cambian `AGENTS.md`, la skill `abrir-pr`, la plantilla de PR, `deploy.yml` (comentario) y `docs/despliegue-ovh.md`. Proyección de la constitución: versión 2.11.0 |
| 2026-11-25 | La rama al día con `main` vuelve a ser obligatoria para fusionar | Decisión del owner, el mismo día en que se quitó la aprobación: sin revisión humana, `Calidad` debe correr sobre el código que de verdad queda en `main`; una rama atrasada podía pasar en verde y romper al combinarse. El ruleset vuelve a exigir checks estrictos (`strict_required_status_checks_policy`); el agente actualiza la rama (`gh pr update-branch`), espera `Calidad` y fusiona. Cambian `AGENTS.md`, la skill `abrir-pr` y la plantilla de PR. Proyección de la constitución: versión 2.11.1 |
| 2026-11-25 | Ensayo general e2e automatizado (`tests/e2e/`, `make ensayo` y `make ensayo-completo`, runbook en [`docs/ensayo.md`](docs/ensayo.md)) contra local o producción | Pedido del owner para la entrega: comprobar app, bot, gateway, `security audit`, repo y CI en verde, la memoria entre dos conversaciones, un mensaje desde un número fuera de la allowlist, los flujos definidos (specs 053 y 054) y la conversación que arranca con una foto, en web y en WhatsApp. Las pruebas hablan con lo **desplegado**, no con el checkout: HTTP como el navegador (`/chat/stream`, `/chat/foto`), MCP como OpenClaw (`consultar_carvajal`, en producción por un túnel SSH al loopback) y SSH de solo lectura al servidor y a la VM de OpenClaw (nunca `config set`, `--fix`, reinicios ni `compose up`). El `security audit` se compara con [`openclaw/auditoria-base.json`](openclaw/auditoria-base.json) (2 críticos aceptados por el grupo abierto); un crítico nuevo hace fallar el ensayo, y el comparador tiene pruebas unitarias en `make check`. Todo el paquete lleva `e2e` y `ensayo`, así que la CI no lo corre; lo que gasta tokens o escribe en la base lleva `gasto`. Los datos de prueba van bajo alias `e2e-<run>-…` y visitantes propios, y los secretos leídos del servidor nunca se imprimen (`Secreto`). En producción `ENSAYO_COTIZAR=una` deja un solo folio por canal. El DM real desde otro número se envía por WhatsApp Web en el Firefox del owner, con un cliente W3C mínimo sobre `geckodriver --connect-existing` (sin Selenium). `compose.yml` pasa ahora Jev y fotos como producción y suma el servicio `mcp` (perfil `whatsapp`, `make up PERFILES="full whatsapp"`), para ensayar WhatsApp en local. |
| 2026-11-25 | La rama al día es obligatoria también cuando GitHub no la impone | La #264 se fusionó atrasada respecto a `main`: el agente corre con la cuenta `almeneses`, que está en el bypass del ruleset, y GitHub acepta la fusión sin avisar. El owner decidió conservar el bypass y hacer la regla obligatoria por proceso: el agente fusiona solo si `mergeStateStatus` es `CLEAN`; con `BEHIND` actualiza la rama (`gh pr update-branch`), espera `Calidad` y vuelve a comprobar. Cambian `AGENTS.md` y la skill `abrir-pr` (paso 8 con la comprobación antes del merge). Proyección de la constitución: versión 2.11.2 |
| 2026-11-26 | Arreglos de los hallazgos del ensayo general: PDF en la web, memoria de la presentación, familia del producto en `cotizar` y «Cambiar algo» | El ensayo e2e (PR #265) encontró cuatro defectos en producción. **PDF en la web:** al registrar, la web decía «📄 Te envío el PDF.» sin entregarlo. Ahora el texto y la tarjeta enlazan `/cotizaciones/{folio}.pdf`; sin `?t=` la ruta solo entrega el PDF a su dueño (la identidad del visitante, comparada en tiempo constante, 404 igual en cualquier fallo), el texto guardado no cambia (el enlace se arma al enviar y al pintar el historial) y la voz no lee la URL. WhatsApp sigue con su enlace firmado. **Memoria:** una presentación («soy Ana… recuérdalo») podía tomar el atajo `gatekeeper_direct` si Jev la leía como respuesta directa y no se guardaba nada; `declares_memory` (reglas, sin Jev) la manda al agente con la regla `memory_write`, y «hola» sigue en el atajo. **Familia en `cotizar`:** una llamada sin `family`, `sku` ni `nombre` reutilizaba el producto del contexto aunque la persona nombrara otro (se cotizaba un portacomidas por un vaso). Ahora, si la pregunta nombra otras familias y no la del contexto, `cotizar` pide la familia (`needs_input`, sin cotizar ni tocar el contexto); sin contexto solo la completa cuando se nombra una sola. **«Cambiar algo»:** el botón y las peticiones de cambio en el resumen o el carrito se resuelven por reglas, sin Jev: muestran el carrito editable o pasan al agente con el borrador en `algo_mas`. Un cierre con un cambio («eso es todo, pero cambia la 2») nunca registra, y pedir otra cotización o cambiar una cantidad no borra líneas. Nada se registra sin el «sí» (SC-002 de la spec 054). |
| 2026-11-26 | Segundo ensayo contra producción: compositor con foto, historial con listas, envío solo en el resumen, comparativo con las unidades compradas y voz en el ensayo | `make ensayo-completo` pasó entero salvo `WEBHOOK_ALLOWED_NUMBERS=*` (configuración), pero la revisión visual con viewports reales (WebDriver BiDi `browsingContext.setViewport`) y la lectura de las respuestas encontraron defectos. **Compositor:** al adjuntar una foto, el aviso de TypeSafe caía en una columna `auto` de la rejilla (`display: contents`) y en el teléfono sacaba «Enviar» de la pantalla (compositor de 1.067 px); la foto, su aviso y «Suelta la foto aquí» van a lo ancho, el selector `sr-solo` ya no desborda, la «×» no se pinta como botón primario y la ficha se desplaza a la vista. **Espacio del hilo:** «Opciones de voz» y «Foto» comparten fila, el chip «Voz lista» en reposo no ocupa fila en el teléfono y en pantallas bajas logo y pestañas van en una fila (375×812: el hilo pasa de ~330 a ~404 px; 812×375: «Enviar» vuelve a verse). **Historial:** `render_answer` usa `nl2br` y separa la lista pegada al título, como el stream; al recargar, alternativas y carrito salían en un párrafo. **Envío:** por la spec 054 (decisión 4) el envío va solo en el resumen: `cotizar` ya no le pasa al modelo «Falta saber si va con domicilio» (preguntaba por la entrega junto al «¿Algo más?») y la nota del motor dice «20 paquetes de 50 unidades a 2.653 COP por paquete. Subtotal: … sin envío» en vez de «20 paquete … por 2.653 COP … El total queda pendiente». **Comparativo:** texto y voz dicen las unidades que se compran cuando las cajas sobran («500 salen en 166.644 pesos, porque se compran 600 unidades»). **Ensayo:** `tests/e2e/test_voz.py` prueba dictado y lectura de ida y vuelta en la web y la nota de voz por MCP. |
| 2026-11-26 | Tercer ensayo: correcciones de la exploración de escenarios (fuentes de WhatsApp, reemplazos del verificador, carrito, «eso es todo», alcance del atajo directo, candidatas por familia y `/corpus`) | `make ensayo-completo` pasó (60, solo falla `WEBHOOK_ALLOWED_NUMBERS=*`), pero 28 guiones por WhatsApp y web leídos uno a uno mostraron fallos. **Fuentes en WhatsApp:** se listaban las cinco primeras sin repetir URL, así que la [2] de la segunda sede (misma URL del directorio) y citas como [7] u [8] quedaban sin fuente; ahora se listan las que cita el texto, cada una con su número, y una cita sin fuente se quita. **Verificador de afirmaciones (Jev, `enforce`):** sustituía ~24 % de las respuestas juzgadas por «Para seguir necesito un dato más…» o «No tengo un dato verificado…»; el reemplazo ahora usa los datos de las herramientas: las `candidatas` de una referencia ambigua, «No tengo precios ni sedes del directorio en esa ciudad» (no «me falta la ciudad») y la cantidad si `cotizar` recibió 0. **Carrito:** «¿qué tengo en la cotización?», «¿cuánto llevo?» muestran el borrador (o que está vacío) sin el modelo. **«Eso es todo» ante «¿Cuál te cotizo?»** agregaba la recomendada («eso» = «esa»); ahora va al resumen o se despide. **Alcance:** el atajo directo respondía «Sí, te ayudo» a una tarea de cálculo; `DIRECT_SYSTEM` limita los temas a Carvajal y sus empaques. **Candidatas:** «vaso Wau 7 oz» ofrecía una tapa; si el nombre dice una sola familia, solo esa. Además «Es 1 producto» en la voz y el `caption` oculto de `/corpus` ya no da scroll horizontal. El ensayo suma dos pruebas: cada cita de WhatsApp tiene su fuente y una petición ajena no se atiende. |

## 10. Notas y tareas pendientes

- [x] Extraer el texto de PDF (informes, memorias anuales) en la carga local de archivos (spec 035, entregada el 2026-11-16).
- [ ] Evaluar OCR para PDF escaneados sin capa de texto.
- [ ] Rastrear el sitio de forma periódica (cron/crawler) sobre `carvajal capture` y `carvajal sync`; la spec 037 deja los contratos de identidad y sincronización.
- [x] Ejecutar `carvajal sync --adopt` y `carvajal sync --prune` en el VPS (spec 037, T056). El owner confirmó la ejecución; ver [registro de adopción](specs/037-corpus-incremental/adopcion-vps.md).
- [ ] Recuperar la evidencia de `/health` posterior a la poda sin repetirla (spec 037, T075).
- [ ] Añadir transcripciones de vídeo (`yt-dlp` / `youtube-transcript-api`).
- [ ] Enriquecer el *front matter* con `entidades` y `especialidades` detectadas.
- [ ] Evaluar *deduplicación* de *boilerplate* entre documentos por hash de bloque.
- [ ] Definir el contrato de *chunking* con el proyecto consumidor.
- [x] Ejecutar `make eval` con credenciales y anexar los resultados al informe.
- [x] Importar el corpus externo como capa extendida con manifiesto y deduplicación.
- [ ] Incorporar PDF prioritarios del corpus externo (estados financieros, informes anuales).
- [ ] Registrar decisiones de arquitectura (ADRs) en `docs/decisions/`. <!-- 2026-11-08: creado el índice y el ADR-0001 (propuesto) -->
- [ ] Aceptar o rechazar el ADR-0001 y, si se acepta, ejecutar la fase 0 (extraer dominio y casos de uso).

## 11. Configuración local (no trackear)

> Configuración específica de la máquina local. **NO incluir en commits.**

Dentro de la carpeta de proyectos, `~/.gitconfig` aplica:

```ini
[includeIf "gitdir:C:/Users/marko/OneDrive/Documentos/Marko/UAO/Semestre 3/TAIAML/carvajal/"]
    path = C:/Users/marko/.gitconfig-personal
```

`~/.gitconfig-personal`:

```ini
[user]
    name = Marko David Garcia
    email = markodavidgm@gmail.com
[core]
    sshCommand = ssh -i ~/.ssh/id_ed25519 -o IdentitiesOnly=yes
```

> Efecto: todo repositorio bajo la carpeta personal firma con la identidad personal y
> usa la llave `id_ed25519`, sin flags por proyecto.
