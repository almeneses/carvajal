# Implementation Plan: Ingesta multiformato de archivos locales

**Branch**: `035-ingesta-multiformato` | **Date**: 2026-11-16 | **Spec**: [`spec.md`](./spec.md)
**Input**: Feature specification from `/specs/035-ingesta-multiformato/spec.md`
**Nota de origen**: plan escrito con el PR #130 ya propuesto como implementación candidata; no
reconstruye un plan aprobado antes de escribir código.

## Summary

Extender la carga de archivos locales —web y consola— más allá de HTML, Markdown y TXT:
PDF extraído por página, datos XML/JSON/JSONL/CSV/TSV validados y conservados como texto, y
un índice del corpus que se actualiza bajo bloqueo para que cargas y capturas concurrentes no
se pisen. La web mantiene sus límites; la consola asume el volumen con lotes y una parada
opcional en Gold.

## Technical Context

| Aspecto | Decisión | Evidencia |
|---|---|---|
| Lenguaje | Python 3.13, paquete en `packages/core` (src-layout) | `pyproject.toml` |
| Entorno | `uv` + `uv.lock`; prohibido `pip` | `CONSTITUTION.md` §3 |
| Web | FastAPI + Jinja2 + HTMX en `apps/api` | `apps/api/main.py` |
| Extracción | módulo nuevo `apps/api/document_formats.py` con solo biblioteca estándar | `apps/api/document_formats.py` |
| PDF | `pdftotext` de `poppler-utils`, invocado con límites de tiempo y tamaño | `Dockerfile` |
| Capas | `carvajal.gold`: Bronze, Silver, Gold e índice JSONL | `packages/core/src/carvajal/gold.py` |
| Concurrencia | `fcntl.flock` + archivo temporal + `Path.replace` | `packages/core/src/carvajal/gold.py` |
| Consola | `python -m apps.api.ingest_cli` con `argparse` | `apps/api/ingest_cli.py` |
| Pruebas | pytest con `monkeypatch` para el extractor de PDF y el entorno | `tests/test_ingestion.py` |
| Límites | web: 100 archivos y 5 MB por archivo; consola: 100 MB por archivo; PDF: 20 s y 16 MB de texto | `apps/api/ingestion.py`, `apps/api/document_formats.py` |
| Rendimiento | lotes acotados por número de archivos y bytes acumulados | `apps/api/ingest_cli.py` |
| Alcance | sin OCR, sin autenticación nueva, sin cambios de esquema relacional | `spec.md` §Assumptions |

**Alternativas descartadas**: dependencia de terceros para PDF (añade peso a la imagen y
un parser propio que no necesitamos), conversión de CSV/JSON a tabla relacional (fuera de
alcance: la spec pide texto citable), y escritura directa del índice sin bloqueo (pierde
entradas cuando coinciden carga y captura).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Puerta | Cómo se cumple |
|---|---|
| §2 Alcance: carga de archivos locales dentro del alcance | La carga ya existía para HTML/Markdown/TXT; esta capacidad amplía formatos, no el modelo de datos. |
| §2 Fuera del alcance: OCR de PDF escaneados | Se avisa y se rechaza el archivo; no se incorpora OCR. |
| §3 Stack: sin dependencias nuevas de Python | PDF vía binario del sistema; XML/JSON/CSV con biblioteca estándar. |
| §4 Estructura: dominio en `packages/core`, web en `apps/api` | El índice y sus garantías viven en `carvajal.gold`; la extracción y la CLI en `apps/api`. |
| §5 Convenciones: Ruff, 100 columnas, comillas dobles, UI en español | Se aplica en los módulos nuevos. |
| §6 Calidad: `make check` con lint, tipos y pruebas | Cada escenario de la spec tiene prueba en `tests/test_ingestion.py` y `tests/test_api_config.py`. |
| §6.1 Imagen: multi-etapa, sin privilegios | `poppler-utils` se instala en la etapa runtime antes de crear el usuario `app`. |
| §9 Decisiones | La entrada de historial debe registrar la ampliación de alcance y el cambio de pendiente de PDFs a OCR. |

**Resultado**: pasa. La actualización de `CONSTITUTION.md` §9 y §10 es una tarea de la fase
final, no una violación.

