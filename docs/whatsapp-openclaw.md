# WhatsApp con OpenClaw (servidor MCP)

El asistente del chat web también responde por WhatsApp. [OpenClaw](https://docs.openclaw.ai)
recibe los mensajes y llama a una sola herramienta MCP, `consultar_carvajal`, que ejecuta
**el mismo turno del chat web** (modo `tools`, con el filtro de evidencia, la abstención, el
respaldo determinista, el tope de cotizaciones y los folios). OpenClaw no redacta datos:
reenvía el texto que devuelve la herramienta.

> Para un relé que no hable MCP (WhatsApp Business API, Twilio u otro), el mismo turno se
> expone como webhook firmado en `POST /api/webhooks/whatsapp`: ver
> [`webhooks.md`](webhooks.md).

```text
WhatsApp ──► OpenClaw (VM aparte) ──► 127.0.0.1:8765 ══ túnel SSH ══► 127.0.0.1:8765 (VPS)
                agente «ventas»                                       servicio mcp
                                                                         │
                                               ChatService ── Qdrant · Ollama · Postgres
```

## Qué hace la herramienta

`consultar_carvajal(mensaje, conversacion, nueva=false, voz=false)`

| Parámetro | Uso |
|---|---|
| `mensaje` | Texto del cliente (máx. 500 caracteres, igual que la web). |
| `conversacion` | Identificador estable del chat: número o JID del remitente. |
| `nueva` | `true` abre un hilo nuevo (equivale a «Nueva conversación»). |
| `voz` | `true` si el cliente mandó una nota de voz: respuesta apta para TTS y enlaces aparte (ver [Mensajes de voz](#mensajes-de-voz)). |

- **Identidad**: el número nunca se guarda en claro. El `owner_key` es
  `wa_` + HMAC-SHA256(`SESSION_SECRET`, número normalizado): cada chat tiene su hilo, su
  memoria comercial y su cupo diario (`DAILY_REQUESTS_LIMIT`), como un visitante web. El
  número o JID se reduce a E.164 (`573100000000@s.whatsapp.net`, `whatsapp:+57…` y
  `+57 310 000 0000` dan `+573100000000`), el mismo que usa el webhook firmado.
- **Cotizaciones**: se leen de la tabla `quotes` por folio y se redactan como texto
  (`fallback.quote_text`), con folio, presentación, subtotal, IVA, envío y total.
- **Formato**: el Markdown del modelo pasa a formato de WhatsApp sin LLM
  (`*negrita*`, viñetas con `-`, enlaces como `texto (url)`), y se añade `*Fuentes:*`.
- **Fallos**: el canal recibe siempre un mensaje legible, nunca una traza interna.

## Cotización en PDF

Cada cotización de WhatsApp sale con folio, presentación, unidades, subtotal, IVA, envío,
total y vigencia, y termina con «¿Quieres esta cotización en PDF? Responde *PDF*».

- Cuando el cliente responde *PDF* (o «sí, el PDF», «envíame la cotización en pdf»), la
  herramienta busca la **última cotización de la conversación activa** y devuelve un
  enlace. No llama al LLM, pero consume el mismo cupo diario que cualquier mensaje.
- El enlace es `PUBLIC_BASE_URL/cotizaciones/{folio}.pdf?e=…&t=…`, firmado con HMAC
  (`SESSION_SECRET`) sobre el folio, el dueño y el vencimiento. Vence con la vigencia de
  la cotización (máximo 7 días); al validar se toleran 5 minutos de desfase de reloj
  entre los procesos MCP y web. El dueño no va en la URL: la ruta lee la cotización por
  folio y verifica la firma contra su `owner_key`. Cualquier fallo responde 404.
- El PDF se genera al vuelo con fpdf2 (sin dependencias del sistema) desde el payload de
  `quotes`: logo de Carvajal, folio y fechas, producto, tabla de importes, total,
  vendedor, fuente del precio y nota legal. Los recursos están en `apps/api/pdf/`.
- Para activarlo, define `PUBLIC_BASE_URL` en el `.env` del servidor
  (p. ej. `http://203.0.113.10:8080`, con la IP del servidor). Sin ella, no se ofrece el PDF.

## Fotos del catálogo

Las fotos son opcionales y se versionan con el catálogo:
`data/catalog/empaques/imagenes/{sku}.jpg` y su procedencia en
`data/catalog/empaques/imagenes.csv` (`sku, archivo, fuente_url, imagen_url,
tipo_fuente, capturada_el`).

- **Curaduría**: `uv run --extra capture python scripts/curar_imagenes_co.py` toma la
  foto principal (`og:image`) de cada ficha oficial de `colombia.carvajalempaques.com`
  para las referencias con precio (`--todas` para todo CO, `--sku` para una), respetando
  `robots.txt`, y la reduce a 600 px en JPEG. Las tallas de una misma línea comparten
  foto. Las fichas de terceros (EDEXA) se omiten salvo `--incluir-terceros`: úsalas solo
  con permiso de su dueño. Revisa `imagenes/revision.html` antes de versionar.
- **PDF**: la foto va junto al bloque del producto; sin foto, el bloque ocupa todo el
  ancho.
- **WhatsApp**: la cotización añade `📷 Foto: PUBLIC_BASE_URL/catalogo/imagenes/{sku}.jpg`
  y WhatsApp muestra la vista previa.
- **Despliegue**: la imagen Docker copia `data/catalog` y el despliegue siembra los
  archivos nuevos en el volumen; además, la app busca en `seed-data/catalog` si el
  volumen aún no tiene la foto.

## 1. Activarlo en el VPS

En `/opt/carvajal/.env`:

```bash
COMPOSE_PROFILES=whatsapp
MCP_TOKEN=<python -c "import secrets; print(secrets.token_urlsafe(32))">
```

El siguiente despliegue (`scripts/deploy.sh`) levanta el servicio `mcp` con la misma
imagen que `app`. Para no esperar al despliegue:

```bash
cd /opt/carvajal
docker compose -f compose.prod.yml up -d mcp
docker compose -f compose.prod.yml ps mcp
curl -s 127.0.0.1:8765/live        # ok
curl -s -o /dev/null -w '%{http_code}\n' -X POST 127.0.0.1:8765/mcp   # 401 sin token
```

El puerto queda en `127.0.0.1` del VPS: no se abre nada hacia internet ni se toca nginx.

## 2. Túnel desde la máquina de OpenClaw

En la máquina de OpenClaw, una llave solo para el túnel:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/ovh_tunnel -N "" -C "openclaw-tunnel"
cat ~/.ssh/ovh_tunnel.pub
```

En el VPS, en `~deploy/.ssh/authorized_keys`, una sola línea con las restricciones:

```text
restrict,port-forwarding,permitopen="127.0.0.1:8765",command="/bin/false" ssh-ed25519 AAAA... openclaw-tunnel
```

La llave no abre shell ni ejecuta comandos: solo reenvía ese puerto. En la máquina de
OpenClaw, un servicio de usuario que lo mantiene arriba:

```ini
# ~/.config/systemd/user/ovh-tunnel.service
[Unit]
Description=Tunel SSH al MCP de Carvajal
After=network-online.target

[Service]
ExecStart=/usr/bin/ssh -NT -i %h/.ssh/ovh_tunnel -p 2422 \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new \
  -L 127.0.0.1:8765:127.0.0.1:8765 deploy@203.0.113.10
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now ovh-tunnel
curl -s 127.0.0.1:8765/live    # ok, ya a través del túnel
```

## 3. OpenClaw: servidor MCP y agente «ventas»

Registra el servidor (la cabecera lleva el `MCP_TOKEN`; guárdala con el mecanismo de
secretos de OpenClaw, no como literal):

```bash
openclaw mcp add carvajal --help          # confirma la opción de cabeceras de tu versión
openclaw mcp add carvajal --url http://127.0.0.1:8765/mcp --transport streamable-http \
  --header "Authorization: Bearer <MCP_TOKEN>"
openclaw mcp probe carvajal               # debe listar consultar_carvajal
```

Crea un agente aislado para clientes y enrútale el número comercial:

```bash
openclaw agents add ventas
openclaw channels login --channel whatsapp --account ventas   # QR del número comercial
```

```json5
{
  bindings: [{ agentId: "ventas", match: { channel: "whatsapp", accountId: "ventas" } }],
}
```

El agente «ventas» **no** debe tener el perfil `full`: solo la herramienta
`consultar_carvajal` (sin `exec`, archivos ni navegador). En su `AGENTS.md`:

```markdown
- Para cada mensaje del cliente llama a consultar_carvajal con el mensaje completo y,
  en `conversacion`, el número o JID del remitente (siempre el mismo para el mismo chat).
- Si el cliente escribe /new o pide empezar de nuevo, llama con nueva=true.
- Responde exactamente con el texto que devuelve la herramienta. No cambies cifras,
  precios, folios ni fuentes, y no agregues información propia.
```

Verifica en los logs del servicio `mcp` que dos chats distintos producen `owner_key`
distintos (`docker compose -f compose.prod.yml logs mcp | grep owner_key`): si el agente
no pasa el remitente en `conversacion`, todos los chats compartirían un hilo.

## Mensajes de voz

El cliente puede hablarle al número comercial. OpenClaw transcribe la nota de voz, llama a
`consultar_carvajal` con el texto transcrito y `voz=true`, y responde con una nota de voz
(TTS). Este repositorio **no** transcribe ni sintetiza audio, y no guarda llaves de ningún
proveedor de voz: todo eso vive en el servidor de OpenClaw.

### Contrato de `voz=true`

La herramienta sigue devolviendo **un solo texto** (el mismo tipo `str`, el mismo
`structuredContent` `{"result": …}`); con `voz=false`, que es el valor por defecto, la
respuesta es idéntica a la de antes. Con `voz=true` el texto tiene dos partes:

```text
<texto para leer en voz alta>

--- ENLACES (enviar como texto, no leer en voz alta) ---
<rótulo>: <url>
<rótulo>: <url>
```

- La sección de enlaces solo aparece si hay alguno (PDF de la cotización, foto del producto,
  fuentes). La línea separadora es la constante `carvajal.speech.LINKS_HEADER` y
  `carvajal.speech.split_voice_reply` separa ambas partes.
- El texto hablado se genera sin LLM (`carvajal.speech.to_speech`) desde el mismo turno:
  sin citas `[n]`, sin Markdown ni formato de WhatsApp, sin emojis y sin URL (dentro de una
  frase se dice «el enlace»); las fuentes en una frase («Según el sitio de Carvajal
  Empaques.», «Lo saqué de dos documentos.»); las listas en una frase o con «Primero…
  Luego…»; las unidades y abreviaturas en palabras («12 oz» → «12 onzas», «x 200 und» → «de
  200 unidades», «aprox.» → «aproximadamente», «19 %» → «19 por ciento», «1 dic 2026» → «1
  de diciembre de 2026»); los importes conservan los dígitos («$370.000 COP» → «370.000
  pesos»), y el folio se deletrea por grupos («a3f09c12b7e4» → «A 3 F 0, 9 C 1 2, B 7 E 4»).
  Los códigos técnicos (SKU) no se leen, igual que en la lectura en voz de la web.
- La cotización se redacta como frases (`spoken_quote`) con las mismas cifras del bloque de
  WhatsApp; la oferta del PDF se dice «Si quieres esta cotización en PDF, respóndeme PDF y
  te envío el enlace». El pedido «PDF» por voz responde con el folio y la vigencia en el
  texto hablado y el enlace firmado en la sección de enlaces.

Ejemplo (pedido «PDF» con `voz=true`):

```text
Te envío por escrito el enlace al PDF de tu cotización con folio A 3 F 0, 9 C 1 2, B 7 E 4. El enlace funciona hasta el 1 de diciembre de 2026.

--- ENLACES (enviar como texto, no leer en voz alta) ---
Cotización a3f09c12b7e4 en PDF: http://203.0.113.10:8080/cotizaciones/a3f09c12b7e4.pdf?e=…&t=…
```

### Configuración de OpenClaw (servidor de OpenClaw)

Los proveedores de transcripción (STT) y de voz (TTS) son un **marcador**: se sugiere
OpenAI (`gpt-4o-mini-transcribe` y `gpt-4o-mini-tts`), pero sirve cualquiera que soporte
OpenClaw. La llave va en el entorno del servidor de OpenClaw (`OPENAI_API_KEY` o el
mecanismo de secretos de OpenClaw), **nunca** en este repositorio ni en su `.env`.

```json5
// ~/.openclaw/openclaw.json (servidor de OpenClaw)
{
  tools: {
    media: {
      // Transcripción de notas de voz entrantes (STT).
      models: [
        {
          provider: "<PROVEEDOR_STT>",          // p. ej. "openai"
          model: "<MODELO_STT>",                // p. ej. "gpt-4o-mini-transcribe"
          capabilities: ["audio"],
        },
      ],
      audio: {
        enabled: true,
        language: "es",                         // español; sin la clave, autodetección
      },
    },
  },
  // Respuesta con nota de voz (TTS) solo cuando el cliente mandó una nota de voz.
  tts: {
    auto: "inbound",
    provider: "<PROVEEDOR_TTS>",                // p. ej. "openai"
    providers: {
      openai: {
        apiKey: "${OPENAI_API_KEY}",            // variable de entorno del servidor de OpenClaw
        model: "<MODELO_TTS>",                  // p. ej. "gpt-4o-mini-tts"
        speakerVoice: "<VOZ>",                  // p. ej. "coral"; prueba una que suene natural en español
        instructions: "Habla en español de Colombia, cálido y claro.",
      },
    },
    modelOverrides: { enabled: true },          // permite el bloque [[tts:text]] del agente
  },
}
```

> ⚠️ Las claves cambian entre versiones de OpenClaw. La documentación vigente
> (2026-11-24) pone los modelos de transcripción en `tools.media.models` y la voz en `tts`
> en la raíz; en versiones anteriores la voz vivía en `messages.tts`. Contrasta el bloque
> con `docs.openclaw.ai` (Audio y TTS) de tu versión y valida con `openclaw doctor` antes de
> reiniciar. Si en ese servidor viven otros agentes, limita la voz al número comercial con
> la anulación por cuenta (`channels.whatsapp.accounts.ventas.tts`) en lugar de la global.

### `AGENTS.md` del agente «ventas» (añadir al existente)

```markdown
## Mensajes de voz

- Si el mensaje del cliente llegó como nota de voz (OpenClaw lo transcribe y lo marca como
  audio), llama a consultar_carvajal con la transcripción en `mensaje` y voz=true. Si llegó
  como texto, no pases voz (o pasa voz=false).
- Con voz=true la herramienta devuelve el texto para leer en voz alta y, si hay enlaces,
  al final una línea «--- ENLACES (enviar como texto, no leer en voz alta) ---» seguida de
  un enlace por línea.
- Responde así: el texto hablado, sin cambios, dentro de [[tts:text]]…[[/tts:text]]; y
  fuera de ese bloque, los enlaces tal cual (sin la línea «--- ENLACES … ---»), para que
  lleguen como texto. Si no hay enlaces, responde solo con el bloque [[tts:text]].
- Nunca leas una URL en voz alta ni la metas dentro de [[tts:text]]. No cambies cifras,
  precios, folios ni fuentes, y no agregues información propia.
- Si el cliente pide el PDF por voz («mándame el PDF»), pásale el mensaje igual, con
  voz=true: el enlace llega en la sección de enlaces.
```

Pendiente de verificar en el servidor de OpenClaw (no se puede probar desde este
repositorio): que WhatsApp entregue la nota de voz **y** el texto visible con los enlaces
cuando la respuesta trae un bloque `[[tts:text]]`, y cómo llega la transcripción al agente
(OpenClaw la pone en el cuerpo del mensaje dentro de un bloque `[Audio]`). Si tu versión
no admite `[[tts:text]]`, que el agente envíe los enlaces en un mensaje aparte con su
herramienta de mensajes y responda solo con el texto hablado.

## Operación

- Logs: `docker compose -f compose.prod.yml logs -f mcp` (JSON, con `owner_key` opaco).
- Apagar WhatsApp: quita `COMPOSE_PROFILES=whatsapp` del `.env` y
  `docker compose -f compose.prod.yml stop mcp`.
- Rotar el token: cambia `MCP_TOKEN`, `docker compose -f compose.prod.yml up -d mcp` y
  actualiza la cabecera en OpenClaw.
- Privacidad: los mensajes de WhatsApp se procesan con DeepSeek igual que los de la web.
