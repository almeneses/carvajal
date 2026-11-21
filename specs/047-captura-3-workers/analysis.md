# Análisis de consistencia: captura web duradera para tres workers (047)

**Fecha**: 2026-11-20
**Alcance analizado**: [spec.md](spec.md), [plan.md](plan.md), [tasks.md](tasks.md) y
[checklists/requirements.md](checklists/requirements.md).
**Tipo**: análisis no destructivo; no modifica los artefactos.

## 1. Resumen

La spec, el plan y las tareas son coherentes entre sí: cada requisito funcional tiene una
decisión de diseño en el plan y al menos una tarea de implementación o verificación. No se
detectaron requisitos huérfanos ni tareas sin requisito. Las contradicciones potenciales se
resolvieron con las decisiones del owner (sin reanudación por URL, sin activar los tres
workers en esta PR, sin infraestructura nueva).

## 2. Cobertura requisito → diseño → tarea → prueba

| Requisito | Diseño (plan) | Tarea | Verificación |
|---|---|---|---|
| FR-001 estado persistido | §1 esquema, `capture_jobs` | T004, T007 | T019, T020 |
| FR-002 progreso persistido y reproducible | §1 `capture_events`, §3 | T004, T007, T009 | T019 |
| FR-003 contrato de estado | §7 | T010 | T002, T019 |
| FR-004 contrato SSE | §3, §7 | T009 | T002, T019 |
| FR-005 contrato del POST | §7 | T010, T011 | T002, T018 |
| FR-006 permisos por `job_id` | §3, §7 | T009, T010 | T002, T019 |
| FR-007 captura única global | §2 | T011, T012 | T018 |
| FR-008 cupo por IP compartido | §2 | T011 | T018 |
| FR-009 detección por latido | §4 | T008, T013 | T020 |
| FR-010 marcar interrumpido y liberar | §4 | T013 | T020 |
| FR-011 recuperación desde punto seguro sin reanudar | §4 | T014 | T020 |
| FR-012 sin duplicados ni escrituras inconsistentes | §2, §4 | T008, T014 | T020 |
| FR-013 retención y poda | §3 | T007 | T001, T019 |
| FR-014 `/live` sin base | §5 | T016, T017 | T021 |
| FR-015 contrato de `/health` | §5, §7 | T017 | T021, suite existente |
| FR-016 recursos y cierre | §5 | T016, T017 | T021 |
| FR-017 migración reversible | §1 | T004 | T006, T022 |
| FR-018 sin tocar compose/CMD | Resumen y entrega | — (restricción) | Revisión del diff y T024 del Lead |
| FR-019 comportamiento actual conservado | §2, §3, §6 | T008–T010 | T001, T002, suite existente |
| FR-020 documentación honesta | spec, plan, §4 | — (artefactos) | Revisión del PR |

## 3. Criterios de éxito

| Criterio | Evidencia prevista |
|---|---|
| SC-001 | Prueba de integración de dos instancias: estado y stream desde la instancia no dueña. |
| SC-002 | Prueba de integración de arranques simultáneos: exactamente un 200, resto 409. |
| SC-003 | Prueba de integración de cupo compartido y de IP independiente; `Retry-After` positivo. |
| SC-004 | Prueba con lease caducado: job interrumpido y nueva captura aceptada. |
| SC-005 | Prueba de recuperación + recaptura: un solo Markdown por fuente y sync idempotente. |
| SC-006 | `make check` completo y pruebas de `/live`/`/health`; sin cambios de contrato. |
| SC-007 | `alembic upgrade head` y `alembic downgrade -1` sobre base de prueba. |

## 4. Hallazgos y decisiones de coherencia

- **H-001 (resuelto)**: el registro actual expone `job.error` interno en el JSON de estado.
  La spec conserva el contrato y el plan mantiene el mismo campo; no se «mejora» ese detalle
  en esta PR para no romper consumidores ni mensajes actuales. Se anota como riesgo menor.
