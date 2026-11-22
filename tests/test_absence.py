"""Normalización de temas sin evidencia para el backlog del corpus."""

from carvajal.application.absence import normalize_topic


def test_quita_datos_de_contacto_e_identificadores() -> None:
    tema = normalize_topic(
        "Mi correo es ana@example.com, mi tel +57 300 123 4567 y mi pedido 123456789"
    )

    assert "ana@example.com" not in tema
    assert "300" not in tema
    assert "123456789" not in tema


def test_conserva_el_tema_y_el_ano() -> None:
    tema = normalize_topic("¿Cuáles son los ingresos de Carvajal en 2024?")

    assert tema == "ingresos carvajal 2024"


def test_normaliza_tildes_y_limita_palabras() -> None:
    assert normalize_topic("¿Dónde queda Bogotá?") == "bogota"
    tema = normalize_topic("uno dos tres cuatro cinco seis siete ocho nueve diez")

    assert len(tema.split()) == 8
