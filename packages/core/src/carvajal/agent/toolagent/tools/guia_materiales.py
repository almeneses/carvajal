"""Herramienta ``guia_materiales`` (spec 045, T065; contracts/agent-tools-045.md).

Consulta la guía de materiales versionada (``data/catalog/empaques/materiales.csv``, leída
por ``Catalog.materials``) y la lista ``materials_without_rule`` del manifiesto. Es
determinista y no consulta la red. Cada regla devuelta registra una fuente
``guia:{material}`` cuyo ``excerpt`` transcribe la fila completa, con la procedencia de la
guía, para que la compuerta de evidencia respalde cada cifra y cada nivel.

La guía no cubre papel ni pulpa: para esos materiales la herramienta lo declara
(``sin_regla``) y no infiere nada; cada ficha declara su propio uso. La refrigeración de PS
y aluminio no la declara la guía: se dedujo de su rango mínimo al transcribirla (nota del
manifiesto), y la herramienta la marca como deducida.

No importa el registro: exporta su esquema, ``DESCRIPTION``, ``status`` y ``run``.
"""

import json
import unicodedata
from collections.abc import Iterable, Mapping
from functools import cache
from pathlib import Path
from typing import Any, Final, Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from carvajal.agent.toolagent.tools._base import (
    CORE_LAYER,
    ToolContext,
    ToolDeps,
    _Draft,
    _is_official,
    _Sources,
)
from carvajal.catalog.loader import DEFAULT_DIR, MANIFEST, CatalogError, load_catalog
from carvajal.catalog.models import MaterialRule

__all__ = [
    "APPLICATIONS",
    "AVISO",
    "BEVERAGE_USES",
    "DEDUCED_REFRIGERATION",
    "DESCRIPTION",
    "GuiaMaterialesArgs",
    "materials_without_rule",
    "run",
    "status",
]

Material = Literal["PP", "EPS", "PS", "PET", "ALUMINIO", "PAPEL", "PULPA"]
Level = Literal["recomendable", "aplicable", "no_apto"]

# Las nueve aplicaciones de ``materiales.csv``, en el orden de la guía.
APPLICATIONS: Final[tuple[str, ...]] = (
    "comida_fria",
    "comida_caliente",
    "sopas",
    "frituras",
    "ensaladas",
    "sandwiches",
    "salsas",
    "panaderia",
    "frutas",
)
# Usos de bebida (``USE_TEMPERATURE`` de ``commercial_intent``): la guía no tiene una
# aplicación para bebidas, así que se responden con sus reglas por temperatura, sin nivel
# (ensayo del 2026-11-26: «aplicación desconocida» en 6 de 8 llamadas de una semana).
# Caliente → ``contenido_caliente`` de la guía; fría → ``refrigeracion``.
BEVERAGE_USES: Final[dict[str, str]] = {"bebida_caliente": "caliente", "bebida_fria": "fria"}
_BEVERAGE_FIELD: Final[dict[str, str]] = {
    "caliente": "contenido_caliente",
    "fria": "refrigeracion",
}
_LEVEL_ORDER: Final[dict[str, int]] = {"recomendable": 0, "aplicable": 1, "no_apto": 2}
_LEVEL_LABELS: Final[dict[str, str]] = {
    "recomendable": "recomendable",
    "aplicable": "aplicable",
    "no_apto": "no apto",
}

# Procedencia de la guía (manifest.json, sources.materiales): texto fijo del contrato.
AVISO: Final = (
    "Guía de materiales de Mi Tiendita Centroamérica (Lineamientos Generales, pág. 3), "
    "transcrita a mano"
)
# Nota del manifiesto: «refrigeration=true para PS y aluminio se deduce de su rango mínimo
# (-30 °C); la guía solo los declara aptos para congelación».
DEDUCED_REFRIGERATION: Final[frozenset[str]] = frozenset({"PS", "ALUMINIO"})
_NO_RULE_MESSAGE: Final = "la guía no cubre este material; cada ficha declara su uso"

DESCRIPTION: Final = (
    "Consulta la guía de materiales: temperatura, microondas y nivel por aplicación. Úsala "
    "para: qué material sirve para un tipo de comida o si aguanta calor o microondas. No la "
    "uses para: buscar referencias o precios (usa buscar_producto) ni la ficha de una "
    "referencia (usa ficha_producto)."
)

