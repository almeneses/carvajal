"""Reglas duras compartidas por los modos ``routes`` y ``tools`` (spec 044, T013).

Cada regla se escribe una sola vez y corre en ambos modos (plan, «Convivencia y retiro»):
una regla que pase en un modo y falle en el otro bloquea el PR. Las pruebas no conocen el
grafo: piden un turno con ``run_turn(mode, question, script)`` y revisan un
``HardRuleTurn`` común (fuentes, tarjetas comerciales, prompt, sistema y abstención).

- ``routes``: grafo actual (``carvajal.agent.graph.build_agent``) con dobles de
  ``VectorStore``/``Embedder``; el guion se ignora porque ese modo no llama al modelo.
- ``tools``: grafo ``build_tool_agent`` (T023) movido por ``ScriptedToolChat`` con el guion
  de cada prueba. Hasta T023/T028 esos casos están marcados ``xfail(strict=True)``: hoy
  fallan por ``ImportError`` y, cuando el adaptador exista y pasen, el ``xfail`` estricto
  obliga a quitar la marca (T028).

Los casos comerciales usan el catálogo y el contrato reales, como ``test_sc_036.py``.
"""

import importlib
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from tests.fakes.tool_chat import ScriptStep
from tests.test_agent_graph import HIT, FakeEmbedder, FakeVectorStore

from apps.api.chat.agent import GraphAgentRunner
from carvajal.agent.graph import AgentDeps, build_agent
from carvajal.application.ask import NO_CONTEXT_ANSWER
from carvajal.application.ports import ScoredChunk
from carvajal.catalog.loader import Catalog, load_catalog
from carvajal.infrastructure.settings import Settings

TOOLS_GRAPH_MODULE = "carvajal.agent.toolagent.graph"

# Hoy falla por ImportError (no existe el módulo, T023). Si el módulo llega antes que el
# adaptador de T028, el lado ``tools`` lanza NotImplementedError y el caso sigue en xfail;
# cualquier otro fallo es un fallo real. Con ``strict`` un XPASS rompe la suite: T028 debe
# quitar la marca en cuanto el modo cumpla.
TOOLS_PENDING = pytest.mark.xfail(
    strict=True,
    raises=(ImportError, NotImplementedError),
    reason="modo tools pendiente: T023/T028",
)
MODES = [
    pytest.param("routes", id="routes"),
    pytest.param("tools", id="tools", marks=TOOLS_PENDING),
]

CATALOG_MATERIALS = {reference.sku: reference.material for reference in load_catalog().references}
OFFICIAL_HOSTS = ("carvajal.com", "carvajalempaques.com")


@dataclass(frozen=True)
class HardRuleTurn:
    """Resultado de un turno visto desde las reglas duras, igual en ambos modos.

    - ``sources``: citas serializadas como ``Source`` (con ``source_type``).
    - ``products``: resultado comercial de las tarjetas (``options``, ``discarded``,
      ``quote``/``quotes``…); vacío si el turno no fue comercial.
    - ``prompt``/``system``: lo que se entrega al modelo para redactar. En ``tools`` el
      prompt no existe como tal y puede ir vacío.
    - ``answer``: texto final cuando el modo lo produce. En ``routes`` la generación es
      externa: solo se rellena con ``NO_CONTEXT_ANSWER`` cuando se abstiene, como hace el
      servicio con ``prompt is None``.
    - ``abstained``: el turno no genera y responde ``NO_CONTEXT_ANSWER`` (FR-004).
    """

    mode: str
    sources: tuple[dict[str, Any], ...]
    products: dict[str, Any] = field(default_factory=dict)
    prompt: str | None = None
    system: str = ""
    answer: str | None = None
    abstained: bool = False


class RunTurn(Protocol):
    def __call__(
        self,
        mode: str,
        question: str,
        script: Sequence[ScriptStep] = (),
        *,
        hits: Sequence[ScoredChunk] = (),
        catalog: Catalog | None = None,
    ) -> HardRuleTurn: ...


def _routes_turn(question: str, *, store: FakeVectorStore, catalog: Catalog | None) -> HardRuleTurn:
    """Turno del grafo ``routes`` a través de su adaptador real (``GraphAgentRunner``)."""

    settings = Settings()
    graph = build_agent(
        AgentDeps(store, FakeEmbedder(), settings, catalog=catalog),
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
    )
    turn = GraphAgentRunner(graph, settings).prepare("dueno-reglas", "hilo-reglas", question, [])
    abstained = turn.prompt is None
    return HardRuleTurn(
        mode="routes",
        sources=tuple(asdict(source) for source in turn.sources),
        products=dict(turn.products),
        prompt=turn.prompt,
        system=turn.system,
        answer=NO_CONTEXT_ANSWER if abstained else None,
        abstained=abstained,
    )


