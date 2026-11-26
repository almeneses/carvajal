"""Las seis herramientas de la spec 044, movidas en bloque desde ``tools.py`` (spec 045).

Contrato: ``specs/044-agente-tool-calling/contracts/agent-tools.md``. Todas son
deterministas y no persisten nada. ``cotizar`` y ``buscar_producto`` comparten ayudantes
(búsqueda por nombre, opciones, ciudad dicha), así que no se parten por módulo (research
R9); las herramientas nuevas de la 045 viven cada una en su propio módulo.
"""

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from typing import Annotated, Any, Literal, cast

from pydantic import BaseModel, Field, StringConstraints, ValidationError, field_validator

from carvajal.agent.commercial import (
    CAPACITY_TOLERANCE_OZ,
    CommercialRequest,
    check_material,
    search_packaging,
)
from carvajal.agent.commercial_flow import (
    MAX_DISCARDED,
    _channel,
    _contract_sites,
    _option,
    commercial_sources,
)
from carvajal.agent.commercial_intent import (
    USE_TEMPERATURE,
    USES,
    family_fits_use,
    family_for_use,
    material_for,
    microwave_for,
    named_families,
    named_use,
    stated_product,
)
from carvajal.agent.memory.store import MemoryProfile
from carvajal.agent.reference_names import (
    capacity_fits,
    informative_terms,
    match_named,
    matched_terms,
    query_terms,
    resolve_reference,
)
from carvajal.agent.toolagent.state import (
    TOOL_BUSCAR_CORPUS,
    TOOL_BUSCAR_PRODUCTO,
    TOOL_BUSCAR_SEDES,
    TOOL_COTIZAR,
    TOOL_INDICADOR_FINANCIERO,
    TOOL_SIN_CONSULTA,
)
from carvajal.agent.toolagent.tools._base import (
    CORE_LAYER,
    ToolContext,
    ToolDeps,
    _book,
    _Draft,
    _is_official,
    _now_bogota,
    _Sources,
    merge_quote,
)
from carvajal.agent.tools import indicator_lookup, known_names, resolve_indicator_name
from carvajal.application.ask import Source, _source
from carvajal.application.evidence import _entities, proper_entities_supported
from carvajal.application.ports import ScoredChunk
from carvajal.catalog.loader import Catalog, load_catalog
from carvajal.catalog.models import Family, Reference
from carvajal.commercial.book import CommercialBook, plain
from carvajal.commercial.models import price_origin, seller_source_type
from carvajal.quotation import Dataset, QuoteRequest, load_dataset, quote_request
from carvajal.quotation.data import REFERENCE_SITE_ID

# Movido en bloque desde ``toolagent/tools.py`` (spec 045, T008; research R9): el código
# no se reescribió. Ver el docstring de ``toolagent/tools/__init__.py``.

FRAGMENT_CHARS = 1500  # texto máximo de un fragmento del corpus que ve el modelo
# Opciones aptas que ve el modelo en ``buscar_producto``. Con tres (``MAX_OPTIONS`` de la
# ruta comercial) el modelo daba por inexistentes referencias que sí estaban (simulación de
# la spec 044: CO-PCOM-J2, CO-CTER-34); el resto se declara con ``total_coincidencias``.
MAX_TOOL_OPTIONS = 8
# Opciones que se presentan al cliente (texto y tarjetas), como las alternativas de una
# cotización (``quotation.engine.MAX_ALTERNATIVES``). El modelo ve ``MAX_TOOL_OPTIONS`` para
# no negar referencias que existen, pero presenta como mucho estas (pedido del owner,
# 2026-11-25); ``apps/api/chat/products.presented_options`` recorta las tarjetas igual.
MAX_PRESENTED_OPTIONS = 4
MAX_OUTSIDE_FILTERS = 5  # coincidencias por nombre fuera de los filtros que se nombran
MAX_NAME_CHARS = 120
_AVAILABILITY_LABELS = {"verificado": "confirmada", "no_disponible": "no disponible"}
_PRODUCT_FIELDS = ("family", "use", "capacity_oz", "temperature", "microwave", "material")
_SHARED_FIELDS = ("quantity", "city", "delivery", "prefers_renewable")
_PRODUCT_CONTEXT_KEYS = {
    "uso": "use",
    "temperatura": "temperature",
    "microondas": "microwave",
    "capacidad_oz": "capacity_oz",
    "familia": "family",
    "prefiere_renovable": "prefers_renewable",
    "pais": "country",
}

Temperature = Literal["caliente", "fria", "ambiente"]
Material = Literal["papel", "pulpa", "icopor", "plastico", "pet", "pp", "ps"]
Unit = Literal["unidades", "cajas", "paquetes", "pacas"]
# Unidad de ``cotizar`` → unidad del motor (``QuoteRequest.unit``).
_UNIT_BASIS: dict[str, str] = {
    "unidades": "unidad",
    "cajas": "caja",
    "paquetes": "paquete",
    "pacas": "paca",
}
_Period = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=r"^\d{4}(-(0[1-9]|1[0-2]))?$")
]
_USES_TEXT = ", ".join(sorted(USES))

# --- Esquemas de entrada -------------------------------------------------------------------


def _optional_text(value: object) -> object:
    """Texto opcional: vacío o solo espacios cuenta como ausente."""

    if isinstance(value, str):
        stripped = " ".join(value.split())
        return stripped or None
    return value


def _check_use(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if normalized not in USES:
        raise ValueError(f"uso desconocido: {value!r}; válidos: {_USES_TEXT}")
    return normalized


class BuscarCorpusArgs(BaseModel):
    """Búsqueda en el corpus documental de Carvajal."""

    consulta: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
    ] = Field(
        description=(
            "Reformulación autocontenida de lo que se busca, con los nombres propios "
            "necesarios (de 1 a 300 caracteres)."
        )
    )
    alcance: Literal["oficial", "todo"] = Field(
        default="todo",
        description="«oficial» busca solo en la capa núcleo de Carvajal; «todo» incluye terceros.",
    )


class BuscarProductoArgs(BaseModel):
    """Búsqueda de referencias del catálogo de empaques."""

    uso: str | None = Field(default=None, description=f"Uso del empaque: {_USES_TEXT}.")
    temperatura: Temperature | None = Field(
        default=None, description="Temperatura del contenido: caliente, fria o ambiente."
    )
    microondas: bool | None = Field(default=None, description="Si se calentará en microondas.")
    capacidad_oz: float | None = Field(
        default=None, gt=0, le=200, description="Capacidad en onzas líquidas."
    )
    familia: Family | None = Field(
        default=None,
        description="Familia: vaso, tapa, contenedor, charola, cubierto, envase o plato.",
    )
    prefiere_renovable: bool = Field(
        default=False, description="Si la persona prefiere papel o pulpa."
    )
    pais: Literal["CO", "MX"] | None = Field(
        default=None, description="País de compra (CO o MX); sin país no hay precio."
    )
    nombre: str | None = Field(
        default=None,
        max_length=MAX_NAME_CHARS,
        description=(
            "Texto libre para una referencia concreta: código (P1, J2, 14x14), SKU "
            "(CO-PCOM-J2) o nombre comercial o coloquial (icopor, portacomidas, bowl, pando). "
            "Filtra por coincidencia en nombre, SKU y material; se combina con los demás "
            "filtros."
        ),
    )

    _uso = field_validator("uso", mode="before")(_optional_text)
    _nombre = field_validator("nombre", mode="before")(_optional_text)

    @field_validator("uso")
    @classmethod
    def _uso_valido(cls, value: str | None) -> str | None:
        return _check_use(value)


class CotizarArgs(BaseModel):
    """Solicitud de cotización (``QuoteRequest``); los campos ausentes se toman del contexto."""

    family: Family | None = Field(
        default=None,
        description="Familia: vaso, tapa, contenedor, charola, cubierto, envase o plato.",
    )
    use: str | None = Field(default=None, description=f"Uso del empaque: {_USES_TEXT}.")
    capacity_oz: float | None = Field(
        default=None, gt=0, le=200, description="Capacidad en onzas líquidas."
    )
    temperature: Temperature | None = Field(
        default=None, description="Temperatura del contenido: caliente, fria o ambiente."
    )
    microwave: bool | None = Field(default=None, description="Si se calentará en microondas.")
    prefers_renewable: bool | None = Field(
        default=None, description="Si la persona prefiere papel o pulpa."
    )
    material: Material | None = Field(
        default=None,
        description=(
            "Material que pidió la persona («vasos de papel» → papel; icopor, pulpa, "
            "plastico, pet, pp, ps). Filtra las referencias: nunca se cotiza otro material."
        ),
    )
    quantity: int | None = Field(
        default=None,
        ge=1,
        le=10_000_000,
        description="Cantidad tal como la dijo la persona, en la unidad de «unidad».",
    )
    cantidades: list[int] | None = Field(
        default=None,
        min_length=2,
        max_length=4,
        description=(
            "Spec 054: de 2 a 4 cantidades del MISMO producto para comparar precios («¿cuánto "
            "valen 100, 500 o 1000?»). Se cotizan todas con la referencia de la primera; no "
            "pases quantity a la vez."
        ),
    )
    unidad: Unit | None = Field(
        default=None,
        description=(
            "Unidad de la cantidad: unidades (por defecto), cajas, paquetes o pacas. Pasa la "
            "que dijo la persona («10 cajas» → quantity 10, unidad cajas); la herramienta la "
            "convierte a unidades con la presentación de cada referencia."
        ),
    )
    city: str | None = Field(
        default=None,
        max_length=80,
        description=(
            "Ciudad de Colombia donde se compra, solo si la dijo la persona; sin ciudad la "
            "herramienta la pide."
        ),
    )
    delivery: bool | None = Field(
        default=None,
        description="true con domicilio, false con retiro en sede; null si no se sabe.",
    )
    sku: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "SKU de la referencia que nombró la persona (como CO-PCOM-J2), tal como aparece "
            "en los datos de las herramientas; fija la referencia que se cotiza."
        ),
    )
    nombre: str | None = Field(
        default=None,
        max_length=MAX_NAME_CHARS,
        description=(
            "Referencia concreta que nombró la persona: código (P1, J2) o nombre comercial "
            "(Vaso Gold de 14 oz). Se busca como en buscar_producto y se cotiza exactamente "
            "esa, sin alternativas. Una descripción genérica (vasos de icopor) no es una "
            "referencia: pásala en family y use."
        ),
    )

    _texts = field_validator("use", "city", "sku", "nombre", mode="before")(_optional_text)

    @field_validator("use")
    @classmethod
    def _uso_valido(cls, value: str | None) -> str | None:
        return _check_use(value)

    @field_validator("material", mode="before")
    @classmethod
    def _material_normalizado(cls, value: object) -> object:
        # El modelo a veces pasa el código del catálogo («EPS», «PAPEL») o una palabra con
        # tilde («plástico»): se lleva al nombre del esquema; lo desconocido no valida.
        if not isinstance(value, str) or not value.strip():
            return None if isinstance(value, str) else value
        return material_for(value) or value.strip().lower()