# Nombres coloquiales del material, plegados (minúsculas, sin tildes). «icopor» (Colombia),
# «unicel» (México) y «anime» (Venezuela) son el EPS, como en ``reference_names``.
_MATERIAL_SYNONYMS: Final[dict[str, str]] = {
    "pp": "PP",
    "polipropileno": "PP",
    "eps": "EPS",
    "icopor": "EPS",
    "unicel": "EPS",
    "anime": "EPS",
    "foam": "EPS",
    "espuma": "EPS",
    "espumado": "EPS",
    "poliestireno expandido": "EPS",
    "ps": "PS",
    "poliestireno": "PS",
    "pet": "PET",
    "aluminio": "ALUMINIO",
    "papel": "PAPEL",
    "carton": "PAPEL",
    "cartulina": "PAPEL",
    "pulpa": "PULPA",
    "bagazo": "PULPA",
    "fibra": "PULPA",
}
_APPLICATION_SYNONYMS: Final[dict[str, str]] = {
    **{name.replace("_", " "): name for name in APPLICATIONS},
    "comidas frias": "comida_fria",
    "comidas calientes": "comida_caliente",
    "almuerzo": "comida_caliente",
    "almuerzos": "comida_caliente",
    "sopa": "sopas",
    "caldo": "sopas",
    "caldos": "sopas",
    "consome": "sopas",
    "consomes": "sopas",
    "fritura": "frituras",
    "frito": "frituras",
    "fritos": "frituras",
    "papas fritas": "frituras",
    "ensalada": "ensaladas",
    "sandwich": "sandwiches",
    "sanduche": "sandwiches",
    "sanduches": "sandwiches",
    "emparedado": "sandwiches",
    "emparedados": "sandwiches",
    "salsa": "salsas",
    "aderezo": "salsas",
    "aderezos": "salsas",
    "pan": "panaderia",
    "panes": "panaderia",
    "pasteles": "panaderia",
    "postres": "panaderia",
    "galletas": "panaderia",
    "fruta": "frutas",
    **{name.replace("_", " "): name for name in BEVERAGE_USES},
    "bebida caliente": "bebida_caliente",
    "bebidas calientes": "bebida_caliente",
    "cafe": "bebida_caliente",
    "tinto": "bebida_caliente",
    "te": "bebida_caliente",
    "aromatica": "bebida_caliente",
    "chocolate": "bebida_caliente",
    "bebida fria": "bebida_fria",
    "bebidas frias": "bebida_fria",
    "jugo": "bebida_fria",
    "jugos": "bebida_fria",
    "gaseosa": "bebida_fria",
    "gaseosas": "bebida_fria",
    "refresco": "bebida_fria",
    "refrescos": "bebida_fria",
    "limonada": "bebida_fria",
    "malteada": "bebida_fria",
    "granizado": "bebida_fria",
}


def _fold(value: str) -> str:
    """Minúsculas sin tildes, con «_» y «-» como espacio y espacios colapsados."""

    decomposed = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(plain.replace("_", " ").replace("-", " ").split())


def _optional(value: object) -> object:
    """Texto vacío o solo espacios cuenta como ausente."""

    if isinstance(value, str) and not value.strip():
        return None
    return value


class GuiaMaterialesArgs(BaseModel):
    """Consulta de la guía de materiales por material, por aplicación o por ambos."""

    material: Material | None = Field(
        default=None,
        description=(
            "Material: PP, EPS, PS, PET, ALUMINIO, PAPEL o PULPA. Acepta nombres comunes "
            "como icopor o unicel (EPS)."
        ),
    )
    aplicacion: str | None = Field(
        default=None,
        description=(
            f"Tipo de comida de la guía ({', '.join(APPLICATIONS)}) o uso de bebida "
            f"({', '.join(BEVERAGE_USES)}): para bebidas la guía no da nivel y se responde "
            "con sus reglas por temperatura."
        ),
    )

    @field_validator("material", mode="before")
    @classmethod
    def _material_canonico(cls, value: object) -> object:
        value = _optional(value)
        if not isinstance(value, str):
            return value
        canonical = _MATERIAL_SYNONYMS.get(_fold(value))
        if canonical is None:
            raise ValueError(
                f"material desconocido: {value!r}; válidos: PP, EPS, PS, PET, ALUMINIO, "
                "PAPEL o PULPA"
            )
        return canonical

    @field_validator("aplicacion", mode="before")
    @classmethod
    def _aplicacion_canonica(cls, value: object) -> object:
        value = _optional(value)
        if not isinstance(value, str):
            return value
        canonical = _APPLICATION_SYNONYMS.get(_fold(value))
        if canonical is None:
            valid = ", ".join((*APPLICATIONS, *BEVERAGE_USES))
            raise ValueError(f"aplicación desconocida: {value!r}; válidas: {valid}")
        return canonical

    @model_validator(mode="after")
    def _al_menos_uno(self) -> Self:
        if self.material is None and self.aplicacion is None:
            raise ValueError("indica el material, la aplicación o ambos")
        return self


