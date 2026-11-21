# Evidencia del chat por voz (040)

Fecha: 2026-11-20. Validación en **localhost** sobre Chrome **157.0.8103.78**
(Linux, sesión Wayland), micrófono **AT2020USB+**, Node **24.16.0**, Python
**3.13** y voz de lectura `Spanish (Latin America) espeak-ng` (`es-419`).

## Resumen de criterios

| Criterio | Estado | Resultado |
|---|---|---|
| **SC-001** paridad de ruta/campos ≥90 % con 20 preguntas y 2 voces | **CUMPLE en Chrome 157/Linux** (Firefox sin STT: no aplica) | **36/40 = 90,0 %** (voz 1: 19/20; voz 2: 17/20). |
| **SC-002** texto visible <3 s tras hablar (p95) | **CUMPLE en Chrome 157/Linux** (Firefox sin STT: no aplica) | **40 intentos, 0 fallos, p95 = 231 ms** (voz 1: 108 ms; voz 2: 231 ms). |
| **SC-003** lectura <2 s tras pulsar «Escuchar» (p95) | **CUMPLE en Chrome 157/Linux** (Firefox sin voces TTS: no medible) | **40/40, p95 = 36 ms** (mín. 30, máx. 37). |
| **SC-004** 0 archivos de audio persistidos | **CUMPLE** | Sin peticiones de audio; POST solo con texto, CSRF e IDs; `localStorage` solo con preferencias booleanas; sin IndexedDB; esquema sin audio. |
| **SC-005** sin errores nuevos de accesibilidad | **CUMPLE** | axe 0/0 (antes y después); teclado; Orca anuncia los controles. |

## Matriz de navegadores

Alcance acordado el **2026-11-20**: Chrome y Firefox de escritorio. El resto de
navegadores queda fuera por ahora. La matriz completa está en
[`docs/pruebas-040.md`](../../docs/pruebas-040.md).

| Navegador | Versión | STT | TTS | Qué se verificó |
|---|---|---|---|---|
| Chrome Linux | 157.0.8103.78 | sí | sí (`es-419`) | SC-001 36/40; SC-002 p95 231 ms; SC-003 p95 36 ms; SC-004; SC-005 |
| Firefox Linux | 159.0 | **no** | sin voces (0 en headless y headed) | FR-009; axe 0; sin peticiones de audio |
| Edge, Safari macOS/iOS y Chrome Android | — | — | — | fuera de alcance por ahora |

La fila Firefox se verificó con
[`tests/browser/chat_voz_040/verificar_firefox.cjs`](../../tests/browser/chat_voz_040/verificar_firefox.cjs)
en localhost; la salida cruda está en
[`firefox-matriz.json`](evidencia/firefox-matriz.json). Firefox no expone
`SpeechRecognition` ni voces TTS en este entorno: 0 voces tanto en headless
([`firefox-matriz.json`](evidencia/firefox-matriz.json)) como en modo gráfico
([`firefox-voces.json`](evidencia/firefox-voces.json)). Por eso SC-001 y SC-002
**no aplican** y SC-003 **no es medible** allí. La fila cubre FR-009. T022 y
T024 se cierran con este alcance.

## SC-001 y SC-002 · dictado real

Protocolo de [quickstart](quickstart.md): 20 preguntas de
`data/eval/routing_questions.json`, dictadas por dos voces (40 intentos), sin
guardar audio. Las preguntas están en
[`tests/browser/chat_voz_040/preguntas_sc001.json`](../../tests/browser/chat_voz_040/preguntas_sc001.json).
La transcripción se tomó del campo real del chat, ya normalizado por la app; la
marca de SC-002 es `speechend` → primer frame con el texto visible, con
`performance.now()`.

| Voz | Intentos | Paridad SC-001 | Fallos de marca | p95 SC-002 |
|---|---|---|---|---|
| Voz 1 | 20 | 19/20 | 0 | 108 ms |
| Voz 2 | 20 | 17/20 | 0 | 231 ms |
| **Total** | **40** | **36/40 (90,0 %)** | **0** | **231 ms** |

Fallos de paridad (4 de 40):

- `r-q27` en las dos voces: «EBITDA» se transcribió como «evita» y el router fue
  a `rag` en vez de `tool`.
- `c-02` (voz 2): «vasos» se transcribió como «pasos»; la ruta pasó a `rag`.
- `c-32` (voz 2): la transcripción se cortó en «cotiza 3000 vasos de café para»;
  faltaron capacidad y país, así que los campos no coinciden.

Los datos crudos están en
[`dictado-voz1.jsonl`](evidencia/dictado-voz1.jsonl),
[`dictado-voz2.jsonl`](evidencia/dictado-voz2.jsonl) y el análisis reproducible en
[`paridad.json`](evidencia/paridad.json). SC-001 queda **exactamente en el
umbral**: cualquier cambio del conjunto o de las voces exige repetir la medición.

## SC-003 · lectura real

40 lecturas (20 respuestas × 2), pulsando «Escuchar» en el turno real. Se mide
el clic hasta el evento `start` de la primera locución.

