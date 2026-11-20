# Pruebas del chat por voz (spec 040)

Protocolo reproducible para validar dictado, lectura y manos libres. Complementa
[`specs/040-chat-por-voz/quickstart.md`](../specs/040-chat-por-voz/quickstart.md):
aquí se fija la matriz de navegadores y cómo se registra cada intento. Los
resultados de aceptación se guardan en
[`specs/040-chat-por-voz/evidencia.md`](../specs/040-chat-por-voz/evidencia.md).

## Preparación

1. `uv sync --all-extras` y `make api`; abrir `http://localhost:8000/chat`.
   `localhost` es contexto seguro, igual que HTTPS. Una IP remota por HTTP **no**
   habilita el micrófono.
2. `make check` y `node --test tests/js/test_chat_voice.cjs` con Node `24.16.0`
   (los mismos comandos de `Calidad`). Los dobles de voz cubren el contrato; no
   sustituyen la prueba con micrófono real.
3. Antes de medir, anotar navegador, versión, sistema, idioma, si hay voz
   española instalada y el tipo de conexión. No se sube audio ni eventos de voz
   al servidor: las marcas se toman con `performance.now()` en la consola del
   navegador.

## Matriz de navegadores

Cada fila se prueba en una ventana nueva (perfil limpio o ventana privada para
partir de preferencias apagadas).

| Navegador | Versión | Contexto | STT esperado | TTS esperado | Qué se valida |
|---|---|---|---|---|---|
| Chrome escritorio | estable de la fecha | `http://localhost:8000` | sí (servicio del navegador, requiere red) | sí, voces del sistema | US1, US2, US3, SC-001–SC-005 |
| Chrome escritorio | estable de la fecha | HTTPS publicado | sí, si el servicio responde | sí | requisito de despliegue: sin HTTPS no se ofrece dictado |
| Chrome escritorio | estable de la fecha | `http://<IP>:8080` | no (sin contexto seguro) | sí | el chat explica la limitación y sigue escribiéndose |
| Edge escritorio | estable de la fecha | `http://localhost:8000` | sí | sí | US1, US2 |
| Safari macOS | estable de la fecha | `http://localhost:8000` | sí (si el sistema lo permite) | sí | US2; US1 si hay STT |
| Safari iOS | estable de la fecha | HTTPS | sí (dictado del sistema) | sí | US1, US2 en móvil |
| Chrome Android | estable de la fecha | HTTPS | sí | sí | US1, US2 en móvil |
| Firefox escritorio | estable de la fecha | `http://localhost:8000` | **no** | sí | FR-009: sin micrófono, se explica y se escribe; US2 completa |

> Si un navegador no ofrece reinicio de manos libres tras la síntesis, se
> registra como limitación de ese navegador; no se aprueba el escenario.

## Guion por historia

### US1 · dictado

1. En una ventana nueva, tocar «Dictar». Antes de que el navegador pida permiso
   debe aparecer el aviso de procesamiento por el navegador con «Continuar» y
   «Cancelar»; cancelar no debe abrir el micrófono.
2. Aceptar, conceder permiso y dictar tres preguntas de
   `data/eval/routing_questions.json`, al menos una con capacidad («doce
   onzas») y una con cantidad («cinco mil vasos»). Comprobar que el texto queda
   en `#pregunta`, editable y **sin enviarse**.
3. Editar una y enviarla. Comparar la ruta y los campos extraídos con la misma
   pregunta escrita (traza del chat o `POST /chat` equivalente).
4. Repetir denegando el permiso, en silencio, hablando más de 60 s y pegando
   texto hasta superar 500 caracteres. El borrador previo nunca se pierde.

### US2 · lectura

1. Pedir una respuesta comercial con tarjetas y otra institucional con citas.
2. Tocar «Escuchar»: debe oírse el texto visible, el nombre, las cajas y el
   precio con moneda y fecha de cada tarjeta, y «según» + título en las citas.
   No deben leerse URLs ni SKU.
3. Probar «Pausar», «Reanudar» y «Detener» con teclado (Tab/Enter/Espacio).
4. Activar «Leer respuestas en voz alta», pedir otra respuesta por streaming y
   comprobar que solo suena al completarse (`done`), no por token. Recargar el
   historial y usar el formulario sin streaming: no debe sonar solo.
5. Cambiar de pestaña durante la lectura: se detiene y no se reanuda sola.
6. Con lector de pantalla activo, comprobar que la autolectura sigue apagada por
   defecto (no se duplica la voz) y que «Escuchar» está disponible.
7. Simular bloqueo de reproducción automática (política del navegador): el
   botón «Escuchar» debe quedar disponible y la interfaz no debe fingir que lee.
8. Enviar con el formulario sin streaming (por ejemplo, sin el JS de stream) y
   recargar el historial: no debe sonar solo; «Escuchar» debe seguir ahí.

