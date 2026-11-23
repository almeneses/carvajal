# Feature Specification: Ingesta multiformato de archivos locales

> **Revisión 2026-11-22:** la carga web es una consola de operador. Exige
> `OPERATOR_TOKEN` y CSRF, no se enlaza desde la navegación y deja el lote en
> Gold para revisión; la publicación se hace con `carvajal sync`. Los
> escenarios de «visitante» se conservan como registro histórico. Ver
> [`docs/seguridad-operacion-corpus.md`](../../docs/seguridad-operacion-corpus.md).

**Feature Branch**: `035-ingesta-multiformato`
**Created**: 2026-11-16
**Status**: Implemented (2026-11-16)
**Input**: User description: "La pestaña Configuración y la consola deben admitir PDF, XML, JSON, JSONL, CSV y TSV además de HTML, Markdown y TXT; los PDF conservan el número de página y avisan cuando requieren OCR, y las cargas concurrentes no pierden entradas del índice."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Cargar archivos de datos desde Configuración (Priority: P1)

Un visitante entra en **Configuración → Alimenta el corpus** y sube archivos XML, JSON,
JSONL, CSV o TSV junto a los HTML, Markdown y TXT que ya admitía. El formulario anuncia
exactamente los formatos que el backend acepta y, después de la carga, la pantalla
informa cuántos archivos, documentos, fragmentos y puntos se actualizaron. Cada original
se conserva íntegro y su contenido queda citable por el asistente.

**Why this priority**: es la vía principal para incorporar datos que el sitio no publica
en HTML sin tocar la terminal; sin ella la capacidad no existe para el visitante.

**Independent Test**: subir un XML, un JSON, un JSONL, un CSV y un TSV desde el
formulario y comprobar que los cinco llegan a Gold y que el informe no reporta errores.

**Acceptance Scenarios**:

1. **Given** el formulario de carga, **When** se seleccionan archivos `.xml`, `.json`,
   `.jsonl`, `.csv`, `.tsv`, `.pdf`, `.md`, `.txt`, `.html` o `.markdown`, **Then** el
   selector los ofrece y la ayuda enumera la misma lista que valida el backend.
2. **Given** un lote con archivos válidos, **When** se envía, **Then** cada archivo se
   guarda en Bronze, su texto extraído en Silver y su ficha en Gold; la carga completa
   actualiza además PostgreSQL y Qdrant.
3. **Given** un lote en el que un JSON está mal formado, **When** se envía, **Then** ese
   archivo produce un error con su nombre y los demás terminan en Gold.
4. **Given** un archivo con extensión ajena a la lista, **When** se envía, **Then** se
   rechaza con «formato no admitido» y no se escribe nada suyo en las capas.

---

### User Story 2 - Extraer PDF con número de página y aviso de OCR (Priority: P1)

Un visitante o un operador carga un informe anual en PDF. El sistema extrae la capa de
texto de cada página, conserva el número de página en el documento Gold y, si el PDF no
tiene texto extraíble, avisa de que requiere OCR sin bloquear el resto del lote.

**Why this priority**: los informes y memorias del corpus viven en PDF; sin página el
documento no es citable con precisión y sin aviso el operador no entiende el fallo.

**Independent Test**: cargar un PDF de varias páginas con texto y comprobar las
secciones «Página N» en Gold; cargar un PDF escaneado y comprobar el aviso de OCR.

**Acceptance Scenarios**:

1. **Given** un PDF con capa de texto, **When** se procesa, **Then** el Markdown de Gold
   separa el contenido por página con el número de página y omite las páginas vacías.
2. **Given** un PDF escaneado sin capa de texto, **When** se procesa, **Then** el informe
   lo reporta como archivo que puede requerir OCR y el resto del lote continúa.
3. **Given** un entorno sin el extractor de PDF instalado, **When** se procesa un PDF,
   **Then** el error nombra la dependencia que falta y sugiere cómo instalarla.
4. **Given** un PDF dañado, protegido o que excede el tiempo o el tamaño de extracción,
   **When** se procesa, **Then** se reporta como error de ese archivo y el lote sigue.

---

### User Story 3 - Recorrer carpetas grandes desde la consola (Priority: P2)