- Intentos: **40**, fallos: **0**.
- p95: **36 ms**; mínimo 30 ms; máximo 37 ms; umbral 2.000 ms.
- Voz: `Spanish (Latin America) espeak-ng (es-419)`; Chrome headed en localhost.
- Datos: [`sc003.json`](evidencia/sc003.json).

## SC-004 · privacidad

- Red del navegador sin peticiones `media`/audio; cuerpos POST solo con
  `question`, `csrf_token`, `client_turn_id`, `thread_id`.
- `localStorage`: `carvajal_voz_autolectura`, `carvajal_voz_aviso_dictado` y la
  identidad de visitante preexistente; nada de transcripciones ni audio.
- `indexedDB.databases()` vacío; el esquema no tiene campos de audio.
- El audio del dictado lo procesa el navegador (puede usar su servicio); la app
  no lo recibe ni lo guarda. Así se avisa antes del primer dictado.

## SC-005 · accesibilidad

- **axe** (`wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`): 0 violaciones antes (sin
  `chat-voice.js`) y 0 después; **0 nuevas**.
- **Teclado**: Enter inicia la lectura, Tab alcanza Pausar, el foco pasa a
  Reanudar al pausar y a Escuchar al detener; los cuatro controles son
  `type="button"` con nombre accesible.
- **Lector de pantalla real**: Orca 50.3 (AT-SPI2) sobre Chrome 157 anunció
  «Leer respuestas en voz alta» como *check box*, «Escuchar» como *toggle
  button not pressed*, la región viva del turno y los landmarks de la página.
  Extracto en [`evidencia/orca.txt`](evidencia/orca.txt). La autolectura sigue
  apagada por defecto, así que no duplica la voz del lector.
- El bloqueo de reproducción automática se cubrió con el arnés: si `speak` no
  dispara `onstart`, el turno no se marca como leído y «Escuchar» sigue
  disponible.

## Reproducción

```bash
# 1. Servidor con dobles de chat/SSE (sin DB ni LLM)
PYTHONPATH="$PWD" uv run python tests/browser/chat_voz_040/servidor.py

# 2. Arranque automático (36 comprobaciones y axe)
NODE_PATH=<deps> node tests/browser/chat_voz_040/validar.cjs

# 3. Dictado real con dos voces (ventana guiada; requiere micrófono)
NODE_PATH=<deps> SALIDA=/tmp/voz1.jsonl VOZ="Voz 1 · " \
  node tests/browser/chat_voz_040/medir_dictado.cjs
NODE_PATH=<deps> SALIDA=/tmp/voz2.jsonl VOZ="Voz 2 · " \
  node tests/browser/chat_voz_040/medir_dictado.cjs

# 4. Lectura real (40 intentos)
NODE_PATH=<deps> SALIDA=/tmp/sc003.json \
  node tests/browser/chat_voz_040/medir_sc003.cjs

# 5. Paridad de ruta y campos
uv run python tests/browser/chat_voz_040/analizar_paridad.py \
  specs/040-chat-por-voz/evidencia/dictado-voz1.jsonl \
  specs/040-chat-por-voz/evidencia/dictado-voz2.jsonl \
  --json specs/040-chat-por-voz/evidencia/paridad.json
```

Los scripts de medición **no** forman parte de `make check` ni de CI: necesitan
micrófono, voces y una persona. Los dobles de `validar.cjs` tampoco sustituyen
estas mediciones.

## Condiciones y límites

- SC-001–SC-003 están medidos de forma completa en **Chrome 157/Linux** con
  micrófono y dos voces. **Firefox 159/Linux** se verificó para FR-009: no
  expone STT ni voces TTS, así que SC-001 y SC-002 no aplican y SC-003 no es
  medible allí. Alcance acordado: Chrome y Firefox de escritorio; Edge, Safari y
  Android quedan fuera por ahora. La matriz está en
  [`docs/pruebas-040.md`](../../docs/pruebas-040.md).
- La voz de lectura es `espeak-ng` local; la latencia de SC-003 es real para esa
  voz, no para voces neuronales o en la nube.
- SC-001 está justo en el umbral del 90 %; los fallos se concentran en siglas
  («EBITDA») y en una palabra aislada («vasos»/«pasos»). No se descartó ningún
  intento fallido.
- No se guardó audio. El navegador puede enviar el audio a su servicio de
  reconocimiento; la aplicación no lo recibe.
- El despliegue por HTTP/IP no habilita el micrófono: HTTPS sigue siendo
  requisito para anunciar dictado en producción.

## Verificación final (2026-11-20)

Ejecutado en la rama de cierre, con Node **24.16.0**:

```text
$ make check
1643 passed, 46 deselected, 2 warnings

$ node --test tests/js/test_chat_voice.cjs
ℹ tests 68
ℹ pass 68
ℹ fail 0
```

`make check` incluye Ruff, el chequeo de tipos y la suite de Python; `Calidad`
ejecuta además la suite JS.
