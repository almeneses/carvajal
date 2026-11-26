# Evidencia — Spec 055, pedidos en Odoo

**Fecha**: 2026-11-26 · **Rama**: `feat/odoo-pedidos` (PR #279) · **Commits**: `5e88367`
(pedidos), `bdc7b57` (prueba local y correo saliente), `eae64f3` (fotos del catálogo),
`688fb9d` (spec y workspace de WhatsApp), `b893c28` (correcciones de la revisión
adversarial)

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
  folio. Sin SMTP, el correo no salió (ver §6).
- Segundo intento con el mismo folio: devolvió el mismo pedido, sin crear otro
  (idempotente).
- Los registros de prueba se borraron después.

`b893c28` cambia el orden de las llamadas (oportunidad después de confirmar, cliente por
correo y nombre, pedido en borrador retomado, `body_is_html=True`). **Falta repetir la
prueba real con esa versión**; hasta entonces, este apartado vale para la primera.

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
`order_failed`). Las pruebas se actualizan en la misma PR con los casos de §5; el
resultado de `make check` con el diseño nuevo se anota aquí cuando se corra.

## 5. Revisión adversarial (2026-11-26)

Una revisión adversarial de la primera versión confirmó **24 hallazgos**. `b893c28` los
corrige agrupados en nueve cambios, que cubren también los cuatro que esta evidencia listaba
como pendientes (datos enviados directamente a la oferta, pregunta a mitad del pedido que
reiniciaba el borrador, promesa de correo sin SMTP y texto de Odoo en los logs):

| # | Corrección | Qué fallaba en la primera versión | Diseño nuevo |
|---|---|---|---|
| 1 | Paso de confirmación | El pedido se creaba en cuanto se leían nombre y correo: un nombre mal leído o un correo con un error de dedo creaba el cliente y disparaba el correo sin que la persona los viera. Los datos enviados directamente a la oferta no se leían e iban a Jev y al agente. Tras un fallo de Odoo, los datos se borraban y había que repetirlos | `awaiting="confirmar_pedido"` con `render.confirm_buyer` («📋 Voy a hacer el pedido con estos datos… ¿Lo confirmo?») y botones «Confirmar pedido» / «Ahora no»; un dato nuevo corrige y vuelve a mostrar la confirmación; la respuesta a la oferta con nombre o correo se toma como datos (`has_buyer_data`); si Odoo falla, el borrador queda en la confirmación con los datos y un «sí» reintenta con el mismo folio |
| 2 | Rechazos | Solo se rechazaba con frases exactas y solo en la oferta y en los datos; «no quiero hacer el pedido» aceptaba el pedido | `declines_order` acepta «no, mejor no», «no quiero el pedido», «ya no», «todavía no, gracias»… (`_DECLINE_START`) en cualquier paso; `wants_purchase` devuelve `False` con negación |
| 3 | Nombres | Muletillas y respuestas se tomaban como nombre («ok gracias»), cualquier palabra posterior reemplazaba el nombre ya dado y las partículas salían en mayúscula | `_name_from` rechaza muletillas y palabras del pedido (`_NOT_NAME`), quita las de los bordes (`_FILLER`), deja «de», «del», «la»… en minúscula; `merge_buyer` solo reemplaza un nombre con un correo o una marca («me llamo…», «a nombre de…», «soy…») |
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

## 6. Pendiente

- **SMTP**: el correo de confirmación no sale; el `mail.mail` queda en estado de excepción y
  no se reintenta solo (se reenvía desde Odoo cuando haya servidor). Falta que el owner
  entregue las credenciales del servidor de correo saliente y el remitente real de la
  compañía (hoy `ventas@example.com`). Pasos en
  [`docs/odoo-pedidos.md`](../../docs/odoo-pedidos.md#correo-saliente).
- **Pruebas del diseño nuevo**: actualizar `tests/test_pedidos_odoo.py` en esta PR y anotar
  el resultado de `make check` en §4.
- **Prueba real con `b893c28`**: repetir §2 (y la pasada del catálogo de §3) contra el Odoo
  de la VPS.
- **Verificación del correo**: ver el riesgo residual de §5.
- **Producción**: el pedido queda activo en web y WhatsApp al fusionar la PR #279 y
  desplegar `main`.
