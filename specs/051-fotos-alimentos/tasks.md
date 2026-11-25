# Tasks: Fotos de alimentos → empaque sugerido (spec 051)

**Input**: `plan.md`, `research.md`, `data-model.md`, `contracts/interfaces.md`

## Phase 1: Setup

- [x] T001 Declarar Pillow como dependencia directa con `uv add "pillow>=13,<14"`
- [x] T002 [P] Añadir `FOOD_PHOTO_*` a `packages/core/src/carvajal/infrastructure/settings.py` y a `.env.example`

## Phase 2: Foundational

- [x] T003 `carvajal.food.photo.prepare_photo` (R3) con `PhotoRejected` en `packages/core/src/carvajal/food/photo.py`
- [x] T004 [P] `FoodProfile`, preguntas de Jev y `parse_food_answers` en `packages/core/src/carvajal/food/profile.py`
- [x] T005 `JevClient.call(..., assets, images, timeout_s)`, `JudgeKind` `"food"` y `JevFoodClassifier` en `adapters/judge/jev.py` y `application/ports.py`
- [x] T006 `build_food_classifier` y `food_photo_health` en `adapters/judge/__init__.py`
- [x] T007 [P] Pruebas de preparación en `tests/test_food_photo.py`
- [x] T008 [P] Pruebas del cliente con imagen (cabecera, assets, plazo, tope, parseo inválido) en `tests/test_jev_food.py`

## Phase 3: US1 + US2 + US3 (P1) — recomendación en la web

- [x] T009 Tabla tipo → requisitos y `recommend()` (R5) en `packages/core/src/carvajal/food/recommend.py`
- [x] T010 Texto determinista y tarjetas (`render_recommendation`, `recommendation_cards`) en `packages/core/src/carvajal/food/render.py`
- [x] T011 Pruebas de tabla, selección, preguntas, «no es comida», «sin empaque» y texto en `tests/test_food_recommend.py`
- [x] T012 `ChatService.photo_stream` (R6, R7) en `apps/api/chat/service.py`
- [x] T013 `POST /chat/foto` en `apps/api/views/foto.py` y registro del router y `/health` en `apps/api/main.py`
- [x] T014 Pruebas del endpoint (CSRF, límites, cupo solo al clasificar, idempotencia, turno guardado con la referencia) en `tests/test_api_foto.py`

## Phase 4: US5 (P2) — interfaz, aviso y opción oculta

- [x] T015 Botón, ficha y aviso en `apps/api/templates/chat.html` según disponibilidad (`foto_disponible`)
- [x] T016 `apps/api/static/chat-photo.js` y envío multipart en `apps/api/static/chat-stream.js`; estilos en `chat.css`
- [x] T017 [P] Pruebas de plantilla (con y sin clasificador) en `tests/test_api_foto.py` y JS en `tests/js/test_chat_photo.cjs`

## Phase 5: US4 (P2) — WhatsApp

- [x] T018 Parámetro `foto` en `apps/mcp/server.py`; lectura segura del adjunto y turno de foto en `apps/mcp/bridge.py`
- [x] T019 Pruebas del puente (nombres inválidos, enlaces simbólicos, sin carpeta, fallo sin cupo, formato) en `tests/test_mcp_foto.py`
- [x] T020 Montaje de solo lectura en `compose.prod.yml` y guía en `docs/whatsapp-openclaw.md` (instrucción del agente, `tools.media.image.enabled=false`)

## Phase 6: Polish

- [x] T021 Conjunto de evaluación `specs/051-fotos-alimentos/evaluacion/fotos.json` (≥ 20 fotos con licencia) y `scripts/evaluar_fotos.py`
- [x] T022 `CONSTITUTION.md` (§2, §3, §4, §9), `.specify/memory/constitution.md`, `README.md` y `specs/README.md`
- [x] T023 `make check` y apertura de la PR (etiquetas y assignee, sin merge)
