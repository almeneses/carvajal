# Implementation Plan: Reglas compartidas del chat, lector SSE único y HTMX solo en Configuración

**Branch**: `refactor/044-chat-sse-interfaz` | **Date**: 2026-11-20 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/044-chat-sse-interfaz/spec.md`

## Summary

Refactorización sin cambio de contrato observable. Se extrae a un módulo de vistas
una única fuente de reglas del chat (constantes de validación, resolución del
visitante, cookie de identidad, cuota inyectada, CSRF, respuesta SSE de error y
traducción de eventos). El formulario, el stream web y el stream de API lo reutilizan
conservando su orden y sus formatos; la FAQ reutiliza identidad, cuota y CSRF. Se
extrae el lector SSE duplicado de `chat-stream.js` y `faq.js` a un archivo estático
común (`sse.js`) con callbacks para las diferencias legítimas. `htmx.min.js` pasa de
`base.html` a `configuracion.html`. Primero se escriben pruebas de caracterización
(pytest y `node:test`) que fijan el comportamiento actual; después se refactoriza y
se comprueba que esas pruebas siguen pasando.

## Technical Context

**Language/Version**: Python 3.13; JavaScript sin dependencias npm (Node 24.16.0 solo
para las pruebas) y navegadores de escritorio soportados (Chrome/Firefox actuales,
mismo alcance que la spec 040).

**Primary Dependencies**: FastAPI + Jinja2 + SQLAlchemy 2.0 + structlog; `node:test`
para JavaScript. Sin dependencias nuevas.

**Storage**: PostgreSQL (chat, cuota, feedback), Qdrant (recuperación) y cookies
firmadas; esta PR no toca el esquema ni los repositorios.

**Testing**: `pytest` con TestClient y dobles inyectados (`app.state.chat`,
`app.state.chat_identity`, `app.state.quota`, `app.state.faq_*`); `node --test` con
dobles de DOM y streams sintéticos, sin red ni micrófono.

**Target Platform**: Servidor Linux (contenedor de la app única en `:8000`) y
navegador de escritorio.

**Project Type**: Aplicación web de un solo runtime (FastAPI + Jinja2) con API SSE.

**Performance Goals**: sin cambio. La refactorización no añade E/S ni llamadas; el
lector SSE común mantiene el procesamiento por bloques y no acumula más que el
bloque en curso.

**Constraints**: conservar CSP sin `unsafe-inline`, scripts servidos desde `/static/`,
sin frameworks nuevos, sin cambios en `apps/api/chat/service.py` ni
`apps/api/chat/agent.py`, y sin tocar rutas ni contratos.

**Scale/Scope**: 3 entradas del chat + FAQ, 2 pantallas con lector SSE, 1 pantalla con
HTMX, ~5 archivos de producción y 3 archivos de pruebas nuevos.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principio / regla | Estado | Evidencia |
|---|---|---|
| La app única FastAPI/Jinja2 + PostgreSQL/Qdrant se conserva | ✅ | No se añaden runtimes ni dependencias; solo se mueve código existente. |
| Seguridad: CSRF antes de cuota, CSP sin `unsafe-inline`, cookies `HttpOnly`/`SameSite=Lax` | ✅ | El orden se fija con pruebas de caracterización; `sse.js` es un archivo externo bajo `script-src 'self'`. |
| Accesibilidad y chat por voz (spec 040) se conservan | ✅ | No se tocan `chat-voice.js`, `chat.html` ni los controles; se verifica con las pruebas existentes. |
| `data/gold/` y los contratos de corpus no cambian | ✅ | Fuera del alcance de esta PR. |
| `make check` y CI en verde | ✅ | Se ejecuta `env -u OLLAMA_MODEL make check` y `node --test`. |
| Presupuesto de cambios (aviso >3.000 líneas) | ✅ | El cambio es una extracción, no una reescritura; se mide al final. |
| Actualizar `CONSTITUTION.md` si cambia la arquitectura | ⚠️ justificado | La directiva exige actualizarla en el mismo commit; el goal limita la escritura a `apps/api/views/`, `apps/api/static/`, `apps/api/templates/`, `tests/` y `specs/044`. La PR de documentación del goal consolida la nota; se registra como riesgo. |

No hay violaciones que exijan Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/044-chat-sse-interfaz/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-sse-contract.md
│   └── sse-reader-contract.md
├── checklists/requirements.md
├── tasks.md
└── analysis.md
```

### Source Code (repository root)

```text
apps/api/
├── sse.py                         # ya existe: codificación SSE del servidor
├── views/
│   ├── chat_rules.py              # NUEVO: reglas compartidas y traducción SSE
│   ├── chat.py                    # formulario POST /chat, reexporta constantes
│   ├── stream.py                  # POST /chat/stream usa chat_rules
│   ├── api.py                     # POST /api/chat/stream usa chat_rules
│   └── faq.py                     # identidad/cuota/CSRF desde chat_rules
├── static/
│   ├── sse.js                     # NUEVO: lector SSE común (window.CarvajalSSE)
│   ├── chat-stream.js             # consume CarvajalSSE; conserva su lógica
│   ├── faq.js                     # consume CarvajalSSE; conserva sus lotes
│   └── htmx.min.js                # sigue igual, ahora solo lo pide Configuración
└── templates/
    ├── base.html                  # deja de cargar htmx
    ├── configuracion.html         # carga htmx.min.js antes de captura.js
    ├── chat.html                  # carga sse.js antes de chat-stream.js
    └── faq.html                   # carga sse.js antes de faq.js

tests/
├── test_044_chat_contract.py      # caracterización de las tres entradas
├── js/test_044_chat_stream_reader.cjs   # caracterización del lector actual
├── js/test_044_sse_shared.cjs           # pruebas del lector compartido
├── test_api.py                    # se ajusta la expectativa de htmx
└── js/test_chat_voice.cjs         # sin cambios; debe seguir en verde
```

**Structure Decision**: se respeta la estructura existente de `apps/api`. El módulo
compartido vive en `apps/api/views/` porque es donde ya viven las reglas y porque el
alcance de escritura acordado no incluye `apps/api/chat/`. El lector SSE común vive en
`apps/api/static/` y se sirve por el montaje `/static` existente.

## Phase 0: Outline & Research

Ver [research.md](research.md). Decisiones: módulo `chat_rules.py` sin capa nueva;
lector `sse.js` como *global* mínimo con opción de bloque final; HTMX condicionado por
plantilla, no por JavaScript; caracterización antes de tocar producción.

## Phase 1: Design & Contracts

Ver [data-model.md](data-model.md), [contracts/http-sse-contract.md](contracts/http-sse-contract.md),
[contracts/sse-reader-contract.md](contracts/sse-reader-contract.md) y
[quickstart.md](quickstart.md).

## Complexity Tracking

Sin violaciones de la constitución. La única desviación (no actualizar
`CONSTITUTION.md`) está justificada por el límite de escritura del goal y se reporta
al Lead.