Un operador con un corpus de PDF y datos descargado en disco ejecuta una orden que
recorre carpetas y subcarpetas, procesa los archivos admitidos por lotes y resume el
resultado. Puede detenerse en Gold para revisar la extracción antes de tocar la base y
el índice vectorial.

**Why this priority**: la web limita a 5 MB por archivo y no sirve para un corpus real;
la consola es el camino para volúmenes grandes sin partir el lote a mano.

**Independent Test**: apuntar la consola a una carpeta con subcarpetas, archivos
admitidos y un archivo ajeno, y comprobar el resumen, el código de salida y los
documentos creados en Gold.

**Acceptance Scenarios**:

1. **Given** una carpeta con subcarpetas y archivos admitidos y ajenos, **When** se
   ejecuta la consola sobre ella, **Then** se procesan los admitidos, los ajenos se
   ignoran y el resumen informa Bronze, Silver, Gold, PostgreSQL y Qdrant.
2. **Given** la opción de detenerse en Gold, **When** se ejecuta, **Then** no se toca
   PostgreSQL ni Qdrant y los documentos quedan disponibles en Gold.
3. **Given** un archivo que supera el límite de la consola o no se puede leer, **When**
   se procesa, **Then** se reporta, el resto continúa y el código de salida es distinto
   de cero.
4. **Given** una ruta sin archivos admitidos, **When** se ejecuta, **Then** se informa
   y el código de salida es distinto de cero.

---

### User Story 4 - Cargas y capturas simultáneas conservan el índice (Priority: P2)

Dos cargas lanzadas a la vez, o una carga mientras corre una captura web, actualizan el
índice del corpus sin pisarse: ninguna entrada existente desaparece y el índice nunca
queda a medio escribir.

**Why this priority**: el índice es la lista de documentos que el asistente puede citar;
perderlo en silencio corrompe el corpus y es invisible hasta que falla una respuesta.

**Independent Test**: lanzar varias actualizaciones concurrentes de documentos distintos
y comprobar que el índice final contiene todas las entradas y ninguna línea corrupta.

**Acceptance Scenarios**:

1. **Given** dos cargas con documentos distintos, **When** terminan a la vez, **Then**
   el índice conserva las entradas de ambas.
2. **Given** una captura web y una carga simultáneas, **When** ambas escriben el índice,
   **Then** se conservan las entradas de las dos y las de capturas anteriores.
3. **Given** un índice con entradas cuyo documento ya no existe en Gold, **When** una
   captura actualiza el índice, **Then** esas entradas se descartan y las vigentes se
   conservan.
4. **Given** una interrupción durante la escritura, **When** se vuelve a leer el índice,
   **Then** se lee la versión anterior completa y nunca un archivo truncado.

---

### Edge Cases

- PDF escaneado sin capa de texto: se avisa de OCR y el lote continúa.
- PDF con páginas vacías entre páginas con texto: las vacías no generan sección.
- JSON con un sustituto Unicode sin pareja: se rechaza como archivo inválido, no rompe el lote.
- XML con DTD o entidades externas: se rechaza; no se resuelven entidades ni recursos externos.
- CSV o TSV con comillas mal cerradas: se rechaza como archivo inválido.
- Archivo vacío o con solo espacios: se reporta «no se extrajo texto legible».
- Extensión en mayúsculas (`.PDF`): se acepta igual que la minúscula.
- Nombre de archivo con caracteres especiales: el documento recibe un identificador estable.
- Dos archivos con el mismo nombre en carpetas distintas: se identifican por nombre de
  archivo, de modo que comparten identificador y el último procesado prevalece.
- Lote que supera el número máximo de archivos: se rechaza antes de escribir en disco.
- Texto extraído de un PDF que supera el máximo: se reporta como error de ese archivo.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El sistema MUST admitir `.pdf`, `.xml`, `.json`, `.jsonl`, `.csv` y `.tsv`
  además de `.html`, `.htm`, `.md`, `.markdown` y `.txt` en la web y en la consola.
- **FR-002**: El formulario MUST ofrecer exactamente las extensiones que acepta el
  backend, tanto en el selector de archivos como en el de carpeta y en el texto de ayuda,
  desde una sola fuente de verdad.
