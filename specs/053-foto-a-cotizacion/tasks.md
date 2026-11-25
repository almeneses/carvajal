# Tasks: De la foto a la cotización (spec 053)

## Phase 1: Diseño

- [x] T001 Pantalla «Flujo Foto a Cotización» en Stitch (proyecto «Asistente Carvajal», sistema «Asistente Carvajal Senior»); copia en `diseno/`

## Phase 2: Núcleo

- [x] T002 Recomendada + hasta 4 alternativas y texto «Te recomiendo… / Alternativas» en `food/recommend.py` y `food/render.py`
- [x] T003 [P] `thumbnail_data_url` en `food/photo.py`
- [x] T004 [P] `resolve_choice` e `is_confirmation` en `agent/followup.py`
- [x] T005 Fuentes del mensaje de la persona en `SqlChatRepository.append_turn`

## Phase 3: US1 + US2 (P1) — elegir y confirmar

- [x] T006 `_followup`, `_confirmation`, `_photo_choice` y `_fixed_turn` en `ChatService`; `prompt` del agente distinto de la pregunta guardada
- [x] T007 Propuestas en lugar de folios en los modos `tools`, respaldo y `routes`; `done.proposals`
- [x] T008 Reglas del prompt del agente (propuesta y elección)
- [x] T009 WhatsApp: bloque de propuesta y PDF solo para registradas
- [x] T010 Pruebas: `tests/test_followup.py` (SC-001), `tests/test_foto_cotizacion.py`, pruebas de folios adaptadas a «propuesta → sí»

## Phase 4: US3 + US4 + US5 — web

- [x] T011 Cuadrícula 2×2 con «Elegir» y propuesta con «Confirmar cotización» en `_productos.html`
- [x] T012 Foto en la burbuja, miniatura del historial, «Mirando tu foto…», arrastrar y pegar, sugerencia de bienvenida (`chat.html`, `_chat_thread.html`, `chat-photo.js`, `chat-stream.js`, `chat.css`)
- [x] T013 `blob:` en `img-src` de las páginas del chat (`security.py`)
- [x] T014 Pruebas web (`tests/test_api_foto.py`) y JS (`tests/js/test_chat_photo.cjs`)

## Phase 5: Cierre

- [x] T015 `CONSTITUTION.md`, proyección, `README.md`, `specs/README.md`, `docs/whatsapp-openclaw.md` y `openclaw/workspace/ventas/AGENTS.md`
- [x] T016 `make check`, pruebas de integración y JS
- [x] T017 Evaluación del agente contra la línea base ([evidencia](evidencia.md))
- [x] T018 PR con etiquetas y assignee, sin merge
