# Implementation Plan: Corpus: Gold fuera del adaptador SQL y verificación en un solo recorrido

**Branch**: `perf/045-corpus-rendimiento` | **Date**: 2026-11-20 | **Spec**: [`spec.md`](./spec.md)
**Input**: Feature specification from `/specs/045-corpus-rendimiento/spec.md`

## Summary

Tres movimientos, en este orden, cada uno con su medición:

1. **Gold al dominio** (`carvajal/gold_documents.py`): `load_document`, `split_document`,
   `load_documents` y `GoldImportError` salen de `adapters/storage/import_corpus.py`. El adaptador
   SQL y `indexing/qdrant.py` importan del módulo nuevo; ninguno redefine lectura ni fragmentación.
2. **Instantánea del índice** (`SyncIndex.snapshot`): el adaptador Qdrant obtiene en un solo
   *scroll* por capa las huellas publicadas y los IDs presentes, con la misma regla de
   completitud. `sync --verify` usa esa instantánea para el mismo diagnóstico de ausente /
   incompleto / modificado / sobrante, sin escrituras.
3. **Lote de capa Gold** (`GoldLayer`): `sync_corpus` lee los Markdown de una capa una vez y
   entrega ese lote a los dos adaptadores. `migrate_gold`/`index_gold` conservan su API de
   lectura para la CLI, pero la sincronización usa las variantes que aceptan el lote ya leído.

No cambian formatos, fragmentos, IDs, huellas, payloads, citas ni orden de recuperación. No se
toca el modelo de embeddings, no hay migraciones y no se reindexa producción.

## Technical Context

| Aspecto | Decisión | Evidencia |
|---|---|---|
| Lenguaje | Python 3.13, paquete `packages/core` (src-layout) | `pyproject.toml` |
| Entorno | `uv` + `uv.lock`; sin dependencias nuevas | `CONSTITUTION.md` §3 |
| Dominio Gold | `carvajal/gold_documents.py` (nuevo): parser, slug, `captured_at`, `Document`, `split_document`, `load_documents` | spec.md FR-004 |
| Adaptador SQL | `import_corpus.py` conserva `migrate_gold`/`prune_layer`/`GoldImportReport`; delega la lectura en el dominio y gana `import_documents(session, documents, ...)` | spec.md FR-006 |
| Índice | `indexing/qdrant.py` gana `documents=` para indexar un lote ya leído; conserva la vía por directorio de la CLI | spec.md FR-006 |
| Lectura única de verificación | `QdrantVectorStore.snapshot(layer) -> IndexSnapshot`; `document_hashes` y `document_ids` se derivan de él | spec.md FR-001, FR-002 |
| Diagnóstico | `_index_differences` recibe la instantánea; mismos mensajes y orden `sorted` | spec.md FR-002 |
| Fallos | Por capa y por almacén, como hoy: `errors` no aborta el resto; `verify` no escribe | spec.md FR-008 |
| Medición | `scripts/medir_corpus_045.py` + pruebas instrumentadas; informe en `mediciones.md` | spec.md FR-009, FR-010 |
| Pruebas | pytest (unitarias y de integración), sin nuevos servicios | `CONSTITUTION.md` §6 |

**Alternativas descartadas**:

- **Dejar `load_document` en el adaptador y reexportarlo**: el índice seguiría dependiendo de
  SQLAlchemy y la definición seguiría teniendo un solo dueño aparente. Se mueve de verdad y se
  actualizan los consumidores.
- **Un segundo scroll “ligero” para los IDs**: el problema es exactamente tener dos recorridos.
  Se resuelve en el adaptador con una sola pasada que acumula huellas e IDs.
- **Cachear Gold entre corridas**: rompe la frescura (Gold es la fuente) y añade un artefacto que
  sincronizar. La relectura a eliminar es la de una misma corrida, no la de corridas distintas.
- **Batch de embeddings**: cambiaría la interfaz del embedder y el número de peticiones, sin
  necesidad operativa demostrada (la reindexación de producción está fuera de alcance). Se mide y
  se documenta; no se cambia.
- **Snapshot también para el lado PostgreSQL**: lectura de una consulta + conteo; el costo es una
  consulta y no un recorrido del corpus. Fuera de alcance para no ampliar la superficie.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Puerta | Cómo se cumple |