class BuscarSedesArgs(BaseModel):
    """Sedes del directorio de distribuidores en una ciudad de Colombia."""

    ciudad: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
    ] = Field(description="Ciudad de Colombia.")
    sku: str | None = Field(
        default=None, max_length=64, description="SKU para marcar las sedes que lo ofrecen."
    )

    _sku = field_validator("sku", mode="before")(_optional_text)


class IndicadorFinancieroArgs(BaseModel):
    """Indicador financiero publicado de Carvajal."""

    nombre: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
    ] = Field(description="Indicador: ingresos_consolidados, ebitda, utilidad_neta, activos…")
    periodo: _Period | None = Field(default=None, description="Año (2024) o año-mes (2025-09).")

    _periodo = field_validator("periodo", mode="before")(_optional_text)


class SinConsultaArgs(BaseModel):
    """Declaración explícita de que el turno se responde sin consultar datos."""

    motivo: Literal["pedir_dato", "conversacion", "fuera_de_alcance"] = Field(
        description="pedir_dato, conversacion o fuera_de_alcance."
    )
    dato: str | None = Field(
        default=None, max_length=120, description="Con pedir_dato: qué dato falta."
    )

    _dato = field_validator("dato", mode="before")(_optional_text)


# Descripciones de la 044 que ve el modelo; el registro (``registry.py``) las expone como
# ``TOOL_DESCRIPTIONS``. Cualquier cambio actualiza el *golden* (FR-027).
DESCRIPTIONS: dict[str, str] = {
    TOOL_BUSCAR_CORPUS: (
        "Busca en los documentos públicos de Carvajal (historia, empresas, sostenibilidad, "
        "noticias, sedes institucionales). Devuelve fragmentos numerados [n] con su fuente. "
        "No la uses para: hitos por año (usa historia_carvajal), las empresas del grupo (usa "
        "empresas_del_grupo) ni preguntas frecuentes (usa preguntas_frecuentes)."
    ),
    TOOL_BUSCAR_PRODUCTO: (
        "Busca empaques del catálogo según uso, temperatura, microondas, capacidad, "
        "familia y nombre (código como P1 o J2, SKU o nombre comercial o coloquial como "
        f"icopor). Devuelve hasta {MAX_TOOL_OPTIONS} opciones aptas numeradas [n], el total "
        "de coincidencias y las descartadas con su motivo. No calcula totales; sirve para "
        "explorar y comparar. No la uses para: la ficha de una referencia ya nombrada (usa "
        "ficha_producto) ni comparar referencias ya nombradas (usa comparar_referencias)."
    ),
    TOOL_COTIZAR: (
        "Calcula una cotización en Colombia para un producto, una cantidad y una ciudad, con "
        "el precio publicado de un vendedor. Los datos ausentes se completan con el contexto "
        "comercial del hilo. Solo para combinaciones que la persona pidió. Si la persona "
        "nombró una referencia concreta (código como P1 o J2, SKU o nombre comercial), pásala "
        "en sku o nombre: se cotiza exactamente esa, sin alternativas, y si el nombre es "
        "ambiguo devuelve las candidatas. La cantidad va en "
        "la unidad que dijo la persona (unidad: cajas, paquetes, pacas); la herramienta la "
        "convierte y devuelve la conversión: no la calcules tú. Sin ciudad dicha por la "
        "persona, o con una ciudad fuera del directorio, pide la ciudad. No la uses para: "
        "convertir una cantidad sin pedir precio (usa convertir_presentacion) ni leer una "
        "cotización ya emitida (usa consultar_cotizacion)."
    ),
    TOOL_BUSCAR_SEDES: (
        "Lista las sedes de distribuidores de una ciudad de Colombia con dirección y teléfono; "
        "con un SKU marca las que lo ofrecen. No la uses para: saber en qué ciudades hay "
        "distribuidores (usa ciudades_con_cobertura)."
    ),
    TOOL_INDICADOR_FINANCIERO: (
        "Consulta un indicador financiero publicado de Carvajal (ingresos, ebitda, utilidad, "
        "activos, empleados) para un año o un año-mes. No estima ni interpola. No la uses "
        "para: saber qué indicadores o períodos hay (usa indicadores_disponibles)."
    ),
    TOOL_SIN_CONSULTA: (
        "Declara que este paso se responde sin consultar datos: para pedir un dato que falta, "
        "para conversación (saludo, agradecimiento) o para algo fuera del alcance."
    ),
}

# --- Ayudantes de las seis ------------------------------------------------------------------


def _today(deps: ToolDeps) -> date:
    # ``deps.today`` (pruebas) manda; si no, la fecha de Bogotá del reloj único (R11).
    return deps.today or _now_bogota(deps).date()


# --- buscar_corpus (T017) ------------------------------------------------------------------


def _retrieve(deps: ToolDeps, query: str, layer: str | None) -> list[ScoredChunk]:
    """Misma recuperación que ``AskQuestion.prepare``, con capa opcional.

    Se conservan los ``ScoredChunk`` (``prepare`` solo devuelve extractos de 280
    caracteres) para que el modelo lea el texto del fragmento.
    """

    settings = deps.settings
    embedding = deps.embedder.embed(query)
    search_text = getattr(deps.vector_store, "search_text", None)
    if callable(search_text):
        results = search_text(
            query,
            embedding,
            limit=settings.rag_top_k,
            layer=layer,
            min_score=settings.rag_min_score,
        )
    else:
        results = deps.vector_store.search(embedding, limit=settings.rag_top_k, layer=layer)
    return [
        result
        for result in results
        if result.score >= settings.rag_min_score and (layer is None or result.chunk.layer == layer)
    ][: settings.rag_top_k]


# Fin de oración en la pregunta: la mayúscula que abre la siguiente no es nombre propio.
_SENTENCE_BREAK = re.compile(r"[.!?¡¿\n]+")


def _entities_supported(
    question: str, query: str, texts: list[str], commercial_cities: Iterable[str]
) -> bool:
    """``proper_entities_supported`` sobre la consulta y la pregunta original del turno.

    Toda entidad de la pregunta original debe aparecer en los fragmentos, la nombre o no la
    consulta del modelo: reformular sin la entidad no consigue evidencia (contrato de
    ``buscar_corpus``). Dos ajustes para no vaciar búsquedas legítimas:

    - la pregunta se evalúa por oraciones, así que la mayúscula que abre la segunda
      («…en Bogotá? Dime…») no cuenta como nombre propio;
    - una ciudad con cobertura del directorio comercial que la consulta no nombra es de la
      parte comercial de una pregunta compuesta («2.000 vasos en Bogotá y quién fundó
      Carvajal»): la respaldan ``cotizar`` y ``buscar_sedes``, no el corpus.
    """

    if not proper_entities_supported(query, texts):
        return False
    folded_query = plain(query)
    context = plain(" ".join(texts))
    cities = {plain(city) for city in commercial_cities}
    for sentence in _SENTENCE_BREAK.split(question):
        for entity in _entities(sentence):
            folded = plain(entity)
            if folded in context:
                continue
            if folded in cities and folded not in folded_query:
                continue
            return False
    return True


def _fragment_type(source: Source) -> str:
    return (
        "oficial" if source.source_type == "oficial" and source.layer != "extended" else "tercero"
    )


def _buscar_corpus(
    args: BuscarCorpusArgs, deps: ToolDeps, context: ToolContext, sources: _Sources
) -> _Draft:
    layer = CORE_LAYER if args.alcance == "oficial" else None
    results = _retrieve(deps, args.consulta, layer)
    if not results:
        return _Draft("empty", "No hay fragmentos del corpus sobre el umbral de relevancia.")
    texts = [result.chunk.text for result in results]
    question = context.question or args.consulta
    if not _entities_supported(question, args.consulta, texts, _book(deps).cities()):
        return _Draft(
            "empty",
            "Los fragmentos recuperados no mencionan un nombre propio de la pregunta; "
            "no hay evidencia.",
        )
    cited = [_source(result) for result in results]
    numbers = sources.add(cited)
    fragments = [
        {
            "n": number,
            "titulo": source.title or source.slug,
            "seccion": source.section,
            "tipo_fuente": _fragment_type(source),
            "capa": source.layer,
            "texto": result.chunk.text[:FRAGMENT_CHARS],
        }
        for number, source, result in zip(numbers, cited, results, strict=True)
    ]
    return _Draft(
        "ok",
        f"{len(fragments)} fragmento(s) del corpus; cada uno se cita con su número [n].",
        data={"fragmentos": fragments},
    )


