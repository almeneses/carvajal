"""Filtro determinista de datos sensibles de la memoria y del resumen (spec 045, T019).

SC-004: cero datos sensibles guardados en al menos 30 frases sensibles y cero rechazos
indebidos en al menos 30 frases válidas. El filtro decide por **forma** (research R8), no por
conteo de dígitos: cantidades, montos, fechas, rangos, tallas y folios no son sensibles.
"""

import pytest
from langgraph.store.memory import InMemoryStore

from carvajal.agent.memory.sensitive import REDACTED, find_sensitive, redact
from carvajal.agent.memory.store import load, remember

OWNER = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"

SENSITIVE = [
    ("mi cédula es 1.234.567.890", "documento"),
    ("cédula 1234567890", "documento"),
    ("CC 123456", "documento"),
    ("C.C. 79.123.456", "documento"),
    ("documento de identidad 52.345.678", "documento"),
    ("NIT 900.123.456-7", "documento"),
    ("pasaporte AB123456", "documento"),
    ("identificación 1098765432", "documento"),
    ("DNI 12345678", "documento"),
    ("mi celular es 300 123 4567", "telefono"),
    ("3001234567", "telefono"),
    ("+57 310 555 1234", "telefono"),
    ("+573105551234", "telefono"),
    ("teléfono fijo (601) 345 6789", "telefono"),
    ("601 345 6789", "telefono"),
    ("+1 305 555 0199", "telefono"),
    ("escríbeme a ana@laespiga.com", "correo"),
    ("ana.perez+compras@gmail.com", "correo"),
    ("tarjeta 4111 1111 1111 1111", "tarjeta"),
    ("4111111111111111", "tarjeta"),
    ("5500-0000-0000-0004", "tarjeta"),
    ("378282246310005", "tarjeta"),
    ("el CVV es 123", "tarjeta"),
    ("CVC 456", "tarjeta"),
    ("fecha de vencimiento 12/28", "tarjeta"),
    ("mi cuenta de ahorros es 123-456789-00", "cuenta"),
    ("cuenta corriente 0012345678", "cuenta"),
    ("número de cuenta 987654321", "cuenta"),
    ("mi contraseña es Espiga2024", "clave"),
    ("la clave es 4455", "clave"),
    ("mi PIN es 1234", "clave"),
    ("usuario y contraseña: ana / secreto", "clave"),
    ("token: abc123xyz", "clave"),
    # Formas que la revisión de la spec 045 encontró sin detectar (FR-012, R8).
    ("la clave de mi tarjeta es 4321", "clave"),
    ("el pin de mi tarjeta es 1234", "clave"),
    ("mi clave del banco es Hunter22", "clave"),
    ("clave 4455", "clave"),
    ("mi cuenta de Bancolombia es 12345678901", "cuenta"),
    ("cuenta en Davivienda: 0550-1234-5678", "cuenta"),
    ("mi RUT es 1020304050-1", "documento"),
    ("mi tarjeta de identidad es 1001234567", "documento"),
    ("TI 1001234567", "documento"),
    ("CE 456789", "documento"),
    ("mi celular es (310) 123-4567", "telefono"),
    ("mi celular es 310 12 34 567", "telefono"),
    ("31 01 23 45 67", "telefono"),
]

VALID = [
    "2.000 vasos",
    "10.000 unidades",
    "1.000.000 de vasos",
    "entre 2.000 y 10.000",
    "tamaños 8 10 12 16 oz",
    "presupuesto de $2.500.000",
    "1.250.000 pesos",
    "pedido del 2026-11-24",
    "8 oz",
    "caja x 1000",
    "Cali",
    "Panadería La Espiga",
    "a1b2c3d4e5f6",
    "123456789abc",
    "mi folio es a1b2c3d4e5f6",
    "llámame Ana",
    "prefiero respuestas cortas",
    "trabajo en Panadería La Espiga y suelo comprar en Cali",
    "suelo comprar en Bogotá",
    "500 vasos de 12 oz",
    "3 cajas",
    "quiero 300 contenedores",
    "COP 1.500.000",
    "$ 450.000",
    "pedimos 3000 vasos al mes",
    "vasos de 250 ml",
    "500 g de ensalada",
    "paquete de 50",
    "2026-12-01",
    "prefiero opciones compostables",
    "atendemos 2000 personas al día",
    "mi número de pedido es 12345",
    "la paca x 200 me sirve",
    "3001 vasos para el evento",
    # Sin falsos positivos de las formas nuevas.
    "para ti 50000 vasos",
    "ce 2026 es el año del evento",
    "la cuenta de cobro llega el 2026-12-01",
    "una cuenta de 500 vasos",
    "3.000.000 de vasos",
    "300 310 320 ml",
]


def test_hay_al_menos_30_frases_de_cada_tipo() -> None:
    assert len(SENSITIVE) >= 30
    assert len(VALID) >= 30


@pytest.mark.parametrize(("frase", "tipo"), SENSITIVE)
def test_las_frases_sensibles_se_detectan(frase: str, tipo: str) -> None:
    # Act
    found = find_sensitive(frase)

    # Assert
    assert tipo in found, (frase, found)


@pytest.mark.parametrize("frase", VALID)
def test_las_frases_validas_no_se_rechazan(frase: str) -> None:
    # Act / Assert
    assert find_sensitive(frase) == [], frase
    assert redact(frase) == frase


@pytest.mark.parametrize(("frase", "tipo"), SENSITIVE)
def test_redact_quita_el_dato_y_deja_la_marca(frase: str, tipo: str) -> None:
    # Act
    clean = redact(frase)

    # Assert
    assert REDACTED in clean
    assert find_sensitive(clean) == [], (frase, clean)


def test_redact_conserva_lo_valido_alrededor() -> None:
    # Arrange
    frase = "Soy Ana, mi celular es 300 123 4567 y necesito 2.000 vasos para el 2026-11-24"

    # Act
    clean = redact(frase)

    # Assert
    assert clean == (f"Soy Ana, mi celular es {REDACTED} y necesito 2.000 vasos para el 2026-11-24")
    assert REDACTED == "[dato omitido]"


def test_varios_tipos_en_una_frase_y_sin_repetir() -> None:
    found = find_sensitive("cédula 1234567890, correo ana@x.co y otro correo b@y.co")

    assert found == ["documento", "correo"]


@pytest.mark.parametrize(
    "frase",
    [
        "la clave de mi tarjeta es 4321",
        "el pin de mi tarjeta es 1234",
        "mi clave del banco es Hunter22",
        "mi cuenta de Bancolombia es 12345678901",
        "mi RUT es 1020304050-1",
        "mi tarjeta de identidad es 1001234567",
        "mi celular es (310) 123-4567",
        "mi celular es 310 12 34 567",
    ],
)
def test_la_memoria_no_guarda_como_nota_las_formas_nuevas(frase: str) -> None:
    # Arrange
    store = InMemoryStore()

    # Act
    operation = remember(store, OWNER, "nota", frase)

    # Assert
    assert operation.outcome == "rejected"
    assert operation.reason == "dato_sensible"
    assert load(store, OWNER).notes == ()
