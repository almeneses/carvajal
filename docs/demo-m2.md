# Guion de la demo M2 — 12 minutos + 3 de preguntas

> Entrada oficial: <http://localhost:8001>. API: <http://localhost:8000>.
> Deja abiertas dos terminales, el navegador en el frontend, el IDE y
> [`docs/eval/retrieval-report.md`](eval/retrieval-report.md).

## Antes de las 8:00 a. m.

```bash
uv sync --all-extras
cp .env.example .env
# En .env: DEEPSEEK_API_KEY, SESSION_SECRET fijo, QDRANT_URL, DATABASE_URL
make db && make qdrant
uv run alembic upgrade head
ollama pull nomic-embed-text
make index
make api      # terminal 1
make web      # terminal 2
curl -s http://localhost:8000/health | python3 -m json.tool
```

Comprueba que `/health` devuelve `"index": {"status": "ok", "points": 125}`.
Fija `SESSION_SECRET` antes de reiniciar: sin él, la cookie cambia de firma y el
hilo no se recupera.

## Guion (12 min)

| Tiempo | Caso | Qué mostrar | Qué decir |
|---|---|---|---|
| 0:00–1:00 | Contexto | README/arquitectura | Frontend y API separados; Postgres para chat/memoria, Qdrant para retrieval, LangGraph para decidir. |
| 1:00–2:30 | **Directa** | «Hola, ¿qué puedes hacer?» | `trace.route=direct`, `retrieved=0`: no se consulta Qdrant ni la herramienta. |
| 2:30–5:00 | **RAG + seguimiento** | «¿En qué ciudad fue fundada Carvajal?» y «¿Y en qué año ocurrió eso?» | Tokens incrementales, 6 fuentes, `quienes_somos`/`historia`; clic en la URL original. El seguimiento conserva el mismo `thread_id` y responde 1904. |
| 5:00–6:30 | **Herramienta** | «¿Cuál fue el EBITDA consolidado a septiembre de 2025 y qué margen reportó?» | `route=tool`, `tool=ebitda`, valor \$351.631 millones, margen 14%, URL de La República. |
| 6:30–8:30 | **Memoria** | «Recuerda que prefiero respuestas breves» → «Nuevo hilo» → «Hola» | `memory=1` en el hilo nuevo; misma cookie, distinto hilo. Abre una ventana incógnito y saluda: `memory=0` (aislamiento). |
| 8:30–10:00 | **Reinicio** | Reinicia el backend (o muestra la evidencia pregrabada) y repite «¿Y en qué año ocurrió eso?» en el hilo | Mismo `thread_id`, `intent=follow_up`, respuesta 1904; el checkpointer y el historial sobreviven. |
| 10:00–11:00 | **Salud y trazas** | `/live`, `/health`, logs JSON | `/live` no toca base; `/health` separa documentos, fragmentos e índice. Logs con `request_id`, `thread_id`, `route`, `tool`, fuentes. |
| 11:00–12:00 | **Evaluación y calidad** | Reporte de retrieval y `make check` | Recall@10=0.8000, nDCG@10=0.7791; 437 unitarias + 41 de integración; cita la consulta sin evidencia. |

## Frases exactas de respaldo

1. Saludo: `Hola, ¿qué puedes hacer?`
2. Ciudad: `¿En qué ciudad fue fundada Carvajal?`
3. Seguimiento: `¿Y en qué año ocurrió eso?`
4. Herramienta: `¿Cuál fue el EBITDA consolidado de Carvajal a septiembre de 2025 y qué margen reportó?`
5. Memoria: `Recuerda que prefiero respuestas breves`
6. Sin evidencia: `¿Cuál es el número de teléfono de la sede de Carvajal en Tokio?`

## Si falla algo

- **DeepSeek sin red/cuota:** cambia a Ollama local
  (`LLM_PROVIDER=ollama`, `OLLAMA_MODEL=gemma3:4b`) y reinicia la API; el
  contrato SSE no cambia.
- **Qdrant caído:** `/live` sigue en 200 y `/health` marca
  `index.status=unavailable`; muestra la evidencia
  [`docs/evidence/m2/smoke.json`](evidence/m2/smoke.json) y el reporte de
  retrieval.
- **El backend se reinició sin `SESSION_SECRET`:** la cookie no valida; vuelve a
  fijarlo y repite el flujo. Es una defensa, no un fallo.
- **Sin navegador:** `curl -N` con el mismo contrato SSE del README; la evidencia
  ya está guardada y el smoke test es reproducible con
  `uv run python scripts/smoke_m2.py`.

## Preguntas previsibles (3 min)

| Pregunta | Respuesta corta |
|---|---|
| ¿Por qué Qdrant y no pgvector? | Qdrant es el índice evaluado del M2, con payload de procedencia y poda; Postgres conserva lo relacional y la memoria. |
| ¿Cómo evitan alucinaciones? | Prompt cerrado al contexto, citas `[n]`, umbral de score, comprobación de nombres propios y abstención. |
| ¿Dónde vive la memoria? | Checkpointer y Store en Postgres; namespace por dueño; el hilo se valida contra la cookie. |
| ¿Qué pasa si el LLM falla? | `fallback` si falla antes del primer token; `error` con `incomplete=true` y mensaje `incomplete` si falla después. |
| ¿Cómo se actualiza el corpus? | `make index` reejecuta con IDs UUID5 estables y poda puntos obsoletos; `--recreate` reconstruye. |
| ¿Frontend y API están desacoplados? | Sí: dos procesos, contrato HTTP/SSE, CORS explícito y cookies; la API concentra credenciales y RAG. |
