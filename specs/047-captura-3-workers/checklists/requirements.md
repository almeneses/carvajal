# Specification Quality Checklist: Captura web duradera para tres workers

**Purpose**: Validar la completitud y calidad de la especificación antes de planificar.
**Created**: 2026-11-20
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No incluye detalles de implementación propios del código (clases, funciones, SQL).
- [x] Está centrada en el valor para las personas y en el comportamiento observable.
- [x] Es legible para una persona no técnica (con un glosario mínimo en las entidades).
- [x] Todas las secciones obligatorias están completas.

## Requirement Completeness

- [x] No quedan marcadores `[NEEDS CLARIFICATION]`.
- [x] Los requisitos son verificables y no ambiguos (FR-001 … FR-020, cada uno con un
      escenario o criterio de éxito asociado).
- [x] Los criterios de éxito son medibles (SC-001 … SC-007).
- [x] Los criterios de éxito son agnósticos de la tecnología, salvo las restricciones
      heredadas del stack vigente (PostgreSQL, `/live`, `/health`, SSE) que el owner fijó
      explícitamente como invariantes.
- [x] Todos los escenarios de aceptación están definidos.
- [x] Los casos límite están identificados (concurrencia, latido tardío, trabajo repetido,
      base caída, apagado ordenado, TTL, ventana del límite).
- [x] El alcance está acotado (dentro/fuera, con la activación de workers y la no
      reanudación por URL declaradas).
- [x] Dependencias y supuestos están identificados (sync idempotente de la 037, un solo
      contenedor/volumen, `job_id` como capacidad, retención y TTL).

## Feature Readiness

- [x] Todos los requisitos funcionales tienen criterio de aceptación observable.
- [x] Los escenarios cubren los flujos principales: progreso multi-worker, exclusión y
      cupo compartidos, recuperación de interrumpidos y operación con tres workers.
- [x] La capacidad cumple los resultados medibles de los criterios de éxito.
- [x] No se filtran detalles de implementación más allá de las invariantes fijadas por el
      owner y el stack existente del proyecto.

## Notas

- La especificación es deliberadamente honesta en FR-011, FR-012 y FR-020: la recuperación
  reconcilia desde Gold mediante `sync`; no promete reanudación exacta por URL.
- La activación de los tres workers queda fuera de esta PR (FR-018) y se verifica en la
  tarea posterior `task-8`, por decisión explícita del owner.
