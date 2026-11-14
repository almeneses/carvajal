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
- Ofrecer una CLI y un Makefile como interfaz única del proyecto.

### Fuera del alcance (por ahora)

- Rastrear el `sitemap.xml` completo del dominio (podcast, noticias, especialistas).
- Vídeos, PDFs y transcripciones.
- Páginas tras autenticación o cualquier contenido no público.
- Evaluación automatizada y benchmarking del asistente (fuera de la interfaz de pruebas).

### Fases

| Fase | Estado | Entregable |
|------|--------|------------|
| 1. Captura inicial (landing + secciones) | ✅ Hecha | 15 documentos limpios en `data/gold/` (~64.5 KB) |
| 2. Ampliación de semillas | ✅ Hecha | 6 fuentes de terceros con indicadores financieros |
| 3. Enriquecimiento (PDFs, vídeo) | ⬜ Pendiente | Nuevas semillas y extractores |
| 4. Chunking y embeddings | ✅ Hecha | Chunking 1000/200 + FAISS con embeddings Ollama |
| 5. Asistente RAG (LLM + prompt + UI) | 🚧 En curso | LangChain + FAISS + Ollama/DeepSeek/Claude + Streamlit |
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
| **Interfaz de pruebas** | Streamlit |
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
| Vectores | Qdrant (M2, índice evaluado); pgvector queda para el prototipo |
| Autenticación | Authlib (OIDC) + sesiones de servidor en Postgres |
| Streaming | SSE (`hx-ext="sse"`) |
| LLM | Puerto `LLMProvider`; LangChain como adaptador |
| Ingesta | CLI/cron (la web no captura) |

> ⚠️ **Implementación parcial (2026-11-11)**: W2.1 (índice pgvector), W2.2
> (adaptadores de LLM), W2.3 (caso de uso `AskQuestion`), W2.4 (shell FastAPI) y
> W2.5 (layout y tema) están en revisión. El resto del stack sigue vigente hasta
> que el ADR pase a `aceptado` y la migración avance fase por fase.

## 4. Estructura del repositorio