def status(_args: Mapping[str, Any]) -> str:
    """Línea de estado fija: no interpola el material ni la aplicación (FR-018)."""

    return "Estoy revisando la guía de materiales…"


@cache
def materials_without_rule(directory: str = str(DEFAULT_DIR)) -> tuple[str, ...]:
    """``materials_without_rule`` del manifiesto del catálogo (vacío si no se puede leer).

    Solo informa qué materiales declara el manifiesto fuera de la guía; que un material no
    tenga regla lo decide ``Catalog.materials``, así que un manifiesto ilegible no cambia
    ninguna respuesta de regla.
    """

    try:
        data = json.loads((Path(directory) / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    values = data.get("materials_without_rule") if isinstance(data, dict) else None
    if not isinstance(values, list):
        return ()
    return tuple(str(value).upper() for value in values)


def _levels(rule: MaterialRule) -> dict[str, str]:
    """Nivel por aplicación de la guía (``comida_fria=aplicable|…``), en su orden."""

    if not rule.applications:
        return {}
    pairs = (part.split("=", 1) for part in rule.applications.split("|") if "=" in part)
    return {use.strip(): level.strip() for use, level in pairs if level.strip() in _LEVEL_ORDER}


def _number(value: float) -> int | float:
    """Temperatura como entero cuando no tiene decimales (``-20.0`` → ``-20``)."""

    return int(value) if float(value).is_integer() else value


def _yes_no(value: bool) -> str:
    return "sí" if value else "no"


def _excerpt(rule: MaterialRule) -> str:
    """Transcripción literal de la fila de la guía: respalda cada dato devuelto."""

    refrigeration = _yes_no(rule.refrigeration)
    if rule.material in DEDUCED_REFRIGERATION:
        refrigeration += (
            " (deducida de su rango mínimo; la guía solo lo declara apto para congelación)"
        )
    recycling = rule.recycling_code or "no declarado"
    levels = _levels(rule)
    applications = "; ".join(
        f"{use.replace('_', ' ')}: {_LEVEL_LABELS[level]}" for use, level in levels.items()
    )
    parts = [
        f"{AVISO}.",
        f"{rule.material}: temperatura de {_number(rule.temp_min_c)} °C a "
        f"{_number(rule.temp_max_c)} °C",
        f"microondas: {_yes_no(rule.microwave)}",
        f"refrigeración: {refrigeration}",
        f"congelación: {_yes_no(rule.freezing)}",
        f"contenido caliente: {_yes_no(rule.hot_content)}",
        f"código de reciclaje: {recycling}.",
    ]
    text = " ".join(parts[:1]) + " " + "; ".join(parts[1:])
    if applications:
        text += f" Aplicaciones: {applications}."
    return text


def _source(rule: MaterialRule) -> dict[str, Any]:
    return {
        "chunk_id": f"guia:{rule.material}",
        "slug": "guia-materiales",
        "section": "guia_materiales",
        "layer": CORE_LAYER,
        "score": 1.0,
        "excerpt": _excerpt(rule),
        "url": rule.source_url,
        "title": f"Guía de materiales: {rule.material}",
        "source_type": "oficial" if _is_official(rule.source_url) else "tercero",
    }


def _row(
    rule: MaterialRule, number: int, application: str | None, *, with_applications: bool
) -> dict[str, Any]:
    levels = _levels(rule)
    row: dict[str, Any] = {
        "n": number,
        "material": rule.material,
        "temp_min_c": _number(rule.temp_min_c),
        "temp_max_c": _number(rule.temp_max_c),
        "microondas": rule.microwave,
        "refrigeracion": rule.refrigeration,
        "refrigeracion_deducida": rule.material in DEDUCED_REFRIGERATION,
        "congelacion": rule.freezing,
        "contenido_caliente": rule.hot_content,
        "codigo_reciclaje": rule.recycling_code,
        "nivel": levels.get(application) if application else None,
    }
    if with_applications:
        row["aplicaciones"] = levels
    return row


def _beverage_row(rule: MaterialRule, number: int, temperature: str) -> dict[str, Any]:
    """Regla de un material para una bebida: la fila de la guía y el campo que aplica.

    ``cumple`` es el valor de ese campo en la guía (``contenido_caliente`` o
    ``refrigeracion``), no una aptitud deducida: el nivel queda vacío.
    """

    row = _row(rule, number, None, with_applications=False)
    field = _BEVERAGE_FIELD[temperature]
    return {**row, "temperatura": temperature, "criterio": field, "cumple": bool(row[field])}


def _beverage(rules: Iterable[MaterialRule], application: str, sources: _Sources) -> _Draft:
    """Materiales de la guía para una bebida, por su regla de temperatura (sin nivel)."""

    temperature = BEVERAGE_USES[application]
    field = _BEVERAGE_FIELD[temperature]
    ordered = list(rules)
    numbers = sources.add([_source(rule) for rule in ordered])
    rows = [
        _beverage_row(rule, number, temperature)
        for rule, number in zip(ordered, numbers, strict=True)
    ]
    rows.sort(key=lambda row: not row["cumple"])  # estable: sigue el orden de la guía
    without_rule = [
        name for name in materials_without_rule() if name not in {r.material for r in ordered}
    ]
    message = (
        f"La guía no tiene la aplicación {application}: se dan las reglas por temperatura de "
        f"cada material ({field}, rango en °C), sin nivel recomendable o aplicable. La guía "
        "no cubre papel ni pulpa; cada ficha declara su uso (buscar_producto filtra por uso)."
    )
    return _Draft(
        "ok",
        message,
        data={
            "reglas": rows,
            "aviso": AVISO,
            "uso": application,
            "materiales_sin_regla": without_rule,
        },
    )


def run(
    args: GuiaMaterialesArgs, deps: ToolDeps, context: ToolContext, sources: _Sources
) -> _Draft:
    """Regla de la guía para el material, o los materiales ordenados por nivel para el uso."""

    try:
        catalog = deps.catalog or load_catalog()
    except CatalogError:
        return _Draft("error", "No se pudo cargar la guía de materiales; no se consultó nada.")
    rules = catalog.materials

    if args.material is not None:
        rule = rules.get(args.material)
        if rule is None:
            return _Draft("empty", _NO_RULE_MESSAGE, data={"sin_regla": True})
        [number] = sources.add([_source(rule)])
        if args.aplicacion in BEVERAGE_USES:
            row = _beverage_row(rule, number, BEVERAGE_USES[args.aplicacion])
            message = (
                f"La guía no tiene la aplicación {args.aplicacion}: se da la regla por "
                f"temperatura de {rule.material} ({_BEVERAGE_FIELD[row['temperatura']]}), sin "
                "nivel. Es la guía de materiales, no la ficha de una referencia."
            )
            return _Draft("ok", message, data={"reglas": [row], "aviso": AVISO})
        row = _row(rule, number, args.aplicacion, with_applications=args.aplicacion is None)
        if args.aplicacion is None:
            message = f"Regla de la guía de materiales para {rule.material}."
        elif row["nivel"] is None:
            message = (
                f"La guía no da nivel para {args.aplicacion} con {rule.material}; "
                "solo su regla general."
            )
        else:
            message = f"Nivel de la guía para {args.aplicacion} con {rule.material}."
        message += " Es la guía de materiales, no la ficha de una referencia."
        return _Draft("ok", message, data={"reglas": [row], "aviso": AVISO})

    application = args.aplicacion
    if application is None:  # el esquema ya exige material o aplicación
        return _Draft("error", "Indica el material, la aplicación o ambos.")
    if application in BEVERAGE_USES:
        return _beverage(rules.values(), application, sources)
    ranked = sorted(
        (rule for rule in rules.values() if application in _levels(rule)),
        key=lambda rule: _LEVEL_ORDER[_levels(rule)[application]],
    )
    without_rule = [name for name in materials_without_rule() if name not in rules]
    if not ranked:
        return _Draft(
            "empty",
            f"La guía no clasifica ningún material para {application}.",
            data={"materiales_sin_regla": without_rule},
        )
    numbers = sources.add([_source(rule) for rule in ranked])
    rows = [
        _row(rule, number, application, with_applications=False)
        for rule, number in zip(ranked, numbers, strict=True)
    ]
    message = (
        f"Materiales de la guía para {application}, de recomendable a no apto. "
        "La guía no cubre los materiales sin regla; cada ficha declara su uso."
    )
    return _Draft(
        "ok",
        message,
        data={"reglas": rows, "aviso": AVISO, "materiales_sin_regla": without_rule},
    )
