# Research: rastreo profundo

## Contexto medido

- Corpus del donante (2,2 GB): 1.595 HTML (456 MB), **550 PDF (1,63 GB)**,
  81 XML, 38 JSON y 149 BIN. El texto de los PDF es el **93 %** del texto útil
  (~39,1 M de 42,2 M caracteres). Cualquier solución que ignore PDF deja fuera casi todo.
- PDF mayor: 67 MB; la extracción con `pdftotext` ya tiene timeout de 20 s y tope de
  16 MB de texto en `apps/api/document_formats.py`.
- Con retardo de 1 s por host, 5.000 páginas son ≥ 83 min solo de espera; el presupuesto
  de 180 min incluye descarga y extracción.
- `WebClient` ya implementa `robots.txt`, `crawl-delay`, throttle por host y caché de
  robots; no hay que reescribirlo.
- `Registry`/`source_key_for_url` ya resuelve identidad estable de URL y `content_hash`
  detecta cambios de texto.

## Decisiones

### 1. El rastreador vive en el CLI, no en la interfaz

**Decisión**: `packages/core/src/carvajal/crawler.py` + subcomando `carvajal crawl`.

**Por qué**: es una operación larga (minutos u horas), con topes y reanudación; encaja
con las demás herramientas de terminal y no necesita sesión ni permisos web. Exponerlo en
Configuración obligaría a gestionar trabajos en segundo plano, que ya tienen su propio
diseño en la 047 y no son parte de esta capacidad.

**Alternativas descartadas**: botón en la UI (fuera de alcance), script suelto en
`scripts/` (no queda como capacidad del proyecto ni se prueba en CI).

### 2. BFS por niveles con topes, no rastreo «sin límite»

**Decisión**: cola FIFO por profundidad, `--depth` (4), `--max-pages` (5.000),
`--max-pages-per-host` (100), `--time-budget-minutes` (180) y presupuesto de bytes (5 GB).

**Por qué**: el sitio oficial y los medios enlazados pueden generar decenas de miles de
URLs (calendarios, paginaciones, etiquetas). Los topes mantienen la corrida acotada y
repetible, y el cupo por host evita arrasar un periódico.

**Alternativa descartada**: confiar solo en `robots.txt`; no acota el volumen ni el tiempo.

### 3. Hosts: propios y externos con cupo independiente

**Decisión**: los hosts de las semillas son «propios»; los externos se siguen si vienen de
una semilla de terceros o de un enlace descubierto dentro del alcance, con su propio
contador. Un enlace que sale de un host externo no se sigue salvo que esté en las semillas.

**Por qué**: permite incorporar notas de prensa sin abrir la puerta a rastrear el resto de
Internet. Los subdominios del grupo se autorizan explícitamente por dominio registrable.

**Alternativa descartada**: allowlist rígida de rutas por host; demasiado mantenimiento.

### 4. Extracción de binarios compartida en el paquete core

**Decisión**: mover `_pdf`, `_xml`, `_json`, `_jsonl` y `_tabular` a
`carvajal.extracting.extract_binary_markdown()`; `apps/api/document_formats.py` delega.
`_html` y `_text` se quedan en su sitio.

**Por qué**: el CLI no puede importar `apps.api` sin invertir la dependencia. Mover solo
los binarios evita tocar la extracción HTML de las cargas web, que tiene su propio
comportamiento y pruebas. La fase 1 va aislada con pruebas de paridad.

**Alternativa descartada**: duplicar la extracción en el crawler; dos implementaciones de
PDF divergen a la primera corrección.

### 5. XLSX con `openpyxl`, en el extra `capture`

**Decisión**: `openpyxl>=3.1` en `[project.optional-dependencies].capture`, import
perezoso con mensaje claro si falta.

**Por qué**: es la librería más madura para `.xlsx`, pure Python y su costo (~250 KB) no
toca la imagen web si no se usa en carga. `pandas`/`calamine` añaden peso y otro modelo de
datos para una necesidad simple: texto por hoja.

**Alternativa descartada**: `python-calamine` (más rápido, dependencia binaria) o `pandas`
(decenas de MB para leer dos hojas).

### 6. Reanudación con checkpoint JSONL, además del registro

**Decisión**: `data/runtime/crawl/<run_id>.jsonl` con una línea por URL procesada; el
registro de Gold sigue siendo la fuente de identidad.

**Por qué**: el registro dice qué documentos existen, pero no qué URLs se visitaron sin
producir documento (robots, 404, noindex, duplicados). Sin ese estado, reanudar vuelve a
descargar medio sitio. El JSONL es append-only, barato y legible.

**Alternativa descartada**: rehacer el BFS desde las semillas confiando en el registro;
correcto pero cuesta una descarga por URL conocida en cada reanudación.

### 7. Secuencial en la v1

**Decisión**: un documento a la vez, con el retardo por host que ya aplica `WebClient`.

**Por qué**: el cuello de botella es la cortesía con cada host, no la CPU. Paralelizar por
host añade coordinación de retardos, reintentos y escritura concurrente del índice; se
puede añadir después con `--workers` sin cambiar el contrato.

**Alternativa descartada**: `asyncio` desde el inicio; el resto del pipeline es síncrono y
mezclar modelos complica las pruebas.

### 8. Bronze binario con los bytes originales

**Decisión**: `write_bronze_bytes(path, data)` con temporal + `replace`; extensión real
(`.pdf`, `.csv`, `.xlsx`).

**Por qué**: el original es la evidencia y permite reprocesar sin volver a descargar. La
escritura atómica es la misma razón que en Gold: un lector puede estar sincronizando.

**Alternativa descartada**: guardar solo el texto y descartar el binario; se pierde la
posibilidad de re-extraer con un mejor parser.

### 9. Sin vectorización por defecto

**Decisión**: `crawl` no escribe en PostgreSQL ni Qdrant; `--sync` es explícito.

**Por qué**: una corrida de 5.000 páginas puede tardar horas en embeddings. El valor
pedido es «dejarlo listo para vectorizar», no vectorizar. Es una diferencia deliberada con
`capture` (que sincroniza salvo `--solo-gold`) y se documenta en la ayuda del comando.

**Alternativa descartada**: replicar `--solo-gold`; invertir el valor por defecto pone el
camino peligroso como opción y el seguro como excepción.

### 10. Sitemap como acelerador opcional

**Decisión**: `--sitemap` añade las URLs de `sitemap.xml` al nivel 0.

**Por qué**: en sitios grandes descubre en una petición lo que el BFS tardaría cientos; el
BFS sigue siendo el mecanismo general y el que respeta profundidad y enlaces.

**Alternativa descartada**: depender solo del sitemap; no todos los medios lo publican ni
incluye enlaces externos.

## Preguntas abiertas

- Ninguna bloqueante. La interfaz web para lanzar el rastreo queda fuera y se decidirá en
  una spec de UI si el owner la pide.
