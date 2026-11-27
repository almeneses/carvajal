"""Lógica pura de ``tests/e2e/whatsapp_web.py`` (corre en la CI, sin navegador)."""

from tests.e2e.whatsapp_web import Burbuja, nuevas, sin_numero


def test_las_respuestas_nuevas_se_reconocen_por_id_y_no_por_posicion() -> None:
    # Arrange: WhatsApp Web virtualiza la lista; la primera fila desaparece al llegar otras.
    antes = {"a", "b", "c"}
    ahora = [Burbuja("b", "out", "hola"), Burbuja("c", "in", "x"), Burbuja("d", "out", "dame esa")]
    ahora.append(Burbuja("e", "?", "[Carva] ✅ Agregué 500 Vaso"))

    # Act
    resultado = nuevas(antes, ahora)

    # Assert
    assert [b.id for b in resultado] == ["d", "e"]
    assert [b.del_bot for b in resultado] == [False, True]


def test_una_burbuja_es_del_bot_por_su_cola_o_por_el_prefijo_del_agente() -> None:
    assert Burbuja("1", "in", "texto").del_bot
    assert Burbuja("2", "?", "  [Carva] Listo").del_bot
    assert not Burbuja("3", "out", "cancela todo").del_bot
    assert not Burbuja("4", "?", "0:07").del_bot  # una nota de voz propia


def test_quitar_el_numero_de_prueba_conserva_los_demas_y_su_orden() -> None:
    # Arrange: alguien agregó otro número mientras corría la prueba.
    vigente = ["+573000000001", "+573000000002", "+57 300 000 0003"]

    # Act
    restante = sin_numero(vigente, "573000000002")

    # Assert
    assert restante == ["+573000000001", "+57 300 000 0003"]