### US3 · manos libres

1. Con el borrador vacío, activar «Manos libres» y aceptar el aviso de envío
   automático. Preguntar y hacer un seguimiento comercial sin tocar la pantalla.
2. Repetir dejando 10 s de silencio y diciendo «terminar» como orden aislada: el
   modo se apaga y la orden no se envía.
3. Intentar activarlo con texto en el borrador: no se activa y el texto queda
   intacto. Con el modo activo, escribir: se apaga, el texto se conserva y una
   transcripción tardía no lo reemplaza ni lo envía.
4. Probar error de red, permiso revocado y cuota agotada: el modo termina y el
   chat escrito sigue disponible.
5. En DevTools, confirmar que el POST conserva el mismo cuerpo y CSRF: el
   origen «manos libres» es solo un marcador local, no un campo nuevo. Ante un
   fallo del stream no debe haber un POST automático de rescate: el texto vuelve
   al borrador y `chat:turn-failed` apaga el modo.
6. Recargar la página con el modo activo: no se restaura; hay que activarlo otra
   vez y el borrador debe estar vacío.

## T011 · paridad de ruta y campos (validación local)

Tres preguntas del conjunto de evaluación, con la forma dictada (números con
palabras, como los devuelve el reconocimiento) y la forma escrita canónica. La
normalización del borde de dictado (`normalizarCantidades` en
`apps/api/static/chat-voice.js`) produce la forma escrita; el router extrae los
mismos campos. No es la medición de SC-001 (esa exige voces reales).

| Pregunta escrita | Forma dictada | Normalizada | Ruta | Campos extraídos |
|---|---|---|---|---|
| `necesito vasos para café caliente de 12 oz` | `…de doce onzas` | `…de 12 onzas` | `commercial` | `bebida_caliente`, `vaso`, `capacity_oz=12` |
| `vasos para chocolate de 8 oz` | `…de ocho onzas` | `…de 8 onzas` | `commercial` | `bebida_caliente`, `vaso`, `capacity_oz=8` |
| `tapas para vasos de bebida caliente de 12 oz` | `…de doce onzas` | `…de 12 onzas` | `commercial` | `bebida_caliente`, `tapa`, `capacity_oz=12` |
| `necesito 5.000 vasos para café caliente de 12 oz` (variante con cantidad de `Necesito 5.000 vasos de 12 oz para café en México`) | `necesito cinco mil vasos…` | `necesito 5.000 vasos…` | `commercial` | `vaso`, `quantity=5000`, `capacity_oz=12` |

Comprobación local: `extract_commercial(normalizada) == extract_commercial(escrita)`
en los cuatro pares (paridad `true`), y ninguna transcripción se envía sola: el
texto queda en `#pregunta` hasta que la persona pulsa «Enviar». Los navegadores
sin STT o sin permiso muestran el motivo y conservan la escritura.

## Arnés de navegador

`tests/browser/chat_voz_040/` ejecuta un recorrido de Chromium con dobles de voz
y un SSE falso (32 comprobaciones: dictado, lectura, manos libres, fallo del
stream, privacidad y axe). Instrucciones en su `README.md`; **no** forma parte
de `make check` ni de CI y no sustituye las mediciones con voces reales. El
resultado de la última ejecución está en
[`evidencia.md`](../specs/040-chat-por-voz/evidencia.md).

## Registro de intentos (SC-001–SC-003)

| Campo | Cómo se registra |
|---|---|
| `speechend` | `performance.now()` al recibir el último resultado final del reconocimiento |
| `resultado visible` | primer `requestAnimationFrame` posterior a escribir el texto en `#pregunta` |
| `clic Escuchar` | `performance.now()` en el manejador del clic |
| `inicio de voz` | evento `start` de la primera `SpeechSynthesisUtterance` |
| Fallo | falta un evento, hay error o pasan más de 10 s desde la marca inicial |

Cada flujo se repite **40 veces** (20 preguntas por dos voces para dictado; 20
respuestas leídas dos veces para síntesis). Se cuentan éxitos, fallos y la
proporción bajo el umbral; el p95 se calcula por rango más cercano
(`ceil(0.95 × n)`) sobre los éxitos ordenados. Con cero éxitos, el p95 no es
calculable y se informa así.

## Evidencia de privacidad y accesibilidad

- **SC-004:** en DevTools → Network, filtrar por *Media* y revisar que ninguna
  petición al origen del proyecto lleva audio; en un entorno limpio, comprobar
  que la base y el historial no contienen archivos ni campos de audio.
- **SC-005:** ejecutar axe sobre `/chat` antes y después de la 040 y comparar.
  Recorrer con Tab/Enter/Espacio los controles de voz y sus estados
  (`role="status"`), con lector de pantalla si está disponible. Sin errores
  nuevos.
