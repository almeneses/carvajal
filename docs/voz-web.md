# Voz del chat web por el servidor (OpenAI)

El chat web tiene voz desde la spec 040: dictado revisable, lectura de respuestas y modo
manos libres con las APIs del navegador (Web Speech). La spec 047 añade una **voz del
servidor** opcional con OpenAI: mejor transcripción en español y una voz única y natural en
todos los navegadores. La voz del navegador sigue siendo el **respaldo**: ante cualquier
fallo el chat vuelve a ella sin que la persona tenga que hacer nada más que repetir el
dictado.

## Cómo se enciende

En el `.env` del servidor (la clave nunca va al repositorio, a `/health` ni a los logs):

```bash
VOICE_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

| Variable | Por defecto | Qué controla |
|---|---|---|
| `VOICE_PROVIDER` | `browser` | `browser` (solo voz del navegador) u `openai` (voz del servidor) |
| `OPENAI_API_KEY` | vacío | Clave de OpenAI; sin ella `openai` se comporta como `browser` |
| `VOICE_STT_MODEL` | `gpt-4o-mini-transcribe` | Transcripción (idioma `es` y pista de vocabulario comercial) |
| `VOICE_TTS_MODEL` / `VOICE_TTS_VOICE` | `gpt-4o-mini-tts` / `coral` | Síntesis y voz |
| `VOICE_TTS_INSTRUCTIONS` | asesora comercial colombiana, cálida y pausada | Estilo de la voz; vacío vuelve al de por defecto |
| `VOICE_TTS_MAX_CHARS` | `1200` | Texto máximo que se lee; el resto queda en pantalla |
| `VOICE_MAX_AUDIO_SECONDS` | `60` | Duración máxima del dictado (la corta el navegador) |
| `VOICE_MAX_UPLOAD_BYTES` | `2000000` | Tamaño máximo del audio subido (lo comprueba el servidor) |
| `VOICE_MAX_CALLS_PER_DAY` | `500` | Tope diario por proceso (STT + TTS), medianoche de Bogotá |
| `VOICE_RATE_LIMIT` / `VOICE_RATE_WINDOW_S` | `20` / `600` | Llamadas por sesión del chat en la ventana |
| `VOICE_TIMEOUT_S` | `20` | Plazo de cada petición a OpenAI |

`/health` publica el bloque `voice` (`provider`, `configured`, `available`, modelos,
`calls_today`, `last_error`) sin secretos; no cambia el estado global.

## Contratos

Las dos rutas exigen CSRF (cabecera `X-CSRF-Token` o campo `csrf_token`) y la cookie
firmada del chat (la misma identidad que el chat). Toda respuesta de error es JSON
`{"detail": "…", "reason": "<código>"}`.

### `POST /api/voz/transcribir`

- Cuerpo `multipart/form-data` con `audio` (`audio/webm`, `audio/ogg` o `audio/mp4`, lo que
  produce `MediaRecorder`). El tamaño se comprueba por `Content-Length` **antes** de leer el
  cuerpo y la firma del contenedor (WebM, Ogg, MP4) debe coincidir con el tipo.
- `200 {"texto": "…"}`: el texto va al borrador para revisarlo; no se envía solo (salvo en
  manos libres, que ya envía sin revisar). El audio no se guarda ni se registra.
- Errores: `403 csrf|no_session`, `411 length_required`, `413 too_large`,
  `415 unsupported_type`, `422 empty`, `429 rate_limited` (con `Retry-After`) y
  `503 off|cap|circuit_open|timeout|error|auth|invalid`.

### `POST /api/voz/hablar`

- Cuerpo `application/x-www-form-urlencoded` con `message_id`: el id de una **respuesta del
  asistente guardada en la conversación de quien llama**. El hilo lo expone como
  `data-voz-mensaje` y el evento `done` del stream como `message_id`.
- El servidor carga el mensaje, lo prepara para voz con `carvajal.speech.to_speech` (sin
  citas, Markdown, emojis ni URL; cotizaciones en su versión corta; fuentes en una frase), lo
  recorta a `VOICE_TTS_MAX_CHARS` en un fin de frase y devuelve `audio/mpeg` con
  `Cache-Control: no-store`. El audio de una respuesta ya leída se guarda en una caché pequeña
  en memoria (32 entradas) para no pagar dos síntesis.
- **No es un TTS público**: no acepta texto libre. Un id de otro dueño, de un mensaje del
  usuario o inexistente responde `404 not_found`.
- Errores: `403`, `404`, `422 invalid_message|no_text`, `429` y `503` como arriba (más
  `db` si la base no responde).

## Respaldo en el navegador

La página del chat marca `data-voz-servidor="1"` solo si la voz del servidor está disponible
(proveedor `openai`, clave, tope y circuito). Con ella:

- **Dictar** graba con `MediaRecorder` (Opus a 32 kbps), muestra «Escuchando…» y termina con
  el clic, tras una pausa de 1,5 s después de hablar, al llegar al límite de segundos o con
  Escape (cancela). Sin voz detectada no se sube nada. Un `422` es silencio; cualquier otro
  fallo (503, 429, red, CSRF) apaga el dictado del servidor el resto de la sesión y el
  siguiente dictado usa el reconocimiento del navegador. Un permiso de micrófono denegado se
  explica igual que antes.
- **Escuchar** pide `/api/voz/hablar` y reproduce el audio desde un `blob:`; pausa, reanuda y
  detiene como la lectura del navegador. Un `404` lee ese turno con la voz del navegador; otro
  fallo apaga la lectura del servidor el resto de la sesión.
- **Manos libres** funciona igual: si la transcripción del servidor falla en medio, sigue
  escuchando con el navegador.
- El aviso previo al primer dictado cambia de texto (el audio pasa por el sitio y lo
  transcribe OpenAI) y se acepta aparte del aviso de la voz del navegador.

La CSP del documento `/chat` añade `media-src 'self' blob:` para reproducir ese audio; el resto
del sitio conserva la política por defecto. El micrófono sigue permitido solo en ese documento.

## Requisitos de despliegue

- El dictado exige contexto seguro: **HTTPS** (o `localhost`). Por IP y HTTP el navegador no
  ofrece el micrófono; la lectura sí funciona.
- Detrás de nginx con TLS, `FORWARDED_ALLOW_IPS` debe ser la puerta de enlace de la red de
  Docker para que la app vea `https` (cookies `Secure`, HSTS): ver
  [`despliegue-ovh.md` §10](despliegue-ovh.md#10-si-más-adelante-pones-un-dominio).
- Coste: el tope diario por proceso y el límite por sesión acotan el gasto; `/health` muestra
  `calls_today`. El tamaño máximo del audio es la defensa del servidor contra audios largos
  (la duración la corta el navegador).
