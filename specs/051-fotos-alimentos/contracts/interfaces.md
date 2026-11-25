# Contratos: spec 051

## 1. `POST /chat/foto` (web)

- Cabecera `X-CSRF-Token` (se valida antes de leer el cuerpo) → 403.
- `Content-Length` obligatorio (411) y ≤ 10 MB + 16 KB (413).
- Sin clasificador disponible → 503 `{"detail", "reason": "off" | "cap" | "circuit_open"}`.
- Multipart: `foto` (archivo, `image/jpeg` o `image/png`), `texto` (≤ 500, opcional),
  `client_turn_id` (≤ 64), `thread_id`.
- Foto inválida → 415 (tipo) / 413 (tamaño) / 422 (pequeña, alargada, ilegible) con
  `{"detail", "reason"}`.
- Éxito: `text/event-stream` con los eventos de `/chat/stream`: `open`, `meta`, `fallback`
  (texto completo), `trace` (`path: "foto"`, `foto: {status, tipo}`), `products` (si hay
  tarjetas) y `done`. Fallo del clasificador: `error` con `PHOTO_UNAVAILABLE` e
  `incomplete: false`; cupo agotado: `error` con el mensaje de límite.
- Emite la cookie de identidad si el visitante es nuevo, como `/chat/stream`.

## 2. MCP `consultar_carvajal`

Nuevo parámetro opcional `foto: str = ""`: la referencia del adjunto
(`media://inbound/<archivo>` o `<archivo>`). Con `foto`, `mensaje` es opcional (texto que
acompaña la foto). Respuestas:

- Foto aceptada: la recomendación en formato WhatsApp.
- Nombre inválido, archivo ausente o no imagen: `PHOTO_REJECTED` (sin cupo).
- Sin carpeta de adjuntos o sin clasificador: `PHOTO_UNAVAILABLE` (sin cupo).

## 3. Jev (`JevClient.call`)

`call(kind, state, questions, parse, *, assets=None, images=False, timeout_s=None)`:
`assets` se añade al cuerpo; `images=True` añade `TypeSafe-Danger-Zone-Images: true`;
`timeout_s` reemplaza el plazo del cliente para esa llamada.

## 4. Configuración

| Variable | Defecto | Uso |
|---|---|---|
| `FOOD_PHOTO_ENABLED` | `true` | Apaga la función sin desplegar |
| `FOOD_PHOTO_TIMEOUT_S` | `4.0` (1-10) | Plazo de Jev con imagen |
| `FOOD_PHOTO_MAX_CALLS_PER_DAY` | `500` | Tope diario del clasificador |
| `FOOD_PHOTO_MIN_CONFIDENCE` | `0.6` | Umbral de tipo y temperatura |
| `FOOD_PHOTO_MEDIA_DIR` | vacío | Carpeta de adjuntos de OpenClaw (MCP) |

La función está disponible si `FOOD_PHOTO_ENABLED` y hay `TYPESAFE_API_KEY`. `/health` añade
el bloque `food_photo: {status: "ok" | "off" | "cap" | "circuit_open"}`.
