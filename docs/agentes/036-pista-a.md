# Tarea: pista A de la spec 036 (asistente comercial de empaques) en el repo `almeneses/carvajal`

Actúa como desarrollador de datos y herramientas del proyecto. Vas a continuar la **pista A**
(datos y herramientas) de la spec 036. La **pista B** (router, grafo, interfaz y comparación
de enrutadores) la lleva otra persona en paralelo; no toques sus archivos.

## 1. Primero: sincroniza y lee

```bash
git fetch origin --prune
git switch dev && git pull --ff-only origin dev
```

Lee en este orden, antes de escribir código:

1. `CONSTITUTION.md` y `AGENTS.md` (reglas no negociables).
2. `specs/036-asistente-empaques/spec.md`: historias US1–US3, FR-001 a FR-008 y la sección
   «Historial de cambios».
3. `specs/036-asistente-empaques/plan.md`: «Datos del catálogo», «Herramientas» y las
   decisiones tomadas al curar los datos.
4. `specs/036-asistente-empaques/tasks.md`: tus tareas son **T003–T015 y T021–T027**.
   Ya están hechas T003–T008 y T010–T012 (PR #136 y #135).
5. El código existente: `packages/core/src/carvajal/catalog/` (modelos, carga y
   construcción) y `data/catalog/empaques/` (CSV y `manifest.json`).
6. `docs/agentes/036-pista-b.md`, para saber qué espera la otra pista de ti.

## 2. Reglas del proyecto (no negociables)

- Solo `uv`; prohibido `pip` y `requirements.txt`. Ruff con 100 columnas y comillas dobles.
  `make check` en verde antes de cada commit.
- Comentarios, docstrings y commits en **español**; identificadores en **inglés**.
- Conventional Commits con el ID de la spec y de las tareas: `feat(036): ... (T013-T015)`.
- Ramas `feat/036-prN-<tema>` desde `dev` (o apiladas sobre la PR de la que dependan). PR con
  la plantilla del repo **completa**, etiquetas del catálogo (`gh label list`), assignee =
  autor real y revisor = `almeneses`.
- **No mergees, no apruebes, no uses `--admin`.** Sin rebase ni force-push sobre ramas
  publicadas: para actualizar una rama usa `git merge`.
- Presupuesto: 300 líneas (aviso) por PR; los CSV curados se presupuestan aparte.
- Al terminar cada tarea, márcala `[X]` en `tasks.md` en la misma PR.
- **Spec-kit:** si una decisión cambia lo que dice la spec o el plan, actualízalos en la misma
  PR y añade una línea a «Historial de cambios» de la spec. Si cambia un contrato con la
  pista B, **detente y acuérdalo** con `almeneses` antes de implementarlo.
- **Datos:** cada fila conserva `source_url` y `captured_at`. Nada de datos personales
  (nombres de contacto, correos). Los precios son siempre de referencia y fechados.

## 3. Archivos que te pertenecen y archivos que no

| Tuyos (pista A) | De la pista B (no editar) |
|---|---|
| `data/catalog/**` | `agent/state.py`, `agent/router.py`, `agent/graph.py`, `agent/routers/**` |
| `packages/core/src/carvajal/catalog/**` | `apps/api/templates/**`, `apps/api/static/**` |
| `agent/commercial.py` | `data/eval/routing_questions.json`, `docs/resultados-router.md` |
| `cli.py` (la orden `catalog-build`) | `cli.py` (la orden `eval-router`) |

## 4. Contrato que publicas para la pista B

Implementa **exactamente** este contrato en `agent/commercial.py` (PR 3). La pista B ya
trabaja contra él con dobles de prueba; cualquier cambio se acuerda antes con `almeneses`.

```python
from dataclasses import dataclass
from typing import Literal
from carvajal.catalog.models import Distributor, Family, Reference

@dataclass(frozen=True)
class CommercialRequest:          # lo llena el router (RouteDecision.extra usa estas claves)
    use: str | None = None        # "bebida_caliente", "bebida_fria", "comida_caliente", "sopas"…
    temperature: Literal["caliente", "fria", "ambiente"] | None = None
    microwave: bool | None = None
    capacity_oz: float | None = None     # el router convierte ml → oz
    family: Family | None = None
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
```

## 5. Orden de trabajo

1. **PR 3: buscar y validar (T013–T015).**
   - `search_packaging` filtra por familia, uso y país, y la capacidad con tolerancia (±5 %),
     porque el router convierte ml a oz.
   - `check_material` aplica FR-003:
     - materiales con regla en `materiales.csv` (PP, EPS, PS, PET, aluminio): manda la guía.
       Contenido caliente exige `hot_content`; microondas exige `microwave`; las aplicaciones
       `no_apto` descartan.
     - materiales en `materials_without_rule` (papel, pulpa): manda la ficha; temperatura
       declarada (`temp_max_c`) y uso (`bebida_caliente`…). Si la ficha no declara ninguno,
       se descarta con motivo para pedidos con temperatura.
   - Cada descarte lleva un motivo legible.
   - Pruebas: caliente nunca devuelve PET ni PS para bebida; microondas solo PP; vaso de papel
     para bebida caliente sí pasa; sin candidatos válidos devuelve vacío con motivos.
2. **PR 2b: catálogo PDF de Colombia (T009).** Transcribe el catálogo «Empaques para cada
   momento» (37 páginas, ~180 referencias, sin precio) leyendo las páginas como imagen: la
   capa de texto mezcla columnas. País `CO`, `source_url` del PDF y fecha de captura. Puede ir
   en paralelo a la PR 3.
3. **PR 5: cajas, cotización y canal (T021–T027).**
   - `boxes_for`: redondeo hacia arriba y excedente.
   - `quote`: total = cajas × precio, con moneda, IVA, fecha de captura, vigencia y aviso de
     precio de referencia; `NoPriceError` si no hay precio; aviso si la vigencia ya pasó.
   - `find_distributors`: por ciudad (sin distinguir tildes ni mayúsculas) y, si no hay,
     la lista de ciudades disponibles.
   - Pruebas de todos los escenarios de US2 y US3 y del borde «más de 100 cajas».

## 6. Antes de abrir cada PR

- `make check` en verde y los escenarios de la spec cubiertos por pruebas.
- Plantilla del repo completa: tipo de cambio, «Resuelve: T0xx», trazabilidad con la spec 036,
  cómo se probó (con tabla de escenarios) y checklist.
- Si cambiaste algo de la spec, del plan o del contrato, dilo en «Decisiones para revisar».

Empieza por el punto 1 y, antes de escribir código, resume en 5 líneas el contrato de la
sección 4 y cómo `check_material` trata papel y pulpa.
