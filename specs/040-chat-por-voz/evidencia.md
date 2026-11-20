# Evidencia del chat por voz (040)

Fecha: 2026-11-20. Validación en **localhost** (`http://127.0.0.1`), con la app
Jinja2/HTMX real y un servidor de pruebas que sustituye chat y SSE (sin base de
datos, LLM ni Qdrant). El navegador es **Chrome for Testing 149.0.7827.55**
(Chromium de Playwright), Node **24.16.0** y Python **3.13**.

> Esta evidencia **no sustituye** SC-001–SC-003: esas metas exigen micrófono,
> voces reales y el servicio de reconocimiento del navegador. Aquí se registra
> lo verificable sin ellos y se declara el bloqueo.

## Resumen de criterios

| Criterio | Estado | Evidencia |
|---|---|---|
| SC-001 paridad de ruta/campos ≥90 % con 20 preguntas y 2 voces | **BLOQUEADO** | No hay micrófono ni voces reales en este entorno; no se inventan porcentajes. La paridad del borde de dictado se verificó con 4 formas (T011) y el arnés queda listo para la medición real. |
| SC-002 texto en <3 s (p95) tras hablar | **BLOQUEADO** | Requiere el servicio de reconocimiento real; el doble no mide latencia de red del navegador. |
| SC-003 lectura en <2 s (p95) | **BLOQUEADO** | Requiere `speechSynthesis` real con voz instalada; el doble emite `start` de forma síncrona. |
| CSRF, CSP, cuota | **CUMPLE** | CSP intacta (sin `unsafe-inline`, `frame-ancestors 'none'`); el permiso de micrófono solo se declara en el documento de `/chat`; los POST conservan token y cuota por visitante, y dictar o escuchar sin enviar no genera POST (las pruebas JS exigen `formulario.enviados === 0`; la cuota solo se consume en `POST /chat`/`/chat/stream`). |
| SC-004 0 archivos de audio persistidos | **CUMPLE** | Red del navegador sin peticiones `media`/audio; cuerpos POST solo con `question`, `csrf_token`, `client_turn_id`, `thread_id`; `localStorage` solo con dos preferencias booleanas de voz (más la identidad de visitante preexistente); `indexedDB.databases()` vacío; el esquema no tiene campos de audio. |
| SC-005 sin errores nuevos de accesibilidad | **CUMPLE** | axe (`wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`): 0 violaciones antes (sin `chat-voice.js`) y 0 después; 0 nuevas. Teclado verificado: Enter inicia la lectura, Tab alcanza Pausar, el foco pasa a Reanudar al pausar y a Escuchar al detener. |

## Comandos y resultados

```text
$ make check
1634 passed, 46 deselected, 2 warnings

$ node --test tests/js/test_chat_voice.cjs      # Node 24.16.0
tests 65 / pass 65 / fail 0

$ NODE_PATH=<deps> node tests/browser/chat_voz_040/validar.cjs
36 pasos / 0 fallos / exit 0
axe: base 0, después 0, nuevos 0
```

El arnés de navegador (`tests/browser/chat_voz_040/`) usa Chromium real con
dobles de `SpeechRecognition`/`speechSynthesis` y un SSE falso. **No corre en
`make check` ni en CI**; instrucciones en su `README.md`.

## Recorrido de navegador (36 comprobaciones)

1. Controles visibles con STT, TTS y contexto seguro; autolectura apagada por
   defecto; botones `type="button"` con nombre accesible; estado `role="status"`.
2. Aviso antes del primer dictado; transcripción «necesito doce onzas…» queda
   como «necesito 12 onzas…» en el campo y **no se envía sola**.
3. Con autolectura apagada, el turno completado no suena.
4. «Escuchar» lee el texto, los datos de la tarjeta (nombre, cajas, precio con
   moneda y fecha) y «Según Manual de empaques»; no lee URLs ni SKU; la primera
   locución no mezcla la tarjeta.
5. Enter/Tab con teclado; Pausar/Reanudar/Detener; el foco no cae al `body`.
6. Autolectura activada: suena tras `done`.
7. Manos libres: aviso de autoenvío, un solo envío, reescucha tras la lectura
   normal, escritura que apaga el modo conservando el texto.
8. Fallo del stream en manos libres: **no hay POST de rescate**, el texto vuelve
   al borrador y el modo se apaga con aviso.
9. Privacidad: sin peticiones de audio, sin IndexedDB, preferencias booleanas.
10. fallback sin `chat-stream.js`: POST normal, historial con «Escuchar» y lectura manual tras recargar; la política de autoplay (sin `onstart`) no finge que lee.
11. axe sin violaciones nuevas.

Cuerpo real de los POST observados (sin campos de voz):

```text
question=necesito+5.000+vasos&csrf_token=…&client_turn_id=…&thread_id=hilo-e2e
```

## Paridad del dictado (T011)

Con `extract_commercial`, las formas normalizadas y escritas producen el mismo
resultado en los cuatro pares de `docs/pruebas-040.md`:

| Dictado | Normalizado | Campos |
|---|---|---|
| necesito vasos para café caliente de doce onzas | …de 12 onzas | `bebida_caliente`, `vaso`, `capacity_oz=12` |
| vasos para chocolate de ocho onzas | …de 8 onzas | `bebida_caliente`, `vaso`, `capacity_oz=8` |
| tapas para vasos de bebida caliente de doce onzas | …de 12 onzas | `bebida_caliente`, `tapa`, `capacity_oz=12` |
| necesito cinco mil vasos para café caliente de 12 onzas | …5.000 vasos… | `vaso`, `quantity=5000`, `capacity_oz=12` |

Esto verifica el borde de normalización, **no** SC-001.

## Límites y bloqueos declarados

- **SC-001, SC-002 y SC-003** no se midieron: no hay micrófono, voces instaladas
  ni servicio de reconocimiento en este entorno. El protocolo de 40 intentos
  por navegador está en [`quickstart.md`](quickstart.md) y
  [`docs/pruebas-040.md`](../../docs/pruebas-040.md); falta ejecutarlo con
  personas y voces reales.
- **T017** queda pendiente de un lector de pantalla real y de la política de
  reproducción automática de un navegador con voz instalada; la estructura
  (nombres, `aria-pressed`, `role="status"`, preferencia apagada) y el teclado
  sí se verificaron.
- **Despliegue:** el VPS de la demo publica por HTTP e IP; el micrófono exige
  contexto seguro. Allí solo se ofrece escritura y lectura hasta publicar por
  HTTPS ([`docs/despliegue-ovh.md`](../../docs/despliegue-ovh.md)).
- El recorrido registra dos mensajes de consola: uno **preexistente** de un
  estilo inline bloqueado por la CSP (no introduce `unsafe-inline`) y el 500
  simulado del escenario de fallo.
- No se probó un lector de pantalla ni el diálogo de permiso del sistema; no se
  guardó audio en ningún momento.
