# Análisis de consistencia (046)

**Alcance**: `spec.md`, `plan.md` y `tasks.md` de la spec 046, contra la constitución del
proyecto.
**Fecha**: 2026-11-20.
**Resultado**: 1 CRITICAL corregido; 3 HIGH y 4 MEDIUM con corrección o disposición; sin
duplicaciones que afecten la ejecución. La implementación no empieza hasta corregir el
CRITICAL.

## Hallazgos

| ID | Categoría | Severidad | Ubicación | Resumen | Corrección aplicada |
|---|---|---|---|---|---|
| C-01 | Alineación con la constitución | **CRITICAL** | `plan.md` (tabla de cambios), `tasks.md` T017–T018 | El Principio I exige que el cambio arquitectónico se registre en `CONSTITUTION.md` **en el mismo commit**; el plan lo dejaba como tarea y commit aparte (T018 después de T017) | Se movió la fila de `CONSTITUTION.md` §9 al commit `refactor(046)` (T016 incluye la edición antes del commit); la tarea separada desaparece |
| H-01 | Cobertura / orden | HIGH | `tasks.md` T001 | T001 mandaba escribir `analysis.md` dentro de la fase de especificación, pero `analysis.md` se produce después de `tasks.md`; contradice el flujo specify→plan→tasks→analyze | T001 se limita a `spec.md`, `checklists/requirements.md`, `plan.md` y `tasks.md`; se añade T008b (`analysis.md` + corregir CRITICAL) antes de tocar código de producción |
| H-02 | Ambigüedad de la prueba de retirada | HIGH | `spec.md` US3 escenario 2; `plan.md` C11 | La spec pedía «copia en el checkpointer y semilla vacía» y el plan original no tenía un escenario con esa forma exacta; sin él, la retirada no tendría prueba roja→verde | Se añadió C11 y la tarea T010 explícita; se aclara en el plan que C11 falla antes del cambio y pasa después |
| H-03 | Criterio de aceptación incompleto | HIGH | `spec.md` SC-002, `plan.md` C9–C11 | SC-002 hablaba de «checkpointer vacío o copia envenenada» sin tarea que lo cubra con el servicio completo, solo con el grafo | T009 usa el servicio real con checkpointer vacío; C10 usa el grafo con copia envenenada. Ambos quedan en la matriz y en la suite de integración |
| M-01 | Cobertura de borde | MEDIUM | `spec.md` Edge Cases («el checkpointer se cae») | No hay tarea que pruebe el fallo del checkpointer; hoy un fallo de construcción del runtime devuelve 503 igual que un fallo del proveedor y esta fase no lo cambia | Se documenta como comportamiento existente fuera del alcance; no se añade tarea para no ampliar el PR |
| M-02 | Trazabilidad | MEDIUM | `tasks.md` | FR-012 (paridad de eventos SSE) solo se verificaba de forma implícita en T003 | T003 y T005 afirman la secuencia de eventos `open/trace/meta/…/done` en la caracterización |
| M-03 | Deriva de término | MEDIUM | `plan.md` «turns sera el contexto…» | Error de redacción en el contrato del plan | Corregido a «`turns` será el contexto sembrado de la invocación» |
| M-04 | Proyección de la constitución | MEDIUM | `plan.md`, `tasks.md` | La proyección `.specify/memory/constitution.md` no se actualiza y declara una fecha de sincronización; la fuente única es la raíz | Se deja constancia: la fase no cambia principios ni stack, así que no se toca la proyección para no competir con la fase 042 que sí la sincroniza |
| L-01 | Estilo | LOW | `tasks.md` | Algunas tareas agrupaban dos archivos | Aceptado: van en el mismo commit y comparten prueba |

## Cobertura de requisitos

| Requisito | ¿Tiene tarea? | Tareas |
|---|---|---|
| FR-001 PostgreSQL es autoridad del historial | ✅ | T003, T005, T011 |
| FR-002 preferencias en el Store por dueño | ✅ | T004, T015 |
| FR-003 checkpointer solo estado técnico | ✅ | T009, T010, T011–T014 |
| FR-004 siembra desde PostgreSQL en cada turno | ✅ | T003, T011 |
| FR-005 hilo nuevo sin memoria heredada | ✅ | T003 |
| FR-006 reinicio sin pérdida visible | ✅ | T005 |
| FR-007 reintento idempotente | ✅ | T006 |
| FR-008 turno incompleto y replay | ✅ | T006 |
| FR-009 aislamiento entre visitantes | ✅ | T004 |
| FR-010 retirada condicionada a evidencia | ✅ | T001, T009–T016 |
| FR-011 sin borrado ni migración destructiva | ✅ | T022 |
| FR-012 paridad de rutas, citas y eventos | ✅ | T003, T005, T024 |
| SC-001 caracterización ×7 antes y después | ✅ | T003–T007, T020 |
| SC-002 checkpointer vacío o envenenado | ✅ | T009, T010, T020 |
| SC-003 estado técnico sin respuesta ni incompleto | ✅ | T010, T012 |
| SC-004 datos históricos intactos | ✅ | T022 |
| SC-005 puerta completa | ✅ | T019–T021 |
| SC-006 PR explicativa | ✅ | T024, T025 |

## Alineación con la constitución

| Principio | Cumple | Nota |
|---|---|---|
| I. La constitución manda | ✅ tras C-01 | La fila de §9 va en el commit del refactor |
| II. El corpus es el contrato | ✅ | No se toca el corpus ni la ingesta |
| III. Sin fuentes no hay respuesta | ✅ | Ruta `rag`, citas y abstención intactas |
| IV. Calidad automatizada | ✅ | `make check` + integración + Node en la puerta |
| V. Captura responsable | ✅ | Fuera de alcance |
| VI. Cambios por Pull Request | ✅ | Rama, PR a `dev`, sin merge ni `--admin` |

## Tareas sin requisito

Ninguna. Las tareas de documentación (T001, T008, T017) mapean a FR-010 y SC-006.

## Métricas

- Requisitos: 12 FR + 6 SC = 18, todos con al menos una tarea (100 %).
- Tareas: 27 (10 con `[P]`).
- Ambigüedades: 1 (H-02, corregida).
- Duplicaciones relevantes: 0.
- CRITICAL: 1 (C-01, corregido antes de implementar).

## Próximos pasos

CRITICAL corregido y HIGH con tarea. Se puede implementar en el orden de `tasks.md`:
caracterización (T002–T008), evidencia de la retirada (T009–T010), refactor (T011–T016) y
puerta/PR (T017–T025). Si la caracterización demuestra una dependencia de la copia, se
ejecuta T016 (conservar la copia y documentar) y el PR se reduce a la separación de capas.
