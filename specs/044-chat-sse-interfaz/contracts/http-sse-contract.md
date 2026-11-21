# Contrato HTTP/SSE del chat (044)

Contrato vigente que la refactorización **no** puede cambiar. Se fija con
`tests/test_044_chat_contract.py` antes de tocar el código.

## Rutas

| Entrada | Método y ruta | Respuesta normal | Errores controlados |
|---|---|---|---|
| Formulario | `POST /chat` | `303` a `/chat` o `/chat?thread=<id>` | `403`/`422`/`429`/`503`: página `chat.html` con `X-Carvajal-Error: 1`; `404` hilo ajeno |
| Stream web | `POST /chat/stream` | `200 text/event-stream; charset=utf-8` | `403`/`422`/`429`/`503`: bloque SSE `error` con `X-Carvajal-Error: 1`; `405` en GET |
| Stream API | `POST /api/chat/stream` | idéntico al stream web | idéntico al stream web |
| FAQ | `POST /api/faq` | `200 text/event-stream` | `403`/`422`/`429`/`503`: bloque SSE `error` |

## Cuerpo del formulario

`question`, `csrf_token`, `thread_id` (opcional) y, solo en los streams,
`client_turn_id` (opcional). `application/x-www-form-urlencoded`.

## Eventos SSE (servidor → cliente)

```text
event: <nombre>\n
data: <json>\n
\n
```

Nombres posibles: `open`, `trace`, `meta`, `products`, `token`, `fallback`, `done`,
`error`. Cabeceras del stream: `Cache-Control: no-store` (middleware) y
`X-Accel-Buffering: no`.

## Errores SSE

Un error controlado usa exactamente:

```text
event: error
data: {"incomplete": false, "message": "<mensaje>"}

```

con estado HTTP `403`, `422`, `429` o `503`. En 429 se añade `Retry-After:
<segundos>`.

## Cookies

- `carvajal_csrf`: la emite el middleware de seguridad; `HttpOnly`, `SameSite=Lax`,
  `Path=/`, 12 h; `Secure` sobre HTTPS.
- `carvajal_sesion`: identidad firmada del chat; `HttpOnly`, `SameSite=Lax`, `Path=/`,
  180 días; `Secure` sobre HTTPS; se emite cuando el visitante es nuevo y la respuesta
  lo permite (incluidos 429/503 de los streams, no en sus 403/422).
- Un 403/422 de los streams **no** emite `carvajal_sesion` (comportamiento vigente).

## Mensajes visibles

| Caso | Mensaje |
|---|---|
| Vacía | `La pregunta no puede estar vacía.` |
| Larga | `La pregunta no puede superar los 500 caracteres.` |
| Cuota | `Has alcanzado el límite diario de preguntas. Vuelve a intentarlo mañana.` |
| CSRF | `La sesión del formulario expiró. Recarga la página e inténtalo de nuevo.` |
| 503 | `El servicio de chat no está disponible en este momento.` |

Los detalles internos (LLM, embeddings, SQL, trazas) nunca aparecen en la respuesta.
