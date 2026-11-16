# Tarea: pista B de la spec 036 (asistente comercial de empaques) en el repo `almeneses/carvajal`

Actúa como desarrollador del agente LangGraph del proyecto. Vas a implementar la **pista B**
(router, grafo, interfaz y comparación de enrutadores) de la spec 036. La **pista A** (datos y
herramientas) la lleva otra persona en paralelo; no toques sus archivos.

## 1. Primero: sincroniza y lee

```bash
git fetch origin --prune
git switch dev && git pull --ff-only origin dev
```

Lee en este orden, antes de escribir código:

1. `CONSTITUTION.md` y `AGENTS.md` (reglas no negociables).
2. `specs/036-asistente-empaques/spec.md`: historias US1–US5, FR-001 a FR-015 y la sección
   «Historial de cambios».
3. `specs/036-asistente-empaques/plan.md`: sobre todo «Router y grafo», «Herramientas» y
   «Evaluación».
4. `specs/036-asistente-empaques/tasks.md`: tus tareas son **T016–T020 y T028–T041**.
5. El código actual del agente: `packages/core/src/carvajal/agent/{state,router,graph,tools}.py`
   y sus pruebas `tests/test_agent_router.py` y `tests/test_agent_graph.py`.
6. El estado de la pista A: PR #136 (modelos y carga del catálogo) y PR #135 (catálogo real en
   `data/catalog/empaques/`). Revisa en `manifest.json` los campos `materials_without_rule` y
   `notes`, y lee `docs/agentes/036-pista-a.md`.

## 2. Reglas del proyecto (no negociables)

- Solo `uv`; prohibido `pip` y `requirements.txt`. Ruff con 100 columnas y comillas dobles.
  `make check` en verde antes de cada commit.
- Comentarios, docstrings, mensajes de UI y commits en **español**; identificadores en **inglés**.
- Conventional Commits con el ID de la spec y de las tareas: `feat(036): ... (T016-T018)`.
- Ramas `feat/036-prN-<tema>` desde `dev`. PR a `dev` con la plantilla del repo **completa**,
  etiquetas del catálogo (`gh label list`), assignee = autor real y revisor = `markodavid`.
- **No mergees, no apruebes, no uses `--admin`.** Sin rebase ni force-push sobre ramas
  publicadas: para actualizar una rama usa `git merge origin/dev`.
- Presupuesto: 300 líneas (aviso) por PR; si un ID crece mucho más, pártelo.
- Al terminar cada tarea, márcala `[X]` en `tasks.md` en la misma PR.
- **Spec-kit:** si una decisión de implementación cambia lo que dice la spec o el plan,
  actualízalos en la misma PR y añade una línea a «Historial de cambios» de la spec. Si
  cambia la arquitectura o un contrato, **detente y propónlo** (spec, plan o ADR) en lugar
  de implementarlo.

## 3. Archivos que te pertenecen y archivos que no

| Tuyos (pista B) | De la pista A (no editar) |
|---|---|
| `agent/state.py`, `agent/router.py`, `agent/graph.py`, `agent/routers/**` | `data/catalog/**` |
| `apps/api/templates/**`, `apps/api/static/**` | `packages/core/src/carvajal/catalog/**` |
| `data/eval/routing_questions.json`, `docs/resultados-router.md` | `agent/commercial.py` |
| `cli.py` (solo la orden `eval-router`) | `cli.py` (la orden `catalog-build`) |

## 4. Contrato compartido con la pista A (confírmalo antes de empezar)

`agent/commercial.py` lo publica la pista A en su PR 3. Mientras tanto, trabaja contra este
contrato con **dobles de prueba** (fakes) en tus tests. Si necesitas cambiarlo, acuérdalo con
`markodavid` antes de escribir código; no lo modifiques por tu cuenta.