## Project Structure

### Documentation (this feature)

```text
specs/035-ingesta-multiformato/
├── plan.md              # Este archivo
├── spec.md              # Contrato funcional
├── tasks.md             # Desglose por historia (/speckit.tasks)
└── checklists/
    └── requirements.md  # Validación de la spec
```

No se generan `research.md`, `data-model.md` ni `contracts/`: la capacidad no introduce
entidades de dominio ni contratos HTTP nuevos (el formulario y el informe ya existen); lo
único nuevo es una interfaz de línea de órdenes, descrita en `plan.md` y cubierta por las
tareas.

### Source Code (repository root)

```text
apps/api/
├── document_formats.py      # Extracción de PDF y validación de datos (nuevo)
├── ingest_cli.py            # Consola de carpetas por lotes (nuevo)
├── ingestion.py             # Lista de formatos, límites y orquestación del lote
└── templates/configuracion.html  # Lista de formatos en el formulario

packages/core/src/carvajal/
└── gold.py                  # Escritura atómica y fusión bloqueada del índice

tests/
├── test_ingestion.py        # Extracción, lotes, concurrencia y CLI
└── test_api_config.py       # Lista de formatos en el formulario
```

**Structure Decision**: mantener la frontera actual. `apps/api` es la capa de interfaz
(HTTP y consola) y `packages/core` la de corpus; el índice no puede depender de FastAPI.

## Phase 0: Investigación

- Confirmar que `pdftotext` está disponible en la imagen y qué salida produce con páginas
  vacías (separador de formulario entre páginas).
- Elegir el mecanismo de bloqueo: `flock` sobre un archivo hermano del índice, portable en
  el contenedor Linux y compatible con procesos distintos.
- Verificar que la biblioteca estándar cubre XML sin resolver entidades externas.

**Salida**: decisiones registradas en la tabla de contexto; sin artefacto aparte.

## Phase 1: Diseño

1. **Contrato de extracción**: `extract_markdown(suffix, data) -> str` devuelve Markdown o
   lanza `ValueError` con un mensaje en español apto para el informe.
2. **Contrato del índice**: `merge_index(index_file, entries, *, gold_dir=None)` fusiona bajo
   bloqueo y escribe de forma atómica; `gold_dir` activa la poda de entradas sin archivo.
3. **Contrato de la consola**: `ingest_cli <rutas...> [--solo-extraer]`, resumen por lote en
   la salida estándar, errores en la salida de error, código 1 si algo falló.
4. **Contrato del formulario**: la plantilla recibe la lista de formatos y la usa en los dos
   selectores y en la ayuda.

**Salida**: los contratos quedan en este archivo y en las tareas; no hay `contracts/` porque
no se expone ninguna ruta HTTP nueva.

## Phase 2: Riesgos

| Riesgo | Mitigación |
|---|---|
| PDF escaneado silencioso: llega a Gold sin texto citable | Error explícito con aviso de OCR y prueba dedicada. |
| PDF malicioso o enorme bloquea el proceso | Tiempo máximo de 20 s, límite de 16 MB de texto y muerte del proceso al agotarse. |
| XXE por DTD en XML | Parser propio que rechaza cualquier DTD antes de construir el árbol. |
| Pérdida de entradas del índice en concurrencia | Bloqueo exclusivo + reemplazo atómico + prueba con hilos. |
| Regresión de la carga existente | La lista anterior sigue siendo subconjunto; las pruebas actuales se conservan. |

## Complexity Tracking

| Decisión | Por qué | Alternativa rechazada |
|---|---|---|
| Módulo de extracción separado de `ingestion.py` | La extracción por formato y los límites del PDF no son responsabilidad del orquestador de capas. | Mantener todo en `ingestion.py`: el archivo crece con lógica de subprocesos y validación ajena. |
| Bloqueo de archivo en `carvajal.gold` | El índice lo escriben dos procesos distintos (web y captura). | Bloqueo en memoria del proceso web: no protege a la captura ni a la consola. |
| `pdftotext` como binario del sistema | Sin dependencias Python nuevas y la imagen ya es multi-etapa. | Biblioteca Python de PDF: más peso, más superficie de ataque y otra cosa que actualizar. |
