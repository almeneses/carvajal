# Specification Analysis Report — 044

**Alcance**: `spec.md`, `plan.md`, `tasks.md` (+ `research.md`, `data-model.md` y
`contracts/`), contra `.specify/memory/constitution.md` y `CONSTITUTION.md`.
**Fecha**: 2026-11-20. Análisis previo a la implementación.

## Hallazgos

| ID | Category | Severity | Location(s) | Summary | Recommendation / Resolución |
|---|---|---|---|---|---|
| D1 | Duplication | LOW | spec.md FR-001, FR-003; data-model.md «Transiciones» | El orden de controles se enuncia en la spec y se repite en el modelo de datos. | Se conserva: spec expresa el requisito y data-model lo hace verificable. Sin acción. |
| A1 | Ambiguity | MEDIUM | spec.md FR-008 | «callbacks/opciones» no concreta la interfaz del lector. | Resuelto en `contracts/sse-reader-contract.md`: `leerEventos(response, alEvento)` + switch en cada consumidor. |
| U1 | Underspecification | MEDIUM | tasks.md T016 | Actualizar la expectativa de la FAQ por el último bloque no indicaba el archivo. | Resuelto: T016 nombra `tests/js/test_044_chat_stream_reader.cjs`. |
| C1 | Constitution Alignment | HIGH | plan.md «Constitution Check»; CONSTITUTION.md §directiva | La directiva pide actualizar `CONSTITUTION.md` en el mismo commit; el alcance de escritura del goal lo excluye. | Justificado y delegado: la PR de documentación del goal lo consolida. Se reporta al Lead. No es CRITICAL: no cambia arquitectura, stack ni alcance, solo extrae módulos existentes. |
| E1 | Coverage | MEDIUM | spec.md FR-004 | El 404 de hilo ajeno no aparecía en la matriz de T003. | Resuelto: T003 incluye 403/404/422/429/503 y `ChatEntry.question`/`IdentityCookie` citados. |
| E2 | Coverage | LOW | spec.md SC-006 | La puerta `node --test` no enumeraba el archivo nuevo en T022. | Resuelto: T022 ejecuta `make check` y `node --test tests/js/test_chat_voice.cjs`; T017 ejecuta los archivos nuevos. |
| I1 | Inconsistency | LOW | spec.md SC-002 | «salvo el nombre del registro de errores interno» filtraba implementación en un criterio de éxito. | Resuelto: SC-002 ahora habla de contenido observable y diferencias internas de registro. |
| M1 | Coverage | LOW | spec.md FR-012/T022 | La caracterización y la puerta son tareas distintas; quedan encadenadas en Dependencies. | Sin acción: T003–T005 bloquean T022. |
| M2 | Underspecification | LOW | tasks.md T024 | No indicaba el cuerpo de la PR ni la evidencia. | Resuelto en la ejecución: cuerpo con qué/por qué/cómo probar/evidencia/reversión/riesgos. |

## Coverage Summary

| Requirement | Has Task? | Task IDs | Notes |
|---|---|---|---|
| FR-001 | ✅ | T006, T007–T010 | Fuente única en `chat_rules.py`. |
| FR-002 | ✅ | T003, T007–T009 | Rutas y métodos fijados. |
| FR-003 | ✅ | T003, T006, T010 | Orden de controles fijado y conservado. |
| FR-004 | ✅ | T003, T006 | Códigos, formatos y cabeceras. |
| FR-005 | ✅ | T003, T006 | Cookies y momento de emisión. |
| FR-006 | ✅ | T003, T006 | Mensajes sin detalles internos. |
| FR-007 | ✅ | T012, T016 | Lector común. |
| FR-008 | ✅ | T013, T014, T016 | Callbacks por consumidor. |
| FR-009 | ✅ | T018, T019 | HTMX solo en Configuración. |
| FR-010 | ✅ | T020, T022 | Captura, a11y, voz y formulario. |
| FR-011 | ✅ | T020, T022 | CSP y `/static/`. |
| FR-012 | ✅ | T003–T005, T016–T017, T022 | Caracterización y puerta. |
| FR-013 | ✅ | T021 | Sin tocar `service.py`/`agent.py`; sin deps. |
| SC-001 | ✅ | T003–T005, T011 | Sin cambios de expectativas. |
| SC-002 | ✅ | T008, T009, T011 | Paridad web/API. |
| SC-003 | ✅ | T018, T019 | HTMX solo Configuración. |
| SC-004 | ✅ | T020, T022 | CSP. |
| SC-005 | ✅ | T012–T016 | Un solo lector. |
| SC-006 | ✅ | T022, T017 | Puertas verdes. |

## Constrained-Field Coverage

| Field (`Entity.field`) | Canonical Location | Implementing Task IDs | Notes |
|---|---|---|---|
| `ChatEntry.question` (≤500, strip, vacía ⇒ 422) | data-model.md | T003, T006 | Citado en la tarea de caracterización y en el módulo. |
| `IdentityCookie` (HttpOnly, SameSite=Lax, Path=/, Secure en HTTPS, 180 días) | data-model.md | T003, T006 | Citado en la matriz y en `set_identity_cookie`. |
| `SseBlock.datos` (varias `data:` unidas con `\n`, JSON inválido ⇒ descarte) | data-model.md | T012, T016 | Citado en el lector y sus pruebas. |
| `Visitor.new_owner` (decide emisión de cookie) | data-model.md | T006 | Citado en `resolve_visitor`. |

## Constitution Alignment Issues

- Sin violaciones de principios de producto ni de stack. Única desviación de proceso:
  la actualización de `CONSTITUTION.md` (§directiva) queda fuera de esta PR por el
  alcance de escritura del goal y se delega en la PR de documentación; riesgo
  registrado en `plan.md`.

## Metrics

- Total Requirements: 13 FR + 6 SC = 19
- Total Tasks: 24
- Coverage: 100 %
- Ambiguity Count: 1 (resuelta)
- Duplication Count: 1 (aceptada)
- Critical Issues Count: 0

## Next Actions

- Sin hallazgos CRITICAL: se puede implementar.
- CRITICAL resueltos antes de implementar: ninguno pendiente.
- Al cerrar la implementación, este archivo se actualiza con el resultado de las
  puertas y la evidencia (`analysis.md` es también registro de verificación).