- **FR-003**: La web MUST mantener sus límites de 100 archivos por carga y 5 MB por
  archivo; la consola MUST admitir hasta 100 MB por archivo y dividir el trabajo en lotes.
- **FR-004**: Cada archivo procesado MUST conservar el original en Bronze, el texto
  extraído en Silver y la ficha en Gold; la carga completa MUST actualizar PostgreSQL y
  Qdrant.
- **FR-005**: Un PDF MUST extraerse por página y conservar el número de página en el
  documento Gold; las páginas sin texto no MUST generar contenido.
- **FR-006**: Un PDF sin texto extraíble MUST reportarse como archivo que puede requerir
  OCR, sin abortar el lote.
- **FR-007**: Si falta la herramienta de extracción de PDF, el sistema MUST fallar con un
  mensaje que nombre la dependencia y su instalación.
- **FR-008**: La extracción de PDF MUST acotarse en tiempo y tamaño, y tratar como error
  el PDF dañado, protegido o que exceda esos límites, sin detener el lote.
- **FR-009**: La imagen de despliegue MUST incluir la herramienta de extracción de PDF.
- **FR-010**: El sistema MUST validar XML sin resolver DTD ni entidades, y rechazar los
  documentos que las declaren.
- **FR-011**: El sistema MUST validar JSON, JSONL, CSV y TSV antes de aceptarlos y
  conservar su contenido íntegro y legible dentro del documento Gold, delimitado por el
  nombre del formato.
- **FR-012**: Un archivo inválido MUST producir un error asociado a su nombre y no MUST
  impedir que los demás archivos del lote terminen.
- **FR-013**: La consola MUST recorrer carpetas de forma recursiva, ignorar las
  extensiones no admitidas y devolver un código de salida distinto de cero si algún
  archivo falló.
- **FR-014**: La consola MUST ofrecer una opción para detenerse en Gold sin tocar
  PostgreSQL ni Qdrant.
- **FR-015**: Toda escritura en el índice MUST fusionarse bajo exclusión mutua y
  reemplazar el archivo de forma atómica, de modo que cargas y capturas solapadas
  conserven todas sus entradas.
- **FR-016**: La actualización del índice desde una captura MUST descartar las entradas
  cuyo documento ya no existe en Gold y conservar las vigentes.
- **FR-017**: La interfaz MUST advertir que los PDF conservan el número de página y que
  los escaneados requieren OCR previo.

### Key Entities

- **Archivo cargado**: nombre, bytes y formato; es la entrada del lote.
- **Documento Gold**: identificador estable, texto extraído, título, sección y origen.
- **Entrada de índice**: identificador, título, URL de origen y metadatos de procedencia.
- **Informe de ingesta**: conteos de archivos, Bronze, Silver, Gold, PostgreSQL y Qdrant,
  más la lista de errores por archivo.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Un lote con PDF, XML, JSON, JSONL, CSV y TSV termina en Gold sin
  intervención manual y solo los archivos inválidos producen error.
- **SC-002**: El 100 % de los PDF con capa de texto procesados conserva el número de
  página en Gold.
- **SC-003**: Un PDF escaneado produce un aviso que menciona OCR y el resto del lote
  termina igualmente.
- **SC-004**: Diez actualizaciones concurrentes del índice no pierden ninguna entrada.
- **SC-005**: Hay cero discrepancias entre las extensiones que ofrece el formulario y las
  que acepta el backend.
- **SC-006**: Un operador procesa una carpeta de varios cientos de MB sin partir el lote a
  mano y obtiene un resumen con conteos y errores.
- **SC-007**: El tiempo dedicado a diagnosticar una carga fallida se reduce a leer el
  mensaje por archivo: no hace falta abrir las capas a mano.

## Assumptions

- La carga y la captura siguen siendo públicas y sin autenticación en esta capacidad.
- El OCR queda fuera: los PDF escaneados se rechazan con aviso, no se convierten.
- Los PDF del corpus son informes y memorias con capa de texto.
- Los datos estructurados se conservan como texto íntegro; no se convierten en tablas ni
  en fichas por registro.
- Las cargas se identifican por nombre de archivo y adoptan la sección «Cargas» y el
  origen «colaborador», igual que las cargas ya existentes.
