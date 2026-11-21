# Implementation Plan: Rastreo profundo con Bronze, Silver y Gold

**Branch**: `048-crawl-profundo` | **Date**: 2026-11-21 | **Spec**: [spec.md](spec.md)

## Summary

Añadir un rastreo BFS por encima del pipeline actual: `carvajal crawl` descubre enlaces
desde las semillas, respeta robots y retardos, y escribe Bronze/Silver/Gold sin tocar
PostgreSQL ni Qdrant. Acepta HTML, PDF y hojas de cálculo, y es reanudable. La extracción
de binarios se mueve a `carvajal.extracting` para que el CLI no dependa de `apps/api`.

## Technical Context

| Aspecto | Decisión |
|---|---|
| Lenguaje | Python 3.13, `uv`, Ruff (`line-length = 100`) |
| CLI | `argparse` existente en `packages/core/src/carvajal/cli.py` |
| HTTP | `WebClient` actual (robots, `crawl-delay`, throttle, `urllib`) |
| Extracción HTML | `carvajal.extracting.html_to_markdown` (bs4 + lxml, extra `capture`) |
| Extracción PDF | `pdftotext` (`poppler-utils`, ya en ambas imágenes) |
| Hojas de cálculo | CSV/TSV con `csv` de la stdlib; XLSX con `openpyxl` (nuevo, extra `capture`) |
| Almacenamiento | Sistema de archivos: `data/raw`, `data/processed`, `data/gold`, `data/runtime/crawl` |
| Dedupe | `Registry`/`source_key_for_url` + huella del texto (`content_hash`) |
| Concurrencia | Secuencial en la v1; un host a la vez respeta el retardo sin coordinación |
| Pruebas | `pytest` + servidor HTTP local (`http.server`) y PDF/XLSX generados en el test |
| Objetivo | 5.000 páginas por corrida, ≤ 5 GB, presupuesto de 180 min, reanudable |

## Constitution Check

| Principio | Evaluación |
|---|---|
| I. Constitución | El rastreo respeta robots, retardos y dominios permitidos; se documenta en §6 y en la decisión de §9 al implementar. |
| II. Corpus | Solo escribe las tres capas de archivos; no toca Gold existente salvo para añadir/actualizar. Base e índice quedan intactos sin `--sync`. |
| III. Fuentes | Cada documento conserva URL, host y fecha de captura; nada se resume ni se inventa. |
| IV. Calidad | `make check` cubre unitarias e integración con servidor local; sin red en CI. |
| V. Captura responsable | `robots.txt`, `crawl-delay`, *user-agent* identificable, cupo por host y límites de bytes. Sin login, paywall ni anti-bot. |
| VI. Pull Requests | Spec + plan en esta PR; implementación por olas (extracción, rastreo HTML, binarios, externos). |

**Revisión**: pasa. No hay cambio de contrato HTTP ni de esquema; la única dependencia
nueva (`openpyxl`) entra en el extra `capture`, no en el runtime web.

## Project Structure

### Documentación (esta feature)

```text
specs/048-crawl-profundo/
├── spec.md
├── plan.md
└── research.md
```

### Código (cuando se implemente)

```text
packages/core/src/carvajal/
├── crawler.py              # nuevo: frontier BFS, política de hosts, bucle de captura
├── extracting.py           # + extract_binary_markdown() (pdf/xml/json/jsonl/csv/tsv/xlsx)
├── gold.py                 # + write_bronze_bytes() para originales binarios
└── cli.py                  # + subcomando `crawl`

apps/api/document_formats.py # delega en carvajal.extracting para binarios (paridad)
tests/
├── test_crawler.py         # nuevo: BFS, política, dedupe, reanudación
├── test_crawler_binaries.py# nuevo: PDF/CSV/XLSX
└── test_extracting.py      # + paridad de extracción movida
```

## Diseño

### Comando

```bash
carvajal crawl --scope all --depth 4 --max-pages 5000 \
  --max-pages-per-host 100 --delay 1.0 --time-budget-minutes 180 \
  [--seed URL ...] [--refresh] [--sitemap] [--ignore-robots] [--sync]
```

- Por defecto **no** sincroniza. `--sync` reutiliza `sync --layer core` al final.
- `--sitemap` añade URLs de `sitemap.xml` al nivel 0 antes del BFS.
- Códigos de salida: `0` completo, `1` parcial (tope/tiempo/errores), `2` uso inválido.

### Frontera y política

- Cola FIFO por niveles `(url, depth)`; semillas en `depth=0`; solo se encolan enlaces
  con `depth + 1 <= --depth`.
- Normalización: minúsculas de host, sin fragmento, sin `utm_*`/`fbclid`/`gclid`, barra
  final homogénea; descarta `mailto:`, `tel:`, `javascript:` y esquemas no HTTP(S).
