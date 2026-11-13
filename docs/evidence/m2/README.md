# Evidencia del M2

> Corrida del 13 de noviembre de 2026 contra servicios reales: Postgres 16 +
> pgvector, Qdrant 1.15.4 con 125 puntos, Ollama `nomic-embed-text` y DeepSeek
> como LLM. No contiene cookies, claves ni datos personales.

| Archivo | Qué prueba | Cómo reproducir |
|---|---|---|
| `smoke.json` | cuatro casos de la rúbrica, ruta, tool, memoria, fuentes, TTFT y abstención | `uv run python scripts/smoke_m2.py` |
| `health.json` | `/health` separa documentos, fragmentos e índice | `curl -s localhost:8000/health \| python3 -m json.tool` |
| `make check` | 437 pruebas unitarias/HTTP | `make check` (la salida no se versiona) |
| `pytest -m integration` | 41 pruebas de integración con Postgres real | `uv run pytest -m integration` |
| `frontend-mobile.png` | frontend a 390×844 | Chrome headless, `--window-size=390,844` |
| `frontend-desktop.png` | frontend a 1440×900 | Chrome headless, `--window-size=1440,900` |
| `../../eval/retrieval-report.md` | Recall@10 y nDCG@10 por consulta | `make index-eval` |

## Resultado del smoke

| Caso | Ruta | Resultado |
|---|---|---|
| Directa | `direct` | sin Qdrant ni herramienta |
| RAG ciudad | `rag` | 6 fuentes con URL |
| Seguimiento | `rag` / `follow_up` | mismo `thread_id`, año 1904 |
| Herramienta | `tool` / `ebitda` | \$351.631 millones, margen 14% |
| Memoria | `direct` | preferencia recuperada en otro hilo |
| Aislamiento | `direct` | `memory=0` en otro visitante |
| Sin evidencia | `rag` | 0 fuentes y abstención |

`smoke.json` incluye `first_token_ms` por caso. En la corrida final la mediana fue ~1.8 s y el máximo 2.0 s; incluye retrieval y espera del proveedor, no solo generación.