def _tools_turn(
    question: str,
    script: Sequence[ScriptStep],
    *,
    store: FakeVectorStore,
    catalog: Catalog | None,
) -> HardRuleTurn:
    """Turno del grafo ``tools`` guiado por ``ScriptedToolChat`` (lo completa T028).

    T028: construir ``build_tool_agent`` con ``ScriptedToolChat(steps=list(script))``, los
    mismos dobles (``store``, ``FakeEmbedder``), ``catalog``, ``InMemorySaver`` e
    ``InMemoryStore``; correr el turno por su adaptador (``ToolAgentRunner``, T025) para que
    actúe la compuerta de evidencia (R3); comprobar que el guion se consumió entero y
    devolver ``HardRuleTurn`` con ``sources`` del registro, ``products`` = tarjetas
    (``cards`` con ``quote``/``quotes``), ``answer`` = texto final y ``abstained`` =
    ``answer == NO_CONTEXT_ANSWER``.
    """

    graph_module = importlib.import_module(TOOLS_GRAPH_MODULE)  # ImportError hasta T023
    raise NotImplementedError(
        f"T028: falta el adaptador de reglas duras para {graph_module.__name__}"
    )


@pytest.fixture
def run_turn() -> RunTurn:
    """Construye el grafo del modo pedido con dobles y corre un turno en un hilo nuevo."""

    def run(
        mode: str,
        question: str,
        script: Sequence[ScriptStep] = (),
        *,
        hits: Sequence[ScoredChunk] = (),
        catalog: Catalog | None = None,
    ) -> HardRuleTurn:
        store = FakeVectorStore(hits)
        if mode == "routes":
            return _routes_turn(question, store=store, catalog=catalog)
        if mode == "tools":
            return _tools_turn(question, script, store=store, catalog=catalog)
        raise ValueError(f"modo desconocido: {mode}")

    return run


def _call(name: str, **args: Any) -> ScriptStep:
    """Paso del guion que solo pide una herramienta."""

    return ScriptStep(tool_calls=[{"name": name, "args": args}])


def _materials(items: Sequence[dict[str, Any]]) -> list[str | None]:
    return [CATALOG_MATERIALS.get(str(item.get("sku"))) for item in items]


def _quotes(products: dict[str, Any]) -> list[dict[str, Any]]:
    """Cotizaciones del turno: ``quotes`` (044) o la ``quote`` única de los lectores 043."""

    quotes = products.get("quotes")
    if quotes:
        return list(quotes)
    quote = products.get("quote")
    return [quote] if quote else []


