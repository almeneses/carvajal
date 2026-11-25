# Feature Specification: Flujos de cotización

**Feature Branch**: `feat/054-flujos-cotizacion`

**Created**: 2026-11-25

**Status**: Implemented (2026-11-25). Evaluación: [evidencia.md](./evidencia.md).

**Input**: User description: «Danos una propuesta de flujo para cuando se está averiguando y/o
cotizando productos»: tendero que va directo; tendero con un producto nuevo que envía foto;
cliente que averigua precios (varias cantidades, foto o descripción); quien elige la
recomendada; quien elige una alternativa; quien solo mira y no compra; quien cotiza dos o más
productos («¿algo más?», con el total hasta ese momento); y quien cambia de producto a mitad
del proceso (se pregunta si se cancela lo que llevaba). Al final de cada flujo se pide
confirmación y, con un «sí», se genera y envía el PDF. Una recomendada y máximo cuatro
alternativas. Mensajes consistentes, profesionales, amigables y cortos, por voz o por texto.
Después: «Aplica todo lo de agregar y quitar que propones, también las propuestas de la
sección de preguntas del owner», «¿cómo respondería preguntas fuera del flujo?», «agrega un
flujo especial para cuando la persona rechaza: "está muy caro, muy ladrones" → "Arrancado,
vaya cómprele a su madre entonces" con un emoji riéndose», «debe funcionar en WhatsApp y en
el chat web» y «utiliza Jev donde pueda ser útil».

Propuesta revisada por el owner (página «Flujos de cotización Carvajal»).

## Decisiones del owner (2026-11-25)

1. **Recomendada**: la más apta para el uso según el catálogo y la guía de materiales; entre
   igual de aptas, la más económica.
2. **Atajo del tendero**: con un producto inequívoco (SKU, nombre o una cotización sin
   alternativas) la línea se agrega directo; si no, se muestran las opciones.
3. **Ciudad y vendedor**: una ciudad y un país por cotización; el vendedor puede variar por
   línea y el resumen y el PDF lo dicen.
4. **Envío**: solo en el resumen, uno por vendedor; el total parcial va sin envío.
5. **Vigencia del borrador**: 7 días, con «retomar».
6. **Límites**: 10 líneas por cotización y 4 cantidades por comparativo.
7. **Abandono**: se registra un evento anónimo (`cotizacion.*` en los logs), sin texto ni
   número.
8. **Rechazo con insulto**: la frase fija «Arrancado, vaya cómprele a su madre entonces 😂».

## Modelo

Cada conversación tiene un **borrador** (`quote_drafts`): líneas sin folio, lo último que se
mostró (opciones o comparativo) y **qué respuesta espera** el asistente (`awaiting`:
`eleccion`, `cantidad`, `algo_mas`, `confirmar`, `cambio_producto`, `oferta`). Las respuestas
cortas se interpretan contra esa pregunta:

1. Reglas deterministas (`carvajal.agent.followup`).
2. Jev (Choice de 8 clases) solo si hay pregunta pendiente, el mensaje es corto, no es una
   pregunta y ninguna regla decidió; confianza ≥ 0,7 (0,85 para el rechazo con insulto). Su
   clase se traduce a una frase canónica que vuelve a pasar por las reglas. Si Jev falla, el
   turno va al agente (*fail-open*).
3. El agente (LLM con herramientas).

Después del turno del agente, las cotizaciones que calculó se convierten en: **opciones**
(una cotización con alternativas), **comparativo** (el mismo SKU en 2 a 4 cantidades),
**líneas** (un producto inequívoco o varios productos distintos) o, sin cotización, un
**recordatorio** de la cotización pendiente.

## User Scenarios & Testing *(mandatory)*

Los guiones de cada flujo, por texto y por voz, están en
[`data/eval/flujos_cotizacion_054.json`](../../data/eval/flujos_cotizacion_054.json).

### US1 – Tendero que va directo (P1)

Pide producto, cantidad y ciudad en un mensaje; recibe la línea con su total y «¿Algo más
para tu cotización?». «No, confírmala» registra de una vez (atajo); «no» muestra el resumen.

### US2 – Tendero con un producto nuevo y foto (P1)

La foto da una recomendada y hasta cuatro alternativas (spec 053). «La dos, son 300 para
Bogotá» elige y cotiza; la línea pasa al carrito. «Sí, también 300 tapas» agrega otra.

### US3 – Cliente que compara precios por cantidad (P1)

«¿Cuánto valen 100, 500 o 1000?» → un comparativo del mismo producto (`cotizar` con
`cantidades`), con total y precio por unidad, y «¿Con cuál cantidad la armo?». «Con mil», «la
de 500» o «la segunda» agregan esa línea sin volver al modelo. No se prometen descuentos que
el catálogo no tiene: solo la diferencia que da la presentación.

### US4 y US5 – Opciones: la recomendada o una alternativa (P1)

