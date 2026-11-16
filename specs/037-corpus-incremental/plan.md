# Implementation Plan: Corpus incremental, deduplicado y sincronizado

**Branch**: `037-corpus-incremental` | **Date**: 2026-11-16 | **Spec**: [`spec.md`](./spec.md)
**Input**: Feature specification from `/specs/037-corpus-incremental/spec.md`

## Summary

Cerrar el ciclo del corpus: identidad estable por fuente, deduplicación por huella del original
antes de Bronze, actualización en sitio con versiones, un único servicio de sincronización
(PostgreSQL + Qdrant) usado por todos los caminos de entrada, indexación incremental por huella
de texto y recuperación con prioridad núcleo → capa extendida. Se retira la ruta heredada
`/ask` y el adaptador pgvector, que hoy no tienen vectores.

La spec 035 dejó la base (formatos, consola de carpetas, `merge_index` con `flock` y escritura
atómica). Esta capacidad no la reescribe: la usa y la completa.

## Technical Context

| Aspecto | Decisión | Evidencia |
|---|---|---|
| Lenguaje | Python 3.13, paquete en `packages/core` (src-layout) | `pyproject.toml` |
| Entorno | `uv` + `uv.lock`; sin dependencias nuevas | `CONSTITUTION.md` §3 |
| Identidad | `source_key` canónico → `slug` inmutable; el hash no forma parte del slug nuevo | `spec.md` FR-001 |
| Registro | Los `index.jsonl` por área (`data/gold`, `data/external/*/gold`) se amplían con `source_key`, `raw_hash`, `layer` y `updated_at`; son la fuente de verdad en disco | `carvajal.gold.merge_index` |
| Huellas | `raw_hash` = SHA-256 de los bytes originales; `content_hash` = SHA-256 del texto normalizado (ya existe) | `carvajal.domain.rules.content_hash` |
| Procedencia | `Document` y el payload de Qdrant llevan `origin`, `category` y `entity` leídos del front matter; no se persisten en PostgreSQL | `import_corpus.load_document`, `qdrant_store.payload_for` |
| Dedupe | Dos compuertas: `raw_hash` antes de Bronze (archivos); `content_hash` tras extraer (todo, incluido scraping) | `spec.md` FR-003, FR-005, FR-006 |
| Actualización | Misma `source_key` + texto distinto → mismo `slug`, `migrate_gold` archiva versión | `document_versions`, `migrate_gold` |
| Sincronización | Servicio único en `packages/core/src/carvajal/application/sync.py`; usado por web, CLI y consola | `spec.md` FR-007, FR-008 |
| Índice del chat | Una colección Qdrant con `layer` en el payload (ya existe) y poda por capa | `adapters/vector/qdrant_store.py` |
| Prioridad | Envoltura del puerto `VectorStore` que busca núcleo y completa con extendida | `application/ports.py`, `agent/graph.py` |
| Consola | Comando nuevo en `packages/core/src/carvajal/cli.py`; la consola de carpetas de 035 se mantiene | `apps/api/ingest_cli.py` |
| Concurrencia | `flock` sobre `data/.corpus.lock` para escrituras y sincronización; índice del corpus atómico | `gold.merge_index` |
| Persistencia | PostgreSQL para texto/FAQ/explorador; Qdrant para recuperación del chat | `apps/api/runtime.py` |
| Pruebas | pytest con dobles inyectados; integración para Postgres/Qdrant | `tests/` |

**Alternativas descartadas**:

- **Hash como identidad**: impide versionar y actualizar; ya demostró duplicar archivos
  modificados.
- **Registro solo en PostgreSQL**: rompe `--solo-extraer` y la consola sin base.
- **Dedupe solo por texto**: gasta extracción en copias exactas, que es justo lo que se quiere
  evitar.
- **Poda global en cada corrida**: con indexación incremental borraría los documentos no
  tocados; la poda se limita a las capas sincronizadas y a los documentos ausentes.