# --- buscar_producto (T018) ----------------------------------------------------------------

# Búsqueda por nombre: la normalización (códigos enteros, icopor → EPS, sin tamaño del
# empaque) vive en ``carvajal.agent.reference_names`` y la comparte ``cotizar``.


def _candidates(req: CommercialRequest, catalog: Catalog) -> list[Reference]:
    """``search_packaging`` con la capacidad también leída del nombre de la ficha."""

    found = search_packaging(replace(req, capacity_oz=None), catalog=catalog)
    if req.capacity_oz is None:
        return found
    capacity = req.capacity_oz
    return [reference for reference in found if capacity_fits(reference, capacity)]


def _temperature(
    explicit: Temperature | None, use: str | None, microwave: bool | None
) -> Temperature | None:
    """Temperatura dada, la que implica el uso o, con microondas, «caliente».

    Calentar en microondas implica contenido caliente (como ``commercial_intent``): sin
    esto, un vaso de PP declarado solo para bebida fría pasaba como apto (s-clientes-20).
    """

    implied = cast(Temperature | None, USE_TEMPERATURE.get(use or ""))
    return explicit or implied or ("caliente" if microwave else None)


def _temperature_matters(refs: list[Any], req: CommercialRequest, catalog: Catalog) -> bool:
    """Sin temperatura, ¿cambiaría el resultado según sea caliente o fría? (FR-005)."""

    if req.temperature or req.microwave or not refs:
        return False

    def accepted(temperature: Temperature | None) -> set[str]:
        changed = replace(req, temperature=temperature)
        valid, _ = check_material(list(refs), changed, catalog=catalog)
        return {reference.sku for reference in valid}

    unknown = accepted(None)
    return any(accepted(value) != unknown for value in ("caliente", "fria"))


def _price_numbers(option: Mapping[str, Any], sources: _Sources) -> tuple[int, dict[str, int]]:
    """Registra la ficha y los precios de terceros de una opción; devuelve sus números."""

    batch = commercial_sources({"options": [option]})
    numbers = sources.add(batch)
    by_url = {
        str(entry.get("url")): number
        for entry, number in zip(batch, numbers, strict=True)
        if str(entry.get("chunk_id", "")).startswith("precio:")
    }
    return numbers[0], by_url


def _option_payload(option: Mapping[str, Any], sources: _Sources) -> dict[str, Any]:
    number, price_numbers = _price_numbers(option, sources)
    payload: dict[str, Any] = {
        "n": number,
        "sku": option["sku"],
        "nombre": option["name"],
        "material": option["material"],
        "capacidad_oz": option["capacity_oz"],
        "por_caja": option["units_per_box"],
        "pais": option["country"],
        "fecha_ficha": option["captured_at"],
    }
    offers = option.get("offers") or []
    if offers:
        first = offers[0]
        payload["precio_referencia"] = {
            "precio_por_caja": first.get("price_per_box"),
            "impuestos": first.get("tax_status"),
            "vendedor": first.get("source_name") or "Carvajal",
            "tipo_fuente": first.get("source_type"),
            "ciudad": first.get("source_city"),
            "aviso": first.get("notice"),
            "n": price_numbers.get(str(first.get("source_url")), number),
        }
        if len(offers) > 1:
            payload["otras_ofertas"] = len(offers) - 1
    elif option.get("price_note"):
        payload["nota_precio"] = option["price_note"]
    return payload


def _suitable_for(req: CommercialRequest) -> dict[str, Any]:
    """Criterios con que ``check_material`` dio por aptas las opciones (``aptas_para``).

    Es un dato de la herramienta: las opciones pasaron la guía de materiales para esa
    temperatura y, si se pidió, para microondas. Sin él, la compuerta de afirmaciones no veía
    por qué «se pueden meter al microondas» (ensayo del 2026-11-26, microondas t1).
    """

    criteria: dict[str, Any] = {}
    if req.temperature:
        criteria["temperatura"] = req.temperature
    if req.microwave:
        criteria["microondas"] = True
    return {"aptas_para": criteria} if criteria else {}


@dataclass(frozen=True)
class _NameMatch:
    """Resultado de la búsqueda por nombre dentro de los filtros y fuera de ellos.

    ``outside`` son las coincidencias fuera de los filtros aptas por material para la
    búsqueda; ``outside_rejected``, las que no lo son, con su motivo de descarte.
    """

    named: list[Reference]
    partial: bool = False
    outside: list[Reference] = field(default_factory=list)
    outside_rejected: list[tuple[Reference, str]] = field(default_factory=list)


def _name_search(
    query: Sequence[frozenset[str]],
    name: str,
    found: list[Reference],
    req: CommercialRequest,
    catalog: Catalog,
) -> _NameMatch:
    """Referencias de ``found`` que coinciden con el nombre.

    Primero las que tienen todos los términos; si no hay y la búsqueda tiene varios, las
    que tienen todos menos uno con al menos un término informativo (``partial``: «vaso de
    icopor 16 oz» trae los vasos de EPS de otra capacidad y los de 16 oz de otro material,
    con su motivo de descarte; «vaso J2» no trae todos los vasos). Si tampoco, las del mismo
    país que tienen los términos informativos fuera de la familia, el uso o la capacidad
    (``outside``), separadas por material como las opciones (``check_material``): una
    referencia no apta nunca se nombra como candidata. El SKU exacto va primero.
    """

    exact = plain(name)
    match = match_named(query, name, found, req.family, catalog)
    if match.named:
        return _NameMatch(named=match.named, partial=match.partial)
    informative = informative_terms(query, found, req.family, catalog)
    skus = {reference.sku for reference in found}
    wanted = informative or list(query)
    outside = [
        reference
        for reference in catalog.references
        if reference.sku not in skus
        and (req.country is None or reference.country == req.country)
        and matched_terms(reference, wanted) == len(wanted)
    ]
    outside.sort(key=lambda reference: plain(reference.sku) != exact)
    valid, discarded = check_material(outside, req, catalog=catalog)
    by_sku = {reference.sku: reference for reference in outside}
    rejected = [(by_sku[item.sku], item.reason) for item in discarded]
    return _NameMatch(named=[], outside=valid, outside_rejected=rejected)


def _product_notices(
    *,
    match: _NameMatch | None,
    shown: int,
    valid: int,
    discarded: int,
) -> list[str]:
    """Avisos para que el modelo no dé por inexistente lo que no vio (simulación spec 044).

    No repiten el ``nombre`` que escribió el modelo: el aviso va en los datos de la
    herramienta y un eco del argumento respaldaría sus propias cifras en el filtro.
    """

    notices: list[str] = []
    named = len(match.named) if match is not None else 0
    outside = match.outside if match is not None else []
    rejected = match.outside_rejected if match is not None else []
    if match is not None and match.partial:
        notices.append(
            "Ninguna referencia coincide con todos los términos del nombre buscado con estos "
            "filtros; se listan las que coinciden con todos menos uno (coincidencia parcial), "
            "que no son exactamente la referencia nombrada."
        )
    if match is not None and not named:
        notices.append(
            "Ninguna referencia coincide con el nombre buscado con estos filtros (familia, "
            "uso, capacidad y país)."
        )
        if outside:
            listed = ", ".join(
                f"{reference.sku} ({reference.family})"
                for reference in outside[:MAX_OUTSIDE_FILTERS]
            )
            notices.append(
                f"El nombre sí coincide con {len(outside)} referencia(s) fuera de estos "
                f"filtros, aptas por material para esta búsqueda: {listed}."
            )
        if rejected:
            notices.append(
                f"Otras {len(rejected)} referencia(s) fuera de estos filtros coinciden con el "
                "nombre pero no son aptas por material o uso (apto: false, con su motivo); no "
                "son opciones."
            )
        if shown:
            notices.append(
                "Las opciones listadas son otras referencias aptas de la misma búsqueda y no "
                "son la referencia nombrada."
            )
    elif match is not None and not valid:
        notices.append(
            f"Las {named} referencia(s) que coinciden con el nombre buscado quedaron "
            "descartadas por material o uso para esta búsqueda."
        )
    if valid > shown:
        how = (
            "un nombre más preciso acota la lista"
            if match is not None
            else "una referencia concreta (código como P1 o J2, SKU o nombre comercial) se "
            "encuentra con el argumento nombre"
        )
        notices.append(
            f"Se muestran {shown} de {valid} referencias aptas; las demás no se listan. Que una "
            f"referencia no esté en esta lista no significa que no exista: {how}."
        )
    if discarded > MAX_DISCARDED:
        notices.append(f"Se listan {MAX_DISCARDED} de {discarded} referencias descartadas.")
    return notices


@dataclass(frozen=True)
class _UseFamily:
    """Familia corregida por el uso: ``previous`` es la que pasó el modelo.

    ``product`` trae la capacidad, la temperatura y el microondas que la persona dijo en el
    turno (``None`` en lo que calló): los que arrastró el modelo eran del producto anterior.
    """

    family: str
    use: str
    previous: str
    product: dict[str, Any]

    def notice(self, action: str = "se buscó") -> str:
        return (
            f"La familia {self.previous} no sirve para el uso {self.use} y la persona no la "
            f"nombró en este turno: {action} en la familia {self.family}, la de ese uso. "
            f"Presenta estas opciones como {self.family}, no como {self.previous}, y dilo."
        )


