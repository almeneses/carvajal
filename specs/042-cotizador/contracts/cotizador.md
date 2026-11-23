# Contrato — Cotizador

**Fecha**: 2026-11-21 · **Plan**: [plan.md](./plan.md)

## Rutas

| Método | Ruta | Respuesta |
|---|---|---|
| `GET` | `/cotizador` | HTML de la sección; admite `?thread=<id>` como el chat |
| `POST` | `/cotizador` | Fragmento HTML con el turno del usuario y la cotización (respaldo sin JavaScript) |
| `POST` | `/cotizador/stream` | SSE con `open`, `token`, `products`, `done` y `error` |
| `POST` | `/cotizador/nuevo` | 303 a `/cotizador?thread=<id>` con un hilo nuevo |
| `GET` | `/cotizador/ficha/<slug>` | Ficha citable de una oferta; 404 si no existe o el slug no es válido |

`POST /cotizador` acepta `application/x-www-form-urlencoded`:

| Campo | Obligatorio | Uso |
|---|---|---|
| `question` | sí | texto del turno, hasta 500 caracteres |
| `thread_id` | no | hilo existente; vacío crea uno en el primer turno |
| `csrf_token` | sí | token del middleware de seguridad |

Errores: `403` CSRF inválido, `429` cuota diaria con `Retry-After`, `503` base
indisponible, `422` pregunta vacía o demasiado larga. El cuerpo de error es HTML con
`role="alert"` para que el cliente lo inserte en el hilo. La respuesta correcta lleva la
cabecera `X-Thread` con el hilo usado, para que el compositor lo reutilice.

## Respuesta del turno

```html
<li class="turno turno-user">…</li>
<li class="turno turno-assistant">
  <div class="texto texto-formateado">…resumen…</div>
  <section class="cotizacion" aria-label="Cotización">…</section>
</li>
```

- La sección `cotizacion` incluye: resumen de lo entendido, oferta principal, alternativas,
  sedes con mapa OSM incrustado, entrega y sellos de datos simulados.
- Los importes van en el texto, nunca en atributos interpretados por scripts.
- El HTML se genera en servidor con autoescape de Jinja; no llega Markdown del LLM.

## Stream SSE

Mismo formato de cable que el chat (`apps/api/sse.py`):

| Evento | Carga |
|---|---|
| `open` | `{"stream": "cotizador"}` |
| `token` | `{"text": "trozo de la respuesta"}` |
| `products` | `{"html": "<section class=\"coti\">…"}` ya escapado |
| `done` | `{"conversation_id", "message_id", "user_message_id", "thread_id"}` |
| `error` | `{"message", "incomplete"}` |

Los errores previos al stream (CSRF, cupo, hilo ajeno) usan el mismo formato SSE con
`X-Carvajal-Error: 1`. La persistencia ocurre una vez, justo antes de `done`; si el cliente
corta antes, no se guarda un turno a medias.

## Cotización emitida

Cada respuesta `kind="cotizacion"` añade:

```json
{"quote": {"id": "b6a9b951ac0f", "valid_until": "2026-11-28", "days": 7}}
```

y se registra en `quotes` con el total, la moneda y el payload mostrado. Las aclaraciones
y los resultados vacíos no emiten registro. El identificador se muestra en la tarjeta.

## Fichas

Una ficha por oferta (`<sku>-<provider_id>.md`) con front matter (producto, sede,
procedencia, fecha y campos simulados) y cuerpo Markdown. Se sirve saneada en
`/cotizador/ficha/<slug>`; el slug se valida con `[A-Za-z0-9._-]` y nunca sale del
directorio de fichas.

## Persistencia

- Hilo: `conversations.owner_key = "cot:" + owner_key` del visitante.
- Mensajes: `messages` con `role`, `content` y `sources`; la cotización estructurada viaja
  en `sources[0]` con `kind="cotizacion"`.
- Reintento: se usa el mismo `client_turn_id` del chat para no duplicar el turno.
- El historial se pinta con `_chat_thread.html`: si el mensaje trae una cotización, se
  renderiza `_cotizacion.html` con ese payload.

## Aislamiento

- El cliente no envía el espacio de nombres; se deriva del `owner_key` firmado.
- Un `thread_id` de otro dueño o del chat general responde `404`.
- El chat general no consulta mensajes con prefijo `cot:`.

## Seguridad y límites

- Se reutilizan CSRF, cuota, cookies `HttpOnly` y `Permissions-Policy` del chat.
- `/cotizador` habilita el micrófono solo en su documento si el dictado se reutiliza.
- El mapa es un iframe de `https://www.openstreetmap.org`; la CSP agrega solo
  `frame-src https://www.openstreetmap.org` y conserva el resto cerrado al origen.
- OpenStreetMap ve la IP del visitante; no recibe la conversación ni el perfil. Los enlaces
  externos se abren en otra pestaña con `rel="noopener"`.
- No se guardan coordenadas del visitante ni se pide geolocalización.
- `COTIZADOR_DEMO=0` apaga los productos simulados y los precios simulados: solo se cotiza
  lo publicado y la existencia se declara desconocida.
- La memoria del visitante vive en `visitor_memory` (tabla 0008), nunca en el payload ni
  en las trazas. Se aplica en la solicitud y se borra con «olvida mis datos»; caduca a los
  180 días.