- **Mezclar capas en el ranking**: diluye las respuestas oficiales; se prioriza el núcleo.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Puerta | Cómo se cumple |
|---|---|
| §2 Alcance: corpus, capas e indexación | La capacidad completa la ingesta y la sincronización; no añade fuentes nuevas ni cambia la taxonomía. |
| §2 Capa extendida: solo recuperación y citación | Se indexa para recuperación con `layer`, se cita con procedencia y la FAQ sigue en el núcleo. |
| §3 Stack: sin dependencias nuevas | Solo biblioteca estándar, Qdrant y PostgreSQL ya presentes. |
| §4 Estructura | Dominio y orquestación en `packages/core`; web y consola en `apps/api`. |
| §5 Convenciones | Ruff, 100 columnas, comillas dobles, UI en español. |
| §6 Calidad | Cada FR con prueba; `make check` antes de abrir PR. |
| §6.1 Imagen | Sin cambios en el Dockerfile; el comando nuevo vive en el paquete ya instalado. |
| §9 Decisiones | Se registran identidad estable, doble huella, sincronización única, prioridad de capas y retiro de pgvector. |
| §10 Pendientes | El rastreo periódico queda como pendiente; esta spec entrega sus contratos. |

**Resultado**: pasa con la enmienda de §2, §9 y §10 en la PR de la spec.

## Project Structure

### Documentation (this feature)

```text
specs/037-corpus-incremental/
├── spec.md              # Contrato funcional
├── plan.md              # Este archivo
├── tasks.md             # Desglose por historia
└── checklists/
    └── requirements.md  # Validación de la spec
```

No hay `research.md`, `data-model.md` ni `contracts/`: no se añaden entidades de dominio nuevas
(el registro es un artefacto del corpus, no una tabla) ni rutas HTTP nuevas; la sincronización
es una interfaz de línea de órdenes descrita aquí.

### Source Code (repository root)

```text
packages/core/src/carvajal/
├── identity.py                 # source_key y asignación de slug (nuevo)
├── registry.py                 # lectura/escritura del registro sobre los index.jsonl (nuevo)
├── application/sync.py         # sync_corpus: disco → PostgreSQL → Qdrant (nuevo)
├── application/retrieval.py    # búsqueda con prioridad de capas (nuevo) o wrapper del puerto
├── application/ask.py          # Source con origin/category/entity
├── domain/entities.py          # Document con la procedencia extendida
├── adapters/storage/import_corpus.py  # lee origin/category/entity del front matter
├── gold.py                     # merge_index admite los campos nuevos
├── cli.py                      # `sync`, `index-qdrant --layer` y `--solo-gold` en capture
├── pipeline.py                 # compuerta de texto sin cambios y registro
├── indexing/qdrant.py          # varios directorios/capas, incremental y borrado por documento
└── adapters/vector/qdrant_store.py  # delete_by_document, payload y poda por capa

apps/api/
├── ingestion.py                # dedupe raw + identidad + sync de núcleo
├── ingest_cli.py               # source_key por ruta relativa y dedupe
├── web_capture.py              # usa el sync del núcleo
├── main.py                     # retira /ask
└── views/*                     # sin cambios funcionales

migrations/versions/
└── 0006_drop_chunk_embeddings.py  # retira la columna de vectores

tests/
├── test_identity.py            # identidad, colisiones y estabilidad (nuevo)
├── test_registry.py            # registro y dedupe por huella (nuevo)
├── test_sync.py                # sincronización, idempotencia y poda (nuevo)
├── test_ingestion.py           # dedupe antes de Bronze y actualización
├── test_pipeline.py            # captura sin cambios
├── test_retrieval.py           # prioridad núcleo → extendida (nuevo)
├── test_cli.py                 # `sync`, `capture` y banderas
├── test_api_m2.py              # sin /ask
└── test_vector_store.py        # retira PgVectorStore
```

**Structure Decision**: la orquestación de sincronización vive en `packages/core` porque la
necesitan la web y la consola; `apps/api` queda como interfaz. El registro se apoya en los
`index.jsonl` existentes para no añadir un artefacto ni una tabla.