def _family_for_use(family: str | None, question: str) -> _UseFamily | None:
    """Corrección de una familia heredada que no sirve para el uso (mejora 4 del ensayo).

    «¿y para sopa caliente?» después de hablar de vasos: el modelo arrastra ``vaso`` del
    turno anterior y buscaría vasos para sopa. Solo corrige el uso que la persona nombra en
    este turno, sin nombrar ninguna familia, como ``_inherit`` en las reglas: el uso que
    pasa el modelo o el del contexto puede ser una elección anterior («vasos para comida
    caliente» y luego «¿y de 16 onzas?»), y entonces la familia ya era la de la persona.
    """

    if family is None or named_families(question):
        return None
    wanted = named_use(question)
    if wanted is None or family_fits_use(family, wanted):
        return None
    corrected = family_for_use(family, wanted)
    if corrected is None:
        return None
    return _UseFamily(
        family=corrected, use=wanted, previous=family, product=stated_product(question)
    )


def _buscar_producto(
    args: BuscarProductoArgs, deps: ToolDeps, context: ToolContext, sources: _Sources
) -> _Draft:
    data = deps.catalog or load_catalog()
    by_use = _family_for_use(args.familia, context.question)
    if by_use is not None:
        args = args.model_copy(
            update={
                "familia": by_use.family,
                "uso": by_use.use,
                "capacidad_oz": by_use.product["capacity_oz"],
                "temperatura": by_use.product["temperature"],
                "microondas": by_use.product["microwave"],
            }
        )
    # Red de seguridad: si la persona pidió microondas y el modelo no lo pasó, se aplica
    # igual, para no recomendar un material que la guía descarta (EPS, evaluación spec 044).
    microwave = args.microondas
    if microwave is None:
        microwave = microwave_for(context.question, args.familia)
    req = CommercialRequest(
        use=args.uso,
        temperature=_temperature(args.temperatura, args.uso, microwave),
        microwave=microwave,
        capacity_oz=args.capacidad_oz,
        family=args.familia,
        quantity=None,  # sin cantidad no hay totales: explorar no es cotizar
        country=args.pais,
        city=None,
        prefers_renewable=args.prefiere_renovable,
    )
    update: dict[str, Any] = {
        _PRODUCT_CONTEXT_KEYS[key]: value
        for key, value in args.model_dump(exclude_unset=True, exclude_none=True).items()
        if key in _PRODUCT_CONTEXT_KEYS  # el nombre describe esta búsqueda, no la necesidad
    }
    if any(key in update for key in _PRODUCT_FIELDS):
        # Explorar otro producto (otra familia, uso o capacidad) suelta la referencia de la
        # cotización anterior: un ``cotizar`` genérico posterior no debe volver a cotizarla.
        update["sku"] = None
    if by_use is not None:
        # El contexto guarda la familia y el uso corregidos, no el vaso que arrastró el
        # modelo: un ``cotizar`` posterior sin familia sigue en la del uso.
        # La capacidad, la temperatura y el microondas son los que dijo la persona; ``None``
        # olvida los del vaso.
        update.update({"family": by_use.family, "use": by_use.use, "sku": None, **by_use.product})
    if args.pais and args.pais not in data.countries:
        return _Draft(
            "empty",
            f"El catálogo cargado no tiene referencias de {args.pais}.",
            data={"pais_catalogo": args.pais},
            context_update=update,
        )
    found = _candidates(req, data)
    query = query_terms(args.nombre)
    name = args.nombre if query else None
    match: _NameMatch | None = None
    name_data: dict[str, Any] = {}
    if name is not None:
        match = _name_search(query, name, found, req, data)
        # Sin el ``nombre``: lo escribe el modelo y su eco respaldaría sus cifras (FR-003).
        search: dict[str, Any] = {"coincidencias": len(match.named)}
        if match.partial:
            search["parcial"] = True
        name_data["busqueda_nombre"] = search
        outside = [
            {"sku": item.sku, "nombre": item.name, "familia": item.family, "apto": True}
            for item in match.outside
        ] + [
            {"sku": item.sku, "familia": item.family, "apto": False, "motivo": reason}
            for item, reason in match.outside_rejected
        ]
        if outside:
            name_data["fuera_de_filtros"] = outside[:MAX_OUTSIDE_FILTERS]
        if match.named:
            found = match.named  # sin coincidencias se ofrecen las de la familia, con aviso
    if _temperature_matters(found, req, data):
        return _Draft(
            "needs_input",
            "Falta la temperatura del contenido: de ella depende qué materiales son aptos.",
            data=name_data,
            missing=["temperatura"],
            context_update=update,
        )
    valid, discarded = check_material(found, req, catalog=data)
    shown = valid[:MAX_TOOL_OPTIONS]
    notices = _product_notices(
        match=match,
        shown=len(shown),
        valid=len(valid),
        discarded=len(discarded),
    )
    if by_use is not None:
        notices.insert(0, by_use.notice())
        name_data["familia_por_uso"] = {"familia": by_use.family, "uso": by_use.use}
    if not valid and not discarded:
        notice_data = {"aviso": " ".join(notices)} if notices else {}
        return _Draft(
            "empty",
            "Ninguna referencia del catálogo coincide con la familia, el uso y la capacidad.",
            # El país del catálogo consultado: sin país se buscó en todos, y si no está en
            # ninguno tampoco está en el de Colombia.
            data={**name_data, **notice_data, "pais_catalogo": args.pais or "CO"},
            context_update=update,
        )
    options = [_option(reference, req, deps.today, data) for reference in shown]
    payload_options = [_option_payload(option, sources) for option in options]
    shown_discarded = discarded[:MAX_DISCARDED]
    if len(valid) > len(shown):
        message = (
            f"Se muestran {len(shown)} de {len(valid)} opción(es) apta(s); "
            f"{len(discarded)} descartada(s) por material o uso, que no son aptas."
        )
    else:
        message = (
            f"{len(shown)} opción(es) apta(s) y {len(discarded)} descartada(s) por "
            "material o uso; las descartadas no son aptas."
        )
    if len(shown) > MAX_PRESENTED_OPTIONS:
        message += (
            f" Al cliente se le presentan como mucho {MAX_PRESENTED_OPTIONS}, las que mejor"
            " encajan; las demás se ven filtrando por capacidad, material o precio."
        )
    if args.pais is None:
        message += " Sin país no se muestran precios."
    payload: dict[str, Any] = {
        "opciones": payload_options,
        **_suitable_for(req),
        "total_coincidencias": len(valid),
        "truncado": len(valid) > len(shown),
        "descartadas": [{"sku": item.sku, "motivo": item.reason} for item in shown_discarded],
        "cantidad_descartadas": len(discarded),
        **name_data,
    }
    if notices:
        payload["aviso"] = " ".join(notices)
    cards: dict[str, Any] = {
        "request": asdict(req),
        "options": options,
        "discarded": [asdict(item) for item in shown_discarded],
        "discarded_total": len(discarded),
        "channel": _channel(req, data),
    }
    if match is not None:
        # La interfaz rotula las tarjetas como alternativas cuando no son la referencia
        # nombrada (sin coincidencias o con coincidencia parcial); el aviso solo lo ve el
        # modelo.
        cards["name_search"] = {"matches": len(match.named), "partial": match.partial}
    return _Draft("ok", message, data=payload, context_update=update, cards=cards)


# --- cotizar (T019) ------------------------------------------------------------------------


def _same_capacity(explicit: Any, stored: Any) -> bool:
    """La capacidad de la llamada no contradice la de la referencia del contexto."""

    if explicit is None:
        return True
    if not isinstance(stored, int | float):
        return False
    return abs(float(explicit) - float(stored)) <= CAPACITY_TOLERANCE_OZ


def _names_stored_reference(
    explicit: Mapping[str, Any], context: Mapping[str, Any], catalog: Catalog | None
) -> bool:
    """El ``sku`` o el ``nombre`` de la llamada identifican la referencia del contexto.

    Un código igual al guardado basta. Si no, el texto se resuelve en el catálogo como lo
    hace el motor, acotado por la familia y la capacidad de la referencia guardada: es la
    misma solo si queda exactamente esa («¿y con retiro del vaso de 7 oz?» tras cotizar ese
    vaso). Un nombre que no se puede resolver cuenta como otra referencia: preguntar la
    cantidad es mejor que cotizar en silencio una que la persona no dijo.
    """

    stored = str(context.get("sku") or "").strip()
    if not stored:
        return False
    said = str(explicit.get("sku") or "").strip()
    name = str(explicit.get("nombre") or "").strip()
    if stored.casefold() in {said.casefold(), name.casefold()}:
        return True
    if said or catalog is None or not name:
        # Un SKU distinto nombra otra referencia; sin catálogo no se puede comparar el nombre.
        return False
    capacity = context.get("capacity_oz")
    match = resolve_reference(
        name,
        [reference for reference in catalog.references if reference.country == "CO"],
        catalog,
        family=str(context.get("family") or "") or None,
        capacity_oz=float(capacity) if isinstance(capacity, int | float) else None,
    )
    return not match.partial and [reference.sku for reference in match.named] == [stored]