```python
# agent/commercial.py — contrato (pista A)
from dataclasses import dataclass
from typing import Literal
from carvajal.catalog.models import Distributor, Family, Reference

@dataclass(frozen=True)
class CommercialRequest:          # lo llena el router (RouteDecision.extra usa estas claves)
    use: str | None = None        # "bebida_caliente", "bebida_fria", "comida_caliente", "sopas"…
    temperature: Literal["caliente", "fria", "ambiente"] | None = None
    microwave: bool | None = None
    capacity_oz: float | None = None     # el router convierte ml → oz
    family: Family | None = None         # vaso, tapa, contenedor, charola, cubierto, envase, plato
    quantity: int | None = None          # unidades
    country: Literal["MX", "CO"] | None = None
    city: str | None = None
    prefers_renewable: bool = False

@dataclass(frozen=True)
class Discarded:  sku: str; reason: str
@dataclass(frozen=True)
class BoxCount:   boxes: int; surplus: int
@dataclass(frozen=True)
class Quote:      total: str; currency: str; tax_included: bool
                  captured_at: str; valid_until: str | None; notice: str

def search_packaging(req: CommercialRequest) -> list[Reference]: ...
def check_material(refs: list[Reference], req: CommercialRequest
                   ) -> tuple[list[Reference], list[Discarded]]: ...
def boxes_for(ref: Reference, units: int) -> BoxCount: ...
def quote(ref: Reference, boxes: int) -> Quote: ...          # NoPriceError si no hay precio
def find_distributors(city: str) -> tuple[list[Distributor], list[str]]: ...
#                                       (distribuidores, ciudades que sí tienen)
```

Datos que debes conocer:

- Solo México tiene precio (en pesos mexicanos, con IVA). Colombia se recomienda sin precio y
  remite a distribuidores.
- La guía de materiales no cubre papel ni pulpa: `check_material` los valida con la
  temperatura y el uso de su ficha (FR-003).

## 5. Orden de trabajo

1. **PR 4: router y grafo (T016–T020).**
   - Agrega `"commercial"` a `RouteName`.
   - Regla comercial en `decide_route`, evaluada después de memoria y saludo y antes de
     indicadores.
   - Extrae los datos a `RouteDecision.extra` con las claves de `CommercialRequest`.
   - Si falta el uso, o la temperatura en las familias que la admiten (vaso, tapa,
     contenedor y envase), decide `direct` con una pregunta de aclaración, igual que la ruta
     `tool` cuando falta el periodo. Charola, cubierto y plato no exigen temperatura: se
     cotizan sin preguntarla.
   - Nodo `commercial` en el grafo con una **secuencia fija**: buscar → validar →
     (cajas → cotizar | distribuidores). El LLM solo redacta con el resultado estructurado,
     y el prompt le prohíbe añadir referencias, cifras o disponibilidad.
   - Las pruebas existentes del router y del grafo deben seguir en verde.
2. **PR 5 de la pista B: casos límite (T031–T035).**
   - Una pregunta institucional sobre empaques va a `rag`.
   - Una necesidad a la medida (industrial o agro) se deriva a un comercial.
   - Una pregunta mixta atiende la parte comercial y avisa la otra.
   - Escribe `data/eval/routing_questions.json` (~100 preguntas: las existentes con su ruta
     actual más las comerciales, con datos esperados) y verifica que no haya regresión
     (SC-005).
3. **PR 6: tarjetas (T028–T030)**: `_productos.html` con SKU, material, unidades por caja,
   precio de referencia **con fecha** y fuente; intégralo en `_chat_stream.html`. Cuando
   lleguen las herramientas reales de la pista A, conecta el nodo a ellas en esta PR.
4. **PR 7: comparación de enrutadores (T036–T041).**
   - `build_agent(router=...)`, con `decide_route` por defecto.
   - `agent/routers/llm.py` (salida estructurada) y `agent/routers/decision.py` (Jev, como
     extra opcional; si no está disponible, error controlado).
   - Orden `eval-router --router rules|llm|decision`, registro de la decisión en los logs y
     `docs/resultados-router.md`.

## 6. Antes de abrir cada PR

- `make check` en verde y los escenarios de la spec cubiertos por pruebas.
- Plantilla del repo completa: tipo de cambio, «Resuelve: T0xx», trazabilidad con la spec 036,
  cómo se probó (con tabla de escenarios) y checklist.
- Si cambiaste algo de la spec o del plan, dilo en la descripción, en una sección
  «Decisiones para revisar».

Empieza por el punto 1 y, antes de escribir código, resume en 5 líneas lo que entendiste del
contrato de la sección 4 y cualquier duda sobre él.
