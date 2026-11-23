# Specification Quality Checklist: Cotizador conversacional de productos Carvajal

**Purpose**: Validar la completitud y calidad de la spec antes del plan y en cada revisión
**Created**: 2026-11-21
**Feature**: [`spec.md`](../spec.md)

## Content Quality

- [x] Centrada en el valor para el usuario y el negocio
- [x] Escrita para personas no técnicas
- [x] Todas las secciones obligatorias completas
- [x] Decisiones tecnológicas acotadas: mapa OSM incrustado con `frame-src` acotado, SSE
  sobre el formato de cable existente y tabla propia para la memoria

## Requirement Completeness

- [x] Sin marcadores [NEEDS CLARIFICATION]
- [x] Requisitos verificables y sin ambigüedad
- [x] Criterios de éxito medibles
- [x] Criterios de éxito sin detalles de implementación
- [x] Escenarios de aceptación definidos para las siete historias
- [x] Casos borde identificados (cantidades, stock, ciudad, festivos, mapa, memoria, saludos)
- [x] Alcance acotado (Colombia, empaques, COP; sin pedidos ni pagos)
- [x] Dependencias y supuestos identificados

## Memory and privacy

- [x] La memoria silenciosa está en FR-022 y SC-010, no solo en el código
- [x] Se define qué se guarda (nombre, trabajo, ciudad, gustos, domicilio) y qué no
- [x] Se define dónde vive (tabla por visitante) y que no viaja en el payload ni en las trazas
- [x] Se define cómo se borra («olvida mis datos») y cuándo caduca (180 días)
- [x] El riesgo de dispositivo compartido está documentado en `plan.md` y `research.md`
- [ ] Retención de 180 días confirmada por el owner (T053)

## Feature Readiness

- [x] Cada requisito funcional tiene una tarea y una prueba en `plan.md`
- [x] Las historias cubren los flujos principales
- [x] La capacidad cumple los resultados medibles definidos
- [x] El aislamiento de hilos y la memoria tienen contrato explícito

## Notes

- La auditoría del catálogo está en
  [`docs/plans/0002-cotizador-preliminar.md`](../../docs/plans/0002-cotizador-preliminar.md).
- SSE reutiliza `apps/api/sse.py`: el Cotizador no inventa un segundo formato de cable.
- La memoria usa extracción por reglas, no LLM: solo se guarda lo que coincide con un
  patrón y nunca se escriben correos, teléfonos ni texto libre.
- El checklist se revisó el 2026-11-21 después de implementar SSE y la memoria: los dos
  requisitos nuevos entraron en spec, plan, contrato, tareas y pruebas.
- Pendientes reales: revisión de retención (T053), verificación SC-007 (T061), regresión
  explícita de chat/comercial (T062) y verificación visual móvil (T038). El dictado por voz
  (T039) queda diferido a una spec posterior.
