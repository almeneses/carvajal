# Validación de navegador del chat por voz (040)

Arnés local, **fuera de `make check` y de CI**, para ejecutar el chat real en
Chromium con dobles de `SpeechRecognition` y `speechSynthesis`. Comprueba
avisos, dictado sin autoenvío, lectura de tarjetas y citas, autolectura tras
`done`, manos libres, fallo del stream, privacidad y axe.

No sustituye la medición de SC-001–SC-003 con voces y micrófono reales.

## Requisitos

- Chromium/Chrome instalado. Ruta en `CHROME_PATH` (por defecto, el navegador
  de Playwright ya descargado en esta máquina).
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
```

Variables: `CHAT_URL` (por defecto `http://127.0.0.1:8123/chat`),
`CHROME_PATH`, `AXE_PATH`.

La salida es un JSON con `pasos`, `errores`, `red` y `axe`; el código de salida
es distinto de cero si algún paso falla. Registrar el resultado en
[`specs/040-chat-por-voz/evidencia.md`](../../../specs/040-chat-por-voz/evidencia.md).

## Límites

- `speechSynthesis` y `SpeechRecognition` son dobles: miden el comportamiento
  del cliente, no la latencia ni la calidad del servicio de voz del navegador.
- axe se inyecta con `bypassCSP` **solo en el contexto de auditoría**; el resto
  del recorrido usa la CSP real.
- No se prueba un lector de pantalla real ni el diálogo de permiso del sistema.
