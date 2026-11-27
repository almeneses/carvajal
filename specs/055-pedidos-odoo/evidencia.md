# Evidencia — Spec 055, pedidos en Odoo

**Fecha**: 2026-11-26 · **Rama**: `feat/odoo-pedidos` (PR #279) · **Commits**: `5e88367`
(pedidos), `bdc7b57` (prueba local y correo saliente), `eae64f3` (fotos del catálogo),
`688fb9d` (spec y workspace de WhatsApp), `b893c28` (correcciones de la revisión
adversarial), `85413bf` (pruebas, ensayo y docs del paso de confirmación), `fcbf3a2` (reglas
explícitas por paso y recibo en PDF por web y WhatsApp)

## 1. Infraestructura (VPS OVH, 2026-11-26)

| Pieza | Estado |
|---|---|
| Odoo | 19, imagen `odoo:19.0-20261123`, proyecto de compose `carvajal-odoo` en `~/carvajal-odoo` del usuario `deploy` (plantilla: [`deploy/odoo/`](../../deploy/odoo/)) |
| Red | Publicado solo en `127.0.0.1:8169`; unido a `carvajal-prod_default` con el alias `odoo` (la app llega a `http://odoo:8069`) |
| Base | `carvajal`, `es_CO`, COP, sin impuestos por defecto, compañía «Carvajal Empaques (demo)» |
| Módulos | `sale_management`, `crm`, `sale_crm`, `contacts` |
| Acceso de la app | Usuario técnico `carvajal-bot` con API key; API JSON-2 (`POST /json/2/<modelo>/<método>`, `Authorization: bearer`, `X-Odoo-Database`) |
| Configuración de producción | `ODOO_*` ya en `/opt/carvajal/.env`; la app de producción sigue en `main`, sin esta rama |
| Odoo 13 del host y Odoo 15 de Maidensoft | Sin cambios |

**API key rotada una vez**: la primera se imprimió en una salida local durante la
instalación; se revocó y se emitió otra, que es la que está en `/opt/carvajal/.env`.

## 2. Prueba real de punta a punta (SC-001, SC-002)

Contra el Odoo de la VPS, con la primera versión del adaptador (antes de `b893c28`):

- Pedido **S00001** en estado `sale` (confirmado), con la línea del producto y la línea de
  envío (`ENVIO-CARVAJAL`).
- Oportunidad del CRM ganada y enlazada al pedido.
- Correo de confirmación con el PDF del pedido en el chatter, más la nota con el canal y el
  folio. Sin SMTP, el correo no salió (ver §7).
- Segundo intento con el mismo folio: devolvió el mismo pedido, sin crear otro
  (idempotente).
- Los registros de prueba se borraron después.

`b893c28` cambia el orden de las llamadas (oportunidad después de confirmar, cliente por
correo y nombre, pedido en borrador retomado, `body_is_html=True`). Las pruebas reales con el
adaptador nuevo, contra el mismo Odoo de la VPS y el 2026-11-26:

| Pedido | Qué se verificó |
|---|---|
| **S00002** | Un pedido que quedó en borrador se retoma con el mismo folio: se le pone el cliente y se confirma, sin crear otro. La nota del chatter se ve como HTML (`body_is_html=True`), no con las etiquetas literales. Repetir el pedido del folio devuelve el mismo (idempotente) |
| Búsqueda del cliente | Con `=ilike` y el correo **sin escapar**, un correo con `_` encontraba a otro contacto; con el correo escapado (`_like_literal`), no |
| **S00003** | El bot (`carvajal-bot`, por la API) descarga el PDF del pedido confirmado: el `ir.attachment` «Orden - S00003.pdf» (31 KB) que Odoo adjuntó al correo de confirmación, con el número del pedido, la referencia del cliente (el folio), la fecha, las líneas y el total. Es lo que lee `OdooOrders.order_pdf` y sirve `/pedidos/{folio}.pdf` |

Los registros de estas pruebas también se borraron después. La pasada del catálogo con la
versión nueva (§3) y un recorrido completo por el chat contra Odoo con `fcbf3a2` siguen
pendientes (§7).

## 3. Catálogo (FR-017, FR-018, SC-005)

`scripts/odoo_catalogo.py --imagenes` contra la VPS, con la primera versión:

| Medida | Resultado |
|---|---|
| Referencias de Colombia | 200 (127 con precio en COP, según `--dry-run` en el repo) |
| Productos en Odoo | 201: las 200 referencias más el servicio de envío |
| Productos con foto | 194 (las que tienen imagen en `data/catalog/empaques/imagenes/`) |
| Segunda pasada | Idempotente: no creó productos ni subió fotos repetidas |

Con `b893c28` la sincronización busca también los productos archivados y compara `active`:
la siguiente pasada reactiva un SKU archivado en lugar de duplicarlo. No se ha corrido aún
contra la VPS.

## 4. Pruebas automáticas (SC-003, SC-004, SC-006, SC-007)

Antes de la revisión, `tests/test_pedidos_odoo.py` tenía 23 pruebas (con los casos
parametrizados), en verde con `uv run pytest tests/test_pedidos_odoo.py -q`, y `make check`
(Ruff, formato, `ty` y pytest sin integración ni e2e) estaba en verde en la rama.

| Grupo | Qué cubrían |
|---|---|
| Puro | Nombre y correo por reglas; «quiero comprar 500 vasos» y las preguntas no aceptan el pedido; «ahora no» lo rechaza |
| Líneas | 2 cajas a $25.000 y envío de $8.000 como servicio; envío pendiente como nota |
| Adaptador JSON-2 | Orden de llamadas, `client_order_ref`, `action_confirm` con `send_email`, cabeceras; idempotencia; fotos solo donde faltan |
| Servicio del chat | Flujo completo sin el modelo, 0 llamadas a Jev en `datos_pedido`, canal, reintento, «ahora no» y sin CRM |

`b893c28` cambia el comportamiento que esas pruebas fijaban (paso `confirmar_pedido`,
reintento con «sí», orden de las llamadas a Odoo, textos de `order_placed` y
`order_failed`), y `fcbf3a2` lo vuelve a cambiar (reglas por paso, recibo en texto,
`order_pdf`, `/pedidos/{folio}.pdf`, `_with_receipt` del puente). Las pruebas
(`tests/test_pedidos_odoo.py`, `tests/test_pedidos_odoo_flujo.py` y las del recibo por web y
WhatsApp) se actualizan y la suite se corre en la misma PR; el resultado de `make check`
con el diseño nuevo se anota aquí cuando se corra. Este documento no da por buena ninguna
corrida que no esté anotada.

## 5. Revisión adversarial (2026-11-26)

Una revisión adversarial de la primera versión confirmó **24 hallazgos**. `b893c28` los
corrige agrupados en nueve cambios, que cubren también los cuatro que esta evidencia listaba
como pendientes (datos enviados directamente a la oferta, pregunta a mitad del pedido que
reiniciaba el borrador, promesa de correo sin SMTP y texto de Odoo en los logs):

| # | Corrección | Qué fallaba en la primera versión | Diseño nuevo |
|---|---|---|---|
| 1 | Paso de confirmación | El pedido se creaba en cuanto se leían nombre y correo: un nombre mal leído o un correo con un error de dedo creaba el cliente y disparaba el correo sin que la persona los viera. Los datos enviados directamente a la oferta no se leían e iban a Jev y al agente. Tras un fallo de Odoo, los datos se borraban y había que repetirlos | `awaiting="confirmar_pedido"` con `render.confirm_buyer` («📋 Voy a hacer el pedido con estos datos… ¿Lo confirmo?») y botones «Confirmar pedido» / «Ahora no»; un dato nuevo corrige y vuelve a mostrar la confirmación; la respuesta a la oferta con nombre o correo se toma como datos (`has_buyer_data`); si Odoo falla, el borrador queda en la confirmación con los datos y un «sí» reintenta con el mismo folio |
| 2 | Rechazos | Solo se rechazaba con frases exactas y solo en la oferta y en los datos; «no quiero hacer el pedido» aceptaba el pedido | `declines_order` acepta «no, mejor no», «no quiero el pedido», «ya no», «todavía no, gracias»… (`_DECLINE_START`) en cualquier paso; `wants_purchase` devuelve `False` con negación |
| 3 | Nombres | Muletillas y respuestas se tomaban como nombre («ok gracias»), cualquier palabra posterior reemplazaba el nombre ya dado y las partículas salían en mayúscula | `_name_from` rechaza muletillas y palabras del pedido (`_NOT_NAME`), quita las de los bordes (`_FILLER`), deja «de», «del», «la»… en minúscula; `merge_buyer` solo reemplaza un nombre con un correo o una marca («me llamo…», «a nombre de…», «soy…»). **Superado en `fcbf3a2`** por las reglas por paso (§6): la lista sigue, pero ya no es la única defensa, y `merge_buyer` y `has_buyer_data` no los usa el flujo |
| 4 | Correo por voz | El correo dictado («ana punto perez arroba gmail punto com») no se leía | `spoken_email` lo convierte (también «guion» y «guion bajo») cuando el texto dice «arroba» y no trae «@» |
| 5 | Otro producto y preguntas a mitad | «Quiero 500 vasos» o una pregunta con «?» reiniciaban el borrador y la oferta no se retomaba por reglas | Otro producto o cantidad sale del pedido y va al agente (si cotiza, el borrador se reinicia); una pregunta sin datos va al agente sin perder el paso (`Pass` con el mismo borrador) y la respuesta lleva una vez `render.order_reminder`; a la segunda, el paso se suelta |
| 6 | Jev | Con la oferta pendiente (`awaiting="pedido"`), una respuesta corta, incluso con datos, iba a Jev | `ORDER_AWAITING` (`pedido`, `datos_pedido`, `confirmar_pedido`) está excluido de `_ask_judge` |
| 7 | Privacidad de lo guardado | El turno se guardaba con el nombre y el correo en claro: los leían el modelo en los turnos siguientes (historial), el resumen de memoria y LangSmith | `Reply.private` marca los turnos con datos; `ChatService._flow_reply` guarda y monitorea la pregunta como «🛒 (datos para el pedido)» y la respuesta y el flujo guardado con el correo enmascarado (`mask_email`) y el nombre como «(tu nombre)» (`_without_buyer`); la persona ve el texto completo en el turno |
| 8 | Odoo | El pedido existente se buscaba en cualquier estado (un borrador sin confirmar se anunciaba como confirmado; uno cancelado bloqueaba el folio); el cliente se reutilizaba solo por correo y con `=ilike` sin escapar (`%` y `_` como comodines), así que un correo ajeno ponía el pedido en la ficha de otro cliente; la oportunidad se creaba antes del pedido (huérfana o repetida en fallos); la nota del chatter salía con las etiquetas HTML literales; los productos archivados se duplicaban; los logs guardaban hasta 200 caracteres del mensaje de Odoo | Existente con `state != cancel`: en `sale` se devuelve (`existing=True`) y en borrador se le pone el cliente actual y se confirma; cliente por correo escapado (`_like_literal`) y mismo nombre (`_same_name`), o contacto nuevo; oportunidad después de confirmar, ligada con `opportunity_id` y creada una vez en el reintento; `message_post` con `body_is_html=True`; productos y plantillas con `active in [True, False]`, reactivados; `sync_catalog` compara `active`; los logs llevan solo `OdooError.status` y el tipo |
| 9 | SMTP | La respuesta decía «Te enviamos la confirmación a …» y la documentación, que el correo «queda en la cola» | «La confirmación del pedido llega a …»; sin SMTP el correo no sale, queda en excepción y se reenvía desde Odoo cuando haya servidor |

**Riesgo residual (aceptado, fuera de alcance)**: no hay verificación del correo. Cualquiera
puede escribir un correo ajeno y, al confirmar, Odoo le envía a esa persona la confirmación
del pedido. Lo mitigan el paso de confirmación, el cupo diario (`DAILY_REQUESTS_LIMIT`) y la
allowlist de WhatsApp (`dmPolicy: allowlist` en OpenClaw y `WEBHOOK_ALLOWED_NUMBERS` en el
webhook); el chat web solo tiene el cupo. Ya no puede ponerle pedidos a la ficha de un
cliente existente sin dar también su nombre.

**Matiz de privacidad**: los datos sí quedan en el borrador de la base local mientras se
piden (se vacían al crear o rechazar el pedido, no al fallar) y en Odoo. Lo guardado conserva
la primera letra y el dominio del correo. Un nombre dentro de una pregunta que las reglas no
reconocen como nombre va al agente como cualquier pregunta.

## 6. Revisión adversarial, segunda ronda (2026-11-26)

Una segunda revisión adversarial, sobre `b893c28`, confirmó **21 hallazgos** (cada uno
reproducido por dos verificadores; varios son el mismo defecto visto por revisores
distintos). Ninguno quedó refutado. `fcbf3a2` reemplaza la lectura heurística (adivinar un
nombre en cualquier frase y defenderlo con `_NOT_NAME` y con `merge_buyer`) por **reglas
explícitas por paso** (spec, [«Reglas por paso»](./spec.md#reglas-por-paso)): `_order_step`
reparte entre `_offer_step`, `_data_step` y `_confirm_step`, con `find_email`,
`name_in(strict)`, `name_marked`, `correction_name`, `is_affirmation` e `is_bare_no` de
`order.py`.

| Grupo | Hallazgos | Qué fallaba en `b893c28` | Cómo se resolvió en `fcbf3a2` |
|---|---|---|---|
| Frases tomadas como nombre | 3 | En la oferta, los datos se buscaban antes que la aceptación y cualquier frase de 1 a 6 palabras fuera de `_NOT_NAME` era un nombre: «Sí, adelante» → «Adelante», «sí señor» → «Señor», «está bien» → «Está», «cuéntame de la empresa» → «Cuéntame de la Empresa». También pasaban por encima de las reglas de la 054 («solo estaba mirando», el chiste del precio) | En la oferta solo cuentan un correo, un nombre con marca o uno escrito como nombre propio (`name_in(strict=True)`: cada palabra con mayúscula, de 1 a 4), y una afirmación (`is_affirmation`) nunca es nombre; si no, el mensaje sigue el resto de las reglas (también las de la 054). En `datos_pedido`, por decisión, el mensaje es el nombre que se pidió si no es una afirmación ni una muletilla: una frase que no es nombre («solo estaba mirando») puede quedar como nombre, pero se ve en la confirmación y se corrige antes de crear el pedido. `_NOT_NAME` y `_FILLER` suman «adelante», «señor», «pero», «pues», «entonces», «bien»… |
| Correcciones imposibles o que cancelaban | 5 | «Si algo está mal, escríbeme el dato correcto» no se podía cumplir: «no, mi correo es…», «no, me llamo…» o «no, es Carla» cancelaban el pedido y borraban los datos (`_DECLINE_START` antes que los datos, en `datos_pedido` y en `confirmar_pedido`); un nombre escrito solo («Marta Gómez») se ignoraba; «sí, pero a nombre de Marta Gómez» daba «Pero Marta Gómez»; «quiero hacer el pedido con <correo nuevo>» creaba el pedido con el correo viejo y guardaba el nuevo en claro | En `confirmar_pedido`, un correo o un nombre con marca corrigen primero y vuelven a mostrar la confirmación, aunque el mensaje diga «sí» o «quiero hacer el pedido»; un nombre tras «no,» (`correction_name`) o escrito como nombre propio también corrige; un «no» solo o «está mal» (`is_bare_no`) pregunta qué corregir (`render.which_correction`) sin borrar nada; solo un «sí» sin datos crea el pedido. La marca se lee en el texto original (`_MARKED_NAME`), así «pero» no entra al nombre |
| «Cancela» sin efecto | 1 | En `datos_pedido` y `confirmar_pedido` solo valía `declines_order`: «cancela el pedido», «cancela todo» u «olvida el pedido» repetían la petición de datos o la confirmación | Los tres pasos aceptan `declines_order` o `is_cancel_all` (en los datos, solo si el mensaje no trae un correo ni un nombre con marca) |
| Datos con cifras que sacaban del paso | 3 | `starts_other` (producto o cualquier cifra, y sin «@» en el texto crudo) iba antes que los datos: un correo dictado con números, un teléfono o una dirección sacaban el turno del pedido y lo mandaban al agente, a Jev, a LangSmith y al historial con el nombre y el correo. Una pregunta con «?» que traía el nombre también salía, porque la palabra con «?» impedía leer el nombre | Las cifras ya no sacan del paso: solo un producto (`product_family`) sin correo ni nombre con marca. El correo se busca tras convertir el dictado (`find_email`). Una pregunta solo va al agente si no trae datos antes del «¿» (`_question_only`) |
| Otro producto que dejaba el paso pegado | 3 | «quiero 500 vasos» en un paso del pedido devolvía el borrador del pedido: si el agente no cotizaba (pedía la ciudad o listaba referencias), la respuesta sobre los vasos llevaba el recordatorio del pedido, la lista de referencias se perdía y el mensaje siguiente («Bogotá») se leía como nombre | Otro producto devuelve `Pass(QuoteDraft())`: borrador nuevo, cotice o no el agente; la lista de referencias del turno sigue viva |
| Turnos con datos guardados en claro | 1 | Solo `_collect_buyer` y `_confirm_order` marcaban el turno como privado; una aceptación o un rechazo con nombre o correo se guardaban y monitoreaban tal cual | Todo turno que lee o muestra datos pasa por `_with_buyer`, que marca `Reply.private`; el servicio guarda la pregunta como «🛒 (datos para el pedido)» y la respuesta sin los datos. Los rechazos solo salen cuando el mensaje no trae datos |
| Reenvío con datos enmascarados | 3 | Un turno del pedido reenviado con el mismo `client_turn_id` (reintento del webhook de WhatsApp, doble envío en la web) repite lo guardado: «(tu nombre)» y el correo enmascarado, no lo que vio la persona | **Aceptado y documentado** (spec, Edge Cases y Fuera de alcance): es la consecuencia de no guardar los datos; no filtra nada |
| Concurrencia de dos «sí» | 1 | La idempotencia es «buscar y luego crear»: dos turnos cruzados pueden crear dos pedidos, o el segundo encuentra el borrador, lo confirma a la vez y responde «No pude crear el pedido» sobre un pedido que sí quedó | **Tolerado en parte**: `OdooOrders._confirm` trata el 422 de `action_confirm` como éxito si el pedido ya está en `sale`. Los dos pedidos por una carrera antes de crear siguen posibles: sin bloqueo por folio (riesgo residual) |
| Ruff | 1 | Una línea de `_NOT_NAME` de 144 caracteres rompía `line-length = 100`: `make check` y `Calidad` en rojo | La lista se reparte en líneas de menos de 100 columnas |

Además, un comentario sin número de la misma revisión: si Odoo confirmaba el pedido pero la
respuesta se perdía, el reintento lo devolvía como existente sin oportunidad. `place()` ahora
crea la oportunidad si el pedido en `sale` no tiene una (la nota del chatter no se repite).

En la misma ronda entra el recibo pedido por el owner (spec, «Cómo se cumple el pedido del
owner sobre el recibo»): `render.order_placed` es el recibo en texto, `OdooOrders.order_pdf`
lee el PDF del pedido en Odoo (verificado con S00003, §2), `GET /pedidos/{folio}.pdf` lo sirve
al dueño con cookie o firma de dominio propio (`ORDER_DOMAIN`, 7 días, 404 sin prueba, 503
sin Odoo) y el puente de WhatsApp lo adjunta con `ADJUNTAR_PDF:` o lo enlaza.

**Riesgo residual**:

- **No se verifica que el correo sea del cliente.** Cualquiera puede escribir un correo
  ajeno y, al confirmar, Odoo le envía a esa persona la confirmación del pedido (cuando haya
  SMTP). Lo mitigan el paso de confirmación, el cupo diario (`DAILY_REQUESTS_LIMIT`) y la
  allowlist de WhatsApp; el chat web solo tiene el cupo. El recibo en PDF no cambia esto: lo
  descarga solo el dueño de la conversación.
- **El reenvío de un turno con el mismo `client_turn_id` muestra los datos enmascarados**
  («(tu nombre)», `a***@dominio`), no los que vio la persona.
- **Dos pedidos por una carrera** de dos «sí» antes de que exista el pedido del folio (ver
  la fila de concurrencia).
- **Lo que las reglas no leen como dato va al agente**: en la oferta, un nombre en minúsculas
  o mezclado con otras palabras («Ana Pérez, ¿cuánto tarda?») no se toma como dato y el
  modelo lo ve, como cualquier mensaje.

## 7. Pendiente

- **SMTP**: el correo de confirmación no sale; el `mail.mail` queda en estado de excepción y
  no se reintenta solo (se reenvía desde Odoo cuando haya servidor). Falta que el owner
  entregue las credenciales del servidor de correo saliente y el remitente real de la
  compañía (hoy `ventas@example.com`). Pasos en
  [`docs/odoo-pedidos.md`](../../docs/odoo-pedidos.md#correo-saliente).
- **Pruebas del diseño nuevo**: actualizar las pruebas del pedido y del recibo en esta PR y
  anotar el resultado de `make check` en §4.
- **Prueba real con `fcbf3a2`**: un recorrido completo por el chat (web y WhatsApp) contra el
  Odoo de la VPS, con la descarga del recibo, y la pasada del catálogo de §3 con la versión
  nueva.
- **Observado al documentar `fcbf3a2`** (sondeo de las reglas con `_order_step`, sin cambiar
  código):
  - En `confirmar_pedido`, «olvídalo», «no, olvídalo» y «cancélalo» se toman como nombre de
    corrección («Olvídalo») en vez de cancelar; «déjalo» vuelve a mostrar la confirmación.
    La confirmación lo deja ver y no crea el pedido.
  - En la oferta, «Sí, Ana Pérez» o «Ana Pérez, quiero hacer el pedido» aceptan pero no toman
    el nombre (lo vuelven a pedir), y ese turno no se marca como privado: la pregunta se
    guarda con el nombre.
  - «no es Ana, es Carla» corrige el nombre a «Ana Carla».
  - Un correo dictado con cifras sueltas («ana punto perez 22 arroba gmail punto com») se lee
    como `22@gmail.com` y el resto como nombre; la confirmación lo muestra antes de crear el
    pedido.
- **Concurrencia**: serializar la creación del pedido por folio (bloqueo del borrador o por
  conversación); ver el riesgo residual de §6.
- **Verificación del correo**: ver el riesgo residual de §6.
- **Producción**: el pedido queda activo en web y WhatsApp al fusionar la PR #279 y
  desplegar `main`.
