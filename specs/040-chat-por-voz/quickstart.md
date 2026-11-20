# Validación de la 040

> Ejecutar una vez implementado el incremento correspondiente. Este plan no cambia la app.

## Preparación

1. `uv sync`, y levantar el chat con `make api` y sus servicios locales (o usar
   un entorno de pruebas ya desplegado).
2. Abrir `http://localhost:8000/chat` para la prueba local o el `/chat` publicado
   mediante HTTPS. Una IP remota en HTTP no sirve para validar el micrófono.
3. Probar al menos un navegador con STT y TTS en español y otro sin STT. Registrar
   navegador, versión, idioma, conexión y si hay voz española instalada.
4. Ejecutar `make check` y, con Node `24.16.0`, `node --test tests/js/test_chat_voice.cjs`,
   el mismo comando que ejecutará `Calidad`. Los dobles no sustituyen la prueba
   con micrófono real.

## Pruebas por historia

| Historia | Pasos | Resultado esperado |
|---|---|---|
| US1 | Dictar tres preguntas de `data/eval/routing_questions.json`, incluidas cantidades y capacidades; revisar y enviar. Probar permiso denegado, silencio, parada a 60 s y texto largo. | Aviso antes del permiso; texto editable, sin envío automático; ruta y datos iguales a la forma canónica escrita; se respeta el límite y el chat escrito. |
| US2 | Pedir una respuesta con tarjetas y otra con citas. Escuchar, pausar, reanudar y detener. Activar lectura automática y probar streaming, recarga y el formulario sin streaming (con el JS de voz activo). Cambiar de pestaña durante la lectura. | Lee datos visibles y títulos, no URLs ni SKU; solo inicia automáticamente tras `done` en el hilo visible. La preferencia se recuerda, pero el historial y la respuesta sin streaming requieren «Escuchar». Se detiene al ocultar o dictar. |
| US3 | Activar manos libres con borrador vacío, preguntar y hacer un seguimiento comercial; repetir con silencio de diez segundos, «terminar», error y cuota agotada. Intentar activar con borrador previo; en otro ciclo, escribir y simular una transcripción tardía. | Conserva el hilo, reescucha tras lectura normal y no duplica envíos. No inicia con borrador previo; al escribir se apaga, conserva el texto intacto y no lo envía. |

## Evidencia de aceptación

- **SC-001:** 20 preguntas representativas, cada una pronunciada por al menos dos
  voces con consentimiento. Comparar ruta y campos extraídos de cada intento final con
  la pregunta escrita de referencia; contar fallos de transcripción y registrar
  aciertos sobre total. Meta ≥90 %. Registrar resultados sin guardar audio en la app.
- **SC-002/003:** hacer **40 intentos por navegador y flujo** en banda ancha:
  20 preguntas por dos voces para dictado y 20 respuestas leídas dos veces para
  síntesis. Usar el reloj monotónico del navegador (`performance.now()`): para
  dictado, marcar `speechend` y el primer cuadro posterior a mostrar el resultado
  final en el campo; para lectura, marcar el clic en «Escuchar» y el evento `start`
  de la primera locución. Si falta un evento, hay error o no se completa en 10 s
  desde la marca inicial, contar ese intento como fallido, sin reponerlo. Reportar
  éxitos, fallos y proporción del total bajo el umbral. Calcular p95 de los éxitos
  por rango más cercano (`ceil(0.95 × n)` en milisegundos ordenados); con cero
  éxitos, indicar que no es calculable. Metas: **<3 s** y **<2 s**, respectivamente.
  Registrar navegador, versión y condiciones; medir localmente sin subir audio ni
  eventos al servidor.
- **SC-004:** revisar en DevTools las peticiones al origen del proyecto y, en un
  entorno de pruebas limpio, confirmar que no aparecen archivos de audio ni campos
  o datos de audio en el historial persistido. El tráfico propio del navegador al
  servicio de voz no pertenece a la aplicación.
- **SC-005:** ejecutar axe para `/chat` en el navegador, antes y después, y comprobar
  con Tab/Enter/Espacio que los controles y estados se entienden. Sin errores nuevos.

Guardar la matriz y los resultados en `specs/040-chat-por-voz/evidencia.md`. Si un
navegador no soporta reinicio de manos libres o el despliegue no tiene TLS, registrarlo
como limitación, no como aprobación de ese escenario.
