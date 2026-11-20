# Validación de la 040

> Ejecutar una vez implementado el incremento correspondiente. Este plan no cambia la app.

## Preparación

1. `uv sync`, y levantar el chat con `make api` y sus servicios locales (o usar
   un entorno de pruebas ya desplegado).
2. Abrir `http://localhost:8000/chat` para la prueba local o el `/chat` publicado
   mediante HTTPS. Una IP remota en HTTP no sirve para validar el micrófono.
3. Probar al menos un navegador con STT y TTS en español y otro sin STT. Registrar
   navegador, versión, idioma, conexión y si hay voz española instalada.
4. Ejecutar `make check` y `node --test tests/js/test_chat_voice.cjs`. La prueba
   con dobles modela errores y tiempos; no sustituye la prueba con micrófono real.

## Pruebas por historia

| Historia | Pasos | Resultado esperado |
|---|---|---|
| US1 | Dictar tres preguntas de `data/eval/routing_questions.json`, incluidas cantidades y capacidades; revisar y enviar. Probar permiso denegado, silencio, parada a 60 s y texto largo. | Aviso antes del permiso; texto editable, sin envío automático; ruta y datos iguales a la forma canónica escrita; se respeta el límite y el chat escrito. |
| US2 | Pedir una respuesta con tarjetas y otra con citas. Escuchar, pausar, reanudar y detener. Activar lectura automática, recargar, enviar y cambiar de pestaña durante la lectura. | Lee datos visibles y títulos, no URLs ni SKU; solo inicia tras `done` en el hilo visible, recuerda la preferencia y se detiene al ocultar o dictar. El historial no se reproduce al cargar. |
| US3 | Activar manos libres, preguntar y hacer un seguimiento comercial; repetir con diez segundos de silencio, «terminar», error y cuota agotada. | Conserva el hilo, reescucha tras lectura normal, no duplica envíos y sale limpiamente ante cada condición. |

## Evidencia de aceptación

- **SC-001:** 20 preguntas representativas, cada una pronunciada por al menos dos
  voces con consentimiento. Comparar ruta y campos extraídos de cada intento final con
  la pregunta escrita de referencia; contar fallos de transcripción y registrar
  aciertos sobre total. Meta ≥90 %. Registrar resultados sin guardar audio en la app.
- **SC-002/003:** en banda ancha, medir fin de habla → borrador visible y clic de
  «Escuchar» → inicio de voz. Calcular p95 de las muestras e informar errores y
  condiciones. Metas <3 s y <2 s respectivamente.
- **SC-004:** revisar en DevTools las peticiones al origen del proyecto y, en un
  entorno de pruebas limpio, confirmar que no aparecen archivos de audio ni campos
  o datos de audio en el historial persistido. El tráfico propio del navegador al
  servicio de voz no pertenece a la aplicación.
- **SC-005:** ejecutar axe para `/chat` en el navegador, antes y después, y comprobar
  con Tab/Enter/Espacio que los controles y estados se entienden. Sin errores nuevos.

Guardar la matriz y los resultados en `specs/040-chat-por-voz/evidencia.md`. Si un
navegador no soporta reinicio de manos libres o el despliegue no tiene TLS, registrarlo
como limitación, no como aprobación de ese escenario.
