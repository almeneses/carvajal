# Plan de implementación: línea base y arquitectura (042)

**Rama**: `docs/042-linea-base-arquitectura` (+ `fix/042-sincroniza-arreglos-main`)
**Fecha**: 2026-11-20
**Spec**: [spec.md](spec.md)

## Resumen

Fase 0 del goal. No cambia el producto: produce el mapa verificable de la aplicación, el
ADR que habilita el paso a 3 workers y la línea base reproducible. La única intervención
en código es de paridad: llevar a `dev` los arreglos que `main` ya fusionó.

## Contexto técnico

| Aspecto | Decisión |
|---|---|
| Base | `origin/dev` (`8927ee7`); `origin/main` (`d975594`) como referencia de paridad |
| Documentos | `inventario.md`, `baseline.md`, `quickstart.md`, ADR `docs/decisions/0002-*.md` |
| Correcciones de paridad | cherry-pick de `5826114`, `9e5484f`, `9371268` (no merge ni squash) |
| Aislamiento | ramas separadas: documentos en `docs/042-*`, correcciones en `fix/042-*` |
| Verificación | `make check`, `node --test tests/js/test_chat_voice.cjs`, pruebas con/sin la corrección |
| Entorno local | `UV_CACHE_DIR=/tmp/uvcache`, `env -u OLLAMA_MODEL` para neutralizar el entorno |
| Alcance | sin migraciones, sin cambios de esquema, sin tocar `main`, VPS ni producción |

## Constitution Check

| Principio | Evaluación |
|---|---|
| I. Fuente única de verdad | El ADR 0002 y el inventario se enlazan desde `CONSTITUTION.md` §9; la constitución sigue siendo la fuente. |
| II. Corpus | No se toca Gold, PostgreSQL, Qdrant ni catálogo. |
| III. Fuentes | No aplica: no se leen ni citan fuentes nuevas. |
| IV. Calidad | `make check` y las pruebas de Node son la puerta; el baseline registra el comando y el entorno. |
| V. Captura responsable | No se añade captura; el inventario documenta los límites actuales como contrato a conservar. |
| VI. Pull Requests | Dos PRs independientes a `dev`, sin merge ni aprobación por agentes; etiquetas y assignee. |

**Revisión del diseño**: pasa. No hay violaciones ni excepciones que justificar.

## Decisiones de investigación

1. **Punto de partida**: `origin/dev` actualizado. Se confirmó con `git log
   origin/main..origin/dev` y `git log origin/dev..origin/main` que la divergencia es
   exactamente: `main` tiene los tres arreglos de trazas/manos libres; `dev` tiene el
   cierre documental de la 040 (#199). No se mezclan: se cherry-pickean solo las
   correcciones.
2. **Numeración**: la 041 está comprometida en `CONSTITUTION.md` §9 (informe de
   enrutadores). Este goal usa 042–047.
3. **Medición**: se mide en el mismo entorno local (PostgreSQL 16 con pgvector en Docker,
   Qdrant 1.15.4, corpus núcleo de 24 documentos / 125 fragmentos) y se anota el entorno
   de cada comando. Los tiempos son observaciones, no garantías.
4. **Workers**: el despliegue objetivo es `compose.prod.yml` con exactamente tres workers
   de Uvicorn; la decisión se documenta ahora y se activa en la fase 047 tras verificar la
   coordinación de captura. El ADR incluye la reversión a uno (`UVICORN_WORKERS=1`).
5. **Sin merge**: los PRs se abren y se detienen; el owner fusiona.

## Estructura

- `specs/042-linea-base-arquitectura/spec.md` — requisitos y criterios.
- `specs/042-linea-base-arquitectura/plan.md` — este documento.
- `specs/042-linea-base-arquitectura/tasks.md` — desglose ejecutable.
- `specs/042-linea-base-arquitectura/inventario.md` — rutas, CLI, SSE, modos y fuentes.
- `specs/042-linea-base-arquitectura/baseline.md` — contratos y mediciones de referencia.
- `specs/042-linea-base-arquitectura/quickstart.md` — cómo repetir la línea base.
- `specs/042-linea-base-arquitectura/analysis.md` — análisis de consistencia previo.
- `docs/decisions/0002-arquitectura-objetivo-3-workers.md` — ADR.
- `docs/decisions/README.md` y `specs/README.md` — índices actualizados.

## Riesgos y mitigación

| Riesgo | Mitigación |
|---|---|
| El cherry-pick arrastra documentos de la 040 | Aplicar solo los tres commits y revisar el diff contra `origin/dev`. |
| El entorno local falla pruebas por variables heredadas | Ejecutar con `env -u OLLAMA_MODEL` y registrar la diferencia. |
| El baseline de `sync --verify` depende de servicios | Anotar disponibilidad y no inventar tiempos; repetir en la fase 045. |
| Los PRs no se fusionan y las fases se apilan | Cada rama parte de `dev`; los solapamientos se limitan y se documentan. |

## Validación

1. `git diff origin/dev --stat` de cada rama: solo los archivos esperados.
2. `make check` y `node --test tests/js/test_chat_voice.cjs` en verde.
3. Pruebas de paridad: fallan en `origin/dev` y pasan en `fix/042-*`.
4. Recuento de rutas/comandos/eventos contra el código.
5. Enlaces del ADR e índices comprobados.
