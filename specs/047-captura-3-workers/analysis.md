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
