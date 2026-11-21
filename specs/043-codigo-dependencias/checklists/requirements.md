# Specification Quality Checklist: Retiro de código y dependencias sin consumidores

**Purpose**: Validar la completitud y calidad de la spec antes del plan
**Created**: 2026-11-20
**Feature**: [`spec.md`](../spec.md)

## Content Quality

- [x] Sin tecnologías nuevas fuera de las ya decididas en la constitución; las
  excepciones están en Notes
- [x] Centrada en el valor para quien mantiene el proyecto y para el usuario final
  (mismo comportamiento, menos mantenimiento)
- [x] Escrita para personas no técnicas en las historias; los símbolos concretos
  viven en el plan y en las tareas
- [x] Todas las secciones obligatorias completas

## Requirement Completeness

- [x] Sin marcadores [NEEDS CLARIFICATION]
- [x] Requisitos verificables y sin ambigüedad (búsqueda, instalación, import, CLI,
  imagen, puerta)
- [x] Criterios de éxito medibles
- [x] Criterios de éxito sin detalles de implementación
- [x] Escenarios de aceptación definidos para cada historia
- [x] Casos borde identificados (falsos negativos de búsqueda, imports indirectos,
  `OLLAMA_MODEL`, build sin red)
- [x] Alcance acotado (solo código y dependencias sin consumidores; sin tocar
  vistas, corpus, plantillas ni contratos)
- [x] Dependencias y supuestos identificados

## Feature Readiness

- [x] Cada requisito funcional es verificable por un escenario de aceptación o un
  criterio de éxito; las excepciones están en Notes
- [x] Las historias cubren los flujos principales (chat, runner, dependencias,
  capacidades heredadas)
- [x] La capacidad cumple los resultados medibles definidos
- [x] Los detalles técnicos que aparecen son contratos existentes o decisiones ya
  registradas, no diseño nuevo

## Notes

- La spec es de refactorización: el «usuario» es quien mantiene el repositorio y el
  valor es el mismo comportamiento con menos superficie que mantener.
- FR-006 y FR-007 son condicionales a propósito: si la instalación o la CLI fallan,
  la dependencia se conserva y el fallo se documenta. La evidencia manda sobre la
  intuición.
- Las capacidades heredadas (FAISS, SQLite, Sentry, pgvector en migraciones) quedan
  explícitamente fuera del retiro: su eliminación pertenece a la spec 030.
- La puerta de calidad se ejecuta con `env -u OLLAMA_MODEL`, como documenta el
  contrato del proyecto; sin eso, dos pruebas de configuración cambian de resultado.
- Lista para `/speckit.plan`.