def _fill_from_context(
    args: CotizarArgs, context: Mapping[str, Any], catalog: Catalog | None = None
) -> tuple[CotizarArgs, list[str]]:
    """Completa los campos ausentes con el contexto comercial vigente.

    Los datos del producto (familia, uso, capacidad, temperatura, microondas) solo se
    reutilizan si la llamada no nombra otro producto; ciudad, domicilio y preferencia se
    reutilizan siempre, y la cantidad salvo que se nombre otra referencia. Un valor del
    contexto que no valida se ignora.
    """

    explicit = args.model_dump(exclude_none=True)
    same_product = all(
        explicit.get(key) is None or explicit.get(key) == context.get(key)
        for key in ("family", "use")
    )
    named = "sku" in explicit or "nombre" in explicit
    # Una referencia nombrada fija la familia y la capacidad: las del contexto describían
    # otro producto. El uso, la temperatura y el microondas siguen siendo la necesidad.
    product_fields = [
        key for key in _PRODUCT_FIELDS if not (named and key in ("family", "capacity_oz"))
    ]
    # Otra referencia nombrada (la elegida de una foto, «cotiza el J1») es otro pedido: la
    # cantidad del anterior no se hereda en silencio, se pregunta (2026-11-26: una elección
    # de foto cotizó 2.000 unidades que la persona nunca dijo).
    # Sin ``sku`` ni ``nombre`` (familia y capacidad) no hay referencia nombrada y la cantidad
    # se reutiliza; la misma referencia por su código o por su nombre sigue siendo el mismo
    # pedido.
    other_reference = named and not _names_stored_reference(explicit, context, catalog)
    shared = [key for key in _SHARED_FIELDS if not (other_reference and key == "quantity")]
    reusable = [*(product_fields if same_product else ()), *shared]
    filled = dict(explicit)
    reused: list[str] = []
    sku = context.get("sku")
    if (
        not named
        and same_product
        and isinstance(sku, str)
        and sku
        and _same_capacity(explicit.get("capacity_oz"), context.get("capacity_oz"))
    ):
        # Seguimiento de la cotización anterior («¿y con domicilio?»): misma referencia. El
        # contexto guarda la familia y la capacidad de la referencia junto a su SKU, así que
        # ``same_product`` ya exige que la familia pedida sea la suya.
        filled["sku"] = sku
        reused.append("sku")
    for key in reusable:
        value = context.get(key)
        if key in explicit or value in (None, ""):
            continue
        if key == "prefers_renewable" and value is False:
            continue
        candidate = {**filled, key: value}
        try:
            CotizarArgs.model_validate(candidate)
        except ValidationError:
            continue
        filled = candidate
        reused.append(key)
    # La unidad acompaña a la cantidad: se reutiliza con ella («10 cajas» sigue siendo de
    # cajas en el turno siguiente), también si el modelo reenvía la misma cantidad sin unidad
    # («con domicilio», «¿y en Cali?»). Una cantidad nueva sin unidad es de unidades.
    unit = context.get("unidad")
    same_quantity = "quantity" in explicit and explicit["quantity"] == context.get("quantity")
    if ("quantity" in reused or same_quantity) and "unidad" not in explicit and unit in _UNIT_BASIS:
        filled["unidad"] = unit
        if unit != "unidades":
            reused.append("unidad")
    return CotizarArgs.model_validate(filled), reused


# Cantidad con su unidad en el texto de la persona: «5 cajas», «2.000 unidades», «10 und».
_QUANTITY_UNIT = re.compile(
    r"(\d{1,3}(?:[.,]\d{3})+|\d+)\s*(unidades|unidad|unds?|uds?|cajas?|paquetes?|pacas?)\b"
)
_UNIT_WORDS: dict[str, Unit] = {
    "unidades": "unidades",
    "unidad": "unidades",
    "und": "unidades",
    "unds": "unidades",
    "ud": "unidades",
    "uds": "unidades",
    "caja": "cajas",
    "cajas": "cajas",
    "paquete": "paquetes",
    "paquetes": "paquetes",
    "paca": "pacas",
    "pacas": "pacas",
}


def _unit_in_question(question: str, quantity: int) -> Unit | None:
    """Unidad con que la persona dijo esa cantidad en la pregunta, si la dijo."""

    for number, word in _QUANTITY_UNIT.findall(plain(question)):
        if int(re.sub(r"[.,]", "", number)) == quantity:
            return _UNIT_WORDS.get(word)
    return None


# Palabras de una ciudad que no la identifican («Ciudad de Panamá», «Bogotá D.C.»).
_CITY_FILLERS = frozenset({"de", "del", "la", "el", "los", "las", "d", "c", "dc"})


def _city_said(city: str, context: ToolContext) -> bool:
    """La persona dijo la ciudad: en la pregunta, en el historial o en el contexto vigente.

    El contexto comercial guarda solo ciudades que pasaron esta misma comprobación. Se
    exige cada palabra significativa del argumento como palabra de lo dicho.
    """

    texts = [
        context.question,
        *context.history,
        json.dumps(dict(context.commercial_context), ensure_ascii=False),
    ]
    if _confirms(context.question):
        # «Sí» a «¿te refieres a Medellín?»: la ciudad la propuso el asistente y la persona
        # la aceptó.
        texts.append(context.last_answer)
    said = set(re.findall(r"[^\W\d_]+", plain(" ".join(texts))))
    words = [word for word in re.findall(r"[^\W\d_]+", plain(city)) if word not in _CITY_FILLERS]
    return bool(words) and all(
        word in said or any(_close_word(word, other) for other in said) for word in words
    )


# Respuestas que solo confirman lo que propuso el asistente.
_CONFIRM_WORDS = frozenset(
    {
        "si",
        "claro",
        "correcto",
        "exacto",
        "eso",
        "esa",
        "ese",
        "dale",
        "ok",
        "okay",
        "listo",
        "de",
        "una",
        "afirmativo",
        "asi",
        "es",
        "por",
        "favor",
        "porfa",
        "ahi",
        "en",
        "la",
        "misma",
    }
)


def _confirms(question: str) -> bool:
    """La pregunta es solo una confirmación («sí», «claro, esa»)."""

    words = re.findall(r"[^\W\d_]+", plain(question))
    return bool(words) and len(words) <= 5 and set(words) <= _CONFIRM_WORDS


def _close_word(word: str, other: str) -> bool:
    """``other`` es ``word`` con una errata: una edición (dos si la palabra es larga).

    Solo para palabras de cinco letras o más: «Medelin» es Medellín, «Cal» no es Cali.
    """

    if len(word) < 5 or abs(len(word) - len(other)) > 2:
        return False
    limit = 2 if len(word) >= 9 else 1
    previous = list(range(len(other) + 1))
    for i, char in enumerate(word, start=1):
        current = [i]
        for j, other_char in enumerate(other, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (char != other_char),
                )
            )
        previous = current
    return previous[-1] <= limit


def _directory_city(city: str, dataset: Dataset) -> str | None:
    """Ciudad con la grafía del directorio; ``None`` si el directorio no la tiene."""

    known = {plain(item): item for item in dataset.cities()}
    return known.get(plain(city))


def _offer_source_type(book: CommercialBook, sku: str, site_id: str, url: str) -> str:
    """Tercero si lo publica un distribuidor (como ``commercial_sources``)."""

    if site_id == REFERENCE_SITE_ID:
        return "sistema"
    for offer in book.offers:
        if offer.sku == sku and offer.site_id == site_id:
            seller = book.seller(offer.seller_id)
            if seller is not None:
                return seller_source_type(seller.kind)
    return "oficial" if _is_official(url) else "tercero"


def _quote_sources(quote: Mapping[str, Any], source_type: str) -> list[dict[str, Any]]:
    primary = quote["primary"]
    presentation = primary["presentation"]
    site_id = (primary.get("provider") or {}).get("id") or ""
    # Precio publicado en otra ciudad que la de la cotización: la fuente dice cuál.
    where = f"publicado en {primary['price_city']} · " if primary.get("price_origin") else ""
    entries = [
        {
            "chunk_id": f"precio:{primary['sku']}:{site_id}",
            "slug": f"precio-{primary['sku']}",
            "section": "cotizador",
            "layer": "core",
            "score": 1.0,
            "excerpt": (
                f"Precio {presentation['price_per_presentation']} {primary['currency']} por "
                f"{presentation['basis']} de {presentation['units']} · IVA "
                f"{primary['tax_status']} · {price_origin(source_type, primary['seller'])} · "
                f"{where}capturado el "
                f"{primary['captured_at']}"
            ),
            "url": primary["source_url"],
            "title": f"{primary['seller']}: {primary['name']}",
            "source_type": source_type,
        }
    ]
    shipping = quote["commercial"]["shipping"]
    if shipping.get("source_url"):
        entries.append(
            {
                "chunk_id": f"envio:{primary['sku']}:{site_id}",
                "slug": f"envio-{primary['sku']}",
                "section": "cotizador",
                "layer": "core",
                "score": 1.0,
                "excerpt": f"Condiciones de envío de {primary['seller']}: {shipping['note']}",
                "url": shipping["source_url"],
                "title": f"Envío de {primary['seller']}",
                "source_type": source_type,
            }
        )
    return entries


def _site_source(site: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "chunk_id": f"sede:{site['site_id']}",
        "slug": f"sede-{site['site_id']}",
        "section": "directorio_distribuidores",
        "layer": "core",
        "score": 1.0,
        "excerpt": (
            f"{site['name']} · {site['city']} · {site.get('address') or 'sin dirección'} "
            f"· precisión {site['precision']}"
        ),
        "url": site.get("source_url") or "",
        "title": f"Sede {site['name']} ({site['city']})",
        "source_type": "oficial",
    }


