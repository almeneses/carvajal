# Feature Specification: Rastreo profundo con Bronze, Silver y Gold listos para indexar

**Feature Branch**: `048-crawl-profundo`
**Created**: 2026-11-21
**Status**: Draft
**Input**: User description: "sería posible hacer un web scrapping profundo, hagas el
bronze-silver-gold y lo dejes listo para vectorizar e indexar? pero sin vectorizar aún"

## Clarifications

### Session 2026-11-21

- **Alcance de hosts**: sitio oficial, dominios propios del grupo y **medios/fuentes
  externas enlazadas** (p. ej. `larepublica.co`, `elpais.com.co`, `caliescribe.com`),
  con cupo independiente por host externo.
- **Primera corrida**: profundidad **4** y hasta **5.000 páginas**. Ambos valores son
  configurables por comando; no se codifican como constante.
- **Formatos**: HTML, **PDF** y **hojas de cálculo** (CSV, TSV y XLSX). El corpus del
  donante tiene 550 PDF que son el 93 % de su texto útil; dejarlos fuera no es aceptable.
- **Entrega**: `spec.md` + `plan.md` primero; la implementación va en PRs posteriores.
- **Decisión de seguridad**: el comando nuevo **no** escribe en PostgreSQL ni en Qdrant
  por defecto. Vectorizar es un paso explícito (`--sync`). Esto difiere de
  `carvajal capture`, que sincroniza salvo `--solo-gold`: una corrida de 5.000 páginas
  no debe disparar horas de embeddings por accidente.

### Contexto del estado actual

- `carvajal capture` recorre exactamente las semillas configuradas: una URL, una página.
  No extrae `<a href>`, no hace BFS y no tiene profundidad ni tope de páginas. Es la
  pieza que falta.
- `WebClient` ya implementa `robots.txt`, `crawl-delay` por host, throttle y *user-agent*.
- `carvajal capture --solo-gold` ya demuestra el corte en Gold sin tocar servicios.
- El pipeline escribe `data/raw/` (Bronze), `data/processed/` (Silver) y `data/gold/`
  (Gold) con escritura atómica e índice JSONL.
- La extracción de PDF, XML, JSON, JSONL, CSV y TSV existe en `apps/api` para las cargas
  web; XLSX no está soportado en ningún lado todavía.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Rastrear el sitio completo y dejarlo en Gold sin indexar (Priority: P1)

Una persona del equipo quiere enriquecer el corpus con lo que el sitio publica y aún no
está en semillas. Ejecuta un rastreo con profundidad 4 y tope de 5.000 páginas. Al
terminar, `data/raw`, `data/processed` y `data/gold` tienen las páginas nuevas, y
PostgreSQL y Qdrant están exactamente como estaban. Mañana, cuando quiera, ejecutará el
`sync` y vectorizará.

**Why this priority**: es la capacidad pedida y sin ella no hay rastreo profundo. El
corte antes de los servicios es lo que la hace segura para una corrida larga.

**Independent Test**: con un servidor HTTP local de tres niveles y enlaces cruzados,
ejecutar el rastreo y comprobar que las páginas alcanzadas están en las tres capas y que
los conteos de Postgres y Qdrant no cambiaron.

**Acceptance Scenarios**:

1. **Given** un `data/` limpio y un grafo de páginas enlazadas, **When** se rastrea con
   profundidad 3, **Then** se guardan las páginas de los niveles 0 a 3 y no las del 4.
2. **Given** una página ya presente en Gold con el mismo texto, **When** se vuelve a
   rastrear, **Then** no se duplica en Bronze, Silver ni Gold.
3. **Given** una corrida terminada, **When** se consultan PostgreSQL y Qdrant, **Then**
   sus conteos son idénticos a los de antes de la corrida.
4. **Given** el tope de 5.000 páginas alcanzado, **When** el rastreador lo detecta,
   **Then** se detiene de forma ordenada, escribe el informe y deja el corpus consistente.
5. **Given** un `crawl` en curso, **When** se intenta lanzar otro, **Then** el segundo
   falla con un mensaje claro y no escribe nada (bloqueo de corpus).

---

### User Story 2 - Incorporar PDF y hojas de cálculo enlazadas (Priority: P1)

Una página enlaza el informe anual en PDF y una tabla de precios en XLSX. El rastreo
guarda el original en Bronze, el texto extraído en Silver y una ficha citable en Gold,
igual que hace hoy la carga multiformato.

**Why this priority**: el corpus real es mayoritariamente PDF; sin esto el rastreo
profundo aporta muy poco texto nuevo.

**Independent Test**: con un servidor local que enlaza un PDF de dos páginas, un CSV y un
XLSX de dos hojas, comprobar que Bronze conserva los bytes, Silver tiene el texto y Gold
la ficha con procedencia.

