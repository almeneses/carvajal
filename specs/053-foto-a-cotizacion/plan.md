# Implementation Plan: De la foto a la cotización

**Branch**: `feat/053-foto-a-cotizacion` | **Date**: 2026-11-25 | **Spec**: [spec.md](./spec.md)

## Summary

Sobre la 051: la recomendación pasa a una referencia recomendada y hasta cuatro alternativas
numeradas; la elección («dame esa», «la 2») y el «sí» a una propuesta se resuelven con
reglas antes de llegar al agente; toda cotización completa queda como **propuesta** (sin
folio ni fila) hasta que la persona confirma; la web muestra la foto (vista local `blob:` y
una miniatura `data:` de 320 px guardada con el mensaje), el estado «Mirando tu foto…», la
cuadrícula de alternativas, arrastrar y pegar y la sugerencia de la bienvenida.

## Technical Context

**Language/Version**: Python 3.13; JavaScript del navegador (sin build).

**Primary Dependencies**: las de la 051 (Pillow, Jev); sin dependencias nuevas.

**Storage**: sin migraciones. La miniatura viaja en `messages.sources` del mensaje de la
persona (`{"result_type": "photo", "thumbnail": "data:image/jpeg;base64,…"}`); la propuesta
en el resultado comercial del turno (`quote.status = "propuesta"`, sin `quote.quote`).

**Testing**: pytest (SQLite y Postgres con testcontainers), `node --test`.

## Diseño

| Pieza | Dónde | Decisión |
|---|---|---|
| Recomendada + 4 | `food/recommend.py`, `food/render.py` | `MAX_SUGGESTIONS = 1 + 4`; las tarjetas marcan `choice` (`"recomendada"` o 1-4) y `origin: "foto"` |
| Elección | `agent/followup.py` (`resolve_choice`) | Reglas: números y ordinales → alternativa; deícticos → recomendada; pregunta, cantidad o cambio → no elige |
| Confirmación | `agent/followup.py` (`is_confirmation`) | El mensaje entero debe ser asentimiento; «sí, pero…» no confirma |
| Enrutado | `ChatService.stream` → `_followup` | Antes del agente: «sí» a la propuesta del turno anterior → turno determinista que registra; elección → el agente lee la referencia resuelta (`_ToolTurn.prompt`) y el hilo guarda el texto original; fuera de rango → turno determinista |
| Propuesta | `_propose_quote(s)`, `_proposal_question` | Mismo cálculo de `cotizar`; sin folio ni fila; «¿Confirmo la cotización?» si el texto no lo pregunta; `done.proposals` para WhatsApp |
| Registro | `_confirmation` → `_issue_quotes` | Folio derivado de la propuesta (`confirmacion:<message_id>`): confirmar dos veces no emite otro; propuesta vencida → se recalcula |
| WhatsApp | `apps/mcp/bridge.py` | Bloque «🧾 Propuesta de cotización (sin folio, por confirmar)»; PDF solo para registradas |
| Foto visible | `chat-photo.js`, `chat-stream.js`, `_chat_thread.html`, CSP | `blob:` solo en `img-src` de las páginas del chat; miniatura `data:` en el historial |
| Diseño | Stitch, `chat.css`, `_productos.html` | Pantalla «Flujo Foto a Cotización» (copia en `diseno/`) |

## Constitution Check

| Principio | Cumple | Cómo |
|---|---|---|
| I. La constitución manda | Sí | §2 y §9 de `CONSTITUTION.md` y su proyección (2.11.0) en la PR |
| II. El corpus es el contrato | Sí | No se toca Gold ni el catálogo |
| III. Sin fuentes no hay respuesta | Sí | Elección y confirmación sin LLM; la confirmación usa las cifras ya calculadas por `cotizar` |
| IV. Calidad reproducible | Sí | Pruebas sin red; `make check`; evaluación del agente pendiente de permiso de tokens |
| V. Captura responsable | N/A | — |
| VI. Cambios por PR | Sí | Rama `feat/053-foto-a-cotizacion`, PR sin merge |

Privacidad: la foto original no se guarda; la miniatura (320 px, sin metadatos) solo la ve
su dueño, vive con su mensaje y el aviso lo dice.