```text
carvajal/
├── apps/
│   ├── api/                   # API FastAPI: /live, /health, /api, portada y pantallas
│   │   ├── main.py            # create_app, CORS, /live y /health
│   │   ├── agent_runtime.py   # checkpointer, Store, Qdrant y grafo compilado
│   │   ├── runtime.py         # templates, sesiones y clientes diferidos
│   │   ├── views/             # routers: corpus, documentos, chat, stream y api
│   │   ├── chat/              # identidad, servicio, adaptador del agente y streaming
│   │   ├── static/            # CSS y JS del chat
│   │   └── templates/         # Jinja2: base, portada, pantallas y parciales
│   └── web/                   # frontend propio del M2 (:8001): shell SPA con
│                              # pestañas Asistente · Corpus · Documento, sin RAG
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
│           │   ├── errors.py    # errores de aplicación (LLMError)
│           │   └── ports.py     # repositorios, vector store, LLM, embedder, fetcher
│           ├── agent/         # router, herramienta, memoria y grafo LangGraph
│           ├── indexing/      # indexación Gold → Qdrant
│           ├── adapters/      # adaptadores de infraestructura
│           │   ├── storage/     # SQLAlchemy Core
│           │   │   ├── database.py      # motor y sesiones
│           │   │   ├── tables.py        # esquema declarativo
│           │   │   ├── repositories.py  # repositorios SQL
│           │   │   ├── chat.py          # conversaciones y mensajes del chat
│           │   │   └── import_corpus.py # importador Gold idempotente
│           │   ├── embeddings/  # Embedder: Ollama (nomic-embed-text)
│           │   ├── llm/         # LLMProvider: DeepSeek y Ollama sobre LangChain
│           │   └── vector/      # pgvector legado + QdrantVectorStore (M2)
│           ├── infrastructure/ # adaptadores y configuración tipada
│           │   └── settings.py  # pydantic-settings (.env + entorno)
│           ├── config.py      # fachada de compatibilidad de settings
│           ├── fetching.py    # cliente HTTP educado (implementa Fetcher)
│           ├── extracting.py  # HTML → Markdown
│           ├── gold.py        # front matter + índice JSONL
│           ├── pipeline.py    # orquestación de las tres capas
│           ├── models.py      # reexporta el dominio (compatibilidad)
│           ├── importer.py    # importación del corpus externo (capa extendida)
│           ├── rag.py         # recuperación, prompt y generación (RAG)
│           ├── ui.py          # interfaz web de pruebas (Streamlit)
│           └── cli.py         # capture / import / ask / status / list
├── data/                    # capas Medallion (raw y processed ignoradas)
│   ├── raw/                 # bronze — HTML crudo
│   ├── processed/           # silver — Markdown limpio
│   ├── gold/                # gold — entregable versionado (núcleo)
│   ├── external/            # capa extendida importada (solo manifiesto en Git)
│   ├── eval/                # consultas etiquetadas para Recall@10 / nDCG@10
│   ├── indicators.json      # herramienta estructurada del M2
│   ├── retrieval_cards.json # fichas de recuperación (no son fuente de respuesta)
│   └── index/               # índice FAISS legado (regenerable, ignorado por Git)
├── migrations/              # migraciones Alembic (esquema relacional)
├── alembic.ini              # configuración de Alembic
├── specs/                   # especificaciones (Spec Kit: spec / plan / tasks)
├── .specify/                # plantillas y constitución del flujo (Spec Kit)
├── tests/                   # pruebas (pytest, patrón AAA)
├── app.py                   # entrada de la interfaz: streamlit run app.py
├── docs/                    # documentación, ADRs e investigación
│   ├── decisions/           # ADRs: contexto, opciones y consecuencias
│   ├── arquitectura.json    # fuente tipada del diagrama (Archify)
│   ├── arquitectura.html    # diagrama interactivo autocontenido
│   └── arquitectura.md      # versión Mermaid del mismo pipeline
├── .github/                 # gobernanza (PR/issue templates, CI, Dependabot, labels)
├── .opencode/rules/         # reglas para agentes (tgrep)
├── AGENTS.md                # contexto para agentes LLM
├── CONSTITUTION.md          # este archivo — verdad central
├── README.md                # puerta de entrada pública
├── CONTRIBUTING.md          # guía de contribución
├── CODE_OF_CONDUCT.md       # código de conducta
├── Makefile                 # automatización de tareas
├── Dockerfile               # imagen multi-etapa de la app web (extras fuera)
├── compose.yml              # Postgres 16 + pgvector para desarrollo local
├── compose.prod.yml         # app + Postgres + Qdrant + Ollama (demo por IP)
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
- El presupuesto de cambios suma adiciones y borrados. CI advierte al superar
  300 líneas y falla al superar 2.000 en PRs con base distinta de `main`.
- Dippy usa DeepSeek para publicar revisiones de GitHub con hallazgos P0/P1.
  Corre automáticamente en PRs internos y se puede pedir con `@dippy review`
  desde una cuenta colaboradora una vez que el workflow llegue a `main`.
- Toda dependencia nueva entra con `uv add` / `uv add --dev` (nunca editando `uv.lock` a mano).

### 6.1 Imagen y despliegue

- `make image` construye la imagen multi-etapa (`Dockerfile`) con las
  dependencias base y el extra `capture` (la captura web desde Configuración
  usa `requests`, `beautifulsoup4` y `lxml` en runtime); `legacy`
  (Streamlit/FAISS) se queda fuera de la imagen y se instala en desarrollo con
  `uv sync --all-extras`.
- `make prod-up` levanta app, Postgres, Qdrant y Ollama con
  `compose.prod.yml`; `make prod-down` los detiene conservando los volúmenes.
  El compose exige `SESSION_SECRET` (falla si falta), publica la app en
  `0.0.0.0:APP_PORT` (8080 por defecto), corre sin privilegios, mantiene el
  sistema de archivos de solo lectura y monta el volumen `appdata` en
  `/app/data` para conservar Bronze, Silver, Gold y el índice de las cargas.
- El despliegue de producción es automático: al fusionar el PR de release en
  `main`, CI debe terminar en verde; `.github/workflows/deploy.yml` publica la
  imagen en GHCR con un tag `sha-<commit>` y `scripts/deploy.sh` la descarga en
  el VPS, aplica migraciones, recrea los contenedores y comprueba `/live` y
  `/health`. Si falla, vuelve al tag anterior. El runbook completo —VPS,
  secretos, primer despliegue, respaldos y rollback— está en
  [`docs/despliegue-ovh.md`](docs/despliegue-ovh.md).
- La demo se publica por IP y HTTP en `APP_PORT` (8080), sin dominio ni TLS.
  Para un dominio, se pone nginx/Caddy delante, se publica la app solo en
  `127.0.0.1:8000` y se ajusta `FORWARDED_ALLOW_IPS` a la IP del proxy.
- Los embeddings se calculan con Ollama (`nomic-embed-text`), configurable con
  `OLLAMA_BASE_URL`; el LLM puede ser DeepSeek. El servicio `ollama` de
  `compose.prod.yml` cubre el caso por defecto.
- `GET /live` es la vitalidad del proceso y no toca la base; `GET /health` es
  el readiness y publica los conteos del corpus. El embedder y el LLM se crean
  en la primera pregunta, nunca al arrancar.
- El CLI completo requiere `carvajal[capture,legacy]`; la imagen web incluye
  el extra `capture` para la captura desde Configuración, pero no `legacy`.
  Las migraciones se aplican aparte con `alembic upgrade head`.

## 7. Repositorio y Git

- **Remoto (SSH):** `git@github.com:almeneses/carvajal.git`
- **Rama principal:** `main` — solo recibe releases aprobados.
- **Rama de integración:** `dev` — recibe todo el trabajo de la app web
  (ver [`docs/plans/0001-migracion-app.md`](docs/plans/0001-migracion-app.md)).
- **Estrategia de ramas:** `feat/...`, `fix/...`, `docs/...`, `refactor/...`,
  `test/...`, `chore/...`; integración vía Pull Request a `dev`, y de `dev` a
  `main` en un PR de release. Los cambios de documentación y decisiones pueden ir
  directo a `main`.
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
| 2026-11-14 | Chat HTMX con compositor inferior y panel lateral de hilos; pestaña Configuración para cargas | El hilo seleccionado se resuelve por `?thread=` y el compositor envía ese id al stream; la carga reutiliza Bronze → Silver → Gold, `migrate_gold` y `index_gold` para no duplicar el pipeline |
| 2026-11-14 | El shell HTMX de compatibilidad (`apps/api`, :8000) adopta el sistema visual institucional del M2, fuentes plegables y un panel lateral de lectura | Cabecera, paleta y componentes unificados; el texto generado se formatea en servidor con Markdown saneado y en cliente para el stream, las fuentes quedan contraídas por defecto y el panel resuelve el documento con `/api/documents/by-slug` y resalta el fragmento citado, sin cambiar rutas ni contratos SSE/CSRF |
| 2026-11-14 | Captura web pública desde Configuración: semillas + URLs, máximo de 24 por ejecución, guard SSRF, job en segundo plano y progreso SSE | La petición solo valida y crea el job; el cierre reutiliza `importar_gold` e `indexar_gold` con `only` y sin poda. Sin autenticación en esta fase: el límite por IP (5 capturas/hora), el máximo de 24 URLs, `robots.txt`, el timeout y el guard SSRF (IPs privadas, puertos 80/443, redirecciones revalidadas y DNS fijado a la IP validada) son la defensa; `CAPTURE_ALLOWED_DOMAINS` permite restringir dominios por entorno |
| 2026-11-14 | Shell HTMX con escala accesible para adulto mayor: 18px base, controles de 54px, contenedores de 1360/1680px y navegación móvil 2×2 | Mejora lectura y precisión de clic sin cambiar contratos; solo CSS del shell `apps/api`. La SPA `apps/web` queda fuera |
| 2026-11-14 | Carga de archivos en segundo plano con spinner y lista de jobs | El POST solo lee los archivos y crea un job; el botón se bloquea y muestra un spinner, la página consulta el estado por polling y la barra lateral acumula las cargas recientes con una animación de entrada. Un solo job activo evita saturar Postgres y Qdrant |
| 2026-11-14 | Carga de documentos pública en Configuración: se elimina `CONFIG_TOKEN` y el gate de loopback | La pestaña queda abierta a cualquier visitante, igual que la captura web; se conservan el CSRF, los límites de 100 archivos/5 MB y el pipeline Bronze → Silver → Gold → PostgreSQL → Qdrant. El riesgo de abuso se acepta por decisión del owner |
| 2026-11-14 | FAQ en la SPA y el shell HTMX (`/faq`): lotes de hasta 10 preguntas, botón «Generar otras 10» y descarga Markdown | El caso de uso lee los documentos `core` de Postgres, excluye las preguntas ya mostradas y no persiste: el estado acumulado vive en la pestaña. Comparte CSRF y cuota diaria con el chat, genera por SSE sobre el corpus completo y advierte si el bloque se truncó por longitud; con Ollama se fija `num_ctx` (32 768) para que el prompt no se recorte en el servidor |

## 10. Notas y tareas pendientes

- [ ] Añadir extracción de PDFs (informes, memorias anuales).
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
