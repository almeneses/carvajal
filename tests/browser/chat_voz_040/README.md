# Validación de navegador del chat por voz (040)

Arnés local, **fuera de `make check` y de CI**, para ejecutar el chat real en
Chromium con dobles de `SpeechRecognition` y `speechSynthesis`. Comprueba
avisos, dictado sin autoenvío, que el turno dictado suene solo y el escrito no (050),
lectura del resumen hablado con la fuente en una frase (048), autolectura tras `done`,
manos libres, fallo del stream, privacidad, fallback sin streaming y axe.

No sustituye la medición de SC-001–SC-003 con voces y micrófono reales.

## Requisitos

- Chromium/Chrome instalado. Ruta en `CHROME_PATH` (por defecto, el navegador que resuelve
  `chromium.executablePath()` de Playwright).
- `playwright-core` y `axe-core` en un directorio de dependencias temporal
  (no se añaden al proyecto):

  ```bash
  mkdir -p /tmp/cv040 && cd /tmp/cv040
  npm init -y && npm install playwright-core axe-core
  ```

## Ejecución

```bash
# Terminal 1: servidor con dobles de chat y SSE en :8123
PYTHONPATH="$PWD" uv run python tests/browser/chat_voz_040/servidor.py

# Terminal 2: recorrido completo
NODE_PATH=/tmp/cv040/node_modules \
  node tests/browser/chat_voz_040/validar.cjs

# Streaming, scroll y compositor en escritorio y viewport móvil (mismo servidor)
NODE_PATH=/tmp/cv040/node_modules node tests/browser/chat_stream.cjs
```

Variables: `CHAT_URL` (por defecto `http://127.0.0.1:8123/chat`),
`CHROME_PATH`, `AXE_PATH`.

La salida es un JSON con `pasos`, `errores`, `red` y `axe`; el código de salida
es distinto de cero si algún paso falla. Registrar el resultado en
[`specs/040-chat-por-voz/evidencia.md`](../../../specs/040-chat-por-voz/evidencia.md).

## Medición real (SC-001–SC-003)

Con micrófono y voces instaladas:

```bash
# Dictado: 20 preguntas por voz (dos ventanas, una por persona)
NODE_PATH=/tmp/cv040/node_modules SALIDA=/tmp/voz1.jsonl VOZ="Voz 1 · " \
  node tests/browser/chat_voz_040/medir_dictado.cjs
NODE_PATH=/tmp/cv040/node_modules SALIDA=/tmp/voz2.jsonl VOZ="Voz 2 · " \
  node tests/browser/chat_voz_040/medir_dictado.cjs

# Lectura: 40 intentos
NODE_PATH=/tmp/cv040/node_modules SALIDA=/tmp/sc003.json \
  node tests/browser/chat_voz_040/medir_sc003.cjs

# Paridad de ruta/campos y p95
uv run python tests/browser/chat_voz_040/analizar_paridad.py /tmp/voz1.jsonl /tmp/voz2.jsonl
```

Requiere Chrome con reconocimiento en español y `speech-dispatcher` con voz
`es-*`. No se ejecuta en CI.

### Fila Firefox (FR-009)

Firefox no implementa `SpeechRecognition`. Con el servidor del arnés levantado:

```bash
NODE_PATH=/tmp/cv040/node_modules AXE_PATH=/tmp/cv040/node_modules/axe-core/axe.min.js \
  node tests/browser/chat_voz_040/verificar_firefox.cjs
```

Comprueba que el dictado y manos libres no se ofrecen, que la nota explica la
limitación, que el chat escrito sigue operativo, que axe no reporta violaciones y
que no hay peticiones de audio. `AXE_PATH` es obligatorio: sin él el script sale
con código 2 en lugar de contar la auditoría como fallo. La salida se guarda en
[`specs/040-chat-por-voz/evidencia/firefox-matriz.json`](../../../specs/040-chat-por-voz/evidencia/firefox-matriz.json).
No mide SC-001 ni SC-002 (sin STT no aplican) ni sustituye SC-003 con voz real.

Para comprobar si Firefox de escritorio expone voces del sistema (condición para
medir SC-003), ejecuta en modo gráfico y guarda la salida:

```bash
NODE_PATH=/tmp/cv040/node_modules \
  node tests/browser/chat_voz_040/medir_voces_firefox.cjs
```

El resultado de este entorno está en
[`evidencia/firefox-voces.json`](../../../specs/040-chat-por-voz/evidencia/firefox-voces.json):
`sintesis: true`, `voces: 0`, `sc003Medible: false`.

## Límites

- `speechSynthesis` y `SpeechRecognition` son dobles: miden el comportamiento
  del cliente, no la latencia ni la calidad del servicio de voz del navegador.
- axe se inyecta con `bypassCSP` **solo en el contexto de auditoría**; el resto
  del recorrido usa la CSP real.
- No se prueba un lector de pantalla real ni el diálogo de permiso del sistema.
