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

`consultar_carvajal(mensaje, conversacion, nueva=false, voz=false, foto="")`

| Parámetro | Uso |
|---|---|
| `mensaje` | Texto del cliente (máx. 500 caracteres, igual que la web). |
| `conversacion` | Identificador estable del chat: número o JID del remitente. |
| `nueva` | `true` abre un hilo nuevo (equivale a «Nueva conversación»). |
| `voz` | `true` si el cliente mandó una nota de voz: resumen hablado apto para TTS y, aparte, el texto escrito completo o los enlaces (ver [Mensajes de voz](#mensajes-de-voz)). |
| `foto` | Referencia del adjunto (`media://inbound/<archivo>.jpg`) si el cliente mandó una foto de un alimento: la herramienta sugiere el empaque (ver [Fotos de alimentos](#fotos-de-alimentos-spec-051)). |

- **Identidad**: el número nunca se guarda en claro. El `owner_key` es
  `wa_` + HMAC-SHA256(`SESSION_SECRET`, número normalizado): cada chat tiene su hilo, su
  memoria comercial y su cupo diario (`DAILY_REQUESTS_LIMIT`), como un visitante web. El
  número o JID se reduce a E.164 (`573100000000@s.whatsapp.net`, `whatsapp:+57…` y
  `+57 310 000 0000` dan `+573100000000`), el mismo que usa el webhook firmado.
- **Cotizaciones** (spec 054): cada conversación arma una cotización con varias líneas
  (borrador de 7 días). Los mensajes con cifras salen de plantillas, iguales a las de la
  web: opciones («⭐ Recomendada» y alternativas 1-4), comparativo de cantidades (📊),
  línea agregada y carrito con el total parcial (✅ 🛒, «¿Algo más?»), resumen (🧾, «¿La
  confirmo y te envío el PDF?») y registro (✅ folio). Las respuestas cortas («la 2», «con
  mil», «eso es todo», «no, confírmala», «cancélalos», «quita los vasos», «retomar», «lo
  pienso») se resuelven sin LLM; si las reglas no deciden, Jev las clasifica. Solo con el
  «sí» del resumen se registra **un** folio con todas las líneas y el PDF se adjunta solo.
  Las preguntas de otro tema se responden y se recuerda la cotización pendiente.
- **Formato**: el Markdown del modelo pasa a formato de WhatsApp sin LLM
  (`*negrita*`, viñetas con `-`, enlaces como `texto (url)`), y se añade `*Fuentes:*`.
- **Fallos**: el canal recibe siempre un mensaje legible, nunca una traza interna.

## Cotización en PDF

Spec 054: al confirmar, la cotización se registra con su folio y **el PDF se adjunta en
el mismo mensaje** (o va como enlace sin `MEDIA_BASE_URL`); sin URL pública, el mensaje no
promete el PDF. Una cotización sin confirmar no tiene folio ni PDF. Con una cotización por
confirmar (resumen o carrito), cualquier pedido del PDF («PDF», «sí, mándame el PDF»,
«envíame la cotización en pdf», «el pe de efe» dictado) la **confirma** y adjunta el PDF:
el puente le pasa al turno la forma `PDF_CONFIRMATION` («sí, mándame el PDF»), que el flujo
reconoce siempre. Sin nada pendiente, «PDF» devuelve la última cotización registrada.

Con el pedido de la spec 056 ([abajo](#pedidos-en-odoo-spec-056)) cambian dos casos:

- **A mitad del pedido** (la oferta, el nombre y el correo o «¿Lo confirmo?»), «PDF», «sí,
  mándame el PDF» o un «pe de efe» dictado entregan el PDF de la cotización y, debajo, la
  pregunta pendiente (`render.order_reminder`: «↩️ ¿Confirmo el pedido con los datos que me
  diste?…»). No corren un turno, no crean el pedido y no tocan el borrador: un «sí» después
  sigue creándolo. El puente consulta el paso con `ChatService.pending_order_step`; si el PDF
  no se puede leer, el recordatorio llega igual.
- **Con el pedido confirmado**, «PDF», «mándame el recibo», «el PDF del pedido» o «recibo de
  mi compra» (`bridge.is_receipt_request`) reenvían el **recibo** del pedido (adjunto o
  enlace firmado), si `ChatService.order_of` encuentra en Odoo el pedido de la última
  cotización. «El PDF de la cotización» sigue trayendo la cotización, y sin pedido o sin forma
  de entregar el recibo, «PDF» devuelve la cotización como siempre. «¿Cuándo recibo el
  pedido?» no es un pedido del recibo: va al agente.

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

### Cotización y PDF en un solo mensaje

«Cotiza 400 portacomidas J1 en Bogotá y genera el PDF» no se le pasa entero al agente:
el puente separa la parte del PDF (`split_pdf_request`) y el agente cotiza solo el pedido.
Desde la spec 054 nada se registra sin confirmar: el cliente recibe la línea y «¿Algo
más?»; «no, confírmala» (o «PDF») la registra y adjunta el PDF. Una pregunta sobre el PDF
(«¿el PDF trae el IVA?») va al agente sin cambios.

### Una sola versión de la cotización

Spec 054: en los turnos del flujo, el bloque es la plantilla (al final, con la pregunta
pendiente) y la voz dice su versión hablada (≤ 45 palabras). Los turnos registrados antes de
la 054 se redactan con el bloque 🧾 de siempre.

Cuando el turno emite una cotización, el mensaje empieza por el bloque 🧾 (folio,
importes, total, vigencia y foto). Del texto del agente solo quedan sus comentarios
(`quote_remarks`). Frase por frase, en párrafos y en viñetas, se quita lo que solo repite el
bloque: sus líneas dichas por el agente («Total: …», «Precio por paquete: …») y las frases
cuyas cifras y palabras ya están en él (o en el SKU, la marca y el material), como «El
total queda en $37.510». Se conserva toda frase que aporte algo: una pregunta (aunque cite
el total), una cifra que el bloque no trae (un plazo de entrega, otro precio) o una palabra
propia aunque repita cifras («el precio de $3.751 sube en enero», «El mínimo es de 10
paquetes», «- No incluye IVA»). Un encabezado con «:» solo se queda si sobrevive alguna
línea debajo.

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
# Opcional (spec 055): bearer distinto que solo abre /health, para la página /metricas de
# la app web. La app nunca recibe MCP_TOKEN.
MCP_HEALTH_TOKEN=<otro valor generado igual>
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

Los archivos del workspace del agente (`SOUL.md`, `IDENTITY.md`, `AGENTS.md`, `USER.md`,
`MEMORY.md` y la bitácora diaria) están versionados en [`openclaw/`](../openclaw/README.md),
con los pasos para desplegarlos.

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

El puente no depende solo de `nueva=true`: si el mensaje es únicamente `/new`, `/nuevo`,
`/reset`, «nueva conversación» o «empecemos de nuevo», abre el hilo nuevo por su cuenta
(desde el 2026-11-25, cuando OpenClaw reenviaba `/new` como texto). Un hilo nuevo trae
el borrador y el pedido en curso vacíos. «Olvida todo» o «cancela todo» en el mismo hilo
cancelan el borrador y también olvidan el pedido en curso (producto, material, ciudad).

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
  Spec 054: las cantidades dichas con palabras antes de una unidad o un producto pasan a
  cifras («trescientos vasos» → «300 vasos», «dos mil quinientos platos» → «2.500
  platos»); «el dos de diciembre» o «un vaso» no cambian. Por escrito (`voz=false`) el
  mensaje no se toca.
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

## Fotos de alimentos (spec 051)

El cliente manda la foto de un plato o una bebida y recibe un empaque recomendado y hasta
cuatro alternativas numeradas del catálogo, aptos para ese alimento. Jev (TypeSafe)
clasifica la foto; la respuesta la arma el servidor sin LLM, con las fichas de las
referencias, y queda en el hilo. El cliente elige por texto o por voz («dame esa», «la
alternativa 1», «la 2») y el asistente sigue con la cotización: pide la cantidad y la ciudad
si faltan y presenta la propuesta para confirmar (spec 053).

**Cómo llega la foto**: OpenClaw guarda el adjunto en `~/.openclaw/media/inbound/` y se lo
muestra al agente como `[media attached: media://inbound/<uuid>.jpg (image/jpeg)]`. El
agente pasa esa referencia en `foto`. El puente lee el archivo **solo** desde la carpeta
montada (nombre sin rutas, sin enlaces simbólicos, JPEG o PNG, ≤ 10 MB), le quita los
metadatos, lo escala y lo descarta al terminar. Si OpenClaw y el servicio `mcp` comparten
máquina, basta montar la carpeta; si OpenClaw vive en otra máquina (la configuración actual:
VM de Google), la carpeta se monta en solo lectura por el túnel (ver
[OpenClaw en otra máquina](#openclaw-en-otra-máquina-sshfs-por-el-túnel)). Sin carpeta,
WhatsApp no ofrece fotos.

**Privacidad**: a TypeSafe (EE. UU.) solo va la imagen preparada y preguntas fijas; nunca
el número, el texto del cliente ni su historial. El owner activó la función en producción
el 2026-11-24.

### Activarlo en el VPS

1. En `/opt/carvajal/.env` (además de `TYPESAFE_API_KEY`):

   ```bash
   OPENCLAW_MEDIA_DIR=/srv/openclaw-media
   ```

   `compose.prod.yml` monta esa carpeta en `mcp` como `/openclaw-media:ro` y define
   `FOOD_PHOTO_MEDIA_DIR`. `FOOD_PHOTO_ENABLED=false` apaga las fotos en la web y en WhatsApp.
2. El contenedor corre como `app` (uid 100) y la carpeta de OpenClaw es `700`, y OpenClaw
   la vuelve a poner en `700` al guardar cada adjunto: un `chmod 711` dura hasta la
   siguiente foto. Monta para `mcp` una vista con bindfs que solo dé paso (sin listado) y
   lectura, y apunta `OPENCLAW_MEDIA_DIR` a esa vista:

   ```bash
   sudo bindfs -r -o allow_other --perms=og-rwx:od+x:of+r ~/.openclaw/media/inbound /srv/openclaw-media
   ```

   Los adjuntos que escribe OpenClaw ya son `644`. Para que sobreviva a un reinicio, usa una
   unidad `.mount` como la de [OpenClaw en otra
   máquina](#openclaw-en-otra-máquina-sshfs-por-el-túnel).
3. `docker compose -f compose.prod.yml up -d mcp` y comprueba:
   `curl -s localhost:8000/health | jq .food_photo` → `"status": "ok"`.

### OpenClaw en otra máquina (sshfs por el túnel)

Configuración vigente desde el 2026-11-25: OpenClaw corre en la VM de Google (usuario
`marko`) y `mcp` en el VPS de OVH. El VPS monta la carpeta de adjuntos de la VM en solo
lectura, por el mismo túnel SSH del MCP.

En la VM de OpenClaw (root):

1. Un usuario solo para esto, sin shell: `useradd --system --create-home --shell
   /usr/sbin/nologin ocmedia`, con la llave pública del VPS en
   `~ocmedia/.ssh/authorized_keys` precedida de `from="127.0.0.1,::1",restrict`.
2. Una jaula con solo la carpeta de adjuntos: `/srv/occhroot` (root, `755`) y la unidad
   `srv-occhroot-inbound.mount` que monta `~marko/.openclaw/media/inbound` en
   `/srv/occhroot/inbound` con bindfs (`apt-get install bindfs`):

   ```ini
   [Mount]
   What=/home/marko/.openclaw/media/inbound
   Where=/srv/occhroot/inbound
   Type=fuse.bindfs
   Options=ro,allow_other,perms=og-rwx:od+x:of+r
   ```

   `allow_other` deja entrar a otros usuarios que no sean root (bindfs lo activa solo al
   montar como root, pero va explícito). Así el usuario solo tiene paso por la carpeta
   (sin listado) y lectura de los archivos,
   sin importar los permisos de la original. Un `chmod 711` o una ACL sobre la carpeta de
   OpenClaw **no sirven**: OpenClaw la vuelve a poner en `700` al guardar cada adjunto (pasó
   el 2026-11-25: el puente respondía «No pude abrir esa foto…» con `adjunto no
   encontrado` en el log de `mcp`). No se le da nada sobre el resto del home de `marko`
   (allí hay credenciales y respaldos legibles por nombre).
3. `/etc/ssh/sshd_config.d/70-ocmedia.conf`:

   ```text
   Match User ocmedia
       ChrootDirectory /srv/occhroot
       ForceCommand internal-sftp -R
       AllowTcpForwarding no
       AllowAgentForwarding no
       X11Forwarding no
       PermitTTY no
   ```

   `sshd -t && systemctl reload ssh`.
4. Un túnel aparte, `ovh-media-tunnel.service` (systemd `--user` de `marko`, con linger),
   abre un puerto inverso hacia el SSH de la VM: `-R 127.0.0.1:2224:127.0.0.1:22`, con la
   misma llave y `Restart=always`. Va separado de `ovh-tunnel` a propósito: si la VM se cae
   de golpe, la sesión vieja puede retener el 2224 en OVH hasta que expire, y con
   `ExitOnForwardFailure=yes` eso tumbaría también el MCP. Así solo se demoran las fotos.

En el VPS (`deploy`):

1. La llave del túnel en `~/.ssh/authorized_keys` permite ese puerto y solo ese:
   `restrict,port-forwarding,permitopen="127.0.0.1:8765",permitlisten="127.0.0.1:2224",command="/bin/false"`.
   Añade `permitlisten` **antes** de reiniciar el túnel: con `ExitOnForwardFailure=yes` un
   puerto rechazado tumba también el MCP.
2. Root, una vez: `apt-get install sshfs` y descomentar `user_allow_other` en
   `/etc/fuse.conf` (el contenedor corre como otro usuario).
3. Una llave propia (`~/.ssh/ocmedia_ro`) y el script `~/bin/openclaw-media-mount.sh`, en el
   cron de `deploy` cada minuto y en `@reboot`. En cada ejecución:
   - Si la carpeta está montada y un `stat` responde en menos de 10 s, no hace nada.
   - Si no está montada, o está colgada (el `stat` no responde o falla al instante con
     «Transport endpoint is not connected», p. ej. tras caerse la VM), primero desmonta
     lo que quede (`fusermount -uz ~/openclaw-media`) y luego monta con la llave propia y
     sin pedir nada por terminal (corre en cron):

     ```bash
     sshfs -p 2224 ocmedia@127.0.0.1:/inbound ~/openclaw-media \
       -o ro,allow_other,reconnect,cache=no,entry_timeout=0,negative_timeout=0,attr_timeout=0 \
       -o ServerAliveInterval=15,ServerAliveCountMax=3,BatchMode=yes \
       -o IdentityFile=$HOME/.ssh/ocmedia_ro,HostKeyAlias=openclaw-media
     ```

     `HostKeyAlias` guarda la llave de host de la VM con su propio nombre en
     `known_hosts`, separada de la de cualquier otro servicio en `127.0.0.1`.
   - Recrea `mcp` (`up -d --force-recreate --no-deps mcp`) **solo si el `sshfs` de esa
     misma ejecución terminó bien**. Si falla (túnel o llave caídos), sale con error y
     `mcp` sigue corriendo: se pierden las fotos, no el servicio, y el cron reintenta al
     minuto siguiente. Docker no propaga un montaje nuevo a un contenedor que ya corre, así
     que sin esa recreación `mcp` seguiría viendo la carpeta vacía.
4. `OPENCLAW_MEDIA_DIR=/home/deploy/openclaw-media` en `/opt/carvajal/.env`.

Comprobación: `docker compose -f compose.prod.yml exec -T mcp python -c "from
apps.mcp.bridge import read_inbound_photo as r; print(len(r('media://inbound/<archivo>.jpg',
'/openclaw-media')))"` imprime el tamaño de una foto real. Desde el VPS,
`echo 'ls /inbound' | sftp -P 2224 -i ~/.ssh/ocmedia_ro ocmedia@127.0.0.1` debe fallar,
porque no se puede listar la carpeta.

### OpenClaw

- Apaga la interpretación de imágenes de OpenClaw para que la foto no vaya a otro
  proveedor de visión: `openclaw config set tools.media.image.enabled false`.
- Añade al `AGENTS.md` del agente «ventas»:

```markdown
## Fotos

- Si el cliente adjuntó una imagen ([media attached: media://inbound/<archivo> (image/...)]),
  llama a consultar_carvajal con esa referencia exacta en `foto`, el texto que la acompaña
  (o vacío) en `mensaje` y el mismo `conversacion` de siempre.
- No describas la foto ni adivines qué alimento es: responde exactamente con el texto que
  devuelve la herramienta.
- Una sola foto por llamada; si mandó varias, usa la primera y pídele que envíe las demás
  de una en una.
- «dame esa», «la 2», «sí» o «confírmala» son mensajes como cualquier otro: pásalos sin
  cambios en `mensaje`; la herramienta sabe a qué opción o propuesta responden.
```

## Pedidos en Odoo (spec 056)

Con `ODOO_*` configurado, el mensaje del folio termina con «🛒 ¿Quieres hacer el pedido? Te
pido tu nombre y tu correo y te llega la confirmación.»: el puente mueve esa línea al final,
después de las fotos y del PDF adjunto, para que la pregunta pendiente cierre el mensaje
(también en turnos guardados antes del cambio). WhatsApp no tiene los botones de la
web: el cliente responde por texto o por voz («sí», «hagamos el pedido», «ahora no») y luego
manda su nombre y su correo, en uno o dos mensajes («Sí, Ana Pérez» ya trae el nombre; por
voz, el correo dictado: «ana punto perez arroba gmail punto com», también con cifras sueltas
o en palabras: «ana punto perez dos dos arroba…» → `ana.perez22@…`). La herramienta muestra
los datos («📋 Voy a hacer el pedido con estos datos: … ¿Lo confirmo?»; en voz, el resumen lee
el nombre y el correo) y solo con un «sí» limpio («sí», «sí, confírmalo») crea el pedido
confirmado en Odoo y responde con el recibo: «🧾 *Pedido S… confirmado*», la fecha, las
líneas, el total y «La confirmación del pedido llega a …» con el correo del cliente del
pedido (Odoo la envía por el SMTP configurado en Odoo); pago y entrega los coordina un
asesor, que recibe en Odoo la actividad «Coordinar pago y entrega del pedido». Para
corregir, el cliente escribe el correo correcto («quiero cambiar el correo a carla@gmail.com»), «me llamo …», «cambia el nombre a
Carla Ruiz», «sí, Carla Ruiz», «no, es Carla» o el nombre como nombre propio (tras «no»
también en minúsculas: «no, carla ruiz»); un «no» solo, un «sí, pero…» o un cambio sin el
dato («sí, pero cambia el correo», «el correo está mal») hacen que la herramienta pregunte
qué dato corregir sin borrar los datos, y «ahora no», «no, mejor no», «no, gracias»,
«olvídalo», «cancela el pedido», «lo voy a pensar» o un adiós («chao», «gracias, adiós») lo
dejan en cualquier paso («…queda registrada sin pedido. Si cambias de opinión, escribe «hacer el pedido» en tu próximo
mensaje.»). Con el pedido ya creado, «cancela el pedido» y «¿cómo va mi pedido?» reciben una
respuesta fija con el número: no se anula ni se consulta el estado desde el chat, lo gestiona
el asesor. Todo lo deciden reglas explícitas por paso del backend, sin Jev; los turnos del
pedido se guardan sin el nombre ni el correo (correo como `a…@dominio`). Flujo, datos
personales y configuración: [`odoo-pedidos.md`](odoo-pedidos.md).

La herramienta lo declara: la descripción de `consultar_carvajal` y las instrucciones del
servidor explican que puede pedir nombre y correo, que el agente pasa cada respuesta tal cual
y nunca confirma por el cliente, que Odoo envía la confirmación y que el recibo puede traer
`ADJUNTAR_PDF:`. Como crea pedidos y envía correo por Odoo, la herramienta lleva
`open_world_hint=True`.

### Recibo del pedido en PDF

El pedido confirmado trae su recibo en PDF: el PDF del pedido que Odoo generó al confirmarlo
(el mismo que adjunta al correo de confirmación). El servicio marca el turno con
`flow.order = {name, folio}` y el puente (`bridge._with_receipt`) cambia la última línea del
recibo, «📄 Te envío el recibo del pedido en PDF.», según lo configurado:

- Con `MEDIA_BASE_URL`, como el PDF de la cotización:

  ```text
  📄 Te envío el recibo de tu pedido S00001 en PDF.
  ADJUNTAR_PDF: http://203.0.113.10:8080/pedidos/c0032295af55.pdf?t=<vence>.<firma>
  ```

  OpenClaw lo envía como documento con la misma regla 5 de `AGENTS.md`; no hace falta otra.
- Con solo `PUBLIC_BASE_URL`: «📄 Recibo de tu pedido S00001 en PDF. Ábrelo aquí:» y el
  enlace firmado.
- Sin ninguna, la línea se quita.

El enlace lo arma `quote_links.order_pdf_url`: firma HMAC con `SESSION_SECRET` y un dominio
propio del recibo (un enlace de cotización no abre el pedido), que vence a los 7 días. La ruta
`/pedidos/{folio}.pdf` de la app responde 404 si la firma no corresponde al dueño de la
cotización o venció, y 503 si Odoo no responde (el cliente puede volver a abrirlo después).

Para volver a pedir el recibo, el cliente responde «PDF» o «mándame el recibo» (ver
[Cotización en PDF](#cotización-en-pdf)); la respuesta trae otra vez el adjunto o el enlace.

El agente «ventas» tiene que pasar esas respuestas **sin cambios**, también el «sí» a «¿Lo
confirmo?»: sus reglas de privacidad y de defensa ante instrucciones no deben quitar ni
enmascarar el nombre o el correo, y él no pide otros datos, no confirma por el cliente ni da
un pedido por hecho. La sección «Pedidos» de
[`openclaw/workspace/ventas/AGENTS.md`](../openclaw/workspace/ventas/AGENTS.md) lo fija (con
el límite de `SOUL.md` y la directiva del 2026-11-26 de `USER.md`). Copia el workspace a la
máquina de OpenClaw con respaldo, como indica [`openclaw/README.md`](../openclaw/README.md#desplegar),
y abre un chat con `/new` para que lo tome. La spec 056 está activa en producción desde la
fusión de la PR #279; sin `ODOO_*`, la herramienta no ofrece el pedido y la sección no se usa.

## Operación

- Logs: `docker compose -f compose.prod.yml logs -f mcp` (JSON, con `owner_key` opaco).
- Compuerta de afirmaciones de WhatsApp: el agente corre en este proceso, así que sus
  contadores no salen en el `/health` de la app. `GET /health` del MCP (mismo bearer que
  `/mcp`, solo `Host` de loopback) publica el bloque `claims` (juzgadas, sustituidas, tasa,
  umbral y modo desde que arrancó el contenedor; contrato en
  `specs/046-jev-router/contracts/jev-gatekeeper.md`):
  `curl -s -H "Authorization: Bearer $MCP_TOKEN" 127.0.0.1:8765/health`. También lo abre
  `MCP_HEALTH_TOKEN` (solo `/health`, nunca `/mcp`), con el que la página `/metricas` de la
  app lo lee por la red interna como `http://mcp:8765/health` (spec 055).
- Apagar WhatsApp: quita `COMPOSE_PROFILES=whatsapp` del `.env` y
  `docker compose -f compose.prod.yml stop mcp`.
- Rotar el token: cambia `MCP_TOKEN`, `docker compose -f compose.prod.yml up -d mcp` y
  actualiza la cabecera en OpenClaw.
- Privacidad: los mensajes de WhatsApp se procesan con DeepSeek igual que los de la web.
