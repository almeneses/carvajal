# Ensayo general (e2e)

El ensayo general comprueba, contra una app **ya desplegada**, que todo lo que ve un cliente
funciona de punta a punta: la app, el bot de WhatsApp, el gateway de OpenClaw, su auditoría
de seguridad, el repositorio y la CI. Puede correr en local (stack de Docker) o contra
producción. Vive en `tests/e2e/`. No importa nada de la app: habla por HTTP, por MCP y por
SSH, igual que el navegador y OpenClaw. Así prueba lo que está desplegado y no el checkout.

`make check` y la CI lo excluyen, porque todas sus pruebas llevan los marcadores `e2e` y
`ensayo`. Una prueba se **salta**, con el motivo, cuando falta lo que necesita (SSH, token,
secreto, Firefox), en vez de fallar por credenciales. Así `make ensayo` informa de lo que
pudo comprobar.

## Qué comprueba

| Bloque | Archivo | Qué afirma | Gasto |
| --- | --- | --- | --- |
| Operación | `test_operacion.py` | `/live` y `/health` (índice, catálogo, modo del agente, Jev, fotos y el bloque `odoo` configurado y alcanzable), la ruta del recibo `/pedidos/<folio>.pdf` desplegada (404 sin prueba de dueño), cabeceras de seguridad, rechazos del webhook y del CSRF sin tocar nada, checkout y CI de `origin/main` en verde, la versión desplegada, el contenedor, el MCP, los medios, Ollama y `ODOO_*` en el `.env` del servidor, y en el gateway: los servicios, WhatsApp conectado con `dm:allowlist`, la configuración sin deriva, el túnel al MCP y `openclaw security audit` frente a `openclaw/auditoria-base.json` | no |
| Web | `test_web.py` | Los flujos definidos (specs 053 y 054, guiones de `guiones.py`), la memoria entre dos conversaciones de un mismo visitante y la conversación que arranca con una foto (`/chat/foto`) | sí |
| WhatsApp por MCP | `test_whatsapp.py` | Los mismos guiones y la misma memoria por `consultar_carvajal`, la herramienta que llama OpenClaw, y la conversación que arranca con foto (`media://inbound/...`) | sí |
| Pedido en Odoo | `test_web.py` y `test_whatsapp.py` | Folio → «Sí, quiero hacer el pedido» → nombre → correo → «📋 … ¿Lo confirmo?» con los dos datos → «Sí, confírmalo» → `Pedido S… confirmado` («La confirmación del pedido llega a» el correo) con el recibo en PDF que abre, el pedido confirmado en Odoo con su correo sin excepción, y un segundo «quiero comprarla» que no duplica (spec 056). Además, el **pedido con foto, texto y voz** (`test_pedido_con_foto_texto_y_voz_*`): la foto del ajiaco abre la conversación, se elige por texto, la cotización, el pedido, el nombre y el correo dictado van por voz y «Sí, confírmalo» por texto; en WhatsApp cada nota de voz trae un resumen hablable sin enlaces y en la web la nota de voz del servidor (TTS y transcripción reales) no dice el nombre ni el correo. Solo con `make ensayo-pedido` (`ENSAYO_PEDIDO=1`) | sí (crea dos pedidos reales por canal en Odoo) |
| Webhook y allowlist | `test_whatsapp.py` | Un remitente fuera de `WEBHOOK_ALLOWED_NUMBERS` (`ENSAYO_WA_FUERA`) recibe 403 sin llegar al agente | no |
| Voz | `test_voz.py` | Web: un turno dictado trae `voz_resumen` sin enlaces; `/api/voz/hablar` lo sintetiza y ese audio (pasado a Ogg/Opus con `ffmpeg`) vuelve por `/api/voz/transcribir` y sirve de siguiente turno; `/hablar` no sintetiza mensajes ajenos. WhatsApp: `consultar_carvajal` con `voz=true` empieza por un resumen para TTS sin URL | sí (STT y TTS de OpenAI) |
| WhatsApp real | `test_whatsapp_real.py` | Un DM real enviado desde un número **fuera** de `allowFrom` no recibe respuesta, y el gateway registra `Inbound message blocked (reason=dm_policy_not_allowlisted)` | sí (envía un WhatsApp) |
| Conversación por WhatsApp real | `test_whatsapp_real_conversacion.py` | Desde WhatsApp Web, con el número agregado **temporalmente** a `allowFrom`: cotizar, «dame esa», la foto del ajiaco (sin «No pude abrir esa foto»), una nota de voz **grabada** y «cancela todo». Solo con `make ensayo-whatsapp-real` (`ENSAYO_WA_CONVERSACION=1`) | sí (varios WhatsApp, DeepSeek, Jev y STT) |

Las pruebas `gasto` gastan turnos de DeepSeek, créditos de Jev o escriben en la base. Solo
corren con `make ensayo-completo`.

Los pasos deterministas (las reglas del flujo, sin LLM) se comprueban de forma estricta. Los
que dependen del agente se comparan con un conjunto de estados permitidos: si se desvían, el
guion se repite una vez en un hilo nuevo. Nunca se comparan precios.

`tests/test_ensayo_guiones.py` corre esos mismos guiones contra `ChatService` con el agente y
Odoo simulados, en la CI normal: si cambia una plantilla del flujo, falla ahí y no en el
ensayo contra producción.

