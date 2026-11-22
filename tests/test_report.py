"""Pruebas de higiene de los mensajes de los informes (spec 037, T061)."""

import pytest

from carvajal.report import redact_credentials, safe_report_error


def test_redacta_la_contrasena_de_una_url_de_conexion() -> None:
    # Arrange
    mensaje = "connection to postgresql://carvajal:s3creto@db:5432/carvajal failed"

    # Act
    limpio = redact_credentials(mensaje)

    # Assert
    assert "s3creto" not in limpio
    assert "postgresql://carvajal:***@db:5432/carvajal" in limpio


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("/srv/carvajal/data/gold/historia.md: no existe"),
        FileNotFoundError(r"C:\carvajal\data\gold\historia.md: no existe"),
    ],
)
def test_reduce_las_rutas_absolutas_al_nombre_del_archivo(error: Exception) -> None:
    # Act
    limpio = safe_report_error(error)

    # Assert
    assert "historia.md" in limpio
    assert "/srv/carvajal" not in limpio
    assert "C:\\carvajal" not in limpio


def test_conserva_las_urls_de_una_fuente() -> None:
    # Arrange
    mensaje = "no se pudo leer https://carvajal.com/historia/"

    # Act
    limpio = safe_report_error(mensaje)

    # Assert
    assert limpio == mensaje