Una recomendada (⭐) y hasta cuatro alternativas numeradas, con el total para la cantidad.
«Dame esa» agrega la recomendada; «la 3» o «la tercera» recotiza la alternativa con el motor
(sin LLM); «la 5» repite las opciones válidas sin cotizar; «la 3 pero en 12 oz» es una búsqueda
nueva.

### US6 – Solo mira o rechaza (P2)

«Gracias, lo pienso» se despide sin folio ni insistencia y guarda el borrador 7 días
(«retomar»). «Está caro» ofrece una sola vez la opción más económica ya calculada; un segundo
«caro» o un «no» se despiden. «Está muy caro, muy ladrones» responde la frase fija del owner.

### US7 – Varios productos con total parcial (P1)

Cada línea agregada muestra el carrito con el total parcial y «¿Algo más?». «Quita los vasos»
o «borra la 2» quitan una línea. Un mismo SKU actualiza su línea en lugar de duplicarla. Al
cerrar, el resumen trae subtotal, IVA, envío y total, y un solo folio y un PDF con la tabla de
productos.

### US8 – Cambio de producto a mitad del proceso (P1)

Con un producto en curso (opciones, comparativo o un dato pendiente) o justo después de
agregar una línea, «no, mejor vasos» pregunta: «Llevábamos X. ¿Qué hago con eso? 1. Cancelar
2. Conservar y agregar lo nuevo». Nada se cancela sin respuesta; después, el pedido nuevo
llega al agente.

### US9 – Preguntas fuera del flujo (P2)

Una pregunta sobre Carvajal o de otro tema en medio de la cotización se responde como siempre
(RAG, herramientas o la negativa fija fuera de alcance) y se agrega una línea «↩️ Seguimos con
tu cotización…» con la pregunta pendiente, en los dos primeros turnos fuera del flujo. El
borrador no cambia.

### Edge Cases

- Confirmar dos veces: un solo folio; el segundo «sí» responde que ya quedó registrada.
- Precio vencido al confirmar: se recalcula con el motor y se muestra otro resumen; si no se
  puede, el agente recotiza.
- «Sí» a «¿Algo más?» pregunta qué producto agregar; «sí, 500 vasos» va directo al agente.
- «Sí, mándame el PDF» con una cotización por confirmar: confirma y adjunta el PDF.
- Una línea sin precio queda fuera del total y se dice.
- Borrador de más de 7 días: no se retoma.

## Requirements *(mandatory)*

- **FR-001**: Toda cotización termina en resumen, «¿La confirmo?» y, con un «sí», folio y PDF;
  nada se registra sin confirmación.
- **FR-002**: Una cotización puede tener hasta 10 líneas con un solo folio.
- **FR-003**: Las respuestas cortas a la pregunta pendiente se resuelven con reglas y, si no,
  con Jev; nunca un registro sin el «sí».
- **FR-004**: Recomendada + hasta 4 alternativas en texto, web y foto, con el criterio de la
  decisión 1.
- **FR-005**: Comparativo de 2 a 4 cantidades del mismo producto.
- **FR-006**: Cambiar de producto pregunta antes de cancelar.
- **FR-007**: Las cifras de los mensajes del flujo salen de plantillas, no del modelo.
- **FR-008**: El mismo mensaje en la web (Markdown y botones), WhatsApp (`*negrita*`) y voz
  (≤ 45 palabras, sin emojis ni Markdown).
- **FR-009**: Las cantidades dictadas con palabras pasan a cifras también en el servidor.
- **FR-010**: Las preguntas fuera del flujo no rompen la cotización.
- **FR-011**: A Jev solo viajan la pregunta fija del asistente y la respuesta corta.

## Formatos de mensaje

Una pregunta por mensaje, al final, con la respuesta sugerida en negrita; una línea por
producto; 12 líneas o menos en los mensajes habituales; emojis fijos como marcadores (🔎 ⭐ 📊
✅ 🔄 🛒 🧾 📍 💰 📄 ↩️), nunca en la prosa ni en la voz; formato colombiano ($206.000, «und»,
«c/u», «IVA incl.»). Plantillas en `carvajal/quotation/render.py`: opciones (T2), comparativo
(T3), línea y carrito (T4), resumen (T5), registrada (T6), cambio de producto (T7),
cancelación (T8), despedida (T9), fuera de rango (T10), recordatorio, oferta económica y la
respuesta al rechazo.

## Fuera de alcance

- Descuentos por volumen: el catálogo no los publica.
- Sugerir la tapa como línea automática tras una foto: queda como texto de la 051.
- Pedidos, reservas o pagos.

## Success Criteria *(mandatory)*

- **SC-001**: Los 18 guiones (8 flujos, texto y voz) pasan sin LLM.
- **SC-002**: 0 registros sin «sí» en las pruebas del servicio, de WhatsApp y del webhook.
- **SC-003**: Las plantillas habituales caben en 14 líneas y su voz en 45 palabras.
- **SC-004**: La suite completa sigue en verde y la evaluación del agente no empeora frente a
  la línea base salvo por el cambio esperado (cifras en la plantilla).
