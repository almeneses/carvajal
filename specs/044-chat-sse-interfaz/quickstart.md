# Quickstart — validación de la 044

## Requisitos

- Worktree `.worktrees/044`, venv sincronizado y Node 24.
- `UV_CACHE_DIR` apuntando a la caché del repo; sin `pip`.
- `OLLAMA_MODEL` sin exportar en las pruebas de backend (`env -u OLLAMA_MODEL`).

```bash
cd /home/alejo/projects/DataScience/tecnicas_avanzadas/carvajal/.worktrees/044
export UV_CACHE_DIR=/home/alejo/projects/DataScience/tecnicas_avanzadas/carvajal/.uv-cache
```

## 1. Caracterización del contrato HTTP/SSE

```bash
env -u OLLAMA_MODEL uv run pytest tests/test_044_chat_contract.py -q
env -u OLLAMA_MODEL uv run pytest tests/test_api_chat.py tests/test_api_stream.py \
  tests/test_api_faq.py tests/test_api.py tests/test_security.py -q
```

Esperado antes y después: todo en verde. Los escenarios cubren rutas, formatos,
códigos, mensajes, cabeceras, cookies y el orden CSRF → validación → cuota.

## 2. Lector SSE

```bash
node --test tests/js/test_044_sse_shared.cjs
node --test tests/js/test_044_chat_stream_reader.cjs
node --test tests/js/test_chat_voice.cjs
```

Esperado: el lector común procesa bloques partidos, `\r\n`, varias líneas `data:`,
cierre y último bloque sin separador; el chat conserva su notificación final y la FAQ
su acumulación.

## 3. HTMX solo en Configuración

```bash
env -u OLLAMA_MODEL uv run pytest tests/test_api.py -q -k "htmx"
```

Esperado: Configuración referencia `/static/htmx.min.js`; chat, FAQ, corpus y
documento no.

## 4. Puerta completa

```bash
env -u OLLAMA_MODEL make check
node --test tests/js/test_chat_voice.cjs
```

## 5. Comprobación manual (opcional)

```bash
env -u OLLAMA_MODEL uv run uvicorn apps.api.main:app --port 8000
```

- `/chat`: formulario sin streaming (desactivando JS), dictado/lectura/manos libres y
  stream SSE; el HTML no carga htmx.
- `/faq`: generación por SSE.
- `/configuracion`: captura web con HTMX y progreso SSE.
