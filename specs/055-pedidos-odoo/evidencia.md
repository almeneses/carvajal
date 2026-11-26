# Evidencia — Spec 055, pedidos en Odoo

**Fecha**: 2026-11-26 · **Rama**: `feat/odoo-pedidos` (PR #279) · **Commits**: `5e88367`
(pedidos), `bdc7b57` (prueba local y correo saliente), `eae64f3` (fotos del catálogo)

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

Contra el Odoo de la VPS:

- Pedido **S00001** en estado `sale` (confirmado), con la línea del producto y la línea de
  envío (`ENVIO-CARVAJAL`).
- Oportunidad del CRM ganada y enlazada al pedido.
- Correo de confirmación con el PDF del pedido en el chatter, más la nota con el canal y el
  folio.
- Segundo intento con el mismo folio: devolvió el mismo pedido, sin crear otro
  (idempotente).
- Los registros de prueba se borraron después.

## 3. Catálogo (FR-017, FR-018, SC-005)

`scripts/odoo_catalogo.py --imagenes` contra la VPS:

| Medida | Resultado |
|---|---|
| Referencias de Colombia | 200 (127 con precio en COP, según `--dry-run` en el repo) |
| Productos en Odoo | 201: las 200 referencias más el servicio de envío |
| Productos con foto | 194 (las que tienen imagen en `data/catalog/empaques/imagenes/`) |
| Segunda pasada | Idempotente: no creó productos ni subió fotos repetidas |

## 4. Pruebas automáticas (SC-003, SC-004, SC-006)

`tests/test_pedidos_odoo.py`: 23 pruebas (con los casos parametrizados), en verde con
`uv run pytest tests/test_pedidos_odoo.py -q`.

| Grupo | Pruebas | Qué demuestran |
|---|---|---|
| Puro | `test_los_datos_de_quien_compra_salen_del_texto`, `test_los_datos_se_completan_en_dos_mensajes`, `test_la_intencion_de_compra`, `test_ahora_no_rechaza_el_pedido` | Nombre y correo por reglas; «quiero comprar 500 vasos» y las preguntas no aceptan el pedido |
| Líneas | `test_las_lineas_del_pedido_usan_los_importes_del_motor`, `test_un_envio_sin_costo_queda_como_nota` | 2 cajas a $25.000 y envío de $8.000 como servicio; envío pendiente como nota |
| Adaptador JSON-2 | `test_el_pedido_se_crea_confirma_y_deja_traza`, `test_el_mismo_folio_no_crea_otro_pedido`, `test_las_fotos_solo_se_suben_a_los_productos_sin_foto` | Orden de llamadas, `client_order_ref`, `action_confirm` con `send_email`, cabeceras; idempotencia; fotos solo donde faltan |
| Servicio del chat | `test_el_folio_ofrece_el_pedido_y_lo_crea_con_nombre_y_correo`, `test_whatsapp_marca_el_canal_del_pedido`, `test_si_odoo_falla_se_puede_reintentar`, `test_ahora_no_deja_la_cotizacion_sin_pedido`, `test_sin_crm_el_folio_no_ofrece_el_pedido` | Flujo completo sin el modelo, 0 llamadas a Jev en `datos_pedido`, canal, reintento, «ahora no» y sin CRM |

`make check` (Ruff, formato, `ty` y pytest sin integración ni e2e) en verde en la rama con
estas pruebas.

## 5. Hallazgos pendientes

Encontrados al escribir la spec, reproducidos con `quotation.flow.before_turn` y un juez
simulado; el código no se cambió en esta revisión:

1. **Datos enviados directamente a la oferta.** Con `awaiting="pedido"`, un mensaje como
   «Ana Pérez, ana@correo.com» o «ana@correo.com» (sin un «sí» antes) no acepta el pedido:
   las reglas no deciden, Jev recibe el texto (`awaiting="pedido"` no está excluido de
   `_ask_judge`) y el turno va al agente. Es plausible porque la oferta dice «Te pido tu
   nombre y tu correo». Contradice FR-006/FR-019 en espíritu: el texto lleva datos
   personales. Corrección sugerida: si el texto trae un correo con la oferta pendiente,
   tratarlo como aceptación y pasar a `datos_pedido` con los datos ya leídos.
2. **Una pregunta en medio del pedido reinicia el borrador.** Un texto con «?» en
   `pedido` o `datos_pedido` (o un «gracias» con la oferta pendiente) va al agente con un
   borrador vacío, como cualquier turno después de T6; la oferta de ese folio ya no se
   retoma por reglas y «hacer el pedido» después lo atiende el agente. Los datos parciales
   se descartan (bien para la privacidad).
3. **Promesa de correo sin SMTP.** La respuesta dice «Te enviamos la confirmación a …»;
   mientras no haya servidor de correo, el `mail.mail` queda en excepción y el correo no
   llega.
4. **Texto de error de Odoo en los logs.** `OdooError` incluye hasta 200 caracteres del
   mensaje de Odoo (`pedido.oportunidad_fallida` lo registra); un error de validación
   podría repetir un valor del cliente. Conviene registrar solo el código o el tipo.

## 6. Pendiente

- **SMTP**: el correo de confirmación no sale; el `mail.mail` queda en estado de excepción y
  no se reintenta solo. Falta que el owner entregue las credenciales del servidor de correo
  saliente y el remitente real de la compañía (hoy `ventas@example.com`). Pasos en
  [`docs/odoo-pedidos.md`](../../docs/odoo-pedidos.md#correo-saliente).
- **Producción**: el pedido queda activo en web y WhatsApp al fusionar la PR #279 y
  desplegar `main`.
- Los hallazgos 1 y 2 de §5.
