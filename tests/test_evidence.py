"""Pruebas de la comprobación léxica de evidencia."""

from carvajal.application.evidence import proper_entities_supported


def test_una_entidad_en_minusculas_tambien_fuerza_abstencion() -> None:
    assert not proper_entities_supported(
        "¿cuál es el teléfono de la sede en tokio?",
        ["texto sin esa ciudad"],
    )


def test_una_entidad_presente_en_el_contexto_pasa() -> None:
    assert proper_entities_supported(
        "¿Cuál es el propósito de Carvajal?",
        ["Carvajal es una empresa"],
    )
