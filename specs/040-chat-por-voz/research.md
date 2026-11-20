# Investigación: chat por voz

## Reconocimiento y síntesis nativos

**Decisión:** usar `SpeechRecognition` o `webkitSpeechRecognition` y, por separado,
`speechSynthesis` con `SpeechSynthesisUtterance`. Preferir `es-CO` en dictado; para leer,
preferir `es-CO`, luego `es-MX` u otra voz en español. Sin voz de español, explicar la
limitación. Detectar capacidades, no marcas ni versiones de navegador.

**Por qué:** el [reconocimiento tiene soporte limitado][stt]; puede usar un servidor
del proveedor del navegador. La [síntesis][tts] tiene mayor alcance y admite pausar,
reanudar y cancelar. `stop()` intenta devolver lo capturado; `abort()` lo descarta.
Leer automáticamente tras `done` evita reproducir fragmentos parciales. El POST sin
streaming redirige al historial y no distingue una respuesta nueva de una recarga,
por lo que ese camino ofrece solo lectura manual.

**Alternativas:** no usar STT/TTS del servidor, `MediaRecorder`, SDK externos ni exigir
paquetes experimentales de reconocimiento local. No se promete funcionamiento offline.

## Permisos y degradación

**Decisión:** habilitar `microphone=(self)` solo en las respuestas HTML del documento
del chat, conservar `microphone=()` en el resto, y exigir `window.isSecureContext`
antes de activar dictado. Mostrar información sobre el servicio del navegador y pedir
una acción de confirmación antes de `start()`. Si el navegador bloquea la lectura
automática, dejar «Escuchar» disponible.

**Por qué:** `apps/api/security.py` hoy bloquea micrófono globalmente.
[`microphone`][mic] permite limitar el permiso al origen propio sin abrir iframes ni
cambiar CSP. La [guía de despliegue](../../docs/despliegue-ovh.md) documenta HTTP por
IP, que no ofrece el contexto seguro del `localhost` de desarrollo.

**Alternativas:** no habilitar el micrófono en todo el sitio ni rebajar las políticas
para el VPS. Introducir HTTPS queda a cargo del despliegue, antes de anunciar dictado
en producción.

## Texto, preferencias y fidelidad

**Decisión:** normalizar solo números inequívocos del español junto a unidades y
cantidades comerciales en el borrador dictado; dejar intactos nombres, SKU y texto
ambiguo. Utilizar el mismo input y envío de hoy. Guardar únicamente dos booleanos:
aviso confirmado y lectura automática. Manos libres es temporal, no se reactiva al
recargar y exige un borrador vacío. Escribir desactiva el modo sin alterar el texto.

**Por qué:** el parser por reglas de `commercial_intent.py` usa patrones numéricos,
no convierte «doce onzas» ni «cinco mil vasos». Normalizar en el borde del dictado
deja que la persona revise lo que el router recibirá sin cambiar las preguntas escritas.
El chat no tiene cuentas; la preferencia se recuerda en ese navegador/origen.

**Alternativas:** no usar LLM para transcripción o normalización, no cambiar el parser
global ni crear una API de preferencias. Para accesibilidad, usar controles nativos
y estados `role="status"`; no intentar detectar lectores de pantalla.

[stt]: https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition
[tts]: https://developer.mozilla.org/en-US/docs/Web/API/SpeechSynthesis
[mic]: https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Permissions-Policy/microphone