## Phase 0: Investigación

- Confirmar que Qdrant permite borrar por filtro `document_id` y podar por `layer` sin recrear
  la colección (cliente `qdrant-client` 1.15 ya instalado).
- Verificar que `migrate_gold` archiva una versión sólo cuando cambia el cuerpo y no duplica
  fragmentos (ya probado en `tests/test_import_corpus.py`).
- Confirmar que la columna `chunks.embedding` está vacía en producción antes de eliminarla
  (`select count(*) ... where embedding is not null`).
- Definir la normalización de `source_key`: URL sin esquema `www.` ni barra final; ruta relativa
  con `/`, sin `./`; nombre de archivo para subidas web.

**Salida**: decisiones en este archivo; sin artefacto aparte.

## Phase 1: Diseño

### Contrato del registro

Cada entrada de `index.jsonl` conserva sus campos actuales y añade:

```json
{
  "slug": "estados-financieros-2025",
  "source_key": "file:estados-financieros/2025/informe.pdf",
  "raw_hash": "sha256 de los bytes originales",
  "hash": "sha256 del texto normalizado",
  "layer": "core",
  "source_type": "oficial",
  "updated_at": "2026-11-16T12:00:00+00:00"
}
```

`carvajal.registry` carga y fusiona los índices del corpus de todas las áreas; `merge_index`
sigue siendo la única escritura (atómica y bloqueada). El registro es la fuente para deduplicar
y para decidir si un documento cambió.

### Contrato de identidad

- `source_key` = `url:<normalizada>`, `file:<basename de la raíz>/<ruta relativa>` o
  `upload:<nombre>`. Para un archivo suelto, `file:<nombre>`.
- `slug` se asigna una vez desde el nombre legible; si ya existe para otra fuente, se añade un
  sufijo numérico. No se renombra un documento existente.
- Un documento legado sin `source_key` se adopta con la URL de su front matter o con el nombre
  recuperable de su slug; `raw_hash` se calcula desde Bronze si existe y si no queda vacío. Si
  el nombre recuperado no es único entre las subidas legadas, se usa `legacy:<slug>`.

### Contrato de procedencia

- `Document` gana `origin`, `category` y `entity` opcionales, leídos del front matter por
  `load_document`; los documentos del núcleo los dejan vacíos.
- `index_gold` los pasa al payload de Qdrant y `Source` los expone en la cita. Un documento sin
  ellos no falla: la cita los omite.
- No se persisten en PostgreSQL; el explorador muestra `layer` y `source_type`.

### Contrato de deduplicación

0. Antes de escribir, validar que el lote no repita `source_key` con contenido distinto; el
   segundo se reporta y se descarta, para no sobrescribir el primero en silencio.
1. Calcular `raw_hash` de los bytes antes de escribir Bronze.
2. Buscar en el registro: si existe y la identidad es la misma, comparar `content_hash`; si
   coincide, saltar; si difiere, actualizar.
3. Si existe bajo otra identidad, reportar duplicado y saltar (sin escribir capas). La
   comparación es por bytes, así que cubre copias y movimientos; no se re-apunta la fuente.
4. Tras extraer, recalcular `content_hash`; si coincide con la versión vigente, saltar.
5. Buscar `content_hash` en el registro: si pertenece a otra identidad, reportar duplicado
   semántico y saltar.
6. En capturas, el paso 2 usa `content_hash` (el HTML cambia aunque el texto no).

### Contrato de sincronización

`sync_corpus(layers, prune=False, only=None, verify=False)`:

1. Cargar el registro y los documentos de cada directorio de capa.
2. Comparar `content_hash` con PostgreSQL: `migrate_gold` upserta y archiva versiones; con
   `prune` elimina los documentos de la capa que ya no están en disco.
3. Para Qdrant, indexar sólo documentos nuevos o cambiados: borrar sus puntos por
   `document_id` y volver a insertarlos. Con `prune`, eliminar puntos de la capa cuyo
   `document_id` ya no existe.
