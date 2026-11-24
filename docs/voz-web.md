# Voz del chat web por el servidor (OpenAI)

El chat web tiene voz desde la spec 040: dictado revisable, lectura de respuestas y modo
manos libres con las APIs del navegador (Web Speech). La spec 047 añade una **voz del
servidor** opcional con OpenAI: mejor transcripción en español y una voz única y natural en
todos los navegadores. La voz del navegador sigue siendo el **respaldo**: si el dictado del
servidor falla, el chat vuelve a ella sin que la persona tenga que hacer nada más que
repetir el dictado; la lectura (spec 050, [notas de voz](#notas-de-voz-por-respuesta)) solo
la usa mientras el servidor no está disponible (503, 401/403).

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
| `VOICE_MAX_AUDIO_SECONDS` | `60` | Duración máxima del dictado: la corta el navegador y el servidor rechaza el audio cuya cabecera declara más |
| `VOICE_MAX_UPLOAD_BYTES` | `0` | Tamaño máximo del audio subido; `0` lo deriva de la duración a 64 kbps (480 KB para 60 s) |
| `VOICE_MAX_CALLS_PER_DAY` | `500` | Tope diario por proceso (STT + TTS), medianoche de Bogotá |
| `VOICE_RATE_LIMIT` / `VOICE_RATE_WINDOW_S` | `20` / `600` | Llamadas por sesión del chat en la ventana |
| `VOICE_IP_RATE_LIMIT` | `60` | Llamadas por IP en la misma ventana |
| `VOICE_GLOBAL_RATE_PER_MIN` | `30` | Llamadas por minuto de todo el proceso |
| `VOICE_TIMEOUT_S` | `20` | Plazo de cada petición a OpenAI (el navegador espera 15 s más) |

`/health` publica el bloque `voice` (`provider`, `configured`, `available` y modelos) sin
secretos; no cambia el estado global. `calls_today` y `last_error` solo salen para el
operador (`X-Operator-Token`, `Authorization` o su cookie), para no contar desde fuera
cuánto falta para el tope o si la clave está rechazada.

## Contratos

Las dos rutas exigen CSRF y la identidad del chat (la misma que el chat), y pasan tres
límites con ventana deslizante: por sesión, por IP y global por minuto. La identidad no es
autenticación: la cookie de visitante la elige el navegador, así que quien rota identidades
choca con el límite por IP (que necesita `FORWARDED_ALLOW_IPS` bien puesto detrás de nginx)
y, si además rota IP, con el global; ninguno deja agotar el tope diario de golpe. Los
límites guardan como mucho 4096 claves y olvidan la menos reciente. Toda respuesta de error
es JSON `{"detail": "…", "reason": "<código>"}`; los fallos internos del proveedor
(`timeout`, `error`, `auth`, `invalid`, base caída) salen como `unavailable` y el detalle
queda en los logs.

### `POST /api/voz/transcribir`

- Cuerpo `multipart/form-data` con `audio` (`audio/webm`, `audio/ogg` o `audio/mp4`, lo que
  produce `MediaRecorder`). El CSRF va **solo** en la cabecera `X-CSRF-Token`. El CSRF, el
  tamaño por `Content-Length`, la identidad y los límites se comprueban **antes** de leer el
  cuerpo; después, la firma del contenedor (WebM, Ogg, MP4) debe coincidir con el tipo y la
  duración que declara su cabecera no puede pasar de `VOICE_MAX_AUDIO_SECONDS` (más 3 s de
  holgura). Es una defensa frente a clientes honestos o mal configurados; contra quien
  falsee la cabecera, el tope de bytes es el límite duro.
- `200 {"texto": "…"}`: el texto va al borrador para revisarlo; no se envía solo (salvo en
  manos libres, que ya envía sin revisar). El audio no se guarda ni se registra. Las cifras
  llegan al estilo colombiano (`carvajal.speech.normalize_transcript`): OpenAI escribe «mil
  ochocientos» como «1,800» y se devuelve «1.800» («12,500,000» → «12.500.000»,
  «2,500.50» → «2.500,50»); decimales («1,5», «12,5 onzas»), horas («10:30») y listas
  («2, 3 y 4») no cambian. Es una decisión ante la ambigüedad: «1,500» (tres cifras tras
  la coma) se toma siempre como miles, también con «kg» o «litros», porque así escribe el
  STT los miles; un decimal dictado de tres cifras («uno coma quinientos») saldría mil
  veces mayor, y por eso el dictado se revisa antes de enviarlo.
- Errores: `403 csrf|no_session`, `411 length_required`, `413 too_large|too_long`,
  `415 unsupported_type`, `422 empty|rejected`, `429 rate_limited` (con `Retry-After`) y
  `503 off|cap|circuit_open|unavailable`. `rejected` es un audio que OpenAI no aceptó
  (400/413/415/422): no cuenta como fallo del proveedor, no abre el circuito de los demás y
  no descuenta del tope diario.

### `POST /api/voz/hablar`

- Cuerpo `application/x-www-form-urlencoded` con `message_id`: el id de una **respuesta del
  asistente guardada en la conversación de quien llama**. El hilo lo expone como
  `data-voz-mensaje` y el evento `done` del stream como `message_id`.
- El servidor carga el mensaje, prepara su **resumen hablado** (ver
  [Resumen hablado](#resumen-hablado); cotizaciones en su versión corta), lo recorta a
  `VOICE_TTS_MAX_CHARS` en un fin de frase como red de seguridad y devuelve `audio/mpeg` con
  `Cache-Control: no-store`. El audio de una respuesta ya leída se guarda en una caché pequeña
  en memoria (32 entradas) para no pagar dos síntesis.
- **No es un TTS público**: no acepta texto libre. Un id de otro dueño, de un mensaje del
  usuario o inexistente responde `404 not_found`.
- Errores: `403`, `404`, `422 invalid_message|no_text|rejected`, `429` y `503` como arriba
  (`unavailable` también si la base no responde).

## Resumen hablado

«Escuchar», la lectura automática y manos libres leen un **resumen** de la respuesta, no
la respuesta entera (spec 048): unas 55 palabras, 15 a 20 segundos. Lo prepara
`apps.api.voice.spoken_message` con `carvajal.speech.spoken_summary` (canal `web`), sin
LLM y de forma determinista, solo con frases y cifras de la propia respuesta:

- una respuesta corta (abstenciones, saludos, datos sueltos) se lee completa;
- si no cabe: la primera frase del primer párrafo, la pregunta final al cliente si la hay,
  la primera lista en una frase («Te muestro tres opciones en pantalla: A, B y C.», o solo
  cuántas hay si son más de cuatro, largas, repetidas o si nombrarlas cortaría una cifra,
  un rango o una enumeración; una lista numerada son «tres pasos») y más frases del primer
  párrafo mientras quepan; las fuentes en una frase; si se omitió algo más que el detalle
  de la lista (frases, otro párrafo, la pregunta), «El detalle completo está en
  pantalla.» antes de la pregunta, haya lista o no. El presupuesto cuenta todo lo que se
  dice: solo la primera frase, la frase corta de la lista, la de las fuentes y el aviso
  entran aunque no quepan. Nunca corta una cifra y nunca lee una URL;
- con cotización se lee solo su versión corta (producto, cantidades, total con IVA y
  envío), las fuentes y «El detalle completo está en pantalla.»;
- con tarjetas de referencias sin cotización, la voz las nombra o las cuenta («Te muestro
  dos referencias en pantalla: A y B.») antes de la pregunta final; con otras tarjetas
  (ficha, distribuidores, sedes) añade «El detalle completo está en pantalla.». Sus datos
  no están en el texto de la respuesta y, sin esto, la voz no diría que existen.

Ejemplo (respuesta con lista y pregunta):

```text
Para bebidas calientes, Carvajal Empaques ofrece vasos de papel de pared sencilla y doble pared. Te muestro tres opciones en pantalla: Vaso de papel 8 onzas, Vaso de papel 12 onzas y Vaso de papel 16 onzas.

Según el sitio de Carvajal Empaques.

¿Cuántas unidades necesitas y en qué ciudad?
```

El mismo texto llega al navegador para su voz de respaldo: el hilo lo publica en
`data-voz-resumen` (texto escapado en un atributo, no HTML) y el stream en el campo
`voz_resumen` del evento `done` (de la respuesta guardada; si no se puede leer, de lo
emitido). `construirSegmentos` (`chat-voice.js`) lo prefiere y lee un párrafo por
locución; si falta (una página vieja, un fallo al prepararlo), lee el turno del DOM como
antes. Así la voz del servidor y la del navegador dicen lo mismo.

## Respaldo en el navegador

La página del chat marca `data-voz-servidor="1"` solo si la voz del servidor está disponible
(proveedor `openai`, clave, tope y circuito). Con ella:

- **Dictar** graba con `MediaRecorder` (Opus a 32 kbps), muestra «Escuchando…» y termina con
  el clic, tras una pausa de 1,5 s después de hablar, al llegar al límite de segundos o con
  Escape (cancela). Sin voz detectada no se sube nada. Mientras se transcribe, el botón
  cancela la subida (la aborta) igual que Escape, y la subida tiene un plazo en el navegador
  (`VOICE_TIMEOUT_S` más 15 s). Un `422` es silencio; cualquier otro fallo (503, 429, red,
  plazo, CSRF) apaga el dictado del servidor el resto de la sesión y el siguiente dictado usa
  el reconocimiento del navegador. Un permiso de micrófono denegado se explica igual que
  antes.
- **La lectura** es una [nota de voz por respuesta](#notas-de-voz-por-respuesta) (spec
  050): pide `/api/voz/hablar` y reproduce el audio desde un `blob:`. Solo un `503` o un
  `401`/`403` dejan la voz del servidor **en espera 60 s** (`data-voz-respaldo="1"`); en
  ese rato la nota lee con la voz del navegador si existe y, si no, dice «La voz no está
  disponible ahora». Pasado el plazo, la siguiente nota vuelve a probar el servidor, y
  «Reintentar» lo prueba de inmediato. Un `404`, un `422`, un `429`, la red, el plazo o un
  audio que no suena dejan **esa nota** en `error` con reintento: nunca cambian de voz ni
  apagan la del servidor para la sesión.
- **Manos libres** funciona igual: si la transcripción del servidor falla en medio, sigue
  escuchando con el navegador y lo avisa en la nota visible. Sin detector de voz (sin Web
  Audio o con el contexto suspendido), a los 10 s transcribe lo grabado en lugar de
  descartarlo. La respuesta suena con la misma nota y, al terminar, vuelve a escuchar.
- El aviso previo al primer dictado cambia de texto (el audio pasa por el sitio y lo
  transcribe OpenAI) y se acepta aparte del aviso de la voz del navegador.

La CSP de las páginas del chat (`/chat`, también la página de error de sus POST, y
`/chat/new`) añade `media-src 'self' blob:` para reproducir ese audio; el resto del sitio
conserva la política por defecto. El micrófono sigue permitido solo en el documento GET de
`/chat`.

## Notas de voz por respuesta

Spec 050. En producción la lectura acababa en la voz del navegador: el `play()` del audio
del servidor llegaba unos 17 s después del último gesto, el navegador lo bloqueaba
(`NotAllowedError`) y ese único fallo apagaba la lectura del servidor el resto de la
sesión. Ahora cada respuesta guardada trae una nota de voz y la voz no cambia por un fallo
de reproducción.

### Cuándo suena sola

| La pregunta… | Lectura automática | Qué pasa al completar el turno |
|---|---|---|
| se escribió | apagada (por defecto) | La nota queda `lista`; **no** se pide el audio hasta que se pulsa ▶ |
| se escribió | encendida | Se pide el audio y suena sola |
| se dictó (borrador dictado y enviado, o manos libres) | cualquiera | Se pide el audio y suena sola |

«Se dictó» quiere decir que el borrador enviado trae texto del dictado (del servidor o del
navegador): si la persona lo vacía y escribe otra pregunta, cuenta como escrita. Con la
pestaña oculta o un dictado nuevo en curso, la nota se precarga pero no suena. La casilla
«Leer respuestas en voz alta» conserva su sentido: encendida, también suenan las preguntas
escritas. Sin voz del servidor la regla es la misma con la voz del navegador.

### Desbloqueo del audio

Safari, iOS y Chrome solo dejan sonar tras un `await` a un elemento que ya hizo `play()`
dentro de un gesto. El adaptador tiene **un solo** `HTMLAudioElement` para todas las notas
y `desbloquear()` le hace reproducir 0,1 s de silencio (un WAV por `blob:`, porque la CSP
admite `media-src 'self' blob:` y no `data:`) en el gesto que empieza el dictado (Dictar,
el aviso, manos libres) y en el envío del borrador dictado. No se desbloquea al enviar una
pregunta escrita sin lectura automática: en iOS ese `play()` pausaría la música de la
persona. Si aun así el navegador bloquea el audio, la nota queda `bloqueada` con el audio en
caché y un toque lo reproduce dentro del gesto; no se cambia de voz ni se apaga el
servidor. En manos libres un bloqueo apaga el modo (no puede quedar «activo» sin sonar ni
escuchar) y lo explica.

### Precarga, caché y coste

- El audio se pide al completar el turno solo si va a sonar (la persona habló o la lectura
  automática está encendida). Las preguntas escritas sintetizan solo al pulsar ▶.
- **No hay síntesis proactiva en el servidor** al guardar la respuesta: sintetizaría cada
  respuesta aunque nadie la escuche, y cada una cuenta para el tope diario
  (`VOICE_MAX_CALLS_PER_DAY`) y el límite por sesión. Una pregunta dictada cuesta dos
  llamadas (transcribir y hablar); una escrita, cero salvo que se escuche.
- Como mucho una síntesis en vuelo por mensaje (la precarga y el clic la comparten). El
  audio de las últimas 6 notas queda en memoria (LRU); repetir una nota no paga otra
  síntesis. Hay como mucho una URL `blob:` viva, la de la nota cargada, y se revoca al
  terminar, al detener, al cambiar de nota y en `pagehide`.
- Al salir de la página (otro hilo, `/chat/new`) se abortan las síntesis en vuelo.

### Contrato del marcado (con el rediseño, 049)

`chat-voice.js` crea la nota (también para el historial, sin pedir audio) solo si la
página tiene voz del servidor y el turno trae `data-voz-mensaje`. Es un solo elemento, hijo
del `<li class="turno">` justo después de `.burbuja`; la franja `.turno-voz` del turno se
oculta con `hidden`:

```html
<div class="turno-nota" role="group" aria-label="Nota de voz de la respuesta"
     data-nota-estado="lista" data-nota-mensaje="<message_id>" style="--nota-avance: 0.000">
  <button type="button" class="turno-nota-boton" data-nota-accion="alternar"
          aria-pressed="false" aria-label="Reproducir la nota de voz"></button>
  <span class="turno-nota-barra" role="progressbar" aria-label="Avance de la nota de voz"
        aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" aria-valuetext="0:00"></span>
  <span class="turno-nota-tiempo">0:00</span>
  <span class="turno-nota-duracion"></span>
  <span class="turno-nota-estado" role="status" aria-live="polite"></span>
  <span class="turno-nota-aviso" aria-hidden="true" hidden></span>
  <button type="button" class="turno-nota-reintentar" data-nota-accion="reintentar" hidden>Reintentar</button>
</div>
```

| `data-nota-estado` | Cuándo | Botón |
|---|---|---|
| `lista` | Sin reproducir, o terminada (avance a 0) | `aria-pressed="false"`, «Reproducir la nota de voz» |
| `cargando` | Se pide el audio o va a empezar | `aria-busy="true"`; pulsarlo cancela la reproducción (la síntesis sigue y queda en caché) |
| `sonando` | Suena (servidor o respaldo del navegador) | `aria-pressed="true"`, «Pausar la nota de voz» |
| `pausada` | En pausa | «Reanudar la nota de voz» |
| `bloqueada` | El navegador no dejó sonar sola | «Reproducir la nota de voz»; `.turno-nota-aviso` visible |
| `error` | Falló la síntesis o el audio | «Reintentar…»; `.turno-nota-reintentar` y `.turno-nota-aviso` visibles |

- `--nota-avance` (0–1, en la raíz) y `aria-valuenow` (0–100) siguen al audio; con el
  respaldo del navegador avanzan por partes leídas. `.turno-nota-duracion` queda vacía hasta
  conocer la duración y después muestra «m:ss».
- `.turno-nota-estado` es la región viva: anuncia «Preparando…», «Reproduciendo…», «En
  pausa», el bloqueo o el error; con nota, la línea de estado global no repite la lectura.
  `.turno-nota-aviso` repite el texto del bloqueo o del error para quien ve la pantalla.
- `chat.css` solo añade lo funcional: `.turno-voz[hidden]` oculto y un mínimo dentro de
  `:where()` (especificidad cero) que cualquier regla `.turno-nota-*` del rediseño sustituye.

## Requisitos de despliegue

- El dictado exige contexto seguro: **HTTPS** (o `localhost`). Por IP y HTTP el navegador no
  ofrece el micrófono; la lectura sí funciona.
- Detrás de nginx con TLS, `FORWARDED_ALLOW_IPS` debe ser la puerta de enlace de la red de
  Docker para que la app vea `https` (cookies `Secure`, HSTS): ver
  [`despliegue-ovh.md` §10](despliegue-ovh.md#10-si-más-adelante-pones-un-dominio).
- Coste: el tope diario por proceso y los límites por sesión, IP y global acotan el gasto;
  `/health` muestra `calls_today` al operador. Contra audios largos, el servidor comprueba la
  duración declarada y un tope de bytes derivado de ella. Queda un riesgo acotado: un
  cliente que falsee la cabecera puede subir, dentro del tope de bytes, Opus a 6 kbps (unos
  10 minutos en 480 KB); los límites por IP y global lo frenan.

## Estados de la voz para el estilo

`chat-voice.js` expone el estado de la voz solo con atributos en `<html>`. El CSS los estila
y el JavaScript no conoce clases de diseño.

| Atributo | Cuándo |
|---|---|
| `data-voz="1"` / `data-voz-tts="1"` | Hay voz, o hay síntesis, disponible en la página |
| `data-voz-escuchando="1"` | Dictado en curso, del navegador o del servidor |
| `data-voz-grabando="1"` + `--voz-progreso` (0–1) | El servidor graba; el progreso se mide frente a `VOICE_MAX_AUDIO_SECONDS` |
| `data-voz-transcribiendo="1"` | El audio se está transcribiendo en el servidor |
| `data-voz-sintetizando="<message_id>"` | Se pide al servidor el audio de esa respuesta (también una precarga; con varias, la última) |
| `data-voz-respaldo="1"` | Dictado o lectura del servidor apagados (el dictado, el resto de la sesión; la lectura, en espera de 60 s tras un 503 o un 401/403) |
| `data-voz-manos="1"` | Modo manos libres activo |

Por turno, los botones `data-voz-accion` usan `aria-pressed` y `aria-busy`; con voz del
servidor la nota los sustituye (ver [su contrato](#contrato-del-marcado-con-el-rediseño-049)).
