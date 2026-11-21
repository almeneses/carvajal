# Contrato del lector SSE común (044)

Archivo servido desde `/static/sse.js`, cargado antes de `chat-stream.js` en
`chat.html` y antes de `faq.js` en `faq.html`. Expone:

```js
window.CarvajalSSE = {
  leerEventos(response, alEvento),
  interpretar(bloque),
};
```

## `leerEventos(response, alEvento) -> Promise<void>`

- Lee `response.body.getReader()` y decodifica con `TextDecoder` (`stream: true`).
- Acumula texto y separa bloques con `/\r?\n\r?\n/`.
- Por cada bloque no vacío llama `alEvento({ nombre, datos })`.
- Al terminar el flujo, procesa el bloque restante aunque no termine en línea en
  blanco.
- Bloque vacío, sin líneas `data:` o con JSON inválido ⇒ no llama al callback.
- Nombre por defecto si falta `event:`: `"message"`.
- Varias líneas `data:` se unen con `"\n"`.
- Acepta `\n` y `\r\n` como separadores de línea y de bloque.
- No conoce nombres de evento ni el negocio: el consumidor decide qué hacer.

## `interpretar(bloque) -> {nombre, datos} | null`

- `event:` define el nombre; el primer espacio opcional se descarta.
- Cada `data:` acumula su contenido (sin el espacio inicial opcional).
- Sin `data:` o con JSON inválido ⇒ `null`.

## Diferencias que conserva cada consumidor

| Consumidor | Tras leer | Al recibir `done` | Al recibir `error` |
|---|---|---|---|
| `chat-stream.js` | Formatea el turno, crea marcas y notifica `chat:turn-complete`; si falla antes del primer token, reenvía por el POST normal. | Cierra y completa. | Marca la respuesta como incompleta; no notifica turno completo. |
| `faq.js` | Acumula el texto del preview y aplica el lote con `done`; deduplica preguntas y muestra avisos. | Aplica el lote. | Aborta y muestra el mensaje. |

El lector común no emite notificaciones ni toca el DOM: solo entrega eventos.

## Casos que fijan las pruebas de Node

1. Bloque partido entre dos chunks (`event:` en uno y `data:` en otro).
2. Separador partido (`\r\n\r` + `\n`).
3. Varias líneas `data:` que juntas forman JSON válido.
4. Varios eventos en un solo chunk, en orden.
5. Último bloque sin separador final.
6. Nombre por defecto y descarte de bloques inválidos.
7. Cierre del flujo sin `done`: el consumidor lo trata como interrupción.
