# Análisis de consistencia: 042 (spec · plan · tasks)

**Fecha**: 2026-11-20 · **Alcance**: `spec.md`, `plan.md`, `tasks.md`, `inventario.md`,
`baseline.md`, `quickstart.md` y ADR 0002.
**Modo**: solo lectura sobre los artefactos; no modifica el código.

## Specification Analysis Report

| ID | Categoría | Severidad | Ubicación | Hallazgo | Recomendación |
|---|---|---|---|---|---|
| A1 | Duplicación | LOW | spec.md FR-004 / inventario §5 | La descripción de modos de ejecución aparece en spec e inventario con distinto nivel de detalle. | Se conserva: la spec fija el requisito y el inventario lo hace operativo. Sin acción. |
| A2 | Ambigüedad | LOW | spec.md SC-005 | «los mismos conteos» no dice qué hacer con los tiempos. | `baseline.md` §1 y quickstart aclaran que los tiempos son observaciones y los conteos son el criterio. |
| A3 | Cobertura | MEDIUM | spec.md SC-004 / tasks.md T016 | El criterio de 3 workers se decide en el ADR 0002, pero la activación efectiva es de la fase 047 y no de esta spec. | Aceptado y documentado: 042 decide y 047 activa; el ADR enlaza la condición de activación. |
| A4 | Cobertura | MEDIUM | spec.md FR-008 / baseline.md | El arranque de la imagen Docker no forma parte del baseline de fase 0; se verifica en 047b. | Aceptado: la imagen requiere las fases 043–047; baseline deja el estado actual documentado y no promete ese arranque. |
| A5 | Inconsistencia | LOW | spec.md SC-002 / baseline §2 | SC-002 fija 68 pruebas de Node, un número que puede crecer con las fases 044/045. | Se interpreta como el valor del punto de partida (`8927ee7`); las fases posteriores actualizan su propio conteo. |
| A6 | Cobertura | LOW | plan.md §Estructura / tasks.md | `checklists/requirements.md` existe pero no se citaba en el plan. | Se cita en `tasks.md` T001 y en esta análisis. |

**Tabla de cobertura de requisitos**

| Requisito | ¿Tiene tarea? | Tareas | Notas |
|---|---|---|---|
| FR-001 Rutas | Sí | T002 | Tabla en inventario §2 |
| FR-002 CLI | Sí | T003 | Tabla en inventario §3 |
| FR-003 SSE | Sí | T004 | Tabla en inventario §4 |
| FR-004 Modos | Sí | T005 | Tabla en inventario §5 |
| FR-005 Fuentes | Sí | T006 | Tabla en inventario §6 |
| FR-006 Paridad dev/main | Sí | T007–T010 | PR independiente |
| FR-007 ADR 0002 | Sí | T016 | ADR 0001 intacto |
| FR-008 Baseline | Sí | T011–T014 | Comandos y resultados |
| FR-009 Contratos | Sí | T014 | Ejemplos literales |
| FR-010 Candidatos por fase | Sí | T006, inventario §7 | Sin implementarlos |
| FR-011 Relación 042–047 | Sí | T001, plan.md | 041 reservada |
| FR-012 Sin main/VPS/merge | Sí | T010, T020 | Regla de proceso |

**Cobertura de campos con restricciones**: no aplica; esta spec no introduce entidades con
esquema (solo documentos y una decisión).

**Alineación con la constitución**: sin conflictos. La spec no cambia alcance, stack ni
reglas; documenta y ordena. La constitución (§9) se actualiza en esta misma rama.

**Tareas sin requisito**: ninguna. T018 (actualizar la proyección de la constitución) se
deriva del principio I («La constitución manda»).

**Métricas**: 12 requisitos, 6 criterios de éxito, 21 tareas, cobertura 100 %, ambigüedades
abiertas 0, duplicaciones 0, hallazgos CRITICAL 0.

## Notas para las fases siguientes

- El hallazgo A3 obliga a que la fase 047 incluya en su `tasks.md` la activación con
  `UVICORN_WORKERS` y la prueba de retorno a 1 worker; el ADR 0002 ya fija el contrato.
- El hallazgo A5 recuerda actualizar el conteo de pruebas de Node en cada spec que añada
  pruebas de cliente.
- La medición instrumentada de `sync --verify` (dos recorridos → uno) pertenece a la 045;
  `baseline.md` §5 deja la definición de la métrica y el método.

## Próximas acciones

No hay hallazgos CRITICAL: se puede implementar. Las tareas T008–T021 se ejecutan en esta
fase; la fase 043 abre su propia spec y PR.
