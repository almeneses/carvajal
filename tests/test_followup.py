"""Elegir una opción y confirmar una propuesta por texto o voz (spec 053, FR-005 y FR-011).

SC-001: con al menos 40 frases de elección (escritas y transcritas), el 95 % o más se
resuelve a la opción correcta y ninguna a una incorrecta: las dudosas no eligen.
"""

import pytest

from carvajal.agent.followup import Choice, ChoiceOutOfRange, is_confirmation, resolve_choice

# (frase, opción esperada): 0 = recomendada; 1-4 = alternativa; None = no es una elección.
CHOICES: list[tuple[str, int | None]] = [
    # La recomendada
    ("dame esa", 0),
    ("Dame esa!", 0),
    ("esa", 0),
    ("esa misma", 0),
    ("la recomendada", 0),
    ("vamos con la recomendada", 0),
    ("me quedo con esa", 0),
    ("quiero esa", 0),
    ("ok, dame esa", 0),
    ("la mejor", 0),
    ("la que me recomiendas", 0),
    ("listo, esa", 0),
    ("me sirve esa", 0),
    ("sí, esa", 0),
    ("vamos con esa, son 300 para Medellín", 0),
    ("tu recomendación", 0),
    # Alternativas por número u ordinal
    ("vamos con la alternativa 1", 1),
    ("alternativa 1", 1),
    ("la 2", 2),
    ("vamos con la 2", 2),
    ("vamos con la 2, son 500 para Cali", 2),
    ("la dos", 2),
    ("opción 3", 3),
    ("la opción 3", 3),
    ("la segunda", 2),
    ("la primera", 1),
    ("me quedo con la tercera", 3),
    ("quiero la número 4", 4),
    ("alternativa #4 por favor", 4),
    ("ok, la 4", 4),
    ("el 2", 2),
    ("2", 2),
    ("prefiero la cuarta", 4),
    ("dame la 3", 3),
    ("cotízame la 2", 2),
    ("bueno, vamos con la uno", 1),
    ("elijo la alternativa dos", 2),
    ("mejor la 3", 3),
    # No son elecciones
    ("2 cajas", None),
    ("son 500 para Cali", None),
    ("¿la 2 sirve para microondas?", None),
    ("la 2 pero en 12 oz", None),
    ("esa pero más grande", None),
    ("dame una de esas", None),
    ("¿cuánto cuesta?", None),
    ("quiero vasos de 12 oz", None),
    ("en Bogotá", None),
    ("500", None),
]


@pytest.mark.parametrize(("text", "expected"), CHOICES)
def test_resuelve_la_eleccion(text: str, expected: int | None) -> None:
    # Act
    found = resolve_choice(text, 4)

    # Assert
    assert found == (None if expected is None else Choice(expected))


def test_el_conjunto_cumple_sc_001() -> None:
    # Arrange
    choices = [(text, expected) for text, expected in CHOICES if expected is not None]

    # Act
    hits = [resolve_choice(text, 4) == Choice(expected) for text, expected in choices]
    wrong = [
        text
        for text, expected in choices
        if isinstance(found := resolve_choice(text, 4), Choice) and found.index != expected
    ]

    # Assert
    assert len(CHOICES) >= 40
    assert sum(hits) / len(hits) >= 0.95
    assert wrong == []


@pytest.mark.parametrize(("text", "asked"), [("la 5", 5), ("alternativa 6", 6), ("la quinta", 5)])
def test_una_alternativa_que_no_existe_no_elige(text: str, asked: int) -> None:
    # Act / Assert
    assert resolve_choice(text, 4) == ChoiceOutOfRange(asked)


def test_con_menos_alternativas_el_rango_se_ajusta() -> None:
    # Act / Assert
    assert resolve_choice("la 2", 1) == ChoiceOutOfRange(2)
    assert resolve_choice("la 1", 1) == Choice(1)


@pytest.mark.parametrize(
    "text",
    [
        "sí",
        "Si",
        "sí, confírmala",
        "Sí, confirma la cotización",
        "confirmo",
        "dale",
        "listo",
        "de una",
        "sale",
        "ok gracias",
        "perfecto, confírmala por favor",
        "sí por favor",
        "hágale",
        "está bien",
        "me sirve",
    ],
)
def test_reconoce_la_confirmacion(text: str) -> None:
    # Act / Assert
    assert is_confirmation(text)


@pytest.mark.parametrize(
    "text",
    [
        "no",
        "sí pero mejor 800",
        "¿sí?",
        "mejor en Cali",
        "dame la 2",
        "cotiza 500 vasos",
        "sí, cámbialo a 1000 unidades por favor",
        "",
    ],
)
def test_lo_que_no_es_un_si_no_confirma(text: str) -> None:
    # Act / Assert
    assert not is_confirmation(text)