**Acceptance Scenarios**:

1. **Given** un enlace a un PDF, **When** se descarga, **Then** Bronze conserva el
   archivo byte a byte y Silver marca los números de página.
2. **Given** un PDF escaneado sin capa de texto, **When** se extrae, **Then** Bronze se
   conserva, Silver queda vacío y el informe lo cuenta como «requiere OCR».
3. **Given** un XLSX de varias hojas, **When** se extrae, **Then** Silver incluye todas
   las hojas con su nombre como encabezado.
4. **Given** un enlace a un archivo mayor que el límite, **When** se evalúa su tamaño,
   **Then** se descarta con motivo y no se descarga completo.

---

### User Story 3 - Seguir medios externos sin arrasar sus sitios (Priority: P2)

El rastreo llega a una nota de `larepublica.co` que habla de Carvajal. Quiere incluir esa
nota, pero no recorrer todo el periódico.

**Why this priority**: las fuentes externas son valiosas, pero sin cupo por host el
rastreo se desborda y se vuelve descortés.

**Independent Test**: con dos hosts simulados, comprobar que el externo se detiene en su
cupo aunque tenga más enlaces, y que el oficial sigue.

**Acceptance Scenarios**:

1. **Given** un host externo con 200 enlaces, **When** se rastrea con cupo por host de
   100, **Then** se descargan como máximo 100 páginas de ese host.
2. **Given** un enlace marcado `rel="nofollow"`, **When** se procesa, **Then** no se sigue.
3. **Given** un `robots.txt` que prohíbe una ruta, **When** se descubre, **Then** no se
   descarga y el informe lo cuenta como bloqueada.
4. **Given** una respuesta 429 con `Retry-After`, **When** se recibe, **Then** se espera
   lo indicado, se reintenta con un máximo y, si persiste, se abandona ese host.

---

### User Story 4 - Reanudar y repetir sin duplicar (Priority: P2)

La corrida se interrumpe a la mitad. Al relanzarla, el rastreo sabe qué ya hizo, no vuelve
a descargar lo que no cambió y no duplica documentos.

**Why this priority**: 5.000 páginas son más de una hora; una interrupción no debe
costar todo el trabajo.

**Independent Test**: Cortar el proceso a mitad, relanzarlo y comprobar que el total de
documentos es el mismo que en una corrida continua.

**Acceptance Scenarios**:

1. **Given** una corrida interrumpida, **When** se relanza, **Then** retoma desde el
   *checkpoint* y no repite descargas ya completadas.
2. **Given** URLs ya presentes en Gold, **When** se rastrea sin `--refresh`, **Then** no
   se descargan y se informan como «ya conocidas».
3. **Given** `--refresh`, **When** una página cambió, **Then** se actualiza en las tres
   capas y se cuenta como actualizada.

---

### User Story 5 - Entender qué pasó (Priority: P3)

Al terminar, la persona ve un informe por host y por formato con descubiertas,
descargadas, duplicadas, bloqueadas, descartadas y con error, y el comando devuelve un
código de salida útil.

**Independent Test**: ejecutar sobre el servidor local con un enlace roto y otro
bloqueado, y comprobar los conteos y el código de salida.

## Edge Cases

- Un enlace redirige fuera de los hosts permitidos: se registra y no se sigue.
- Paginaciones infinitas o calendarios de archivo: el cupo por host y la profundidad
  cortan el bucle.
- Mismo texto en URLs distintas: se conserva la primera y se cuentan las demás como
  duplicadas.
- Parámetros de seguimiento (`utm_*`, `fbclid`) y fragmentos `#`: se normalizan para no
  duplicar.
- `Content-Type` que no coincide con la extensión: manda el `Content-Type`.
- PDF de 67 MB o timeout de extracción: se conserva Bronze y se informa; no se descarta la
  página que lo enlazaba.
- XLSX enorme o corrupto: el archivo se descarta con motivo, la corrida sigue.
- `meta robots noindex`: el documento no se escribe en Gold aunque la página sea HTML
  válida.
- Disco lleno o presupuesto de bytes agotado: se detiene de forma ordenada con aviso.
- La página depende de JavaScript para listar enlaces: sus enlaces no se descubren; es una
  limitación conocida, no un error silencioso.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: El CLI MUST exponer `carvajal crawl` con semillas repetibles (`--seed`) o
  con `--scope all|oficial|terceros`, reutilizando las semillas actuales.
- **FR-002**: El rastreo MUST ser BFS por niveles, con `--depth` (por defecto 4),
  `--max-pages` (5.000), `--max-pages-per-host` (100) y `--time-budget-minutes` (180).
