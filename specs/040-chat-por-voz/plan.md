# Plan de implementación: chat por voz

**Rama**: `040-chat-por-voz` | **Fecha**: 2026-11-19 | **Spec**: [spec.md](spec.md)

## Resumen

Añadir dictado editable y lectura en español al chat Jinja2/HTMX con la Web Speech API.
El MVP entrega **US1 + US2**: dictar sin autoenvío y escuchar respuestas completas bajo
demanda o por preferencia. **US3** (manos libres) va después, con activación explícita y
sin alterar el flujo de seguridad del chat.

## Contexto técnico

| Aspecto | Decisión |
|---|---|
| Cliente | JavaScript sin framework en `apps/api/static/`; detección independiente de reconocimiento y síntesis |
| Servidor | FastAPI/Jinja2, formulario y SSE actuales (`POST /chat`, `POST /chat/stream`); sin contrato HTTP nuevo |
| Micrófono | Contexto seguro: `localhost` para pruebas, HTTPS en despliegue y `microphone=(self)` solo para el documento del chat |
| Almacenamiento | En `localStorage`, solo preferencias booleanas por navegador/origen; el historial guarda solo texto enviado, como hoy |
| Pruebas | `pytest` para plantillas y seguridad, `node --test` con dobles de voz en el check `Calidad` y pruebas reales de navegador para compatibilidad, accesibilidad y latencia |
| Metas | Paridad de ruta y campos ≥90 %; dictado p95 <3 s, lectura p95 <2 s; ningún audio persistido; sin errores nuevos de accesibilidad |
| Alcance | Español de Colombia y México, sin SDK de voz, cambios al enrutador, migraciones, límite nuevo por IP en el chat ni dependencia de la 039 |

## Constitution Check

| Principio | Evaluación |
|---|---|
| I. Constitución | §2 admite dictado y lectura desde el navegador sin guardar audio. El permiso se limita al documento de chat y se documentará al implementarlo. |
| II. Corpus | No se modifica Gold, PostgreSQL, Qdrant ni el catálogo. |
| III. Fuentes | Se leen títulos y datos renderizados; no se recalculan precios ni se generan citas extra. |
| IV. Calidad | Se conserva `uv`/`make check`; `Calidad` ejecutará también los dobles de voz con la versión de Node fijada en T002. Los dobles no requieren micrófono ni red; las mediciones reales tienen protocolo separado. |
| V. Captura responsable | No se añade captura web, se conservan CSRF y cuota; el visitante conoce el posible procesamiento externo del navegador antes de usar voz. |
| VI. Pull Requests | Plan y tareas a `dev`; implementación por incrementos revisables. |

**Revisión del diseño**: pasa. El permiso de micrófono es la única modificación de
cabeceras prevista; CSP, `frame-ancestors`, CSRF y límites de chat se mantienen. El VPS
documentado expone HTTP por IP: **HTTPS es requisito para liberar dictado allí**, no
para planificar ni validar en `localhost`.

## Estructura

- `apps/api/static/chat-voice.js`: reconocimiento, síntesis y coordinación de estados.
- `apps/api/static/chat-stream.js`: notificación interna tras completar el turno SSE
  y liberar el formulario, sin cambiar el protocolo.
- `apps/api/templates/chat.html`, `_chat_thread.html`, `_chat_stream.html` y
  `_productos.html`: controles, estados accesibles y marcadores de datos visibles;
  estilos en `apps/api/static/chat.css`.
- `apps/api/security.py`: política de micrófono acotada al documento del chat.
- `tests/test_security.py`, `tests/test_api_chat.py`, `tests/test_productos.py` y
  `tests/js/test_chat_voice.cjs`: pruebas de servidor, render y cliente.
- `.github/workflows/ci.yml`: integrar la suite JS en `Calidad` al introducirla.

La lectura toma las tarjetas del DOM. Los marcadores se pactan con la 039 si cambia
`_productos.html`; la voz no depende de que esa spec se fusione ni de su esquema de datos.

## Diseño y entrega

1. **US1 — dictado**: detección de soporte, aviso previo, transcripción revisable y envío
   por el formulario normal. Normalización conservadora de cantidades/capacidades en la
   transcripción, no en el enrutador. Se conservan la cuota por visitante, el límite de
   longitud y los controles de seguridad en ambos POST.
2. **US2 — lectura**: respuestas del historial y del stream, con pausa, reanudación y
   parada; lectura automática opcional solo tras `done` del stream visible. El fallback
   sin streaming y el historial conservan lectura manual. Tarjetas y citas leídas desde
   sus datos renderizados, no desde URLs o SKU.
3. **US3 — manos libres (posterior al MVP)**: reescucha tras síntesis normal, envío
   solo de la transcripción del ciclo activo con borrador vacío, salida por escritura
   manual (se conserva el borrador), silencio/comando, fallo o cuota agotada.

Ver [investigación](research.md), [estados y datos](data-model.md),
[contrato de interfaz](contracts/chat-voz.md) y [validación](quickstart.md).