- **H-002 (resuelto)**: la reconexión en memoria de hoy entrega solo el resultado final; con
  eventos persistidos se reproducen también los intermedios. El plan evita duplicados con
  `Last-Event-ID`, que es aditivo al contrato SSE y no requiere tocar el JS existente.
- **H-003 (deliberado)**: no se edita `CONSTITUTION.md` ni `specs/README.md` porque no
  pertenecen al ámbito de archivos de esta tarea; el plan lo deja explícito y el PR lo
  reporta para la tarea del Lead.
- **H-004 (ambiguo por diseño)**: la medición exacta de conexiones por worker se acota por
  configuración del pool y se documenta; no se mide en un despliegue real en esta PR porque
  los tres workers se activan en `task-8`.
- **H-005 (supuesto verificado en código)**: `write_gold_file` usa temporal + `replace` y
  `sync` compara huellas; ambos ya se apoyan en la idempotencia que exige FR-012.

## 5. Conclusión

Los artefactos cumplen las comprobaciones de calidad y están listos para implementar. No
quedan `[NEEDS CLARIFICATION]`. La verificación definitiva de SC-001…SC-007 depende de las
pruebas de integración y de la puerta de calidad que lista T022.

## 6. Resultado de la implementación y evidencias

Estado a 2026-11-20: T001–T023 completadas; T024 (activación de tres workers) queda
para `task-8` del Lead.

| Evidencia | Comando / prueba | Resultado |
|---|---|---|
| Caracterización del contrato | `pytest tests/test_capture_jobs.py tests/test_api_capture.py` | 24 pruebas en verde sobre el comportamiento previo |
| Calidad completa | `env -u OLLAMA_MODEL make check` | 1651 passed, 58 deselected; Ruff y ty en verde |
| Integración con PostgreSQL real | `env -u OLLAMA_MODEL make test-integration` | 51 passed (12 de `test_capture_durable.py`) |
| Migración arriba/abajo | `alembic upgrade head` + `alembic downgrade -1` + `upgrade head` | las 4 tablas de captura aparecen, desaparecen y vuelven; la fila de control queda en `1` |
| Simulación de navegador | `node --test tests/js/test_chat_voice.cjs` | 68 pass, 0 fail |
| Arranque simultáneo | `test_dos_solicitudes_simultaneas_solo_inician_una_captura` | un 200 y un 409; un solo job en la base |
| Cupo compartido | `test_el_cupo_por_ip_es_compartido_y_no_guarda_la_ip` | 429 con `Retry-After` en la segunda instancia; la IP no aparece, solo resúmenes de 64 hex |
| Progreso y reconexión | `test_el_progreso_se_sirve_desde_otra_instancia_y_respeta_last_event_id` | eventos 1–3 en orden desde la otra instancia; `after=1` devuelve 2–3; el final se reemite |
| Caída y recuperación | `test_un_job_interrumpido_se_detecta_y_recupera_sin_duplicar` | antes 0 documentos/0 fragmentos; tras recuperar 1/≥1; repetir no cambia y Gold queda en un solo Markdown |
| Transición única | `test_la_transicion_a_interrumpido_ocurre_una_sola_vez` | dos instancias recuperando a la vez: una sola transición y una sola sincronización |
| `/live` y `/health` | `test_live_no_toca_la_base_y_health_conserva_el_contrato` | `/live` responde con el registro de captura roto; `/health` conserva claves y conteos |
| Conexiones | `test_el_stream_no_retiene_conexiones` | ambos pools con `checkedout() == 0` tras estado y stream |

Sin hallazgos bloqueantes nuevos. La reducción de tiempos por entorno
(`CAPTURE_HEARTBEAT_SECONDS`, `CAPTURE_STALE_SECONDS`, `CAPTURE_JOB_TTL_SECONDS`) queda
probada en `tests/test_settings.py` para que el ensayo de tres procesos sea rápido.
