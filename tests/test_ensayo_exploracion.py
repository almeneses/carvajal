"""Alertas de la exploración de escenarios (tests/e2e/test_exploracion.py), sin red."""

from itertools import pairwise

from tests.e2e.test_exploracion import ESCENARIOS, alertas

from carvajal.agent.followup import is_confirmation, resolve_more


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
    # Solo «¿La confirmo?» (``confirmar``) registra un folio, y a esa pregunta se llega
    # únicamente tras un cierre («eso es todo», «no nada más», «…, confírmala»). Con las
    # mismas reglas del flujo: ningún guion afirma después de un cierre, ni cierra
    # confirmando, así la exploración no deja folios ni PDF en producción.
    for nombre, pasos in ESCENARIOS:
        textos = [mensaje for mensaje, _voz in pasos]
        for texto in textos:
            assert resolve_more(texto) != "close_confirm", (nombre, texto)
        for anterior, actual in pairwise(textos):
            cierra = resolve_more(anterior) in ("close", "close_confirm")
            assert not (cierra and is_confirmation(actual)), (nombre, anterior, actual)
    # La guarda no es vacía: el par que registraría un folio sí se detecta.
    assert resolve_more("no, nada más") == "close"
    assert is_confirmation("sí")