|---|---|
| §2 Alcance: corpus, capas e indexación | La capacidad reorganiza la sincronización existente; no añade fuentes, capas, formatos ni taxonomía. |
| §2 Capa extendida | Se conserva la poda y la indexación por capa; el diagnóstico extendido no cambia. |
| §3 Stack: sin dependencias nuevas | Solo se mueven funciones dentro del paquete; no cambian `pyproject.toml` ni `uv.lock`. |
| §4 Estructura | Dominio en `packages/core/src/carvajal` (módulo raíz, junto a `gold.py`/`identity.py`); adaptadores en `adapters/`; orquestación en `application/sync.py`. |
| §5 Convenciones | Ruff, 100 columnas, comillas dobles, docstrings y comentarios en español. |
| §6 Calidad | Cada FR con prueba; `make check` + `make test-integration` antes de abrir PR; medición reproducible. |
| §6.1 Imagen | Sin cambios en Dockerfile ni en el arranque. |
| §9 Decisiones | Se añade la fila de la decisión (Gold en dominio, instantánea del índice y lote por capa) en la misma PR. |
| §10 Pendientes | Sin cambios en pendientes; la reindexación de producción sigue fuera de alcance. |

**Resultado**: pasa sin violaciones; no hay `Complexity Tracking` que justificar.

## Project Structure

### Documentation (this feature)

```text
specs/045-corpus-rendimiento/
├── spec.md                 # Contrato funcional
├── plan.md                 # Este archivo
├── tasks.md                # Desglose por historia
├── analysis.md             # Análisis de consistencia (speckit-analyze)
├── mediciones.md           # Antes/después con comando, entorno y conteos
└── checklists/
    └── requirements.md     # Validación de la spec
```

No hay `research.md`, `data-model.md`, `contracts/` ni `quickstart.md`: no se añaden entidades,
rutas HTTP ni contratos externos; el contrato interno son los puertos de `application/sync.py` y
las firmas descritas aquí.

### Source Code (repository root)

```text
packages/core/src/carvajal/
├── gold_documents.py               # NUEVO: load_document, split_document, load_documents, GoldImportError
├── application/sync.py             # GoldLayer, IndexSnapshot, SyncIndex.snapshot, lectura única por capa
├── adapters/storage/import_corpus.py   # conserva import/prune; importa del dominio; import_documents
├── adapters/storage/sync_postgres.py   # import_gold(gold: GoldLayer) sin releer Gold
├── adapters/vector/sync_qdrant.py      # snapshot() e index_gold(gold: GoldLayer)
├── adapters/vector/qdrant_store.py     # snapshot() de una pasada; document_hashes/document_ids delegan
├── indexing/qdrant.py                  # load/split desde el dominio; acepta documents= ya leído
└── cli.py                         # sin cambios de contrato; sigue usando sync_corpus

tests/
├── test_gold_documents.py          # NUEVO: caracterización de la lectura/fragmentación en el dominio
├── test_sync.py                    # contadores de lecturas de Gold y scrolls; invariantes del diagnóstico
├── test_sync_adapters.py           # adaptadores con el lote y la instantánea
├── test_qdrant_store.py            # snapshot en una pasada y equivalencia con hashes/ids
└── test_import_corpus.py           # importación con documentos ya leídos

scripts/medir_corpus_045.py         # NUEVO: medición reproducible antes/después
```

**Structure Decision**: el módulo `carvajal/gold_documents.py` vive en la raíz del paquete, junto a
`gold.py`, `identity.py` y `registry.py`, que ya son módulos del corpus compartidos por CLI,
adaptadores y app. `domain/` se reserva para entidades y reglas sin IO; la lectura de archivos es
IO y no debe entrar allí. La orquestación sigue en `application/sync.py`, que es el único punto que
lee Gold para una sincronización.

## Phase 0: Investigación

- Confirmar que `qdrant-client` 1.15 devuelve `id` y `payload` en el mismo `scroll` con
  `with_payload=True, with_vectors=False` (ya lo hace `_payloads`); la instantánea solo necesita
  acumular ambos.
- Confirmar que el orden de los puntos de un scroll no influye en las huellas (la huella de cada
  documento se publica solo cuando `seen == chunks_total`, sin depender del orden).
- Medir la línea base: pasadas completas sobre Gold por capa en sync con poda (no lecturas de
  archivo sueltas), pasadas sobre el índice por capa en `--verify` (no páginas del cliente),
  llamadas al embedder por fragmento y tiempos con el corpus local.

**Salida**: números en `mediciones.md` (sección «Antes»), con comando y entorno.

## Phase 1: Diseño