- **FR-003**: El rastreo MUST respetar `robots.txt`, `crawl-delay`, un *user-agent*
  identificable y un retardo mínimo de 1 s por host. `--ignore-robots` MUST limitarse a
  hosts locales.
- **FR-004**: El comando MUST NOT escribir en PostgreSQL ni en Qdrant salvo `--sync`
  explícito.
- **FR-005**: Cada recurso MUST escribirse en Bronze (original), Silver (texto) y Gold
  (ficha con procedencia y URL), con las convenciones y escritura atómica actuales.
- **FR-006**: La deduplicación MUST usar URL canónica y huella del texto; una URL ya
  conocida MUST saltarse sin descargar salvo `--refresh`.
- **FR-007**: El despacho de formatos MUST basarse en `Content-Type` y extensión para
  HTML, PDF, CSV, TSV y XLSX; límites por archivo (100 MB) y por corrida (5 GB).
- **FR-008**: Los PDF MUST conservar el número de página en Silver; los sin texto MUST
  informarse como «requiere OCR» sin abortar. Las hojas de cálculo MUST conservar el
  nombre de cada hoja.
- **FR-009**: El rastreo MUST NOT seguir enlaces `rel="nofollow"` ni escribir en Gold
  documentos con `meta robots noindex`.
- **FR-010**: Los hosts externos MUST tener cupo independiente y solo se siguen si
  provienen de una semilla o de un enlace dentro del alcance permitido.
- **FR-011**: La corrida MUST ser reanudable mediante un *checkpoint* en
  `data/runtime/crawl/`; un relanzamiento MUST converger al mismo corpus.
- **FR-012**: El comando MUST tomar el bloqueo de corpus existente; una segunda corrida
  simultánea MUST fallar sin escribir.
- **FR-013**: El informe final MUST desglosar por host y formato: descubiertas,
  descargadas, nuevas, actualizadas, ya conocidas, duplicadas, bloqueadas, descartadas y
  con error. El código de salida MUST distinguir éxito, parcial y fallo.
- **FR-014**: El comando MUST NOT borrar ni podar capas existentes; solo añade o actualiza.
- **FR-015**: Los tests MUST poder ejecutarse sin red usando un servidor HTTP local; la
  corrida real es un paso aparte con evidencia.

### Key Entities

- **CrawlRun**: identificador, semillas, profundidad, topes, inicio, fin y estado.
- **CrawlFrontier**: URLs pendientes con su profundidad y host.
- **CapturedResource**: URL, `Content-Type`, bytes originales, texto extraído, huella y
  estado (`nuevo`, `actualizado`, `conocido`, `duplicado`, `descartado`, `error`).
- **HostPolicy**: host, cupo, retardo efectivo, robots permitido y contadores.

## Success Criteria *(mandatory)*

- **SC-001**: La primera corrida real (profundidad 4, tope 5.000) sobre las semillas
  produce al menos 1.000 documentos nuevos en las tres capas, y los conteos de PostgreSQL
  y Qdrant no cambian.
- **SC-002**: Ninguna petición del rastreo viola `robots.txt` ni el retardo configurado;
  las bloqueadas quedan contadas.
- **SC-003**: Una segunda corrida sin cambios no descarga ninguna URL ya conocida y no
  crea documentos nuevos.
- **SC-004**: PDF, CSV y XLSX enlazados producen Bronze byte a byte, Silver con
  páginas/hojas y Gold con procedencia.
- **SC-005**: Una interrupción y relanzamiento produce el mismo corpus que una corrida
  continua, sin duplicados.
- **SC-006**: El informe final permite responder cuántas páginas se descargaron por host
  y formato y por qué se descartaron las demás.
- **SC-007**: `make check` sigue en verde y `carvajal capture` conserva su comportamiento
  actual.

## Assumptions

- Las semillas actuales (oficiales y de terceros) son el punto de partida; ampliarlas es
  parte de esta capacidad.
- Los hosts externos se reconocen por dominio registrable; los subdominios del grupo se
  autorizan explícitamente.
- El rastreo corre en el host o en la imagen de desarrollo; no se expone como botón de la
  interfaz en esta spec (queda para una spec de UI si se pide).
- `pdftotext` (poppler-utils) está disponible en los entornos donde corre el rastreo.

## Out of Scope

- Ejecutar JavaScript o renderizar con navegador headless.
- Saltar muros de pago, inicios de sesión o protecciones anti-bot.
- OCR de PDF escaneado.
- Vectorizar o indexar durante el rastreo (se hace después con `sync`).
- Traducción, resumen o enriquecimiento con LLM durante la captura.
- Rastreo distribuido o reanudación entre máquinas.
