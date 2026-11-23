# Contrato del chat comercial — Spec 043

## Entradas

| Consumidor | Contrato |
| --- | --- |
| Compositor del chat | `POST /chat` y `POST /chat/stream` conservan pregunta, token CSRF, identidad y el hilo cuando exista. Ambos aceptan y propagan el mismo `client_turn_id`. |
| Cliente de API | `POST /api/chat/stream` conserva el contrato SSE vigente. Los cambios de ciudad o cantidad pasan por el mismo servicio de turnos que el HTML. |
| Acceso heredado | `GET /cotizador` lleva al chat cuando el corte se activa. Un enlace con hilo histórico lo resuelve mediante lectura compatible y comprueba el dueño firmado, incluido el espacio de identidad heredado. |

## Resultado estructurado

- **Información**. Texto y citas de las fuentes documentales pertinentes.
- **Ficha**. Producto, usos, material, presentación y fuente.
- **Ofertas**. Vendedor, observación publicada, presentación, moneda, IVA
  declarado y vigencia de vendedor si existe.
- **Sedes**. Contactos con precisión de ubicación, vínculo documentado con el
  SKU cuando exista y estado explícito de disponibilidad.
- **Cotización**. Folio y cálculo determinista cuando se emite. Cantidades,
  precio por presentación, subtotal, envío y total final tienen campos
  distintos. Un valor pendiente se conserva como ausencia.
- **Aclaraciones**. Solicitud de cantidad para calcular o elección de la
  primera línea, conservando el resto del contexto. La búsqueda de sedes
  puede devolver contactos sin esos datos.

## Respuesta, reenvío y recarga

Conservar los eventos SSE que consumen el cliente y la voz, incluidos
`meta`, `products`, `token`, `done` y `error` donde correspondan. El
evento de tarjetas mantiene el HTML renderizado por el servidor y el cierre
mantiene las identidades que usa el cliente. El resultado confirmado
se reconstruye con las mismas fuentes, tarjetas, hilo y folio desde SSE,
POST HTML y recarga del historial. Un fallo parcial mantiene la semántica
de respuesta incompleta.

El alcance de identidad del reintento es el visitante dueño y el envío.
El reenvío con el mismo identificador recupera la operación original,
incluido el primer hilo. Acceso a un hilo ajeno conserva el rechazo del
chat y los resultados históricos no publicables se excluyen antes de
renderizar o sembrar el contexto.

El plan de retiro recoge los consumidores vigentes de los POST del
Cotizador y de la API de FAQ. La navegación pública puede cambiar a
preguntas de inicio del chat manteniendo la API que tenga consumidores.
