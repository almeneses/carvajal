# Specification Quality Checklist: Corpus incremental, deduplicado y sincronizado

**Purpose**: Validar la completitud y la calidad de la spec antes de planificar
**Created**: 2026-11-16
**Feature**: [`spec.md`](../spec.md)

## Content Quality

- [x] Sin detalles de implementación en la spec (lenguajes, marcos, API)
- [x] Centrada en el valor para el usuario y las necesidades del negocio
- [x] Comprensible para un stakeholder no técnico
- [x] Secciones obligatorias completas

## Requirement Completeness

- [x] Sin marcadores `[NEEDS CLARIFICATION]`
- [x] Requisitos verificables y sin ambigüedad
- [x] Criterios de éxito medibles
- [x] Criterios de éxito agnósticos de la tecnología
- [x] Todos los escenarios de aceptación definidos
- [x] Casos límite identificados
- [x] Alcance acotado de forma explícita
- [x] Dependencias y supuestos identificados

## Feature Readiness

- [x] Cada requisito funcional tiene un escenario de aceptación
- [x] Las historias cubren los flujos principales
- [x] La capacidad cumple los resultados medibles de Success Criteria
- [x] Ningún detalle de implementación se filtra a la spec

## Notes

- La identidad de las subidas web es el nombre del archivo porque el navegador no envía la
  ruta relativa. Es una limitación de la plataforma, no del diseño: la consola sí usa la ruta.
  Si el owner quiere evitar los choques de nombre en la web, hay que cambiar el formulario.
- El retiro de `/ask` y pgvector es una decisión de producto (un solo índice) y depende de que
  la columna de vectores esté vacía en producción; verificado el 2026-11-16.
- La prioridad de capas se valida con la evaluación existente (`make index-eval`), no con una
  impresión subjetiva de las respuestas.
- El rastreo periódico queda fuera; esta spec entrega los contratos de identidad, dedupe y
  sincronización que el cron necesitará.
- La adopción del corpus existente conserva los slugs actuales para no perder versiones ni
  romper enlaces; no hay migración destructiva.
