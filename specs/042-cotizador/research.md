# Research — Cotizador conversacional

**Fecha**: 2026-11-21 · **Spec**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md)

## 1. ¿Quién redacta la cotización: el LLM o el código?

**Decisión**: el código, sin LLM.

**Por qué**: la spec exige exactitud de dinero, presentaciones y existencias. El flujo
comercial de la 036 ya separa hechos (herramientas deterministas) y redacción (LLM); aquí
los hechos son la respuesta completa y un modelo solo podría desviarlos. Además la
cotización se resuelve en memoria y no consume cuota del proveedor.

**Consecuencia**: la ruta no depende de DeepSeek ni de Ollama; si el LLM está caído, el
Cotizador sigue funcionando. El texto se arma con plantillas Jinja en español.

## 2. ¿Cómo se muestra el mapa?

**Decisión**: mapa incrustado de OpenStreetMap (`export/embed.html`) centrado en la sede de
la oferta, con `frame-src https://www.openstreetmap.org` como única excepción de la CSP.

**Por qué**: es un mapa real de calles y no exige JavaScript ni clave de API. Se mantiene la
lista de sedes con dirección, teléfono y enlace de respaldo, así que si el iframe no carga
(o no hay coordenadas) la información sigue completa. La excepción se limita a `frame-src`:
`script-src`, `connect-src` y `img-src` siguen cerrados al origen.

**Privacidad**: el iframe lo sirve OpenStreetMap, que ve la IP del visitante; no recibe la
conversación, el perfil ni el término buscado. El `marker` es la coordenada de una sede
pública del directorio, no la del cliente.

## 3. ¿Cómo se aíslan los hilos del Cotizador?

**Decisión**: prefijo de dueño `cot:<owner_key>` sobre las tablas existentes.

**Por qué**: las conversaciones ya se separan por `owner_key`; derivar un espacio de
nombres en el servidor evita una migración y cumple FR-016. El valor nunca llega del
cliente: se compone con la cookie firmada.

**Riesgo aceptado**: si en el futuro se comparte el panel de hilos entre secciones, habrá
que añadir una columna `kind`; queda anotado en el plan.

## 4. ¿Streaming o turno completo?

**Decisión**: SSE para el texto y las tarjetas, con `POST /cotizador` como respaldo sin
JavaScript.

**Por qué**: el owner pidió la misma experiencia que el chat. El motor no genera tokens,
así que los trozos salen de la respuesta ya calculada, sin pausas artificiales; el cliente
pinta mientras llegan y las tarjetas viajan en un evento `products`. Si el navegador no
soporta streaming, el mismo turno se pide por el POST normal y se inserta completo.
Se persiste una sola vez, al cerrar con `done`.

## 5. ¿Calendario de festivos?

**Decisión**: Pascua por algoritmo de Meeus/Jones/Butcher y los 18 festivos colombianos
derivados por año; sin tabla manual.

**Por qué**: la ventana de entrega debe correr festivos en cualquier año de la demo sin
mantener una lista. El cálculo se prueba contra fechas conocidas (por ejemplo, 20 de julio
y 7 de agosto de 2026).

## 6. ¿Qué se calcula sobre las existencias?

**Decisión**: la comparación usa las unidades comprables (presentaciones completas), no
las solicitadas.

**Por qué**: si se piden 20 y la presentación es de 50, hacen falta 50; con 30 en stock la
oferta no sirve aunque cubra las 20 pedidas. La tarjeta muestra ambas cifras.

## 7. ¿Cómo se recuerda al visitante?

**Decisión**: extracción por reglas, tabla propia `visitor_memory`, caducidad de 180 días y
borrado con «olvida mis datos».

**Por qué**: guardar PII con un LLM añade costo, latencia y un margen de error que aquí no
compensa: los datos que importan tienen patrones claros («me llamo», «trabajo en», «vivo
en», «prefiero»). Una tabla propia mantiene el perfil fuera del payload del mensaje y de
las trazas, permite borrarlo con una fila y caducarlo sin trabajos programados.

**Riesgo aceptado**: en un dispositivo compartido la cookie identifica al navegador, no a
la persona; el perfil se comparte. La sección no anuncia la memoria, pero responde a
«olvida mis datos».