def _quote_payload(
    quote: Mapping[str, Any], number: int, source_type: str, shipping_n: int | None
) -> dict[str, Any]:
    primary = quote["primary"]
    presentation = primary["presentation"]
    commercial = quote["commercial"]
    shipping = commercial["shipping"]
    envio: dict[str, Any] = {
        "estado": shipping["status"],
        "costo": shipping["cost"],
        "nota": shipping["note"],
    }
    if shipping_n is not None:
        envio["n"] = shipping_n
    return {
        "n": number,
        "sku": primary["sku"],
        "nombre": primary["name"],
        "material": primary["material"],
        "capacidad_oz": primary["capacity_oz"],
        "ciudad": quote["city"],
        "presentacion": (
            f"{presentation['count']} {presentation['basis']} de {presentation['units']} unidades"
        ),
        "precio_por_presentacion": presentation["price_per_presentation"],
        **({"conversion": primary["conversion"]["text"]} if primary.get("conversion") else {}),
        "unidades_pedidas": presentation["units_requested"],
        "unidades_compradas": presentation["units_bought"],
        "sobrante": presentation["units_surplus"],
        "subtotal": commercial["subtotal"],
        "iva": {"estado": commercial["tax"]["status"], "tasa": commercial["tax"]["rate"]},
        "envio": envio,
        "total": quote["total"],
        "moneda": commercial["currency"],
        "vendedor": primary["seller"],
        # Ciudad donde el vendedor publica el precio, si no es la de la cotización.
        **({"ciudad_del_precio": primary["price_city"]} if primary.get("price_origin") else {}),
        "tipo_fuente": source_type,
        "fuente": primary["source_url"],
        "fecha": primary["captured_at"],
        "vigencia": commercial["valid_until"],
        "existencias": primary["stock"]["status"],
        "aviso_existencias": primary["warning"],
        # Spec 054: las alternativas llegan con su nombre y subtotal; el sistema las muestra
        # (recomendada + hasta 4) y el modelo no las repite.
        "alternativas": [
            {
                "sku": item.get("sku"),
                "nombre": item.get("name"),
                "subtotal": (item.get("presentation") or {}).get("subtotal"),
                "diferencia": item.get("difference"),
            }
            for item in quote.get("alternatives") or []
        ],
    }


def _habitual_city(context: ToolContext) -> str | None:
    """Ciudad habitual de la memoria del dueño: solo para sugerirla (spec 045, FR-013).

    Nunca se añade a lo que ``_city_said`` toma como dicho.
    """

    return MemoryProfile.from_snapshot(context.memory).values.get("ciudad_habitual") or None


def _suggest_city(draft: _Draft, context: ToolContext, said: bool) -> _Draft:
    """Añade ``sugerencia_ciudad`` a un ``needs_input`` que pide una ciudad no dicha."""

    habitual = _habitual_city(context)
    if habitual and not said and draft.status == "needs_input" and "ciudad" in draft.missing:
        draft.data["sugerencia_ciudad"] = habitual
    return draft


def _cotizar(args: CotizarArgs, deps: ToolDeps, context: ToolContext, sources: _Sources) -> _Draft:
    """``cotizar`` con la ciudad habitual como sugerencia cuando falta la ciudad (FR-013).

    Spec 054: con ``cantidades`` cotiza la primera como siempre y las demás con la referencia
    que salió de la primera, para que el comparativo sea del mismo producto.
    """

    said = args.city is not None and (not context.question or _city_said(args.city, context))
    if not args.cantidades:
        return _suggest_city(_quote(args, deps, context, sources), context, said)
    amounts = [amount for amount in dict.fromkeys(args.cantidades) if amount >= 1][:4]
    first = args.model_copy(update={"quantity": amounts[0], "cantidades": None})
    draft = _suggest_city(_quote(first, deps, context, sources), context, said)
    if draft.status != "ok" or draft.quote is None:
        return draft
    sku = str(draft.quote["primary"]["sku"])
    compared = [draft.data.get("cotizacion")]
    for amount in amounts[1:]:
        following = first.model_copy(update={"quantity": amount, "sku": sku, "nombre": None})
        turn = replace(context, quotes=draft.quotes or context.quotes)
        step = _quote(following, deps, turn, sources)
        if step.status != "ok":
            break
        compared.append(step.data.get("cotizacion"))
        draft = replace(
            draft,
            quotes=step.quotes,
            message=f"{draft.message} Comparativo de {len(compared)} cantidades del producto.",
        )
    draft.data["comparativo"] = [item for item in compared if item]
    return draft


def _other_family(said: frozenset[str], context: Mapping[str, Any]) -> _Draft:
    """La persona nombra otra familia y la llamada no dice cuál cotizar.

    Sin ``family``, el contexto daría por hecho el producto anterior; tomar la familia que
    se nombra cotizaría en silencio lo que quizá solo se mencionó («esos con cucharas»,
    «para platos típicos»). Se pide al modelo que la diga y el contexto no cambia, para que
    una llamada con la familia anterior siga reutilizando su referencia.
    """

    named = sorted(said)
    previous = context.get("family")
    before = f"de la familia {previous}" if previous else "de otra referencia"
    return _Draft(
        "needs_input",
        f"La persona nombra {', '.join(named)} y la cotización anterior era {before}: no se "
        "reutilizó ese producto ni se cotizó otro. Vuelve a llamar con la family (o el "
        "nombre) del producto que pide; si sigue siendo el anterior, pasa su familia. No "
        "elijas la familia por tu cuenta: si no está claro, pregúntaselo a la persona.",
        data={
            "reutilizado": [],
            "familia_por_confirmar": {"nombradas": named, "anterior": previous},
        },
        missing=["familia"],
    )


