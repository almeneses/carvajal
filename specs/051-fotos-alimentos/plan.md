# Implementation Plan: Fotos de alimentos → empaque sugerido

**Branch**: `feat/051-fotos-alimentos` | **Date**: 2026-11-24 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/051-fotos-alimentos/spec.md`

## Summary

La persona adjunta una foto en el chat web o en WhatsApp. El servidor la prepara (endereza,
quita metadatos, escala), Jev la clasifica en una sola petición (comida, tipo, consistencia,
temperatura, porción) y una tabla determinista elige hasta tres referencias del catálogo del
país con `check_material`. La respuesta se redacta sin LLM y se guarda como un turno normal
del hilo, con las tarjetas de producto, para que la conversación siga («cotízame 500 del
primero»). Con confianza baja, pregunta; sin comida o sin empaque apto, lo dice.

## Technical Context

**Language/Version**: Python 3.13; JavaScript del navegador (sin build).

**Primary Dependencies**: FastAPI, httpx (Jev), Pillow (declarada directa; ya estaba en el
lock), MCP SDK; catálogo y reglas de `carvajal.agent.commercial`.

**Storage**: ninguno nuevo; el turno usa `SqlChatRepository.append_turn`.

**Testing**: pytest con doble de Jev (`httpx.MockTransport`) y clasificador falso; pruebas
JS con `node --test` (CI).

**Target Platform**: VPS Linux (Docker Compose), OpenClaw en el mismo host.

**Project Type**: web-service + servidor MCP.

**Performance Goals**: respuesta < 5 s p95 (Jev ~0,2-0,6 s medido; preparación < 0,5 s).

**Constraints**: sin guardar la foto; 10 MB por subida; 500 llamadas diarias al clasificador;
principio III (sin datos fuera del catálogo).

**Scale/Scope**: 11 tipos de alimento, 200 referencias, 2 países.

## Constitution Check

| Principio | Cumple | Cómo |
|---|---|---|
| I. La constitución manda | Sí | §2, §3, §4 y §9 de `CONSTITUTION.md` y su proyección se actualizan en la PR |
| II. El corpus es el contrato | Sí | No se toca Gold ni el catálogo; se leen `referencias.csv` y `materiales.csv` |
| III. Sin fuentes no hay respuesta | Sí | Respuesta determinista; cada sugerencia enlaza su ficha; la guía de materiales justifica la aptitud; ningún LLM redacta |
| IV. Calidad reproducible | Sí | `uv add pillow`; pruebas sin red; `make check` |
| V. Captura responsable | N/A | No hay captura web |
| VI. Cambios por PR | Sí | Rama `feat/051-fotos-alimentos`, PR sin merge |

Privacidad (enmienda D, 046): a TypeSafe solo va la imagen preparada y preguntas fijas; nunca
`owner_key`, número, memoria ni texto. Logs y libro sin imagen ni texto.

## Project Structure

### Documentation (this feature)

```text
specs/051-fotos-alimentos/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/interfaces.md
├── checklists/requirements.md
├── evaluacion/fotos.json      # conjunto de evaluación (URLs con licencia y esperado)
└── tasks.md
```

### Source Code (repository root)

```text
packages/core/src/carvajal/
├── food/
│   ├── __init__.py
│   ├── photo.py          # prepare_photo: Pillow, sin metadatos
│   ├── profile.py        # FoodProfile, preguntas de Jev y parseo
│   ├── recommend.py      # tabla tipo → requisitos y selección en el catálogo
│   └── render.py         # texto determinista y tarjetas
├── adapters/judge/
│   ├── jev.py            # call(..., assets, images, timeout_s); FoodClassifier
│   └── __init__.py       # build_food_classifier, food_photo_health
├── application/ports.py  # JudgeKind "food", puerto FoodClassifier
└── infrastructure/settings.py  # FOOD_PHOTO_*

apps/api/
├── views/foto.py         # POST /chat/foto
├── chat/service.py       # ChatService.photo_stream
├── main.py               # router, /health food_photo
├── templates/chat.html   # botón, ficha y aviso
└── static/chat-photo.js  # selección y aviso; chat-stream.js envía la foto

apps/mcp/
├── server.py             # parámetro foto
└── bridge.py             # lectura segura del adjunto y turno de foto

tests/
├── test_food_photo.py        # preparación
├── test_food_recommend.py    # tabla, selección y texto
├── test_jev_food.py          # cliente Jev con imagen (MockTransport)
├── test_api_foto.py          # endpoint, cupo, idempotencia, plantilla
├── test_mcp_foto.py          # puente
└── js/test_chat_photo.cjs    # selección y envío
```

**Structure Decision**: el dominio (preparación, perfil, recomendación, texto) vive en
`carvajal.food`, sin FastAPI; el adaptador de Jev se extiende; la web y el MCP solo orquestan.

## Complexity Tracking

Sin violaciones.
