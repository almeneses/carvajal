# Contrato de interfaz: chat por voz

## Dictado

- El botón de micrófono es `type="button"`, tiene nombre accesible y alterna entre
  «Dictar» y «Detener dictado». Un estado visible `role="status"` anuncia inicio,
  término, ausencia de voz y errores de permisos o compatibilidad.
- El primer dictado muestra un aviso sobre el posible procesamiento por el
  navegador y ofrece continuar o cancelar antes de iniciar el micrófono. Ni
  cargar la página ni restaurar una preferencia inicia reconocimiento.
- Al detener o llegar a 60 s se aprovecha el resultado final disponible. Al
  cancelar, recibir un error o no obtener texto, no se pierde el borrador previo.
  Se respeta el cursor o selección al insertar y el límite de 500 caracteres;
  se avisa si se recorta. No se envía fuera de manos libres.

## Lectura

- «Escuchar» en el historial y en el turno completado. Mientras se lee, permite
  «Pausar», «Reanudar» y «Detener», con teclado y etiquetas visibles. Solo habla
  un turno del hilo visible; al ocultar la pestaña se cancela sin reanudar por sí solo.
- Tras recibir `done`, formatear el DOM y liberar el formulario, `chat-stream.js`
  emite `chat:turn-complete` una vez con el elemento del turno y el `thread_id`.
  No se emite por token, error, carga de historial ni corte sin `done`. La lectura
  automática requiere la preferencia activa; la lectura manual no. Cuando
  `POST /chat` redirige sin streaming, «Escuchar» queda disponible sin disparo automático.
  Un evento `fallback` dentro del stream no dispara lectura por sí solo; si le sigue
  `done`, se trata como cualquier otro turno SSE completado.
- Se usa el texto visible de la respuesta y, si están presentes, nombres, cajas,
  cantidades, precios, moneda y fechas de tarjetas; una tarjeta sin precio no
  adquiere uno al leerla. Las citas se leen como «según» seguido del título. No se
  agregan URLs, SKU ni códigos técnicos. Coexistir con `aria-live` del chat no
  implica activación automática por defecto.

## Manos libres (US3)

- Requiere activar el modo explícitamente con reconocimiento, síntesis y streaming
  disponibles y **borrador vacío**. Si el borrador no está vacío, explicar el motivo y
  conservarlo. Avisar que en ese modo sí se envía texto sin confirmar cada turno.
- Si la persona escribe durante el modo, desactivarlo y conservar el borrador. Antes
  del autoenvío, comprobar de nuevo que el ciclo sigue vigente y el campo está vacío.
  Insertar solo la transcripción final no vacía de ese ciclo (no concatenarla) y enviar
  una vez con `form.requestSubmit()`; no se construyen fetch ni bypasses paralelos.
  No se abre el micrófono durante la lectura, ni se reabre cuando termina por
  cancelación o error. Si pasan 10 s sin voz o se dice «terminar» como orden aislada,
  se apaga y no se envía esa orden.
- Cuota agotada, permiso revocado, fallo de streaming o pérdida del hilo visible
  terminan el modo; el chat escrito sigue disponible. Navegadores sin soporte de
  reinicio tras la síntesis degradan a dictado manual.
- En manos libres, una caída del stream no reenvía el turno por POST normal:
  el adaptador de streaming devuelve el texto al formulario para revisión y
  notifica `chat:turn-failed`. El envío escrito/manual conserva su fallback.

## HTTP y privacidad

Los POST conservan el formato actual, el límite de longitud, CSRF, cuota por
visitante y el mecanismo existente de idempotencia del stream. No se introduce un
límite por IP para el chat. Ningún flujo de voz envía audio, flags de voz ni
datos del micrófono al servidor del proyecto.
El JS se sirve desde el propio origen con CSP vigente. Cámara y geolocalización
siguen deshabilitadas.