def _quote(args: CotizarArgs, deps: ToolDeps, context: ToolContext, sources: _Sources) -> _Draft:
    # Una ciudad que la persona no dijo no se usa (FR-005: se pregunta, no se supone). Solo
    # se comprueba dentro de un turno: una llamada suelta no tiene pregunta con qué comparar.
    unsaid_city = bool(
        args.city is not None and context.question and not _city_said(args.city, context)
    )
    requested_city = args.city
    commercial_context = dict(context.commercial_context)
    if unsaid_city:
        # Ni se usa ni se sustituye por la ciudad del contexto: el modelo pidió otra ciudad
        # y una cotización de la anterior se le atribuiría a la que pidió.
        args = args.model_copy(update={"city": None})
        commercial_context.pop("city", None)
    if args.quantity is not None and args.unidad is None:
        said_unit = _unit_in_question(context.question, args.quantity)
        if said_unit is not None:
            args = args.model_copy(update={"unidad": said_unit})
    if args.family is None and not (args.sku or args.nombre):
        # Red de seguridad de la familia: sin ``family``, el contexto completaba el producto
        # de la cotización anterior y «Cotízame 1.000 vasos …» tras un portacomidas volvía a
        # cotizar el portacomidas.
        said = named_families(context.question or "")
        previous = commercial_context.get("family")
        has_product = bool(previous or commercial_context.get("sku"))
        if said and previous not in said:
            if has_product:
                return _other_family(said, commercial_context)
            if len(said) == 1:
                # Sin producto anterior no hay nada que confundir: la única familia que
                # nombró la persona es la que pide (como ``microwave_for``).
                args = args.model_copy(update={"family": next(iter(said))})
    # El mismo catálogo con que ``quote_request`` resuelve el nombre de la referencia.
    filled, reused = _fill_from_context(args, commercial_context, load_catalog())
    by_use = None if args.sku or filled.nombre else _family_for_use(filled.family, context.question)
    if by_use is not None:
        # Como en ``buscar_producto``: el vaso heredado no se cotiza para sopa. La referencia,
        # la capacidad, la temperatura y el microondas eran del producto anterior, vinieran
        # del contexto o del argumento; solo quedan los que la persona dijo en el turno.
        filled = filled.model_copy(
            update={"family": by_use.family, "use": by_use.use, "sku": None, **by_use.product}
        )
        replaced = {"family", "use", "sku", *by_use.product}
        reused = [key for key in reused if key not in replaced]
    if filled.microwave is None and microwave_for(context.question, filled.family):
        # Misma red de seguridad que ``buscar_producto``: el microondas lo pidió la persona.
        filled = filled.model_copy(update={"microwave": True})
    if filled.material is None and not (filled.sku or filled.nombre):
        # Misma red de seguridad para el material: «vasos de papel» no cotiza un vaso de PP
        # aunque el modelo olvide pasar ``material``.
        said = material_for(context.question or "")
        if said is not None:
            filled = filled.model_copy(update={"material": said})
    # El contexto comercial guarda lo que dijo la persona, no lo que se infiere. La
    # referencia se guarda por su SKU resuelto, no por el texto del nombre; ``None`` hace que
    # el grafo olvide la de una cotización anterior.
    update = {**filled.model_dump(exclude_none=True), "country": "CO", "sku": filled.sku}
    if by_use is not None:
        # ``None`` olvida en el contexto la capacidad y la temperatura del producto anterior.
        update = {**by_use.product, **update}
    update.pop("nombre", None)
    update.pop("cantidades", None)
    if "quantity" in update:
        # La unidad viaja con la cantidad: una cantidad nueva sin unidad deja de ser de cajas.
        update["unidad"] = filled.unidad or "unidades"
    request = filled
    temperature = _temperature(filled.temperature, filled.use, filled.microwave)
    if filled.temperature is None and temperature == "caliente" and filled.microwave:
        # Microondas sin temperatura: contenido caliente, como en ``buscar_producto``. Solo
        # para esta cotización: guardado, un turno posterior sin microondas lo reutilizaría
        # y descartaría sin motivo el PP y el PET de bebida fría.
        request = filled.model_copy(update={"temperature": temperature})
    base: dict[str, Any] = {"reutilizado": reused}
    if by_use is not None:
        base["familia_por_uso"] = {"familia": by_use.family, "uso": by_use.use}
        base["aviso"] = by_use.notice("se cotizó")
    day = _today(deps)
    dataset = deps.dataset or load_dataset(day.isoformat())
    if filled.city is not None and _directory_city(filled.city, dataset) is None:
        # Ciudad fuera del directorio (otra ciudad de Colombia sin sedes o fuera del país): se
        # pide una del directorio en vez de cotizar en otra. No se guarda en el contexto.
        update.pop("city", None)
        return _Draft(
            "needs_input",
            "La ciudad indicada no está en el directorio de sedes de Colombia; solo se cotiza "
            "en las ciudades disponibles. Pide a la persona una de ellas; no cotices en otra "
            "ciudad por tu cuenta.",
            data={
                **base,
                "ciudad_fuera_del_directorio": True,
                "ciudades_disponibles": list(dataset.cities()),
            },
            missing=["ciudad"],
            context_update=update,
        )
    fields = request.model_dump()
    fields.pop("cantidades", None)
    unit = _UNIT_BASIS.get(fields.pop("unidad") or "unidades", "unidad")
    name = fields.pop("nombre")
    quoted = quote_request(
        QuoteRequest(**fields, unit=unit, name=name), today=day, now=deps.now, dataset=dataset
    )
    kind = quoted.get("kind")
    if filled.sku or name:
        _remember_reference(quoted, update, dataset)
    if kind == "aclaracion" and "referencia" in (quoted.get("missing") or []):
        asked = dict(quoted.get("request") or {})
        # Spec 054: lo demás de la solicitud viaja con las candidatas para que el flujo
        # cotice la que elija la persona («la 2», «el Gold») sin volver al modelo.
        choice_request = {
            "quantity": asked.get("quantity"),
            "unit": unit,
            "city": asked.get("city"),
            "delivery": fields.get("delivery"),
            "use": asked.get("use"),
            "temperature": asked.get("temperature"),
            "microwave": asked.get("microwave"),
        }
        return _ambiguous_reference(quoted, base, update, choice_request)
    if kind == "sin_resultados" and quoted.get("reason") in _NAMED_EMPTY:
        return _named_empty(quoted, base, update)
    if kind == "aclaracion":
        missing = [str(item) for item in quoted.get("missing") or []]
        data = dict(base)
        if quoted.get("cities"):
            data["ciudades_disponibles"] = list(quoted["cities"])
        message = f"Falta un dato para cotizar: {', '.join(missing) or 'sin especificar'}."
        if quoted.get("unconvertible") and quoted.get("presentations") is not None:
            published = ", ".join(quoted["presentations"]) or "no consta"
            message = (
                f"No se puede convertir la cantidad en {filled.unidad} a unidades con "
                "certeza para la referencia nombrada: el catálogo y el contrato no declaran "
                f"esa presentación. Su presentación publicada: {published}. Pide la cantidad "
                "en unidades o en esa presentación, nombrando la referencia; no la calcules."
            )
            data["presentaciones_publicadas"] = _published_presentations(
                dataset, quoted["unconvertible"]
            )
        elif quoted.get("unconvertible"):
            message = (
                f"No se puede convertir la cantidad en {filled.unidad} a unidades con "
                "certeza: el catálogo y el contrato no declaran esa presentación para las "
                "referencias que aplican. Pide la cantidad en unidades; no la calcules."
            )
            data["presentaciones_publicadas"] = _published_presentations(
                dataset, quoted["unconvertible"]
            )
        elif unsaid_city and "ciudad" in missing:
            message += (
                f" La persona no ha dicho la ciudad «{requested_city}»: confírmala con ella; "
                "no se cotizó en esa ni en la del contexto."
            )
            data["ciudad_no_dicha"] = requested_city
        return _Draft(
            "needs_input",
            message,
            data=data,
            missing=missing,
            context_update=update,
        )
    if kind == "sin_resultados":
        draft = _quote_without_price(quoted, dataset, base, update, sources)
        if quoted.get("reason") == "sin_precio":
            _named_without_price(draft, quoted)
        return draft
    if kind != "cotizacion":
        return _Draft("error", "El motor de cotización no devolvió un resultado válido.")

    merge = merge_quote(context.quotes, quoted, max_quotes=deps.settings.agent_max_quotes)
    if merge.action == "limit":
        return _Draft(
            "error",
            f"Este turno ya tiene {len(merge.quotes)} cotizaciones, el máximo; esta no se calculó.",
            data=base,
            quotes=merge.quotes,
        )
    primary = quoted["primary"]
    site_id = (primary.get("provider") or {}).get("id") or ""
    source_type = _offer_source_type(_book(deps), primary["sku"], site_id, primary["source_url"])
    numbers = sources.add(_quote_sources(quoted, source_type))
    shipping_n = numbers[1] if len(numbers) > 1 else None
    message = f"Cotización calculada con el precio publicado por {primary['seller']}."
    if primary.get("price_origin"):
        message = (
            f"Cotización calculada con el precio que publica {primary['seller']} en "
            f"{primary['price_city']}: en {quoted['city']} no hay precio publicado de esta "
            f"referencia. Dilo así y no lo presentes como precio de {quoted['city']}; las sedes "
            f"de {quoted['city']} del directorio son dónde consultarla."
        )
    if source_type == "sistema":
        message = (
            "Cotización calculada con un precio de referencia del catálogo, sin distribuidor: "
            "no lo atribuyas a ninguno. No menciones su origen salvo que la persona pregunte de "
            "dónde sale; entonces di que es el precio del catálogo de México convertido a COP."
        )
    if primary.get("conversion"):
        message += f" Conversión hecha por la herramienta: {primary['conversion']['text']}."
        others = _other_conversions(quoted)
        if others:
            message += (
                " Cada referencia trae otra cantidad por presentación ("
                + "; ".join(others)
                + "): la principal es la de menor precio por unidad, no la del total más "
                "bajo. Pregunta qué referencia busca la persona."
            )
    shipping = quoted["commercial"]["shipping"]
    if quoted["total"] is None and shipping.get("requested") is None:
        # Spec 054 (decisión 4): el envío solo va en el resumen. Si el modelo pregunta aquí
        # por domicilio o retiro, choca con la pregunta del flujo («¿Algo más?», «¿Cuál te
        # cotizo?») y el resumen luego cierra con «Envío no incluido» sin usar la respuesta.
        message += (
            " El precio va sin envío: en el flujo de cotización el envío solo se resuelve en "
            "el resumen, así que domicilio o retiro no es un dato pendiente en este turno. Ante "
            "una consulta explícita por el envío, cotizar acepta delivery."
        )
    elif quoted["total"] is None:
        message += f" El total queda pendiente: {shipping['note']}"
    if merge.action == "replaced":
        message += " Reemplaza a la cotización anterior que solo difería en el domicilio."
    stored = merge.quotes[merge.index] if merge.index is not None else None
    data = {**base, "cotizacion": _quote_payload(quoted, numbers[0], source_type, shipping_n)}
    mismatch = quoted.get("family_mismatch")
    if mismatch:
        # «vaso P1»: se cotiza el P1 nombrado, pero es un contenedor y hay que decirlo.
        data["familia_distinta"] = {"pedida": mismatch["asked"], "referencia": mismatch["family"]}
        message += (
            f" La referencia nombrada es de la familia {mismatch['family']}, no "
            f"{mismatch['asked']} como se pidió: díselo a la persona y preséntala como la "
            "referencia que nombró, no como lo que pidió."
        )
    return _Draft(
        "ok",
        message.strip(),
        data=data,
        quotes=merge.quotes,
        quote=stored,
        context_update=update,
    )


_NAMED_EMPTY = ("referencia_no_encontrada", "referencia_no_apta")


def _remember_reference(
    quoted: Mapping[str, Any], update: dict[str, Any], dataset: Dataset
) -> None:
    """Deja en el contexto la referencia identificada, con su familia y su capacidad.

    Queda la referencia que el motor identificó y aceptó (cotizada, sin precio en la ciudad
    o a la espera de la cantidad o la ciudad); una ambigua, inexistente o no apta suelta la
    anterior (``sku: None``). La familia y la capacidad son las de la ficha, para que un
    seguimiento que las repite («contenedor» del J2, «14 oz» del Gold) la reconozca y uno de
    otra familia no la reutilice.
    """

    request = quoted.get("request") or {}
    primary = quoted.get("primary") or {}
    sku = primary.get("sku") if quoted.get("kind") == "cotizacion" else request.get("sku")
    product = dataset.product(sku) if isinstance(sku, str) and sku else None
    if product is None:
        update["sku"] = None
        family = request.get("family")
        if family and "family" not in update:
            # Nombre genérico («vaso de icopor»): la familia que resolvió el motor.
            update["family"] = family
        return
    update["sku"] = product.sku
    update["family"] = product.family
    update["capacity_oz"] = product.capacity_oz


def _ambiguous_reference(
    quoted: Mapping[str, Any],
    base: dict[str, Any],
    update: dict[str, Any],
    choice_request: Mapping[str, Any] | None = None,
) -> _Draft:
    """El nombre coincide con varias referencias (o solo en parte): se pregunta cuál.

    Las candidatas, en su orden, y ``choice_request`` (cantidad, unidad, ciudad, domicilio,
    uso, temperatura y microondas) van en la tarjeta ``reference_choice``: el flujo de la
    spec 054 las guarda en el borrador y resuelve la respuesta («la 2», el SKU, «el Gold»)
    con reglas, sin que el modelo elija.
    """

    candidates = [
        {
            "sku": item["sku"],
            "nombre": item["name"],
            "familia": item["family"],
            "material": item["material"],
            "capacidad_oz": item["capacity_oz"],
        }
        for item in quoted.get("candidates") or []
    ]
    partial = bool(quoted.get("partial"))
    conflict = bool(quoted.get("conflict"))
    if conflict:
        message = (
            "El sku y el nombre corresponden a referencias distintas del catálogo; no se "
            "cotizó ninguna."
        )
    elif partial:
        message = (
            "Ninguna referencia coincide con todos los términos del nombre; estas coinciden "
            "en parte y no son exactamente la nombrada."
        )
    else:
        message = "El nombre coincide con varias referencias del catálogo."
    message += (
        " Pregunta a la persona cuál quiere, con las candidatas numeradas en este orden (1, 2, "
        "…), y cotízala con su sku; no elijas tú."
    )
    data: dict[str, Any] = {
        **base,
        "candidatas": candidates,
        "total_candidatas": quoted.get("candidates_total", len(candidates)),
    }
    if partial:
        data["parcial"] = True
    if conflict:
        data["conflicto_sku_nombre"] = True
    listed = [dict(item) for item in quoted.get("candidates") or [] if isinstance(item, Mapping)]
    cards = {"reference_choice": {"candidates": listed, "request": dict(choice_request or {})}}
    return _Draft(
        "needs_input",
        message,
        data=data,
        missing=["referencia"],
        context_update=update,
        cards=cards,
    )