4. Informar `nuevos`, `actualizados`, `sin_cambios`, `duplicados`, `podados`, `errores`.
5. Cualquier fallo por documento no aborta el resto; el siguiente `sync` converge.
6. Con `verify=True` no escribe: compara huellas y conteos entre Gold, PostgreSQL y Qdrant y
   devuelve las diferencias.

La sincronización y las escrituras comparten `data/.corpus.lock`; dos sincronizaciones se
serializan (la segunda espera) y el índice del corpus (JSONL) conserva su bloqueo interno.

### Contrato de recuperación con prioridad

Un envoltorio del puerto `VectorStore` busca primero en `layer=core`; si devuelve menos de
`limit` resultados o el mejor puntaje queda por debajo de `RAG_MIN_SCORE`, completa con
`layer=extended`. Conserva `source_type`, `origin`, `category`, `entity` y `layer` en la cita.
La FAQ no cambia: lee `layer=core` de PostgreSQL.

### Contrato de la consola

- `carvajal sync [--layer core|extended|all] [--prune] [--verify]`.
- `carvajal capture` termina en `sync_corpus` con los slugs capturados; `--solo-gold` se
  detiene en Gold para pruebas o trabajo sin base.
- `carvajal index-qdrant [--layer core|extended|all]` indexa la capa elegida en una sola
  corrida; `make index` e `make index-eval` usan `--layer all`.
- `python -m apps.api.ingest_cli` mantiene sus banderas y gana dedupe por `raw_hash` y
  `source_key` por ruta relativa.

### Migración del corpus existente

1. `carvajal sync --adopt` completa `source_key` y `raw_hash` en el registro sin reindexar.
2. `carvajal sync --prune` reconcilia ambos almacenes.
3. Los slugs actuales se conservan; no hay renombres ni pérdida de versiones.

## Phase 2: Riesgos

| Riesgo | Mitigación |
|---|---|
| Adopción incompleta deja dos identidades para el mismo documento | Prueba de adopción con los índices del corpus reales; `--verify` reporta documentos sin `source_key`. |
| Borrado por `document_id` elimina puntos de otro documento | El filtro usa el `document_id` exacto del payload; prueba con dos documentos que comparten título. |
| Poda global borra lo no tocado | La poda se limita a la capa sincronizada y al conjunto válido de esa corrida. |
| Indexación incremental deja fragmentos viejos | Borrar por documento antes de insertar; prueba de documento acortado. |
| Prioridad mal ajustada esconde respuestas del núcleo | Umbral y límite configurables; evaluación antes/después con `make index-eval`. |
| Retirar pgvector rompe `/ask` o pruebas heredadas | Se elimina la ruta, el adaptador y sus pruebas en el mismo cambio; migración reversible. |
| Fallo a mitad de sincronización | Gold es la fuente; el registro y el índice del corpus se escriben al final; prueba de reejecución. |
| Duplicado semántico falso positivo (misma nota sindicada en dos fuentes) | Se reporta con el documento existente; el operador decide renombrar o conservar una sola copia. |
| Colisión de nombres en una carga web | El segundo archivo del lote se rechaza con su nombre; no se sobrescribe el primero. |
| Documento extendido sin `origin`, `category` o `entity` | La cita los omite; el indexador no falla y el payload queda con cadenas vacías. |

## Complexity Tracking

| Decisión | Por qué | Alternativa rechazada |
|---|---|---|
| Registro sobre los `index.jsonl` | Ya son la lista publicada del corpus y su escritura es atómica y bloqueada | Un archivo nuevo o una tabla: más artefactos que sincronizar |
| Doble huella | El SHA del archivo evita extracción; el del texto detecta cambios reales y re-codificaciones | Una sola huella: o gastas extracción o duplicas |
| Envoltorio de recuperación | La prioridad es política de producto y no debe vivir en el adaptador de Qdrant | Filtro plano: diluye el núcleo |
| `sync` en el núcleo | Web y consola necesitan la misma garantía; dos implementaciones se desincronizan | Duplicar la lógica en `apps/api` |