## Pedidos en Odoo (spec 056)

Con `ODOO_URL`, `ODOO_DB` y `ODOO_API_KEY` en la app, el folio termina con «🛒 ¿Quieres hacer
el pedido?…» y el flujo queda en `awaiting="pedido"`. Un «sí» ya no repite el folio: acepta
el pedido y pide nombre y correo (`awaiting="datos_pedido"`). Con los dos, el asistente los
muestra y pide confirmarlos (`awaiting="confirmar_pedido"`): el pedido solo se crea con un
«sí» a esa confirmación. Por eso cambian los guiones:

- **Guion con folio** (`cierre()` en `guiones.py`). Si el folio trae la oferta, se responde
  «ahora no», se exige «tu cotización #folio queda registrada» (ruta `pedido`) y el segundo
  «sí» sigue exigiendo «ya quedó registrada con el folio». Sin la oferta (app sin `ODOO_*`),
  ese paso se salta y el guion es el de la spec 054. No crea nada en Odoo.
- **Escenario del pedido** (`test_pedido_en_odoo_desde_web` y
  `test_pedido_en_odoo_desde_whatsapp`). Llega al folio con el mismo guion de opciones y
  luego, todo con reglas (ruta `pedido`, sin Jev ni el modelo):

  | Mensaje del ensayo | Respuesta exigida | `awaiting` |
  | --- | --- | --- |
  | «Sí, quiero hacer el pedido» (botón «Hacer el pedido») | «Para el pedido envíame tu **nombre** y tu **correo**…» | `datos_pedido` |
  | el nombre | «Gracias, <nombre>. ¿A qué **correo** te envío la confirmación…» | `datos_pedido` |
  | el correo | «📋 Voy a hacer el pedido con estos datos:» con `- Nombre: **…**`, `- Correo: …` (sin negrita: WhatsApp metía el `_` del énfasis en el enlace `mailto:`) y «¿Lo confirmo? Si algo está mal, escríbeme el dato correcto.»; todavía sin pedido | `confirmar_pedido` |
  | «Sí, confírmalo» (botón «Confirmar pedido») | `🧾 **Pedido S… confirmado** · cotización #folio` y «La confirmación del pedido llega a <correo>» | ninguno |
  | «quiero comprarla» | «Ese pedido ya quedó creado con el número **S…**» (el mismo) | ninguno |

  El «Sí, confírmalo» trae además el recibo (fecha, líneas, total y el PDF del pedido,
  [`odoo-pedidos.md`](odoo-pedidos.md#recibo-del-pedido)), y el ensayo lo exige (SC-008):

  - **Web**: el enlace «📄 [Descarga el recibo de tu pedido S…](…/pedidos/<folio>.pdf)» del
    mismo folio y número. El ensayo lo abre con la cookie del visitante y exige 200,
    `application/pdf` y `%PDF` (un 503 de Odoo se reintenta una vez a los 10 s), y con la
    cookie de **otro** visitante exige 404.
  - **WhatsApp**: «📄 Te envío el recibo de tu pedido S… en PDF.» con la línea
    `ADJUNTAR_PDF:` hacia `/pedidos/<folio>.pdf?t=…` (o «Ábrelo aquí:» con el enlace
    firmado). La URL se abre por la app del ensayo (misma ruta firmada) o tal cual; si
    ninguna es alcanzable desde el ensayo, solo avisa. Luego «mándame el recibo» debe
    reenviar el mismo recibo sin pasar por el agente. Sin enlace (MCP local sin
    `PUBLIC_BASE_URL` ni `MEDIA_BASE_URL`) avisa fuera de producción y falla en producción.
  - En los dos canales, la promesa cruda «📄 Te envío el recibo del pedido en PDF.» no puede
    llegar al cliente: cada canal la cambia por el PDF o la quita.

  En WhatsApp no llega `done.flow`: el estado se deduce del texto (`inferir_awaiting`), que
  reconoce también la confirmación («Voy a hacer el pedido con estos datos», «¿Lo
  confirmo?…»), «¿Qué dato corrijo?» (un «no» o un «sí, pero…» ante la confirmación), el
  fallo de Odoo y los recordatorios «↩️» del pedido; la negrita llega con un solo `*`
  (`bridge.to_whatsapp`).
- **Si Odoo falla al confirmar** («No pude crear el pedido… responde **sí** en unos minutos y
  lo intento de nuevo»), el flujo sigue en `confirmar_pedido` con los mismos datos. El ensayo
  lo comprueba, espera `PAUSA_REINTENTO_PEDIDO` (30 s, `guiones.py`) y responde «sí»: el
  reintento usa el mismo folio, así que no duplica el pedido. Si sale, la línea `PEDIDO`
  avisa «tras reintentar»; si vuelve a fallar, la prueba falla con «Odoo no creó el pedido
  del folio #… ni al reintentar».

El escenario del pedido crea un pedido **real** en el Odoo de la app (pedido confirmado,
oportunidad ganada, una actividad para el vendedor y el correo de confirmación, que Odoo
envía por el SMTP de producción), así que lleva `gasto` y además se salta salvo con
`ENSAYO_PEDIDO=1`. Córrelo con su objetivo, que carga `.env.ensayo` como los demás `ensayo*`
y activa `ENSAYO_PEDIDO` solo en esa corrida:

```bash
make ensayo-pedido   # = ENSAYO_PEDIDO=1 … pytest tests/e2e -m ensayo -k "pedido_en_odoo or foto_texto_y_voz"
```

No lo lances con `uv run pytest` a mano sin cargar `.env.ensayo`: sin `ENSAYO_BASE_URL`
correría contra `127.0.0.1`.

- Se salta si `ENSAYO_COTIZAR=ninguna` (el pedido necesita un folio). Si el folio no trae la
  oferta (la app no tiene `ODOO_*`), **falla** cuando se esperan pedidos
  (`ENSAYO_EXPECT_PEDIDOS`, activo por defecto en producción) y se salta en local.
- Con `ENSAYO_COTIZAR=una` y `ENSAYO_PEDIDO=1`, los folios de cada canal los registran los
  dos escenarios del pedido y el guion designado (`opciones_y_registro`) se cierra con
  «cancela todo»: dos folios por canal (uno por escenario).
- El **pedido con foto, texto y voz** (`test_pedido_con_foto_texto_y_voz_web` y
  `…_en_whatsapp`, `guiones.ejecutar_pedido_mixto`) también se salta o falla como arriba si el
  folio no trae la oferta, y además se salta sin fotos (`food_photo` distinto de `ok` en
  `/health`; en WhatsApp también sin `ENSAYO_OPENCLAW_SSH` en producción o sin
  `ENSAYO_MEDIA_DIR` en local). En la web comprueba sin audio el texto que leería la voz del
  servidor (`data-voz-resumen` del hilo: la frase neutra, ni el nombre ni el correo), que la voz
  del navegador (`voz_resumen`) sí lea los datos y que el hilo marque los turnos por voz; con la
  voz del servidor activa y `ffmpeg` instalado, además sintetiza y transcribe la confirmación
  (TTS y STT reales de OpenAI). Sin ellos solo avisa.
- El comprador es ficticio y propio de la corrida: el nombre es `Ensayo <run_id en letras>`
  (el flujo no acepta dígitos en un nombre: 0→a … 9→j, p. ej. `Ensayo Cacgbbcgbadaaa`) y el
  correo, `e2e-<run_id>-web@example.com` o `e2e-<run_id>-whatsapp@example.com` (en el pedido
  con foto, texto y voz, `…-web-mixto@…` y `…-whatsapp-mixto@…`). `example.com`
  está reservado (RFC 2606): aunque Odoo lo envía por el SMTP de producción, no le llega a nadie.
  El flujo descarta muletillas y respuestas como nombre («ok», «sí», «ya», «gracias»…): un
  `ENSAYO_RUN_ID` de menos de seis letras lleva «prueba» al final (`ok` → `Ensayo
  Okprueba`). No uses como `ENSAYO_RUN_ID` una palabra como «gracias» o «perfecto».
- En el historial guardado (y en LangSmith) los turnos con datos quedan como «🛒 (datos para
  el pedido)», con el nombre como «(tu nombre)» y el correo enmascarado (`e…@example.com`);
  en el turno la persona ve el texto completo, que es lo que comprueba el ensayo.
- La salida (`-rA`) imprime `PEDIDO <canal>: S… · folio #… · <correo>` por canal.

### Correo de confirmación (SC-001)

Odoo envía la confirmación con el PDF al confirmar el pedido (SMTP de Gmail configurado en
producción). Con `ENSAYO_APP_SSH` en producción, el escenario lo comprueba solo, **en modo de
solo lectura** (`tests/e2e/correo_odoo.py`): por SSH corre un script en el contenedor `app`
(`docker compose -f compose.prod.yml exec -T app python -c …`), que usa la `ODOO_API_KEY`
que ya tiene en su entorno sin imprimirla y solo hace `search_read`. Exige:

- un solo `sale.order` con `client_order_ref` igual al folio, en `state=sale`;
- ningún `mail.mail` de ese pedido en `exception` (si alguno sigue `outgoing`, lo vuelve a
  leer dos veces cada 10 s y, si no sale, avisa). Si la clave del bot no puede leer
  `mail.mail`, solo avisa.

También avisa si el chatter del pedido no tiene ningún mensaje con el PDF adjunto. Sin
`ENSAYO_APP_SSH`, o en local, avisa y la revisión queda manual. Para revisarlo a mano en
Odoo (por el túnel de [`odoo-pedidos.md`](odoo-pedidos.md)):

1. **Ventas → Pedidos**, abre el pedido del folio (*Referencia del cliente*). En el
   **chatter** debe estar el mensaje de confirmación enviado al correo del comprador, con el
   PDF del pedido adjunto.
2. **Ajustes → Técnico → Correos** (activa el modo desarrollador): el correo del pedido no
   debe aparecer con el filtro **Excepción**. Odoo borra los correos enviados
   (`auto_delete`), así que una lista vacía para ese pedido es lo normal; uno en excepción
   trae el motivo del SMTP.
3. **CRM**: la oportunidad «Pedido de la cotización #folio» queda ganada.

El correo del ensayo va a `example.com`, que no recibe nada: Gmail suele aceptarlo (el
`mail.mail` sale de la cola) y el rebote, si llega, va al buzón del remitente.

### Borrar o cancelar los pedidos del ensayo

Quedan en Odoo → **Ventas → Pedidos**, con el cliente `Ensayo …`, el correo `e2e-…@example.com`
y la referencia del cliente (`client_order_ref`) igual al folio. Para limpiarlos (ver
[`odoo-pedidos.md`](odoo-pedidos.md) para abrir la interfaz por el túnel):

1. **Ventas → Pedidos**, busca `e2e-` en *Cliente* (o el folio en *Referencia del cliente*).
2. Abre cada pedido y pulsa **Cancelar**. Si Odoo ofrece avisar al cliente, cancela sin
   enviar el correo. Odoo solo borra pedidos cancelados o en borrador: ya cancelado, el menú
   de acciones (⚙) → **Eliminar** lo quita. Si prefieres conservar la traza, déjalo
   cancelado.
3. **CRM**: la oportunidad «Pedido de la cotización #folio» queda ganada; búscala por el folio
   (quita el filtro por defecto e incluye las ganadas) y archívala o elimínala.
4. **Contactos**: archiva el contacto `Ensayo …` (`e2e-…@example.com`) si no quieres que el
   próximo ensayo lo reutilice; el adaptador reutiliza un contacto solo si lo creó el
   asistente (`ref = asistente-carvajal`) y coinciden el correo y el nombre (si no, crea otro
   con ese `ref`). Los contactos `Ensayo …` de corridas anteriores a la tercera ronda de la
   spec 056 no llevan el `ref` y no se reutilizan.

Repetir el escenario con el mismo `ENSAYO_RUN_ID` crea otro folio y, por tanto, otro pedido:
la idempotencia es por folio, no por comprador.

## En local

```bash
# 1. Stack completo con el servidor MCP (perfil whatsapp) en 127.0.0.1:8765
make up PERFILES="full whatsapp"
#    equivalente: docker compose --profile full --profile whatsapp up -d --build --wait

# 2. Ensayo sin gasto, y luego el completo
make ensayo
make ensayo-completo

# Un solo bloque o una sola prueba
uv run pytest tests/e2e/test_web.py -m ensayo -rA
uv run pytest tests/e2e -m ensayo -k memoria -rA
```

- El MCP local usa el token `dev-mcp-solo-local` (`compose.yml`). No hace falta configurarlo.
- El webhook necesita `WEBHOOK_SECRET` y `WEBHOOK_ALLOWED_NUMBERS` en el `.env`. El ensayo
  lee el secreto de ese `.env` y, sin él, se salta el bloque (la ruta responde 404).
- Jev y las fotos necesitan `TYPESAFE_API_KEY` y `JEV_ENABLED=true` en el `.env`. Sin ellos,
  `/health` los muestra apagados y sus pruebas se saltan. Las fotos que recibe el MCP local se
  leen de `./data/openclaw-media`, el «inbound» de OpenClaw en local, montado en
  `/app/data/openclaw-media`.
- En local, `ENSAYO_COTIZAR` vale `todas` por defecto: cada guion llega hasta el folio.

## Contra producción

```bash
cp .env.ensayo.example .env.ensayo   # .env.ensayo no se versiona
$EDITOR .env.ensayo                  # hosts reales, número del bot, etc.
make ensayo                          # lo lee solo si existe
make ensayo-completo
```

El Makefile carga `.env.ensayo` con `set -a; . ./.env.ensayo`, así que debe ser un archivo de
shell válido: `CLAVE=valor`, sin espacios alrededor del `=`.

**Requisitos de SSH.** Todo corre con `BatchMode=yes`, así que nunca pide contraseña:

- `ENSAYO_APP_SSH` es la clave del usuario `deploy` del servidor de la app, en el puerto de
  `ENSAYO_APP_SSH_PORT`. Con ella el ensayo lee estados (`docker compose ps`, `.deployed_tag`,
  `stat`) y saca `MCP_TOKEN` y `WEBHOOK_SECRET` del `.env` del servidor (`ENSAYO_APP_DIR`).
  Los valores nunca se imprimen ni se guardan.
- `ENSAYO_OPENCLAW_SSH` es la VM de OpenClaw, con `sudo -n -u <ENSAYO_OPENCLAW_USER>` sin
  contraseña. Los comandos corren con la sesión `systemd --user` de ese usuario.
- **El túnel al MCP es automático.** En producción el MCP solo escucha en el loopback del
  servidor, así que el fixture `mcp` abre `ssh -L <puerto libre>:127.0.0.1:8765` y lo cierra
  al terminar.

**Solo lectura.** Salvo `make ensayo-whatsapp-real` (sección «Conversación por WhatsApp real (foto y voz)»), el ensayo nunca corre `openclaw config set`, `security audit --fix`,
reinicios, `compose up` ni `compose down`. Tampoco imprime secretos ni números de teléfono:
los mensajes de fallo pasan por un redactor. Los hosts reales solo viven en `.env.ensayo`.

## Exploración de escenarios

`make ensayo-exploracion` conversa por WhatsApp (`consultar_carvajal`) y por la web con
unos treinta guiones (`tests/e2e/test_exploracion.py`): los flujos de la spec 054 y sus
bordes (referencia ambigua, «eso es todo» ante las opciones, ver el carrito, ciudad sin
distribuidor, cantidad 0, producto que no existe), preguntas fuera del flujo, voz, inglés,
errores de tipeo, alcance e inyección. Esos guiones no confirman ninguna cotización.

Los guiones del pedido (spec 056, `ESCENARIOS_PEDIDO`) sí llegan al folio y recorren la
oferta con el comprador ficticio del run (`Ensayo …`, `e2e-<run>-explora-<canal>@example.com`)
**sin crear el pedido**: el nombre, el correo dictado por voz («… arroba example punto com»),
una pregunta a mitad («¿cuánto tarda el envío?», «¿aceptan Nequi?»), una corrección («no, el
nombre es …»), «ahora no», «chao» en los datos, otro producto tras el folio y «sí, pero cambia
el correo» seguido de «cancela». Ninguno dice un «sí» limpio a «¿Lo confirmo?» (lo comprueba
`tests/test_ensayo_exploracion.py` contra el servicio con Odoo simulado), y si alguno creara
un pedido la prueba falla para que lo canceles a mano. Registran un folio cada uno según
`ENSAYO_COTIZAR` (`una`: solo el primero; `ninguna`: ninguno). `pedido_creado` sí crea el
pedido (estado, «cancela el pedido» y «mándame el recibo» después) y solo corre con
`ENSAYO_PEDIDO=1`.

Guarda una transcripción por guion y canal en `ENSAYO_EXPLORA_DIR` (por defecto
`data/ensayo-exploracion/<run_id>`, ignorada por git) e `informe.md` con alertas: respuestas
genéricas del verificador («Para seguir necesito un dato más», «No tengo un dato
verificado»), citas de WhatsApp sin fuente, «Sí, te ayudo» a un tema ajeno y una opción
agregada tras «eso es todo». Del pedido: un dato del comprador que en la web no fue por la
ruta `pedido`, la promesa del recibo sin enlace ni adjunto y el asistente diciendo que no
toma pedidos con un pedido pendiente. Las alertas son pistas para leer la transcripción, no fallos: el
modelo varía entre corridas. `make ensayo-completo` no la incluye.

Leer las transcripciones encontró, el 2026-11-26, fallos que el ensayo no veía porque no
compara textos: fuentes que no cuadraban con las citas, reemplazos genéricos del verificador
y un «Sí, te ayudo» a una tarea de cálculo.

## Costo y efectos secundarios

| Qué | Dónde | Cuánto |
| --- | --- | --- |
| Turnos de DeepSeek | Pasos del agente en web y MCP; la memoria; el reintento | Unas decenas de turnos por `make ensayo-completo` |
| Llamadas a Jev / TypeSafe | Clasificación de la foto (`ajiaco.jpg`, que Jev clasifica como sopa caliente) y las elecciones cortas del flujo | Una o dos fotos por canal (una más con `make ensayo-pedido`), más las de Jev Choice |
| Voz del servidor (OpenAI) | `test_voz.py` y, en `make ensayo-pedido`, la confirmación del pedido con foto, texto y voz: TTS de `/api/voz/hablar` y STT de `/api/voz/transcribir` | Unas pocas frases cortas por corrida |
| Cotizaciones y folios | Producción, según `ENSAYO_COTIZAR`: `una` registra solo el guion designado (o, con `ENSAYO_PEDIDO=1`, los dos escenarios del pedido), `ninguna` se detiene antes del «sí» | Por defecto un folio real por canal y corrida en producción; dos con `make ensayo-pedido` |
| Pedidos en Odoo | Solo con `ENSAYO_PEDIDO=1` (`make ensayo-pedido`, y `pedido_creado` en la exploración): un pedido confirmado, una oportunidad ganada, una actividad del vendedor, un correo de confirmación a `example.com` y un contacto `e2e-…@example.com` por pedido. Se borran a mano (sección «Pedidos en Odoo») | Dos pedidos por canal y corrida (el del guion de opciones y el de foto, texto y voz) |
| Filas en la base | Conversaciones, mensajes, uso, borradores y cotizaciones bajo los alias `e2e-<run>-<prueba>`, y visitantes web derivados del `run_id`. No hay API para borrarlas | Se acumulan por corrida |
| Cupo diario | Cada alias y cada visitante del ensayo tiene su propio cupo (20/día), así que no gasta el de los clientes | — |
| Un WhatsApp real | `test_whatsapp_real.py`: un mensaje `ensayo allowlist <run>` del segundo número del owner al bot | Queda en los dos chats |

Para no ensuciar producción, corre `make ensayo` (sin gasto) a diario y `make ensayo-completo`
solo antes de una entrega o tras un cambio grande.

## WhatsApp real con Firefox (allowlist)

El número vinculado en OpenClaw es el del owner, con `dmPolicy: allowlist` y un único número en
`allowFrom`. La prueba manda un DM real desde **otro** número, el que tiene abierto WhatsApp
Web en el Firefox del owner, y comprueba que nadie responde.

1. Cierra Firefox y ábrelo con Marionette. Abre el perfil de siempre, el que tiene la sesión
   de WhatsApp Web:

   ```bash
   firefox --marionette &          # snap: /snap/bin/firefox; escucha en 127.0.0.1:2828
   ```

   Si usas otro puerto (`marionette.port` en `about:config`), indícalo en
   `ENSAYO_FIREFOX_MARIONETTE`. geckodriver viene con el snap (`/snap/bin/geckodriver`). El
   ensayo usa un cliente WebDriver propio (`tests/e2e/webdriver.py`), sin Selenium.
2. Pon en `.env.ensayo` el número del bot en E.164 (`ENSAYO_WA_BOT`) y el acceso SSH a la VM
   de OpenClaw.
3. Corre `uv run pytest tests/e2e/test_whatsapp_real.py -m ensayo -rA` (o
   `make ensayo-completo`).

La prueba:

1. Por SSH confirma que `dmPolicy` sigue en `allowlist` y que el journal de
   `openclaw-gateway` es legible. Si algo de eso falla, no envía nada.
2. Abre una **pestaña propia** en `web.whatsapp.com/send?phone=<bot>&text=<marca>`.
3. Lee de WhatsApp Web el número con el que está abierta la sesión. Si es el del bot, o si
   está en `allowFrom`, se salta. El número solo se usa en memoria.
4. Envía la marca con Enter y vigila el chat durante `ENSAYO_WA_ESPERA` segundos (90 por
   defecto). Falla si llega cualquier burbuja entrante después de la suya.
5. Lee el journal del gateway desde el envío. Exige que no haya ni
   `Inbound message <remitente> -> …` ni `sessionKey=…<remitente>…` de ese número, y busca
   `[whatsapp] Inbound message blocked (reason=dm_policy_not_allowlisted)`. Esa línea no trae
   el remitente, porque el owner recibe DMs de otros contactos todo el día. Si falta pero el
   remitente se conoce y no hubo ni entrada ni sesión, la prueba pasa con un aviso de
   evidencia indirecta.
6. Cierra **solo su pestaña**, vuelve a la que estaba activa y termina geckodriver **sin**
   `DELETE /session`. Con `--connect-existing`, geckodriver 0.37.1 no cierra Firefox ni al
   borrar la sesión (comprobado en un perfil aislado), pero el ensayo no depende de eso.

**Efectos visibles:** se abre y se cierra una pestaña, y el mensaje queda en los dos chats.
WhatsApp Web solo permite una pestaña activa: si ya lo tenías abierto, la prueba pulsa «Usar
aquí» y tu pestaña original pedirá lo mismo al volver.

Si la página muestra el código QR, la prueba se salta. Abre WhatsApp Web en ese Firefox y
vincúlalo antes.

## Conversación por WhatsApp real (foto y voz)

`make ensayo-whatsapp-real` conversa con el bot como un cliente, desde el WhatsApp Web del
Firefox del owner (el mismo de la prueba de la allowlist). Ese número está **fuera** de
`allowFrom`, así que la prueba:

1. Lee `allowFrom`, agrega el número de WhatsApp Web y abre una pestaña propia.
2. Empieza con «nuevo» (conversación limpia, sin el modelo) y envía «Cotízame 500 vasos para bebida fría en Bogotá», «dame esa», la foto del ajiaco (por
   «Adjuntar → Fotos y videos»), una **nota de voz grabada** y «cancela todo», y exige marcas
   mínimas en cada respuesta (opciones, «Agregué», nunca «No pude abrir esa foto»,
   «cancelé»).
3. Al terminar, pase lo que pase, quita **solo** ese número de la lista vigente (no reescribe
   una copia vieja: otras personas pueden cambiar el gateway a la vez) y comprueba que ya no
   está. Si no lo consigue, la prueba falla pidiendo quitarlo a mano. Si el proceso muere antes
   (Ctrl-C, SSH caído), la marca `data/ensayo-whatsapp/.allowfrom-pendiente` (la huella SHA-256 del número, nunca el número)
   hace que la siguiente corrida lo quite al empezar; lo mismo con el micrófono
   (`.microfono-previo`, solo el id de PipeWire).

La nota de voz es real: un micrófono virtual de PipeWire (`pw-loopback`) se pone como fuente
por defecto, la prueba pulsa «Mensaje de voz» con un clic nativo de WebDriver (el navegador
exige un gesto real para abrir el micrófono), reproduce `fixtures/nota-voz.ogg` (pasada a WAV
con `ffmpeg`) y la envía; al salir vuelve el micrófono de siempre. Sin PipeWire o sin `ffmpeg`
ese paso se salta con un aviso. Los adjuntos se copian a `data/ensayo-whatsapp/medios`: el
Firefox de snap no lee `/tmp`.

La conversación queda, sin números, en `data/ensayo-whatsapp/<run_id>/conversacion.md`
(`ENSAYO_WA_DIR`). Córrela **después** de `make ensayo-completo`: con el número en `allowFrom`,
la prueba de la allowlist y la de deriva del gateway no tendrían sentido. Un despliegue a
mitad de la conversación reinicia el MCP: no fusiones mientras corre.

Lo que encontró la primera corrida (2026-11-27): WhatsApp entrega las fotos en WebP y el puente
las rechazaba (PR #291), y un DM que llegó antes de una `binding` al agente de ventas lo
contestó el agente personal del gateway.

## Actualizar `openclaw/auditoria-base.json`

La base guarda la versión de OpenClaw, el resumen y los `checkId` del audit, y los valores
endurecidos de la configuración (solo formas, nunca números ni JID). Reglas de
`test_auditoria_de_seguridad_sin_regresiones`:

- **Falla** con un crítico nuevo o con más críticos que la base.
- **Avisa** con un `warn` nuevo o con una versión distinta de OpenClaw.
- **Informa** cuando desaparece un hallazgo de la base: conviene actualizarla.

Cuando un cambio **intencional** mejora o cambia el gateway, por ejemplo `groupPolicy:
allowlist`, que deja 0 críticos, o una versión nueva de OpenClaw:

```bash
ssh "$ENSAYO_OPENCLAW_SSH" "sudo -n -u marko -H bash -lc \
  'export XDG_RUNTIME_DIR=/run/user/\$(id -u); openclaw --version; openclaw security audit --json'" \
  > /tmp/audit.json   # solo lectura; revisa que no contenga números antes de copiar nada
```

1. Copia a la base `openclaw_version`, `summary` y la lista `findings` (solo `checkId`,
   `severity` y una `nota` si hace falta), y actualiza `fecha`.
2. Si cambió el endurecimiento, ajusta `config.valores`, `deny_minimo` o
   `plugins_permitidos`.
3. Corre `uv run pytest tests/test_ensayo_auditoria.py` (en la CI normal) y `make ensayo`.
4. Sube la base en la **misma PR** que el cambio del gateway y explica en ella el motivo.

## Variables

Las lee `Config.desde_entorno()` en `tests/e2e/clientes.py`. La tabla coincide con ese código:

| Variable | Por defecto | Uso |
| --- | --- | --- |
| `ENSAYO_BASE_URL` | `http://127.0.0.1:8000` | App bajo prueba (sin `/` final) |
| `ENSAYO_TARGET` | `local` si el host es `127.0.0.1`, `localhost` o `::1`; si no, `prod` | Cambia los valores por defecto y el camino del MCP |
| `ENSAYO_RUN_ID` | Hora UTC `AAAAMMDDhhmmss` | Sufijo de alias, visitantes y marcas de la corrida |
| `ENSAYO_APP_SSH` | vacío | `usuario@host` del servidor de la app; sin él se saltan las pruebas del servidor y, en producción, el MCP |
| `ENSAYO_APP_SSH_PORT` | `22` | Puerto SSH del servidor de la app |
| `ENSAYO_APP_DIR` | `/opt/carvajal` | Carpeta del despliegue (su `.env`, `compose.prod.yml`, `.deployed_tag`) |
| `ENSAYO_OPENCLAW_SSH` | vacío | `usuario@host` de la VM de OpenClaw; sin él se salta el gateway |
| `ENSAYO_OPENCLAW_USER` | `marko` | Usuario del gateway (`sudo -n -u`) |
| `ENSAYO_MCP_URL` | `http://127.0.0.1:8765/mcp` | MCP local; en producción se ignora y se usa el túnel |
| `ENSAYO_MCP_TOKEN` | `dev-mcp-solo-local` en local; vacío en producción | En producción, si está vacío, se lee `MCP_TOKEN` del `.env` del servidor |
| `ENSAYO_WEBHOOK_SECRET` | vacío | Si está vacío, se lee `WEBHOOK_SECRET` del `.env` local o del servidor; sin secreto se salta el webhook |
| `ENSAYO_WA_FUERA` | `+15550100999` | Remitente ficticio fuera de la allowlist del webhook |
| `ENSAYO_COTIZAR` | `todas` en local, `una` en producción | `todas`, `una` o `ninguna`: cuántos guiones registran folio |
| `ENSAYO_PEDIDO` | apagado | `1` corre el escenario del pedido (nombre, correo, confirmación de los datos y «Sí, confírmalo»): crea un pedido real en el Odoo de la app (spec 056). `make ensayo-pedido` la activa solo en esa corrida |
| `ENSAYO_EXPECT_PEDIDOS` | activo en producción, apagado en local | Exige Odoo: el bloque `odoo` de `/health` configurado y alcanzable, `ODOO_*` en el `.env` del servidor y que cada folio ofrezca el pedido; sin ella, un folio sin oferta solo se salta. `0` la apaga en producción |
| `ENSAYO_MEDIA_DIR` | `data/openclaw-media` | «Inbound» local donde se deja la foto para el MCP |
| `ENSAYO_FIREFOX_MARIONETTE` | vacío (la prueba usa `2828`) | Puerto de Marionette del Firefox con WhatsApp Web |
| `ENSAYO_WA_BOT` | vacío | Número del bot en E.164; sin él se salta WhatsApp real |

Algunas variables solo las lee un archivo concreto:

| Variable | Archivo | Por defecto | Uso |
| --- | --- | --- | --- |
| `ENSAYO_WA_ESPERA` | `test_whatsapp_real.py` | `90` | Segundos que se vigila el chat tras el envío |
| `ENSAYO_WA_CONVERSACION` | `test_whatsapp_real_conversacion.py` | apagado | `1` corre la conversación real (escribe en `allowFrom` durante la prueba); la activa `make ensayo-whatsapp-real` |
| `ENSAYO_WA_DIR` | `test_whatsapp_real_conversacion.py` | `data/ensayo-whatsapp/<run_id>` | Dónde queda `conversacion.md` |
| `ENSAYO_EXPECT_AGENT_MODE` | `test_operacion.py` | `tools` | Modo del agente esperado en `/health` |
| `ENSAYO_EXPECT_WEBHOOK` | `test_operacion.py` | apagado | Exige webhook encendido (401, no 404) |
| `ENSAYO_EXPECT_JEV` | `test_operacion.py` | apagado (en producción, siempre) | Exige Jev configurado y disponible |
| `ENSAYO_EXPECT_DIMENSIONS` | `test_operacion.py` | `768` | Dimensiones del índice (nomic-embed-text) |
| `ENSAYO_REPO_LIMPIO` | `test_operacion.py` | apagado | Exige checkout sin cambios aunque no sea `main` |
| `ENSAYO_OPENCLAW_UNITS` | `test_operacion.py` | `unidades` de la base | Unidades `systemd --user` del gateway |

## Solución de problemas

| Síntoma | Causa probable | Qué hacer |
| --- | --- | --- |
| Todo `SKIPPED: la app no responde` | App caída o `ENSAYO_BASE_URL` mal | `curl $ENSAYO_BASE_URL/live`; en local, `make up` |
| `sin MCP local en :8765` | Falta el perfil `whatsapp` | `make up PERFILES="full whatsapp"` |
| `el túnel SSH no abrió el puerto` | Clave o puerto SSH del servidor, o MCP parado | `ssh -p $ENSAYO_APP_SSH_PORT $ENSAYO_APP_SSH true` y `docker compose -f compose.prod.yml ps mcp` en el servidor |
| `sudo -n -u marko no funciona` | Falta sudo sin contraseña en la VM de OpenClaw | Configúralo para ese usuario, o corre el bloque desde la VM |
| El webhook se salta (404) | `WEBHOOK_SECRET` vacío | Defínelo en el `.env` (local) o en el del servidor |
| Pruebas de Jev o de fotos saltadas | `TYPESAFE_API_KEY` o `JEV_ENABLED` | Mira `/health` → `jev` y `food_photo` |
| Un paso del agente falla dos veces | El LLM se salió de los estados permitidos | Lee la transcripción del fallo; si es una regresión, `evaluar-agente` |
| El segundo «sí» del guion pide nombre y correo | La app ofrece el pedido pero el texto del folio ya no trae «¿Quieres hacer el pedido?» | Copia el texto nuevo de `render.ORDER_OFFER` en `OFERTA_PEDIDO` (`guiones.py`); `tests/test_ensayo_guiones.py` lo detecta |
| `el folio #… no ofrece el pedido` (saltada en local, fallo en producción) | La app no tiene `ODOO_*` (o corre una versión sin la spec 056) | Normal en local sin Odoo; en producción revisa `/opt/carvajal/.env`, el bloque `odoo` de `/health` y [`odoo-pedidos.md`](odoo-pedidos.md) |
| `/health no publica el bloque «odoo»` o `Odoo configurado pero no alcanzable` | Versión anterior a la spec 056, o Odoo caído o con la clave del bot rechazada (`reason=auth`) | Mira `/health` → `odoo`; con `auth`, revisa `ODOO_API_KEY`; con `unreachable`, la red al Odoo |
| `el recibo /pedidos/… respondió 503` o `404` | 503: Odoo no respondió al pedir el PDF. 404: la cookie no es del dueño o Odoo no tiene el PDF del pedido | Revisa el log `order_pdf.fetch_failed` de la app y el chatter del pedido en Odoo |
| `el correo de confirmación quedó en excepción` | El SMTP rechazó el envío | Odoo → Ajustes → Técnico → Correos, filtro «Excepción»: el motivo del SMTP; revisa el servidor de correo saliente |
| `Odoo no creó el pedido del folio #… ni al reintentar` | Odoo no respondió o rechazó el pedido dos veces (el «Sí, confírmalo» y el «sí» del reintento) | Revisa el log `pedido.fallido` de la app (solo trae el tipo y el status HTTP) y que Odoo esté arriba; el folio queda en `confirmar_pedido` y un «sí» en esa conversación lo reintenta sin duplicar |
| El correo da `awaiting='datos_pedido'` y no llega «📋 Voy a hacer el pedido con estos datos» | El flujo no leyó el nombre (p. ej. un `ENSAYO_RUN_ID` que es una muletilla) o la app corre una versión sin el paso de confirmación | Mira el «Gracias, <nombre>.» del turno anterior; si cambió la plantilla, copia el texto de `render.confirm_buyer` en `CONFIRMAR_DATOS` (`guiones.py`) |
| `Firefox no escucha Marionette` | Firefox abierto sin `--marionette` | Ciérralo del todo y ábrelo con `firefox --marionette` |
| `no se pudo abrir la sesión de WebDriver` | Otro cliente (otro geckodriver) ya tiene Marionette | Termina ese geckodriver; Marionette admite un cliente a la vez |
| `WhatsApp Web no tiene sesión` | La pestaña muestra el QR | Vincula WhatsApp Web en ese mismo perfil de Firefox |
| `llegó una respuesta … fuera de la allowlist` | `allowFrom` o `dmPolicy` cambiaron, o el agente responde a cualquiera | **Regresión de seguridad**: revisa `openclaw config get channels.whatsapp` (solo lectura), [`whatsapp-openclaw.md`](whatsapp-openclaw.md) y la PR #252 (`openclaw/SEGURIDAD.md`) |
| Aviso «evidencia indirecta» | El journal no registró el bloqueo en la ventana | Mira `journalctl --user -u openclaw-gateway --since -10min` en la VM; puede ser una reconexión de WhatsApp |
| `audit` falla con crítico nuevo | Cambio en el gateway o en OpenClaw | Si es intencional, actualiza la base (sección anterior); si no, revierte |
