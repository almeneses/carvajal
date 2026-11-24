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
| `voz` | `true` si el cliente mandó una nota de voz: resumen hablado apto para TTS y, aparte, el texto escrito completo o los enlaces (ver [Mensajes de voz](#mensajes-de-voz)). |

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
- El enlace es `PUBLIC_BASE_URL/cotizaciones/{folio}.pdf?t=<vence>.<firma>` (un solo parámetro, sin `&`, para que WhatsApp lo detecte entero), firmado con HMAC
  (`SESSION_SECRET`) sobre el folio, el dueño y el vencimiento. Vence con la vigencia de
  la cotización (máximo 7 días); al validar se toleran 5 minutos de desfase de reloj
  entre los procesos MCP y web. El dueño no va en la URL: la ruta lee la cotización por
  folio y verifica la firma contra su `owner_key`. Cualquier fallo responde 404.
- El PDF se genera al vuelo con fpdf2 (sin dependencias del sistema) desde el payload de
  `quotes`: logo de Carvajal, folio y fechas, producto, tabla de importes, total,
  vendedor, fuente del precio y nota legal. Los recursos están en `apps/api/pdf/`.
- Para activarlo, define `PUBLIC_BASE_URL` en el `.env` del servidor
  (p. ej. `http://203.0.113.10:8080`, con la IP del servidor). Sin ella, no se ofrece el PDF.
- Usa un **nombre**, no la IP: WhatsApp en el celular no siempre convierte en enlace
  una URL con IP. Sin dominio propio sirve `http://<ip-con-guiones>.sslip.io:8080`
  (p. ej. `http://203-0-113-10.sslip.io:8080`), que resuelve a esa misma IP.

### PDF como documento adjunto (recomendado)

Con `MEDIA_BASE_URL` (p. ej. `http://203.0.113.10:8080`, la IP del servidor), al pedir
«PDF» la herramienta responde:

```text
📄 Te envío tu cotización #c0032295af55 en PDF (válida hasta el 1 dic 2026).
ADJUNTAR_PDF: http://203.0.113.10:8080/cotizaciones/c0032295af55.pdf?t=<vence>.<firma>
```

OpenClaw descarga esa URL **desde el servidor** y la envía como documento, así que no
depende de que el navegador del celular abra un enlace `http` (Safari lo fuerza a HTTPS).
Para eso el agente «ventas» necesita la herramienta `message` (quítala de su `deny`) y
esta regla en su `AGENTS.md`:

```markdown
- Si el resultado de consultar_carvajal trae una línea que empieza por "ADJUNTAR_PDF: ",
  envía esa URL como documento al MISMO chat con la herramienta message (media = la URL,
  sin texto), y responde al cliente el resto del texto SIN esa línea.
- Nunca uses message para escribirle a otro chat o número.
```

Sin `MEDIA_BASE_URL`, el PDF se entrega como enlace con `PUBLIC_BASE_URL`.

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
respuesta es idéntica a la de antes. Con `voz=true` la nota de voz es un **resumen** de la
respuesta (unos 15-20 s) y el detalle va por escrito. El texto tiene una de dos formas:

```text
<resumen para leer en voz alta>

--- TEXTO (enviar como mensaje escrito, no leer en voz alta) ---
<la respuesta completa de WhatsApp, igual que con voz=false: formato, enlaces, fuentes,
cotización y oferta del PDF>
```

```text
<texto para leer en voz alta>

--- ENLACES (enviar como texto, no leer en voz alta) ---
<rótulo>: <url>
<rótulo>: <url>
```

- **Con sección `TEXTO`** (spec 048): el resumen dejó algo fuera (frases, el detalle de una
  lista) o el turno trae cotización. Lo que sigue a la línea es exactamente la respuesta de
  `voz=false` y ya lleva los enlaces, así que **no** hay sección de enlaces (solo aparecería,
  detrás, con algún enlace que no estuviera en el texto escrito). Esa parte se envía como
  mensaje de texto normal: dentro de `[[tts:text]]` no se vería y el cliente perdería el
  detalle.
- **Sin sección `TEXTO`**: la respuesta era corta y se lee completa (abstenciones, saludos,
  el pedido «PDF», el aviso de cupo). Si hay enlaces (PDF de la cotización, foto, fuentes),
  van tras la línea `ENLACES`, como antes.
- Las líneas separadoras son las constantes `carvajal.speech.TEXT_HEADER` y
  `carvajal.speech.LINKS_HEADER`. `carvajal.speech.parse_voice_reply` devuelve las tres
  partes (`spoken`, `written`, `links`); `carvajal.speech.split_voice_reply` sigue
  devolviendo `(texto hablado, enlaces)` y descarta la parte escrita. Ningún código de
  producción de este repositorio las usa: quien interpreta el texto es el agente de
  OpenClaw, según su prompt.
- ⚠️ **Nota de despliegue.** Con `voz=true` la respuesta cambia de forma (antes: texto
  hablado + `ENLACES`; ahora, casi siempre: resumen + `TEXTO`). El prompt del agente vive en
  el servidor de OpenClaw, no aquí: con el anterior («el texto hablado dentro de
  [[tts:text]]; fuera, los enlaces tras la línea ENLACES») el agente leería en voz alta todo
  lo que va antes de `ENLACES`, incluidas la línea `--- TEXTO … ---` y la respuesta
  completa, y la nota de voz sería más larga que antes. **Actualiza la sección «Mensajes de
  voz» del `AGENTS.md` del agente ([abajo](#agentsmd-del-agente-ventas-añadir-al-existente))
  antes de desplegar esta versión del servidor MCP, o en el mismo momento.**
- El resumen (`carvajal.speech.spoken_summary`, canal `whatsapp`) es **extractivo y sin
  LLM**: nunca inventa, solo reutiliza frases y cifras de la respuesta, completas (no corta
  una cifra). Presupuesto de unas 55 palabras: la primera frase del primer párrafo
  (siempre), la pregunta final al cliente si la hay («¿Cuántas unidades necesitas y en qué
  ciudad?»), la primera lista en una frase y más frases del primer párrafo mientras quepan.
  Una lista de hasta cuatro ítems cortos se nombra por la cabeza de cada ítem (lo que va
  antes de la primera coma, dos puntos, raya o paréntesis): «Te dejo por escrito tres
  opciones: A, B y C.»; si no, solo cuántas hay: «Te dejo cinco opciones por escrito.». Una
  lista numerada son pasos («Te dejo tres pasos por escrito.»). Las cabezas tampoco se
  nombran si cortarían una cifra, un rango o una enumeración («Vasos de 7, 9 y 12 oz» no se
  dice «Vasos de 7»), si quedarían repetidas o si solo nombran un campo («Precio: …»). Las
  fuentes, en una frase. Si se omitió algo más que el detalle de la lista (frases, otro
  párrafo, la pregunta), «Te dejo el detalle por escrito.» antes de la pregunta, haya lista
  o no. El presupuesto cuenta todo lo que se dice; solo la primera frase, la frase corta de
  la lista, la de las fuentes y el aviso entran aunque no quepan, así que un resumen que
  pasa del presupuesto dice únicamente esas piezas.
- El mensaje transcrito pasa por `carvajal.speech.normalize_transcript` antes del turno: los
  STT escriben «mil ochocientos» como «1,800» (miles de EE. UU.), que aquí se leería «uno
  coma ocho». Se reescribe a «1.800» («12,500,000» → «12.500.000», «2,500.50» →
  «2.500,50»); decimales («1,5», «0,75»), horas («10:30») y listas («2, 3 y 4») no cambian.
  Por escrito (`voz=false`) el mensaje no se toca.
- El texto hablado se genera sin LLM (`carvajal.speech.to_speech`) desde el mismo turno:
  sin citas `[n]`, sin Markdown ni formato de WhatsApp, sin emojis y sin URL (dentro de una
  frase se dice «el enlace»); las fuentes en una frase («Según el sitio de Carvajal
  Empaques.», «Lo saqué de dos documentos.»); las listas en una frase o con «Primero…
  Luego…»; las unidades y abreviaturas en palabras («12 oz» → «12 onzas», «x 200 und» → «de
  200 unidades», «aprox.» → «aproximadamente», «19 %» → «19 por ciento», «1 dic 2026» → «1
  de diciembre de 2026»); los importes conservan los dígitos («$370.000 COP» → «370.000
  pesos»). Del folio solo se dicen los **últimos cuatro caracteres**, deletreados («tu
  cotización terminada en B 7 E 4»); el folio completo va por escrito. Los códigos técnicos
  (SKU) no se leen, igual que en la lectura en voz de la web.
- La cotización hablada (`spoken_quote`) es **corta, unos 15 segundos** (≤ 45 palabras con
  la oferta del PDF): cuántas presentaciones y de cuántas unidades, el producto, la ciudad,
  el total con el IVA y el envío, con las cifras exactas del payload. La presentación con su
  precio, el subtotal, el vendedor, las fechas y el folio quedan en el texto de WhatsApp y
  en el PDF. La oferta del PDF se dice «Si quieres el detalle, respóndeme PDF y te lo mando
  por escrito.». El pedido «PDF» por voz responde con el final del folio en el texto
  hablado y el enlace firmado (con su vigencia) en la sección de enlaces. Con cotización
  siempre va la sección `TEXTO`: el folio, la presentación con su precio, el subtotal y la
  foto van por escrito.
- Con `voz=true` el pedido «PDF» acepta también lo que un STT suele oír: «pe de efe»,
  «pe de fe», «p de f», «impedefe», «MPDF», con o sin tildes, mayúsculas o letras
  cirílicas. Por escrito valen «PDF», «pdf», «P.D.F.» y «p d f». En ambos casos el mensaje
  debe ser corto y sin otra intención: «¿el PDF trae el IVA?» sigue yendo al agente.

Ejemplo de respuesta del catálogo con `voz=true`:

```text
Para bebidas calientes, Carvajal Empaques ofrece vasos de papel de pared sencilla y doble pared. Te dejo por escrito tres opciones: Vaso de papel 8 onzas, Vaso de papel 12 onzas y Vaso de papel 16 onzas.

Según el sitio de Carvajal Empaques.

¿Cuántas unidades necesitas y en qué ciudad?

--- TEXTO (enviar como mensaje escrito, no leer en voz alta) ---
Para bebidas calientes, Carvajal Empaques ofrece vasos de papel de pared sencilla y doble pared [1]. …
- *Vaso de papel 8 oz*: pared sencilla, caja x 1.000 und, ideal para tinto.
…
*Fuentes:*
[1] Vasos de papel — https://colombia.carvajalempaques.com/vasos
```

Ejemplo de cotización con `voz=true`:

```text
Son 2 cajas de 1.000 unidades de Vaso de papel 12 onzas en Cali, por un total de 370.000 pesos con IVA incluido. El envío se confirma con el vendedor.

Según el sitio de Carvajal Empaques.

Si quieres el detalle, respóndeme PDF y te lo mando por escrito.

--- TEXTO (enviar como mensaje escrito, no leer en voz alta) ---
Te comparto la cotización de *2 cajas* …
🧾 *Cotización #a3f09c12b7e4*
…
¿Quieres esta cotización en PDF? Responde *PDF* 📄
```

Ejemplo (pedido «PDF» con `voz=true`):

```text
Te mando por escrito el PDF de tu cotización terminada en B 7 E 4.

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
- Con voz=true la herramienta devuelve primero un resumen corto para leer en voz alta y,
  después, una de estas secciones, las dos (primero TEXTO y luego ENLACES) o ninguna:
  - «--- TEXTO (enviar como mensaje escrito, no leer en voz alta) ---» seguida de la
    respuesta completa por escrito (con sus enlaces, fuentes y oferta del PDF);
  - «--- ENLACES (enviar como texto, no leer en voz alta) ---» seguida de un enlace por
    línea.
- Responde así: el resumen, sin cambios, dentro de [[tts:text]]…[[/tts:text]]; y fuera de
  ese bloque, tal cual y sin las líneas «--- TEXTO … ---» ni «--- ENLACES … ---», el texto
  escrito y los enlaces que vengan, para que lleguen como mensaje de texto. Si no viene
  ninguna sección, responde solo con el bloque [[tts:text]].
- Nunca metas el texto escrito ni una URL dentro de [[tts:text]]: solo el resumen se lee.
  No cambies cifras, precios, folios ni fuentes, y no agregues información propia.
- Si el cliente pide el PDF por voz («mándame el PDF»), pásale el mensaje igual, con
  voz=true: el enlace llega en la sección de enlaces.
```

Pendiente de verificar en el servidor de OpenClaw (no se puede probar desde este
repositorio): que WhatsApp entregue la nota de voz **y** el texto visible (la sección
`TEXTO` o los enlaces) cuando la respuesta trae un bloque `[[tts:text]]`, y cómo llega la
transcripción al agente (OpenClaw la pone en el cuerpo del mensaje dentro de un bloque
`[Audio]`). Si tu versión no admite `[[tts:text]]`, que el agente envíe el texto escrito y
los enlaces en un mensaje aparte con su herramienta de mensajes y responda solo con el
resumen hablado.

## Operación

- Logs: `docker compose -f compose.prod.yml logs -f mcp` (JSON, con `owner_key` opaco).
- Apagar WhatsApp: quita `COMPOSE_PROFILES=whatsapp` del `.env` y
  `docker compose -f compose.prod.yml stop mcp`.
- Rotar el token: cambia `MCP_TOKEN`, `docker compose -f compose.prod.yml up -d mcp` y
  actualiza la cabecera en OpenClaw.
- Privacidad: los mensajes de WhatsApp se procesan con DeepSeek igual que los de la web.
