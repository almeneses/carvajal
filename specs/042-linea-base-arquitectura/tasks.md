# Tareas: línea base y arquitectura (042)

**Fuentes**: [spec](spec.md), [plan](plan.md), [inventario](inventario.md),
[baseline](baseline.md) y [quickstart](quickstart.md).
`[P]` indica trabajo paralelizable una vez cumplidos sus prerequisitos.

## Fase 1 — Reconocimiento

- [X] T001 Confirmar la base `origin/dev` y la divergencia con `origin/main`; documentar los tres commits de paridad y los documentos que no deben importarse (spec.md, plan.md).
- [X] T002 [P] Inventariar rutas HTTP con método, formato, entrada y efecto en `specs/042-linea-base-arquitectura/inventario.md`.
- [X] T003 [P] Inventariar comandos CLI, opciones, escrituras y códigos de salida.
- [X] T004 [P] Inventariar eventos SSE, productores, consumidores y cierre.
- [X] T005 [P] Inventariar modos de ejecución (CLI, `make api`, imagen, Compose) y número de workers.
- [X] T006 [P] Inventariar fuentes de verdad por dato compartido y persistente.

## Fase 2 — Paridad `dev`/`main`

- [X] T007 Escribir en `tests/test_api_chat.py` y `tests/test_chat_tracing.py` las pruebas que fijan la traza con largo acumulado y la devolución de la pregunta en manos libres (ya existentes en `main`; se conservan al cherry-pickear).
- [X] T008 Crear `fix/042-sincroniza-arreglos-main` desde `origin/dev` y aplicar `5826114`, `9e5484f`, `9371268`; revisar que el diff no incluye documentos de la 040.
- [X] T009 Ejecutar `pytest tests/test_chat_tracing.py tests/test_api_chat.py`, `node --test tests/js/test_chat_voice.cjs` y `make check` en la rama de paridad.
- [X] T010 Abrir PR a `dev` con labels `bug` y `backend`, assignee del autor y explicación de la brecha.

## Fase 3 — Baseline y contratos

- [X] T011 Registrar en `baseline.md` las versiones del entorno, la base, los comandos y los resultados de `make check` y Node.
- [X] T012 Medir conteos de PostgreSQL (documentos y fragmentos) y de Qdrant (puntos) y anotarlos como observaciones del entorno local.
- [X] T013 Ejecutar `carvajal sync --verify --layer all` sin escrituras y registrar el diagnóstico y el tiempo; si los servicios no responden, anotarlo.
- [X] T014 Documentar contratos literales: SSE, `/health`, cookies de identidad y CSRF, códigos de error compartidos.
- [X] T015 Escribir `quickstart.md` para repetir la línea base.

## Fase 4 — ADR e índices

- [X] T016 Escribir `docs/decisions/0002-arquitectura-objetivo-3-workers.md` con arquitectura actual, decisión de 3 workers, consecuencias y reversión.
- [X] T017 Actualizar `docs/decisions/README.md` y `specs/README.md` sin editar el ADR 0001.
- [X] T018 Añadir la fila de decisión en `CONSTITUTION.md` §9 (ADR 0002 y baseline) y su proyección en `.specify/memory/constitution.md` si aplica.
- [X] T019 Ejecutar `make check` y revisar enlaces y tablas de los documentos nuevos.

## Fase 5 — Publicación

- [X] T020 Abrir PR a `dev` con los documentos y la fila de la constitución; labels `documentation` y `refactor`, assignee del autor.
- [ ] T021 Registrar en la PR la ruta de reversión (revertir el commit de documentos) y enlazar la spec 043.

## Dependencias

- T008 depende de T001.
- T009 depende de T008.
- T011–T015 dependen de T001–T006.
- T016–T018 dependen de T011–T014.
- T020 depende de T019.
