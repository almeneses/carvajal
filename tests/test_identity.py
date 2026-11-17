"""Pruebas de la identidad estable del corpus (spec 037, Phase 1)."""

import pytest

from carvajal.identity import (
    assign_slug,
    normalize_url,
    slug_for_file,
    slug_for_url,
    source_key_for_file,
    source_key_for_upload,
    source_key_for_url,
)


def test_normalize_url_quita_www_barra_final_y_fragmento() -> None:
    # Arrange
    url = "https://www.Carvajal.com/historia/#equipo"

    # Act
    normalizada = normalize_url(url)

    # Assert
    assert normalizada == "https://carvajal.com/historia"


def test_normalize_url_conserva_la_portada_y_la_cadena_de_consulta() -> None:
    # Arrange
    portada = "http://www.carvajal.com/"
    con_parametros = "https://carvajal.com/inversionistas?anio=2025#tabla"

    # Act
    normalizada_portada = normalize_url(portada)
    normalizada_parametros = normalize_url(con_parametros)

    # Assert
    assert normalizada_portada == "https://carvajal.com/"
    assert normalizada_parametros == "https://carvajal.com/inversionistas?anio=2025"


def test_normalize_url_coincide_con_la_del_importador() -> None:
    """La normalización del corpus extendido y la de la identidad son la misma."""

    from carvajal.importer import normalize_url as normalize_url_importador

    # Arrange
    urls = [
        "https://www.carvajal.com/",
        "http://carvajal.com.co/nota/",
        "https://www.larepublica.co/empresas/nota-4099828?origen=rss#top",
        "HTTPS://WWW.CARVAJAL.COM/A/B/",
    ]

    # Act
    pares = [(normalize_url(url), normalize_url_importador(url)) for url in urls]

    # Assert
    assert all(normalizada == importada for normalizada, importada in pares)


def test_source_key_de_url_envuelve_la_url_normalizada() -> None:
    # Arrange
    url = "https://www.carvajal.com/quienes-somos/"

    # Act
    clave = source_key_for_url(url)

    # Assert
    assert clave == "url:https://carvajal.com/quienes-somos"


def test_source_key_de_archivo_usa_la_raiz_y_la_ruta_relativa() -> None:
    # Arrange
    raiz = "/home/operador/corpus"

    # Act
    clave = source_key_for_file("inversionistas/2025/informe.pdf", root=raiz)

    # Assert
    assert clave == "file:corpus/inversionistas/2025/informe.pdf"


def test_source_key_de_archivo_normaliza_separadores_y_puntos() -> None:
    # Arrange
    ruta = "./inversionistas\\2025\\informe.pdf"

    # Act
    clave = source_key_for_file(ruta, root="corpus")

    # Assert
    assert clave == "file:corpus/inversionistas/2025/informe.pdf"


def test_source_key_de_archivo_suelto_usa_solo_el_nombre() -> None:
    # Arrange
    ruta = "cargas/2025/informe.pdf"

    # Act
    clave = source_key_for_file(ruta)

    # Assert
    assert clave == "file:informe.pdf"


def test_source_key_de_archivo_rechaza_rutas_que_salen_de_la_raiz() -> None:
    # Arrange
    ruta = "../../etc/passwd"

    # Act / Assert
    with pytest.raises(ValueError, match="raíz"):
        source_key_for_file(ruta, root="corpus")


def test_source_key_de_archivo_rechaza_la_ruta_vacia() -> None:
    # Arrange
    ruta = "./"

    # Act / Assert
    with pytest.raises(ValueError, match="vacía"):
        source_key_for_file(ruta, root="corpus")


def test_source_key_de_subida_usa_el_nombre_del_archivo() -> None:
    # Arrange
    nombre = "cargas/Informe 2025.pdf"

    # Act
    clave = source_key_for_upload(nombre)

    # Assert
    assert clave == "upload:Informe 2025.pdf"


def test_source_key_de_subida_rechaza_el_nombre_vacio() -> None:
    # Arrange / Act / Assert
    with pytest.raises(ValueError, match="vacío"):
        source_key_for_upload("")


def test_slug_de_archivo_pliega_acentos_y_separa_palabras() -> None:
    # Arrange
    nombre = "Cambio climático — Informe 2025.pdf"

    # Act
    slug = slug_for_file(nombre)

    # Assert
    assert slug == "cambio-climatico-informe-2025"


def test_slug_de_archivo_sin_caracteres_legibles_usa_documento() -> None:
    # Arrange
    nombre = "---.pdf"

    # Act
    slug = slug_for_file(nombre)

    # Assert
    assert slug == "documento"


def test_slug_de_url_usa_el_ultimo_segmento() -> None:
    # Arrange
    url = "https://www.carvajal.com/inversionistas/estados-financieros-2025/"

    # Act
    slug = slug_for_url(url)

    # Assert
    assert slug == "estados_financieros_2025"


def test_assign_slug_reutiliza_el_slug_de_la_misma_fuente() -> None:
    # Arrange
    ocupados = {"informe": "upload:informe.pdf"}
    clave = "upload:informe.pdf"

    # Act
    slug = assign_slug("informe", clave, ocupados)

    # Assert
    assert slug == "informe"


def test_assign_slug_agrega_sufijo_cuando_otra_fuente_lo_ocupa() -> None:
    # Arrange
    ocupados = {"informe": "upload:informe.pdf"}

    # Act
    slug = assign_slug("informe", "file:corpus/informe.pdf", ocupados)

    # Assert
    assert slug == "informe-2"


def test_assign_slug_salta_los_sufijos_ya_ocupados() -> None:
    # Arrange
    ocupados = {
        "informe": "upload:informe.pdf",
        "informe-2": "file:corpus/informe.pdf",
        "informe-3": "upload:otro.pdf",
    }

    # Act
    slug = assign_slug("informe", "file:otra/informe.pdf", ocupados)

    # Assert
    assert slug == "informe-4"


def test_la_identidad_no_depende_del_contenido() -> None:
    """FR-001: dos archivos iguales de nombre y distintos de contenido comparten clave."""

    # Arrange
    nombres = ("informes/enero.pdf", "informes/enero.pdf")

    # Act
    claves = {source_key_for_file(nombre, root="corpus") for nombre in nombres}

    # Assert
    assert claves == {"file:corpus/informes/enero.pdf"}


def test_la_identidad_es_estable_entre_corridas() -> None:
    # Arrange
    url = "https://www.carvajal.com/historia/"

    # Act
    primera = source_key_for_url(url)
    segunda = source_key_for_url(url)

    # Assert
    assert primera == segunda
    assert "sha256" not in primera