### Contrato del módulo de dominio Gold

```python
# carvajal/gold_documents.py
class GoldImportError(ValueError): ...  # nombre conservado: la CLI ya lo captura

def load_document(path: Path, *, layer: str) -> Document
def split_document(document: Document) -> list[Chunk]
def load_documents(directory: Path, *, layer: str) -> list[Document]  # valida duplicados
```

`import_corpus.py` importa estas funciones y conserva `GoldImportError` en su espacio de nombres
para no romper consumidores externos, pero la implementación vive en el dominio.

### Contrato del lote de capa

`application/sync.py`:

```python
@dataclass(frozen=True)
class GoldLayer:
    layer: CorpusLayer
    documents: tuple[Document, ...]
    hashes: Mapping[str, str]        # huella base (PostgreSQL)
    index_hashes: Mapping[str, str]  # huella con procedencia (Qdrant)

def read_gold_layer(layer: CorpusLayer) -> GoldLayer
def gold_hashes(layer) -> dict[str, str]        # compat: read_gold_layer(layer).hashes
def gold_index_hashes(layer) -> dict[str, str]  # compat: read_gold_layer(layer).index_hashes
```

`_sync` llama `read_gold_layer` una vez por capa, filtra por `only` en memoria y pasa el lote a los
adaptadores. Un error de lectura sigue siendo un error por capa que no toca almacenes.

### Contrato de los puertos

```python
class SyncDatabase(Protocol):
    def hashes(self, layer: str) -> Mapping[str, str]: ...
    def import_gold(
        self, gold: GoldLayer, *, only: set[str] | None, prune: bool
    ) -> tuple[int, int, int]: ...
    def count(self, layer: str) -> int: ...


@dataclass(frozen=True)
class IndexSnapshot:
    hashes: Mapping[str, str]
    document_ids: Collection[str]


class SyncIndex(Protocol):
    def hashes(self, layer: str) -> Mapping[str, str]: ...  # camino de escritura
    def snapshot(self, layer: str) -> IndexSnapshot: ...  # camino de verificación
    def document_ids(self, layer: str) -> set[str]: ...  # compat/poda
    def index_gold(self, gold: GoldLayer, *, only, prune) -> tuple[int, int]: ...
    def count(self, layer: str) -> int: ...
```

`verify` usa `snapshot`; la escritura usa `hashes` (un solo recorrido porque solo necesita
huellas); la poda del índice usa `document_ids` a través de `prune_layer`.

### Contrato de importación e indexación con lote

- `import_corpus.import_documents(session, documents, *, layer, prune, only)` escribe documentos y
  fragmentos ya cargados y poda por `keep` de la capa. `migrate_gold` = `load_documents` +
  `import_documents` (misma API para la CLI).
- `index_gold(..., documents=...)` usa el lote ya leído, conserva `gold_dirs` para fichas,
  capas de poda y validación de áreas. La vía actual por directorios sigue intacta para la CLI.

### Contrato de la instantánea del índice

`QdrantVectorStore.snapshot(layer)` recorre los payloads una vez —una invocación de `_payloads`,
con las páginas que el cliente necesite— y acumula:

- `ids`: todos los `document_id` con puntos (incluye incompletos);
- `hashes`: huella solo de los documentos con `seen == chunks_total`.

`document_hashes` y `document_ids` delegan en `snapshot` para que no existan dos definiciones de
completitud. `verify` compara con `_index_differences` sin cambios: primero ausente/sobrante,
después incompleto y por último huella distinta.

## Phase 2: Riesgos

| Riesgo | Mitigación |
|---|---|
| Mover `load_document` cambia el módulo que la CLI captura como error | `GoldImportError` sigue accesible desde `import_corpus`; prueba de la CLI y de la ruta de error. |
| El lote reutilizado altera la selección por `only` o la poda | Pruebas de sync con `only` y con poda multi-fuente; comparar conteos con los actuales. |
| El snapshot cambia el diagnóstico | Pruebas de ausente/sobrante/incompleto/modificado y comparación textual con la implementación anterior. |
| Dobles de prueba desactualizados | Actualización de fakes al contrato nuevo; los tests de caracterización fijan el comportamiento antes del cambio. |
| Medición sobre la base/índice de desarrollo | La medición no reindexa producción; usa la colección local ya existente y declara el entorno. |
| Relecturas eliminadas ocultan una validación | La lectura única conserva las validaciones de slug y duplicados; pruebas de error. |
