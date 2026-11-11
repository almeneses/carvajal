# Implementation Plan: Asistente Q&A con RAG sobre el corpus de Carvajal

**Spec**: [`spec.md`](./spec.md) · **Estado**: implementado · **Rama**: `001-asistente-rag-carvajal`

## Summary

Pipeline reproducible que captura el sitio de Carvajal y seis fuentes de prensa, los normaliza
en tres capas, consolida el corpus en un prompt de sistema y responde tres tareas de LLM
(Resumen, FAQ, Q&A) con fuentes citadas. La interfaz Streamlit muestra el scraping por etapas
y las tres tareas.

## Technical Context

| Aspecto | Decisión |
|---|---|
| Lenguaje | Python 3.13 (src-layout, paquete `carvajal`) |
| Dependencias | `uv` + `uv.lock` |
| HTTP | `requests` (sesión única, UA propio, timeout 30 s, 1 s/host) |
| HTML | `beautifulsoup4` + `lxml` con extractor propio |
| RAG | LangChain 1.x + FAISS (CPU) |
| Embeddings | Ollama `nomic-embed-text` |
| LLM | DeepSeek `deepseek-chat` (configurable: Claude, Ollama) |
| Interfaz | Streamlit (`app.py`) |
| Pruebas | pytest (71 pruebas), Ruff, `ty`, pre-commit, GitHub Actions |

## Constitution Check

| Principio | Cumplimiento |
|---|---|
| I. La constitución manda | `CONSTITUTION.md` actualizado en cada PR relevante |
| II. El corpus es el contrato | `data/gold/` versionado con front matter; bronze/silver regenerables |
| III. Sin fuentes no hay respuesta | Prompts con «no inventes» + citas + temperatura 0; medido en `docs/prompts.md` |
| IV. Calidad automatizada | `make check` y CI con los mismos pasos |
| V. Captura responsable | `robots.txt` normalizado (RFC 9309), rate limit, timeouts como descartes |
| VI. Cambios por PR | 11 PRs fusionados con revisión y checks verdes |

**Violaciones conocidas y aceptadas**: el corpus no cubre PDFs ni contenido dinámico
(documentado en `docs/alcance.md`); la capa extendida está disponible pero desactivada por
defecto.

## Project Structure

### Documentation (this feature)

```text
specs/001-asistente-rag-carvajal/
├── spec.md      # qué y para quién
├── plan.md      # este documento
└── tasks.md     # tareas con estado
```

### Source Code (repository root)

```text
src/carvajal/
├── config.py      # semillas, rutas, límites, LLM y embeddings
├── fetching.py    # cliente HTTP, robots.txt y rate limit
├── extracting.py  # HTML → Markdown
├── gold.py        # front matter, capas e índice JSONL
├── pipeline.py    # orquestación por etapas + reporte
├── corpus.py      # carga del corpus (núcleo y capa extendida)
├── rag.py         # prompts, retriever, tres tareas
├── ui.py          # interfaz Streamlit
└── cli.py         # capture / ask / summarize / faq / eval / status

data/
├── raw/           # bronze (ignorado)
├── processed/     # silver (ignorado)
├── gold/          # gold + index.jsonl (versionado)
└── index/         # FAISS cacheado (ignorado)

tests/             # pytest (patrón AAA)
docs/              # alcance, prompts, arquitectura, informe, resultados
scripts/           # experimentos de prompts
```

## Complexity Tracking

| Decisión | Costo | Mitigación |
|---|---|---|
| FAISS en proceso, sin base vectorial | Reconstrucción completa al cambiar el corpus (~15 min en CPU) | Índice persistido con huella; capturas de páginas estables |
| Corpus consolidado en el prompt | 95 000 caracteres por llamada | Corpus acotado a 24 documentos; capa extendida fuera del prompt |
| Embeddings locales (Ollama) | Dependencia de un servicio local (~2 GB RAM) | Índice cacheado; pendiente mensaje amable si cae |
| Evaluación manual | No es un benchmark automático | 30 preguntas versionadas + criterios explícitos |
