"""Perfil del alimento que devuelve el clasificador (spec 051, research R1).

Las preguntas son fijas y cerradas: el clasificador solo elige entre opciones, así que
ningún texto de la imagen (p. ej. «ignora las reglas») llega a quien redacta la respuesta.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal, cast, get_args

__all__ = [
    "CONSISTENCIES",
    "FOOD_KINDS",
    "PHOTO_REF",
    "TEMPERATURES",
    "Consistency",
    "FoodKind",
    "FoodProfile",
    "Temperature",
    "food_questions",
    "parse_food_answers",
]

FoodKind = Literal[
    "sopas",
    "plato_fuerte",
    "frituras",
    "ensaladas",
    "salsas",
    "panaderia",
    "frutas",
    "postres_helados",
    "bebida_fria",
    "bebida_caliente",
    "no_es_comida",
]
Consistency = Literal["solido", "semiliquido", "liquido"]
Temperature = Literal["caliente", "fria", "ambiente"]

FOOD_KINDS: Final[tuple[FoodKind, ...]] = get_args(FoodKind)
CONSISTENCIES: Final[tuple[Consistency, ...]] = get_args(Consistency)
TEMPERATURES: Final[tuple[Temperature, ...]] = get_args(Temperature)

# Marcador opaco de la foto en ``state`` (TypeSafe lo sustituye por la imagen de ``assets``).
PHOTO_REF: Final = "foto-alimento-051"

_KIND_CRITERIA: Final[dict[FoodKind, str]] = {
    "sopas": "sopa, caldo, crema o consomé servido en recipiente",
    "plato_fuerte": (
        "plato principal con varias preparaciones (arroz, carne, granos, huevo, acompañamientos)"
    ),
    "frituras": "alimento frito o apanado: empanadas, papas fritas, chicharrón, buñuelos",
    "ensaladas": "ensalada de hojas o verduras frescas",
    "salsas": "salsa, dip, aderezo o untable (guacamole, ají, hogao)",
    "panaderia": "pan, galleta, pandebono, arepa, ponqué u otro producto horneado",
    "frutas": "frutas enteras o picadas para comer con cubierto o la mano",
    "postres_helados": "postre o helado",
    "bebida_fria": "bebida fría: jugo, limonada, gaseosa, batido, bebida con hielo",
    "bebida_caliente": "bebida caliente: café, tinto, chocolate, aromática, té",
    "no_es_comida": "la foto no muestra comida ni bebida como tema principal",
}
_CONSISTENCY_CRITERIA: Final[dict[Consistency, str]] = {
    "solido": "sólido, se puede tomar con la mano o el tenedor sin derramarse",
    "semiliquido": "semilíquido o pastoso: puré, crema espesa, helado, guacamole",
    "liquido": "líquido que se derrama: sopa, caldo, bebida",
}
_TEMPERATURE_CRITERIA: Final[dict[Temperature, str]] = {
    "caliente": "recién preparado y caliente (más de 60 °C)",
    "fria": "frío o refrigerado, o congelado",
    "ambiente": "a temperatura ambiente",
}
_PORTION_LEVELS: Final = [
    "muy pequeña: un bocado, una salsa o una unidad pequeña",
    "individual pequeña: un vaso o un pocillo, un acompañamiento",
    "individual: un plato o una bebida para una persona",
    "grande o para compartir: un plato abundante o una jarra",
]


@dataclass(frozen=True)
class FoodProfile:
    """Lo que el clasificador vio en la foto, con la confianza de cada respuesta."""

    food_p: float
    kind: FoodKind
    kind_confidence: float
    kind_runner_up: FoodKind | None
    consistency: Consistency
    consistency_confidence: float
    temperature: Temperature
    temperature_confidence: float
    portion: float
    portion_confidence: float

    def metadata(self) -> dict[str, Any]:
        """Campos sin texto para el libro de Jev, la traza y los logs."""

        return {
            "comida": round(self.food_p, 3),
            "tipo": self.kind,
            "conf_tipo": round(self.kind_confidence, 3),
            "consistencia": self.consistency,
            "temperatura": self.temperature,
            "conf_temperatura": round(self.temperature_confidence, 3),
            "porcion": round(self.portion, 2),
        }


def food_questions() -> dict[str, Any]:
    """Las cinco preguntas de la foto (una sola petición)."""

    return {
        "es_comida": {
            "type": "noul",
            "instructions": (
                "¿El tema principal de `foto` es comida o bebida lista para servir o empacar?"
            ),
        },
        "tipo": {
            "type": "choice",
            "instructions": "¿Qué tipo de comida o bebida muestra `foto`?",
            "criteria": dict(_KIND_CRITERIA),
        },
        "consistencia": {
            "type": "choice",
            "instructions": "¿Cuál es la consistencia del alimento principal de `foto`?",
            "criteria": dict(_CONSISTENCY_CRITERIA),
        },
        "temperatura": {
            "type": "choice",
            "instructions": (
                "¿A qué temperatura se sirve y se empaca normalmente el alimento de `foto`?"
            ),
            "criteria": dict(_TEMPERATURE_CRITERIA),
        },
        "porcion": {
            "type": "score",
            "instructions": "¿Qué tamaño tiene la porción de `foto`?",
            "criteria": list(_PORTION_LEVELS),
        },
    }


def _unit(value: Any) -> float:
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError("probabilidad fuera de [0, 1]")
    return number


def _choice(answer: Any, options: tuple[str, ...]) -> tuple[str, float, str | None]:
    if not isinstance(answer, Mapping):
        raise ValueError("choice sin objeto")
    choice = answer.get("choice")
    if choice not in options:
        raise ValueError("opción desconocida")
    probabilities = answer.get("probabilities")
    ranked: list[tuple[str, float]] = []
    if isinstance(probabilities, Mapping):
        ranked = sorted(
            ((str(k), float(v)) for k, v in probabilities.items() if k in options),
            key=lambda item: -item[1],
        )
    confidence = answer.get("confidence")
    if confidence is None:
        confidence = ranked[0][1] if ranked else 0.0
    runner_up = next((name for name, _ in ranked if name != choice), None)
    return str(choice), _unit(confidence), runner_up


def parse_food_answers(payload: Mapping[str, Any]) -> tuple[FoodProfile, dict[str, Any]]:
    """Respuesta de Jev → perfil y campos del libro; ``ValueError`` si la forma no cuadra."""

    answers = payload.get("answers")
    if not isinstance(answers, Mapping):
        raise ValueError("respuesta sin answers")
    food = answers.get("es_comida")
    if not isinstance(food, Mapping):
        raise ValueError("falta es_comida")
    kind, kind_confidence, runner_up = _choice(answers.get("tipo"), FOOD_KINDS)
    consistency, consistency_confidence, _ = _choice(answers.get("consistencia"), CONSISTENCIES)
    temperature, temperature_confidence, _ = _choice(answers.get("temperatura"), TEMPERATURES)
    portion = answers.get("porcion")
    if not isinstance(portion, Mapping):
        raise ValueError("falta porcion")
    score = float(portion.get("score"))
    if not 0.0 <= score <= len(_PORTION_LEVELS) - 1:
        raise ValueError("porción fuera de escala")
    profile = FoodProfile(
        food_p=_unit(food.get("noul")),
        kind=cast(FoodKind, kind),
        kind_confidence=kind_confidence,
        kind_runner_up=cast(FoodKind | None, runner_up),
        consistency=cast(Consistency, consistency),
        consistency_confidence=consistency_confidence,
        temperature=cast(Temperature, temperature),
        temperature_confidence=temperature_confidence,
        portion=score,
        portion_confidence=_unit(portion.get("confidence") or 0.0),
    )
    return profile, {"food": profile.metadata()}
