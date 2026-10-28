# Informe — Asistente Q&A del corpus Carvajal

> Proyecto: **Carvajal Corpus** · Repositorio: <https://github.com/markodavid/carvajal>
> Fase: captura del corpus + asistente RAG con interfaz de pruebas.

---

## 1. Descripción del problema

Carvajal es una empresa multilatina con más de 120 años de historia y presencia en
10 países. Su información institucional está distribuida en un sitio web con decenas
de páginas: historia, propósito superior, sectores de negocio, sostenibilidad,
gobierno corporativo, compromiso ético, inversionistas y noticias.

**Necesidad:** un canal de comunicación automatizado y preciso que permita a
colaboradores, inversionistas y público general resolver preguntas sobre la empresa
sin navegar manualmente el sitio, y sin el riesgo de respuestas inventadas que
desinformen.

## 2. Planteamiento de la solución

Se construyó un **sistema Q&A basado en RAG** (*Retrieval-Augmented Generation*) como
núcleo de un futuro chatbot institucional:

```text
Sitio web de Carvajal
        │  scraping (requests + BeautifulSoup/lxml)
        ▼
data/gold/*.md  ──►  chunking (1000/200)  ──►  embeddings (Ollama: nomic-embed-text)
                                                        │
Pregunta del usuario ──► retriever FAISS (k=5) ─────────┘
        │
        ▼
Prompt de sistema (corpus consolidado + reglas anti-alucinación)
        │
        ▼
LLM (Claude vía API) ──► Respuesta basada únicamente en el corpus
        │
        ▼
Interfaz Streamlit (campo de pregunta + área de respuesta)
```

Decisiones técnicas:

| Componente | Elección | Motivo |
|---|---|---|
| Framework de LLM | **LangChain** | Recomendado por el curso; integra Ollama y Anthropic |
| Modelo (módulo 2) | **DeepSeek vía API** (`deepseek-chat`, configurable) | API compatible con OpenAI, bajo costo y buen rendimiento en español |
| Alternativa local | **Ollama** (`llama3.2`) | Permite demo sin API mediante `LLM_PROVIDER=ollama` |
| Embeddings | **Ollama `nomic-embed-text`** | Open source, local y sin costo |
| Base vectorial | **FAISS** (en memoria) | Suficiente para 15 documentos; cero infraestructura |
| Interfaz | **Streamlit** | Campo de pregunta y área de respuesta en minutos |

## 3. Preparación de los datos

### 3.1 Scraping

- Semillas explícitas en `src/carvajal/config.py` (`config.SEEDS`): 15 URLs del sitio.
- Cliente HTTP `requests` con sesión única, `User-Agent` identificable y `timeout`
  de 30 s; se respeta `robots.txt`.
- Se descartan del alcance vídeos, PDFs y páginas autenticadas.

### 3.2 Limpieza

Pipeline Medallion: **bronze** (HTML crudo) → **silver** (Markdown limpio) →
**gold** (Markdown + *front matter* + índice JSONL). El entregable versionado es
`data/gold/`.

El extractor (`src/carvajal/extracting.py`) recorre el DOM y conserva encabezados,
párrafos, listas y tablas, descartando subárboles de navegación (`nav`, `header`,
`footer`, formularios, modales y contenedores con clases de menú). Se colapsan
espacios y se eliminan bloques duplicados.

**Corrección clave:** la primera versión del extractor emitía el texto de cada
elemento padre *y* de sus hijos, duplicando el menú decenas de veces: el corpus pesaba
**2.45 MB** y contaminaba la recuperación. Tras corregirlo (y excluir las plantillas
`header`/`footer` de Elementor), el corpus quedó en **64.5 KB de texto editorial
limpio** (≈ 15 000 tokens), apto para consolidarse en el prompt de sistema.

### 3.3 Chunking

- `RecursiveCharacterTextSplitter` con `chunk_size=1000` y `chunk_overlap=200`.
- Recuperación de los **5 fragmentos** más similares por pregunta (`k=5`).
- Los metadatos de cada fragmento conservan la fuente (`slug` del documento).

## 4. Modelado

### 4.1 Embeddings

`nomic-embed-text` servido por Ollama. Es open source, se ejecuta localmente y no
requiere claves ni cuotas de API.

### 4.2 LLM

- **DeepSeek vía API** (`ChatOpenAI` apuntando a `https://api.deepseek.com/v1`) como
  opción principal del módulo 2, con modelo `deepseek-chat`.