- Hosts: los de las semillas son «propios»; un enlace externo se admite si proviene de
  una semilla de terceros o de un enlace dentro de un host admitido. Cada host externo
  tiene su propio contador y cupo.
- Antes de encolar: `robots.can_fetch`, `rel="nofollow"` y `meta robots noindex` del
  documento de origen. Los bloqueos se cuentan, no se silencian.
- Reintentos: 429/5xx con `Retry-After` o *backoff* exponencial (máx. 3); luego se
  abandona el host y se registra.

### Recursos y capas

| Tipo | Bronze | Silver | Gold |
|---|---|---|---|
| HTML | `raw/<slug>.html` | `processed/<slug>.md` | `gold/<slug>.md` |
| PDF | `raw/<slug>.pdf` (bytes) | `processed/<slug>.md` con `[página N]` | ficha con procedencia |
| CSV/TSV/XLSX | `raw/<slug>.<ext>` (bytes) | tablas por hoja/sección | ficha con procedencia |

- `write_bronze_bytes(path, data)` escribe con temporal + `replace`, igual que Gold.
- El slug se reutiliza del `Registry` si la URL ya existe; si no, se deriva de la URL.
- El índice Gold se actualiza al final de cada lote de 50 documentos con `merge_index`,
  para no reescribirlo 5.000 veces.
- `CapturedResource` generaliza lo que hoy es `CapturedPage`: bytes originales,
  `content_type`, texto extraído y estado.

### Extracción compartida

- `apps/api/document_formats.py` ya implementa `_pdf`, `_xml`, `_json`, `_jsonl` y
  `_tabular`. Se mueven a `carvajal.extracting.extract_binary_markdown()` y el módulo web
  pasa a delegar; `_html` y `_text` se quedan como están para no alterar la carga web.
- XLSX: `openpyxl` en modo `read_only=True`, una sección `## Hoja: <nombre>` por hoja,
  celdas unidas por tabuladores. Límite de 100 MB por archivo y 50.000 filas por hoja (se
  trunca con aviso en el informe).
- PDF escaneado: si el texto extraído es vacío, Silver queda vacío y el recurso se marca
  `requiere_ocr` en el informe; Bronze se conserva.

### Reanudación

- `data/runtime/crawl/<run_id>.jsonl`: una línea por URL procesada con
  `{url, depth, state, hash, ts}`. Al arrancar, si existe un `run_id` previo sin cerrar,
  se carga para saltar descargas y reconstruir la frontera de las URLs conocidas.
- El estado `completado` se escribe al final; un `run_id` con estado distinto se reanuda.
- `--refresh` ignora el estado de descarga (sí respeta el checkpoint para no repetir
  dentro de la misma corrida).

### Bloqueo y seguridad

- `corpus_lock(data)` alrededor de toda la corrida: dos rastreos simultáneos no se pisan.
- `--ignore-robots` solo se acepta si todos los hosts son `localhost`/`127.0.0.1`; si no,
  el comando falla con uso inválido.
- Sin autenticación ni cookies: el rastreador es anónimo y se identifica con el
  `user-agent` del proyecto.

## Fases (olas de PR)

1. **Extracción compartida** — mover PDF/XML/JSON/CSV a `carvajal.extracting`, delegar
   desde `apps/api`, pruebas de paridad. Gate: `make check` verde y cargas web idénticas.
2. **Rastreo HTML** — `crawler.py` + `cli.py` + frontera + robots + dedupe + reportería,
   sin `--sync`. Gate: integración con servidor local, SC-001/002/003.
3. **Binarios** — PDF/CSV/XLSX + `write_bronze_bytes` + límites + informe por formato.
   Gate: SC-004.
4. **Externos y resiliencia** — cupo por host, 429/backoff, `nofollow`/`noindex`,
   `--refresh` y reanudación. Gate: SC-002/005.
5. **Evidencia y operación** — corrida real con `--depth 4 --max-pages 5000`, evidencia de
   que Postgres/Qdrant no cambiaron, sección en README/`CONSTITUTION.md` y decisión en §9.
   Gate: SC-006/007.

## Riesgos

| Riesgo | Mitigación |
|---|---|
| Los medios externos cambian de estructura o bloquean | Cupo por host, `robots.txt`, backoff y corrida incremental |
| 5.000 páginas exceden el tiempo | `--time-budget-minutes`, checkpoint y salida parcial con código 1 |
| PDF de 67 MB o extracción lenta | Límite por archivo, timeout de `pdftotext`, Bronze se conserva |
| Refactor de extracción rompe las cargas web | Fase 1 aislada con pruebas de paridad antes de tocar el rastreador |
| Duplicados por URL canónica mal normalizada | Tabla de casos en pruebas + dedupe por huella de texto |
| Disco o memoria | Límite de 5 GB por corrida y escritura en streaming por documento |

## Fuera de alcance (confirmado)

JS/headless, login, paywalls, OCR, rastreo distribuido y vectorización durante el rastreo.