def _named_empty(quoted: Mapping[str, Any], base: dict[str, Any], update: dict[str, Any]) -> _Draft:
    """La referencia nombrada no existe o no es apta: no se cotiza ni se sustituye."""

    if quoted.get("reason") == "referencia_no_apta":
        reason = str((quoted.get("discarded") or [""])[0])
        return _Draft(
            "empty",
            "La referencia nombrada no es apta para el uso o la temperatura que dijo la "
            "persona; no se cotizó ni se sustituyó por otra.",
            data={**base, "no_apta": {"sku": quoted.get("sku"), "motivo": reason}},
            context_update=update,
        )
    return _Draft(
        "empty",
        "Ninguna referencia del catálogo de Colombia coincide con ese nombre o SKU; no se "
        "cotizó otra en su lugar.",
        data={
            **base,
            "referencia_encontrada": False,
            "aviso": (
                "cotizar ya buscó ese nombre en todo el catálogo de Colombia con la misma "
                "búsqueda de buscar_producto: no repitas la búsqueda por nombre. Di que no la "
                "encontraste en el catálogo de Colombia y ofrece buscar por uso, familia o "
                "capacidad."
            ),
        },
        context_update=update,
    )


def _named_without_price(draft: _Draft, quoted: Mapping[str, Any]) -> None:
    """La referencia nombrada no tiene precio en la ciudad: dónde sí, sin sustituirla."""

    priced = list(quoted.get("priced_cities") or [])
    city = (quoted.get("request") or {}).get("city")
    message = (
        f"No hay precio publicado de la referencia nombrada en {city}"
        if city
        else "La referencia nombrada no tiene precio publicado"
    )
    message += "; no se sustituyó por otra."
    if priced:
        message += f" Tiene precio publicado en {', '.join(priced)}."
    sites = len(draft.data.get("sedes") or [])
    if sites:
        message += f" Hay {sites} sede(s) del directorio con contacto."
    draft.message = message
    draft.data["referencia"] = {"sku": quoted.get("sku"), "nombre": quoted.get("name")}
    draft.data["ciudades_con_precio"] = priced


def _other_conversions(quoted: Mapping[str, Any]) -> list[str]:
    """Conversiones de las alternativas que no dan las mismas unidades que la principal."""

    units = quoted["primary"]["conversion"]["units"]
    return [
        f"{item['name']}: {item['conversion']['text']}"
        for item in quoted.get("alternatives") or []
        if item.get("conversion") and item["conversion"]["units"] != units
    ]


def _published_presentations(dataset: Dataset, skus: Sequence[str]) -> list[dict[str, Any]]:
    """Presentaciones publicadas en el contrato de las referencias que no se convirtieron."""

    shown: list[dict[str, Any]] = []
    for sku in list(skus)[:MAX_OUTSIDE_FILTERS]:
        product = dataset.product(sku)
        labels = sorted(
            {f"{offer.basis} de {offer.units_in_basis}" for offer in dataset.offers_for(sku)}
        )
        if product is not None and labels:
            shown.append({"sku": sku, "nombre": product.name, "presentaciones": labels})
    return shown


def _quote_without_price(
    quoted: Mapping[str, Any],
    dataset: Dataset,
    base: dict[str, Any],
    update: dict[str, Any],
    sources: _Sources,
) -> _Draft:
    """``sin_resultados`` → ``empty`` con las sedes del directorio, citadas."""

    providers = list(quoted.get("providers") or [])
    sites = []
    for provider in providers:
        known = dataset.provider(str(provider.get("id")))
        sites.append(
            {
                "site_id": provider.get("id"),
                "name": provider.get("name"),
                "city": provider.get("city"),
                "address": provider.get("address"),
                "phone": provider.get("phone"),
                "latitude": provider.get("lat"),
                "longitude": provider.get("lon"),
                "precision": provider.get("precision"),
                "source_url": known.source_url if known else None,
                "captured_at": known.captured_at.isoformat() if known else None,
                "linked": False,
                "availability": "desconocido",
            }
        )
    numbers = sources.add(_site_source(site) for site in sites)
    data: dict[str, Any] = {
        **base,
        "sedes": [
            {
                "n": number,
                "nombre": site["name"],
                "direccion": site["address"],
                "telefono": site["phone"],
                "ciudad": site["city"],
            }
            for number, site in zip(numbers, sites, strict=True)
        ],
    }
    if quoted.get("cities"):
        data["ciudades_disponibles"] = list(quoted["cities"])
    if quoted.get("discarded"):
        data["descartadas"] = list(quoted["discarded"])
    request = quoted.get("request") or {}
    if sites:
        message = (
            f"No hay precio publicado para esta búsqueda en {request.get('city')}; hay "
            f"{len(sites)} sede(s) del directorio con contacto."
        )
    else:
        message = "Ninguna referencia con precio publicado cumple la solicitud."
    return _Draft(
        "empty",
        message,
        data=data,
        context_update=update,
        cards={"sites": sites} if sites else {},
    )


# --- buscar_sedes e indicador_financiero (T020) --------------------------------------------


def _buscar_sedes(
    args: BuscarSedesArgs, deps: ToolDeps, context: ToolContext, sources: _Sources
) -> _Draft:
    book = _book(deps)
    if context.question and not _city_said(args.ciudad, context):
        # Cambio declarado sobre la 044 (spec 045, FR-013): la misma regla de ``cotizar``.
        # Una ciudad copiada de las preferencias o de una respuesta anterior no se usa; la
        # ciudad habitual de la memoria solo se sugiere.
        draft = _Draft(
            "needs_input",
            "La persona no ha dicho la ciudad de las sedes; no se buscaron sedes. Falta que "
            "la diga o la confirme.",
            data={"ciudades_disponibles": list(book.cities())},
            missing=["ciudad"],
        )
        return _suggest_city(draft, context, said=False)
    sites = _contract_sites(book, sku=args.sku, city=args.ciudad, as_of=_today(deps))
    if not sites:
        return _Draft(
            "empty",
            f"No hay sedes del directorio en {args.ciudad}.",
            data={"ciudades_con_cobertura": list(book.cities())},
        )
    numbers = sources.add(_site_source(site) for site in sites)
    payload = [
        {
            "n": number,
            "nombre": site["name"],
            "ciudad": site["city"],
            "direccion": site["address"],
            "telefono": site["phone"],
            "precision": site["precision"],
            "vinculada_al_sku": bool(site["linked"]),
            # ``as_of`` ya degradó a «por confirmar» la evidencia sin vigencia o vencida.
            "disponibilidad": _AVAILABILITY_LABELS.get(site["availability"], "por confirmar"),
        }
        for number, site in zip(numbers, sites, strict=True)
    ]
    message = f"{len(payload)} sede(s) del directorio en {sites[0]['city']}."
    if args.sku:
        linked = sum(1 for item in payload if item["vinculada_al_sku"])
        message += f" {linked} ofrece(n) el SKU {args.sku}."
    return _Draft("ok", message, data={"sedes": payload}, cards={"sites": sites})


def _indicator_source(result: Mapping[str, Any]) -> dict[str, Any]:
    url = str(result["source_url"])
    return {
        "chunk_id": f"tool:{result['name']}:{result['period']}",
        "slug": str(result["source_document"]),
        "section": "prensa",
        "layer": "core",
        "score": 1.0,
        "excerpt": str(result["quote"]),
        "url": url,
        "title": f"Indicador {result['name']} {result['period']}",
        "source_type": "oficial" if _is_official(url) else "tercero",
    }


def _indicador_financiero(
    args: IndicadorFinancieroArgs, deps: ToolDeps, context: ToolContext, sources: _Sources
) -> _Draft:
    name = resolve_indicator_name(args.nombre)
    if name is None:
        return _Draft(
            "error",
            f"Indicador desconocido; válidos: {', '.join(sorted(known_names()))}.",
        )
    if args.periodo is None:
        return _Draft(
            "needs_input", "Falta el período (año o año-mes) del indicador.", missing=["periodo"]
        )
    result = indicator_lookup.invoke({"name": name, "period": args.periodo})
    if not isinstance(result, Mapping) or not result.get("found"):
        return _Draft(
            "empty",
            f"No hay registro de {name} para {args.periodo}; no se estima ni se interpola.",
        )
    source = _indicator_source(result)
    [number] = sources.add([source])
    return _Draft(
        "ok",
        "Indicador publicado con su cita textual y su documento.",
        data={
            "n": number,
            "nombre": result["name"],
            "valor": result["display"],
            "unidad": result["unit"],
            "periodo": result["period"],
            "cita": result["quote"],
            "documento": result["source_document"],
            "url": result["source_url"],
            "tipo_fuente": source["source_type"],
        },
    )


# --- sin_consulta (T021) -------------------------------------------------------------------


_SIN_CONSULTA_MESSAGES = {
    "pedir_dato": "Sin consulta: falta un dato de la persona.",
    "conversacion": "Sin consulta: turno de conversación.",
    "fuera_de_alcance": "Sin consulta: el tema está fuera del alcance de Carvajal.",
}


def _sin_consulta(
    args: SinConsultaArgs, deps: ToolDeps, context: ToolContext, sources: _Sources
) -> _Draft:
    data = {"motivo": args.motivo}
    if args.dato:
        data["dato"] = args.dato
    return _Draft("ok", _SIN_CONSULTA_MESSAGES[args.motivo], data=data)