- Alternativas configurables: Ollama (`llama3.2`) y Anthropic Claude.
- Configuración por entorno: `LLM_PROVIDER=deepseek|ollama|claude`, `DEEPSEEK_API_KEY`,
  `DEEPSEEK_MODEL`, `DEEPSEEK_BASE_URL` (y `ANTHROPIC_API_KEY` / `CLAUDE_MODEL`).
- Temperatura `0` para respuestas deterministas y fieles al corpus.

### 4.3 Base de datos vectorial

**FAISS** en memoria. El índice se reconstruye al iniciar la aplicación; es adecuado
para el tamaño actual del corpus (15 documentos, ~64.5 KB). Para producción se
recomendaría una base vectorial persistente (Chroma, pgvector o Qdrant).

### 4.4 Diseño del prompt

El *system prompt* es la **memoria completa del asistente**: consolida todo el texto
limpio del corpus (cada documento con su fuente) y fija reglas estrictas:

- Responder **únicamente** con información del contexto/corpus.
- Si la respuesta no está en el corpus, decirlo explícitamente ("no lo sé").
- No inventar datos ni usar conocimiento externo.
- Indicar las fuentes consultadas.

El mensaje del usuario incluye, además, los **fragmentos recuperados** por FAISS
para enfocar la respuesta. El enfoque es *zero-shot*: no se usan ejemplos few-shot;
la robustez se logra con instrucciones explícitas, delimitadores del corpus y
temperatura 0.

## 5. Resultados

### 5.1 Metodología

- **25 preguntas** de prueba en `data/questions.json`, cubriendo los sectores,
  historia, propósito, sostenibilidad, gobierno corporativo, inversionistas y
  contacto, más preguntas fuera del alcance para verificar la abstención.
- Ejecución reproducible: `make eval` (o `uv run carvajal eval`), que genera
  `docs/resultados.md` con pregunta + respuesta.
- Criterios de evaluación: **exactitud** (¿la respuesta coincide con el corpus?),
  **coherencia** (¿es clara y completa?), **fidelidad** (¿cita fuentes y evita
  alucinaciones?) y **abstención** (¿reconoce lo que no sabe?).

### 5.2 Ejemplos

> Completar tras ejecutar `make eval` con las credenciales configuradas.

| # | Pregunta | Respuesta generada | Evaluación |
|---|---|---|---|
| 1 | ¿En qué año y ciudad fue fundada Carvajal? | _(ejecutar `make eval`)_ | |
| 2 | ¿Cuáles son los cuatro sectores en los que opera? | | |
| 3 | ¿Qué es Bioform? | | |
| 4 | ¿Cuál es la capital de Francia? (fuera de alcance) | | |

### 5.3 Limitaciones

- El corpus cubre 15 páginas; el `sitemap.xml` completo (noticias, podcast,
  especialistas) queda fuera.
- La recuperación depende de la calidad del *chunking* y de los embeddings locales;
  FAISS se reconstruye en memoria en cada ejecución.
- El asistente no tiene memoria conversacional (una pregunta por turno).
- Las respuestas no verifican automáticamente que cada afirmación exista en el
  corpus; la abstención depende del cumplimiento del prompt por parte del LLM.
- La evaluación es cualitativa y manual; no se implementó un *benchmark* automático.

## 6. Presentación

Sustentación en vivo de **15 minutos** con demostración funcional en Streamlit.
El guion detallado está en [`docs/presentacion.md`](presentacion.md).

> **No se realizan presentaciones de PowerPoint.**

## Anexos

### Comandos

```bash
uv sync                    # entorno
uv run carvajal capture    # regenerar el corpus Gold
uv run carvajal ask "..."  # pregunta por CLI
make app                   # interfaz Streamlit
make eval                  # 25 preguntas -> docs/resultados.md
make check                 # lint + formato + tipos + pruebas
```

### Variables de entorno

| Variable | Descripción | Default |
|---|---|---|
| `LLM_PROVIDER` | `ollama`, `deepseek` o `claude` | `ollama` |
| `DEEPSEEK_API_KEY` | Clave API de DeepSeek | `""` |
| `DEEPSEEK_MODEL` | Modelo DeepSeek | `deepseek-chat` |
| `DEEPSEEK_BASE_URL` | Endpoint compatible con OpenAI | `https://api.deepseek.com/v1` |
| `ANTHROPIC_API_KEY` | Clave API de Anthropic | `""` |
| `CLAUDE_MODEL` | Modelo Claude | `claude-sonnet-4-5` |
