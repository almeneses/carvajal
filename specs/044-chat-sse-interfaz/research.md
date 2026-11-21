# Research — 044 Reglas compartidas, lector SSE único y HTMX por pantalla

## Decisión 1: dónde vive el módulo de reglas compartidas

- **Decisión**: crear `apps/api/views/chat_rules.py` con las constantes de
  validación, `resolve_visitor`, `validate_question`, `require_csrf`,
  `consume_quota`, `set_identity_cookie`, `invalid_response` y
  `stream_events`. `chat.py` lo usa y reexporta los nombres que hoy importan las
  pruebas (`EMPTY_QUESTION`, `TOO_LONG_QUESTION`, `SERVICE_UNAVAILABLE`, etc.).
- **Rationale**: las vistas ya importan reglas desde `views.chat`, así que mover la
  definición sin romper imports evita un cambio masivo. El alcance de escritura del
  goal no permite tocar `apps/api/chat/`, y las reglas son de presentación/HTTP, no de
  dominio.
- **Alternativas descartadas**: (a) dejar las reglas en `chat.py` y que los demás
  importen de allí — conserva el acoplamiento que la spec quiere eliminar y el módulo
  de chat seguiría siendo el dueño de reglas ajenas; (b) crear un paquete nuevo en
  `apps/api/chat/` — fuera del alcance de escritura y mezcla dominio con HTTP.

## Decisión 2: qué se comparte y qué no

- **Decisión**: se comparte la definición de las reglas y el orden de las llamadas en
  cada vista; **no** se fuerza el mismo orden entre chat y FAQ. La FAQ mantiene
  `preparar corpus → cuota` porque evita cobrar una generación que no puede ejecutarse.
- **Rationale**: el pedido es centralizar reglas, no homogeneizar flujos con
  requisitos distintos. Conservar el orden de la FAQ evita una regresión de cuota.
- **Alternativas descartadas**: mover la cuota antes de preparar en la FAQ — cambiaría
  el comportamiento observable (422 sin cobrar) que ya prueban sus tests.

## Decisión 3: cómo se comparte el lector SSE

- **Decisión**: un archivo `apps/api/static/sse.js` que expone
  `window.CarvajalSSE = { leerEventos, interpretar }`. `leerEventos(response, alEvento)`
  lee el cuerpo con `ReadableStream`, decodifica con `TextDecoder`, separa bloques por
  `/\r?\n\r?\n/`, interpreta `event:` y una o más `data:` (unidas con `\n`) y entrega
  un objeto `{nombre, datos}`; al cerrar, procesa el último bloque sin separador. Los
  consumidores conservan sus callbacks.
- **Rationale**: es exactamente la lógica que hoy tiene el chat (la más completa) y la
  FAQ gana el último bloque, que el servidor no necesita pero que evita perder eventos
  si un proxy recorta el final. Sin dependencias, bajo la CSP actual.
- **Alternativas descartadas**: (a) `EventSource` — no permite POST ni cabeceras CSRF;
  (b) copiar la lectura dos veces con un helper inline — viola la CSP; (c) una clase o
  framework — desproporcionado para 40 líneas.

## Decisión 4: HTMX solo en Configuración

- **Decisión**: quitar `<script src="/static/htmx.min.js" defer>` de `base.html` y
  añadirlo en `{% block head_extra %}` de `configuracion.html`, antes de
  `captura.js`. No se usa un bloque condicional en `base.html` porque solo una pantalla
  lo necesita.
- **Rationale**: el único uso de atributos `hx-*` está en `_captura.html`, que solo
  renderiza Configuración; `captura.js` solo escucha eventos `htmx:*` en esa pantalla.
  Las demás páginas no lo usan.
- **Alternativas descartadas**: cargar HTMX con `defer` bajo una variable de contexto
  en `base.html` — funcionaría, pero deja el peso y la dependencia en la plantilla
  compartida y no expresa que es una pantalla concreta la que lo necesita.

## Decisión 5: estrategia de pruebas

- **Decisión**: primero un commit `test(044): ...` con caracterización (pytest para los
  tres caminos y su matriz de errores/cookies/orden; `node:test` para el lector actual
  de chat y FAQ, incluido el último bloque sin separador). Después el refactor, sin
  reescribir las expectativas salvo la diferencia intencional documentada (FAQ procesa
  el último bloque) y la expectativa de HTMX. Las pruebas nuevas del lector compartido
  se añaden con el refactor.
- **Rationale**: fijar el contrato antes evita "arreglar" la prueba para que pase el
  código nuevo; es el requisito explícito del goal.
- **Alternativas descartadas**: refactor primero y pruebas después — no demuestra
  ausencia de regresiones.

## Decisión 6: alcance de los archivos de producción

- **Decisión**: solo se tocan `apps/api/views/*.py`, `apps/api/static/*.js`,
  `apps/api/templates/*.html` y `tests/`. No se tocan `apps/api/chat/service.py` ni
  `agent.py`, que otros PRs del goal modifican.
- **Rationale**: evita conflictos entre PRs independientes del goal y respeta el
  alcance de escritura.
