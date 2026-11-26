"""Alertas de la exploración de escenarios (tests/e2e/test_exploracion.py), sin red."""

from itertools import pairwise

from tests.e2e.test_exploracion import ESCENARIOS, alertas


def test_marca_las_respuestas_genericas_y_las_citas_sin_fuente() -> None:
    # Arrange
    turnos = [
        {"msg": "Medellín", "resp": "Para seguir necesito un dato más. ¿Me cuentas…?"},
        {"msg": "sedes", "resp": "Dos sedes [1][2].\n\n*Fuentes:*\n[1] Sede A — https://x"},
    ]

    # Act
    encontradas = alertas("sedes", "wa", turnos)

    # Assert
    assert any("respuesta genérica" in a for a in encontradas)
    assert any("citas sin fuente ['2']" in a for a in encontradas)


def test_marca_la_ayuda_ajena_y_lo_agregado_tras_eso_es_todo() -> None:
    assert alertas("fuera_de_alcance", "web", [{"msg": "tarea", "resp": "Sí, te ayudo."}])
    assert alertas("x", "web", [{"msg": "eso es todo", "resp": "✅ Agregué 2.000 platos"}])


def test_una_respuesta_buena_no_tiene_alertas() -> None:
    turnos = [{"msg": "¿Dónde compro?", "resp": "En Pereira [1].\n\n*Fuentes:*\n[1] Sede — u"}]
    assert alertas("sedes", "wa", turnos) == []


def test_ningun_guion_confirma_una_cotizacion() -> None:
    # Sin «sí» después de un resumen: la exploración no deja folios en producción.
    for _nombre, pasos in ESCENARIOS:
        textos = [mensaje.lower() for mensaje, _voz in pasos]
        for anterior, actual in pairwise(textos):
            assert not ("eso es todo" in anterior and actual.strip(" .!") in {"sí", "si"})
