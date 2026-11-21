# Análisis de especificación: 045 Corpus: Gold fuera del adaptador SQL y verificación en un solo recorrido

**Fecha**: 2026-11-20
**Artefactos**: [`spec.md`](./spec.md) · [`plan.md`](./plan.md) · [`tasks.md`](./tasks.md)
**Constitución**: `.specify/memory/constitution.md` (proyección de `CONSTITUTION.md`)

## Resumen del análisis

La spec, el plan y las tareas son consistentes en alcance: los tres objetivos (Gold en el dominio,
instantánea del índice y lectura única de Gold por capa) aparecen alineados y ninguna historia
introduce formato, entidad ni servicio nuevo. No hay conflictos con la constitución: no se añaden
dependencias, no hay migraciones y la puerta de calidad (`make check` + integración) está en las
tareas finales.

Se detectaron **dos ambigüedades de medición** que, de no corregirse, harían que la prueba de
aceptación pudiera interpretarse como «24 lecturas de archivo» en lugar de «un recorrido de la
capa»; se corrigieron antes de implementar. También se añadieron dos coberturas explícitas que
faltaban (fallos parciales y no-migraciones) y se corrigió una errata del plan.

## Hallazgos

| ID | Categoría | Severidad | Ubicación | Resumen | Recomendación |
|----|-----------|-----------|-----------|---------|---------------|
| AM1 | Ambigüedad | HIGH | `spec.md` FR-006 / SC-002 | «leer los Markdown una sola vez» podía contarse por archivo (24) o por recorrido de la capa (1); la prueba no sería unívoca. | Definir «recorrido completo de la capa» (una pasada sobre el conjunto de Markdown, con 24 lecturas de archivo) y usar ese conteo en la prueba. **Aplicado**: FR-006 y SC-002 reformulados. |
| AM2 | Ambigüedad | HIGH | `spec.md` FR-001 / SC-001 | «un único recorrido de Qdrant» podía confundirse con un único *scroll* de red: el cliente pagina en varios *scrolls* aunque se haga una sola pasada lógica. | Definir el conteo como invocaciones de `_payloads`/snapshot por capa (1), declarando que la paginación interna del cliente no cuenta. **Aplicado**: FR-001 y SC-001 reformulados. |
| CO1 | Cobertura | MEDIUM | `tasks.md` Fase 4 | FR-008 (fallos parciales) no tenía tarea explícita de prueba; solo aparecía implícito en T026. | Añadir tarea de pruebas de PostgreSQL caído y Qdrant caído con la lectura única. **Aplicado**: T027 ampliada. |
| CO2 | Cobertura | MEDIUM | `tasks.md` Fase 8 | FR-011 (sin dependencias, sin migraciones, sin modelo nuevo) no tenía tarea de verificación explícita. | Añadir tarea que compruebe que no cambian `pyproject.toml`, `uv.lock`, `migrations/` ni el modelo de embeddings. **Aplicado**: T036 ampliada. |
| IN1 | Inconsistencia | LOW | `plan.md` Phase 1 | Errata «escribre» en el contrato de importación. | Corrección ortográfica. **Aplicado**. |
| TE1 | Terminología | LOW | `plan.md` / `tasks.md` | El plan llama «lote de capa» y las tareas «lectura única de Gold»; la spec dice «lote de capa Gold». | Sin cambio: son sinónimos explícitos en `Key Entities`; no afecta la ejecución. |

## Tabla de cobertura de requisitos

| Requisito | ¿Tiene tarea? | Tareas | Notas |
|-----------|---------------|--------|-------|
| FR-001 | Sí | T009–T012 | Snapshot de una pasada e implementación en el adaptador. |
| FR-002 | Sí | T011, T012 | Diagnóstico textual y clasificación conservados. |
| FR-003 | Sí | T012 | Prueba con dobles de que verify no escribe. |
| FR-004 | Sí | T014–T019 | Módulo de dominio y consumidores. |
| FR-005 | Sí | T003, T004 | Caracterización antes de tocar código. |
| FR-006 | Sí | T021–T028 | Lote de capa y adaptadores. |
| FR-007 | Sí | T029–T030 | Metadatos sin reembeber y texto sí reembebe. |
| FR-008 | Sí | T026, T027 | Fallos parciales por almacén. |
| FR-009 | Sí | T005, T006, T031, T032 | Contadores y medición reproducible. |
| FR-010 | Sí | T033 | Decisión de embeddings justificada con números. |
| FR-011 | Sí | T036 | Sin modelo, sin migraciones, sin dependencias. |
| FR-012 | Sí | T036, T037 | Contratos de CLI/web y puerta. |

## Tabla de cobertura de criterios de éxito

| Criterio | ¿Tiene tarea? | Tareas |
|----------|---------------|--------|
| SC-001 | Sí | T006, T012 |
| SC-002 | Sí | T005, T027 |
| SC-003 | Sí | T003, T004, T019 |
| SC-004 | Sí | T029 |
| SC-005 | Sí | T007, T032 |
| SC-006 | Sí | T033 |
| SC-007 | Sí | T037–T039 |

## Alineación con la constitución

| Principio | Resultado |
|---|---|
| §2 Alcance (corpus, capas, indexación) | Sin violación: reorganiza la sincronización existente. |
| §3 Stack sin dependencias nuevas | Sin violación; FR-011 y T036 lo verifican. |
| §4 Estructura (dominio/orquestación/adaptadores) | El módulo Gold va a la raíz del paquete, no a `domain/` (que es sin IO); el plan lo justifica. |
| §5 Convenciones | Ruff/100 columnas/docstrings en español en todas las tareas de código. |
| §6 Calidad | T037–T039 ejecutan la puerta completa. |
| §9 Decisiones | T035 actualiza `CONSTITUTION.md` §9 y `specs/README.md` en la misma PR. |

**Constitution Alignment Issues**: ninguno.

## Tareas sin requisito mapeado

Ninguna: T001–T002 preparan el entorno/artefactos y T035–T040 son puerta y documentación exigidas
por §6 y §9.

## Métricas

- Requisitos funcionales: 12
- Criterios de éxito: 7
- Tareas: 40
- Cobertura de requisitos con al menos una tarea: 12/12 (100 %)
- Ambigüedades: 2 (ambas corregidas antes de implementar)
- Duplicaciones: 0
- Hallazgos críticos: 0

## Acciones siguientes

No hay hallazgos crítricos. Las dos ambigüedades de medición y las dos coberturas se corrigieron
en `spec.md`, `plan.md` y `tasks.md` antes de empezar la implementación. Se puede pasar a
`/speckit-implement` (T003 en adelante), empezando por la caracterización y la medición base.