def _is_official(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    return any(host == base or host.endswith(f".{base}") for base in OFFICIAL_HOSTS)


# --- Material prohibido no recomendado (FR-002) --------------------------------------------


@pytest.mark.parametrize("mode", MODES)
def test_eps_no_se_recomienda_para_bebida_caliente(mode: str, run_turn: RunTurn) -> None:
    # Arrange
    question = "Necesito 1000 vasos térmicos de 12 oz para café caliente en México"
    script = [
        _call(
            "buscar_producto",
            uso="bebida_caliente",
            temperatura="caliente",
            familia="vaso",
            capacidad_oz=12,
            pais="MX",
        ),
        ScriptStep(text="Para café caliente le sirven los vasos de papel de 12 oz [1]."),
    ]

    # Act
    turn = run_turn(mode, question, script)

    # Assert
    options = turn.products.get("options", [])
    discarded = turn.products.get("discarded", [])
    assert options, "debe haber alternativas válidas para bebida caliente"
    assert "EPS" not in _materials(options)
    assert "EPS" in _materials(discarded)
    assert all(item.get("reason") for item in discarded)


@pytest.mark.parametrize("mode", MODES)
def test_eps_no_se_recomienda_para_microondas(mode: str, run_turn: RunTurn) -> None:
    # Arrange
    question = "Necesito 500 contenedores para comida caliente para microondas en México"
    script = [
        _call(
            "buscar_producto",
            uso="comida_caliente",
            temperatura="caliente",
            microondas=True,
            familia="contenedor",
            pais="MX",
        ),
        ScriptStep(text="Ninguna referencia del catálogo declara ser apta para microondas."),
    ]

    # Act
    turn = run_turn(mode, question, script)

    # Assert
    options = turn.products.get("options", [])
    discarded = turn.products.get("discarded", [])
    assert "EPS" not in _materials(options)
    eps_reasons = [
        item["reason"] for item in discarded if CATALOG_MATERIALS.get(item["sku"]) == "EPS"
    ]
    assert eps_reasons
    assert all("microondas" in reason for reason in eps_reasons)


# --- Precio ausente sin estimar (FR-003) ---------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
def test_precio_ausente_se_declara_sin_estimar(mode: str, run_turn: RunTurn) -> None:
    # Arrange
    question = "Necesito 1000 vasos para café en Bogotá"
    script = [
        _call(
            "buscar_producto",
            uso="bebida_caliente",
            temperatura="caliente",
            familia="vaso",
            pais="CO",
        ),
        ScriptStep(text="Hay vasos rígidos; uno de ellos no tiene precio publicado."),
    ]

    # Act
    turn = run_turn(mode, question, script)

    # Assert
    options = turn.products.get("options", [])
    without_price = [option for option in options if option.get("price_note")]
    assert without_price, "el catálogo debe tener una opción sin precio publicado"
    for option in without_price:
        assert "total" not in option
        assert "price_per_box" not in option
        assert not option.get("offers")
        assert "no se estima" in option["price_note"]


# --- Total pendiente si falta el envío (FR-003, data-model QuoteRequest.delivery) ---------


@pytest.mark.parametrize("mode", MODES)
def test_total_pendiente_si_el_envio_no_tiene_precio(mode: str, run_turn: RunTurn) -> None:
    # Arrange
    question = "Necesito 1000 vasos para café en Bogotá con domicilio"
    script = [
        _call(
            "cotizar",
            use="bebida_caliente",
            temperature="caliente",
            family="vaso",
            quantity=1000,
            city="Bogotá",
            delivery=True,
        ),
        ScriptStep(text="El subtotal está calculado; el envío y el total quedan pendientes."),
    ]

    # Act
    turn = run_turn(mode, question, script)

    # Assert
    quotes = [item for item in _quotes(turn.products) if item.get("kind") == "cotizacion"]
    assert quotes, "debe haber una cotización con subtotal respaldado"
    for quote in quotes:
        assert quote["commercial"]["subtotal"]
        assert quote["commercial"]["shipping"]["status"] == "pending"
        assert quote["commercial"]["shipping"]["cost"] is None
        assert quote["commercial"]["total"] is None
        assert quote["total"] is None
        assert "queda pendiente" in quote["reply"]


# --- Abstención sin evidencia (FR-004) -----------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    ("question", "hits"),
    [
        pytest.param("¿Cuál es el teléfono de Tokio?", (), id="sin-resultados"),
        pytest.param("¿Cuándo abrió Carvajal su sede en Tokio?", (HIT,), id="entidad-sin-respaldo"),
    ],
)
def test_sin_evidencia_se_abstiene_sin_generar(
    mode: str, question: str, hits: tuple[ScoredChunk, ...], run_turn: RunTurn
) -> None:
    # Arrange
    script = [
        _call("buscar_corpus", consulta=question),
        # La compuerta debe descartar este paso: afirma datos sin evidencia.
        ScriptStep(text="Carvajal abrió su sede en Tokio en 1950; el teléfono es 555-0100."),
    ]

    # Act
    turn = run_turn(mode, question, script, hits=hits)

    # Assert
    assert turn.abstained
    assert turn.answer == NO_CONTEXT_ANSWER
    assert turn.sources == ()


# --- Fuente de tercero etiquetada como tercero (FR-007 de la 036, spec 039 FR-005) --------


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    ("question", "script"),
    [
        pytest.param(
            "¿Cuáles fueron los ingresos consolidados de Carvajal en 2024?",
            [
                _call("indicador_financiero", nombre="ingresos_consolidados", periodo="2024"),
                ScriptStep(text="Los ingresos consolidados de 2024 fueron $3,96 billones [1]."),
            ],
            id="indicador-de-prensa",
        ),
        pytest.param(
            "Necesito 1000 vasos para café en Bogotá",
            [
                _call(
                    "cotizar",
                    use="bebida_caliente",
                    temperature="caliente",
                    family="vaso",
                    quantity=1000,
                    city="Bogotá",
                ),
                ScriptStep(text="El precio lo publica un distribuidor [1]."),
            ],
            id="precio-de-distribuidor",
        ),
    ],
)
def test_fuente_de_tercero_se_etiqueta_como_tercero(
    mode: str, question: str, script: list[ScriptStep], run_turn: RunTurn
) -> None:
    # Act
    turn = run_turn(mode, question, script)

    # Assert
    third_party = [source for source in turn.sources if not _is_official(source["url"])]
    assert third_party, "el turno debe citar al menos una fuente externa a Carvajal"
    assert all(source["source_type"] == "tercero" for source in third_party)
    assert all(source["url"] for source in turn.sources)


def test_run_turn_rechaza_un_modo_desconocido(run_turn: RunTurn) -> None:
    # Act / Assert
    with pytest.raises(ValueError, match="modo desconocido"):
        run_turn("otro", "Hola")
