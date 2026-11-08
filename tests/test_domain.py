"""Pruebas del dominio puro: reglas del corpus y entidades."""

import pytest

from carvajal.domain.entities import CapturedPage
from carvajal.domain.rules import (
    OFFICIAL_DOMAIN,
    content_hash,
    domain_from_url,
    estimate_tokens,
    is_duplicate,
    is_official_domain,
    section_from_url,
    slug_from_url,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.carvajal.com/", "home"),
        ("https://www.carvajal.com/inversionistas/inicio/", "inicio"),
        ("https://www.carvajal.com/quienes-somos/", "quienes_somos"),
        ("https://www.carvajal.com/informe.2024/", "informe2024"),
    ],
)
def test_slug_from_url(url: str, expected: str) -> None:
    """Portada ``home``, último segmento, guiones a bajos y puntos eliminados."""

    # Act
    slug = slug_from_url(url)

    # Assert
    assert slug == expected


@pytest.mark.parametrize(
    "url",
    ["https://WWW.Carvajal.com/historia/", "https://CarvaJal.com/historia/"],
)
def test_domain_from_url_quita_www_y_normaliza(url: str) -> None:
    """El dominio se registra en minúsculas y sin el prefijo ``www.``."""

    # Act
    domain = domain_from_url(url)

    # Assert
    assert domain == OFFICIAL_DOMAIN


@pytest.mark.parametrize(
    ("domain", "expected"),
    [("carvajal.com", True), ("inversionistas.carvajal.com", True), ("carvajal.com.co", False)],
)
def test_is_official_domain(domain: str, expected: bool) -> None:
    """El dominio raíz y sus subdominios son oficiales; un tercero no."""

    # Act
    official = is_official_domain(domain)

    # Assert
    assert official is expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.carvajal.com/", "home"),
        ("https://www.carvajal.com/inversionistas/inicio/", "inversionistas"),
        ("https://www.larepublica.co/empresas/nota-4099828", "prensa"),
    ],
)
def test_section_from_url(url: str, expected: str) -> None:
    """Portada ``home``, primer segmento oficial y ``prensa`` para terceros."""

    # Act
    section = section_from_url(url)

    # Assert
    assert section == expected


def test_content_hash_es_determinista_y_distingue_contenidos() -> None:
    """El mismo Markdown produce la misma huella; otro contenido produce otra."""

    # Act
    primera = content_hash("# Título\n\nCuerpo.")
    repetida = content_hash("# Título\n\nCuerpo.")
    distinta = content_hash("# Título\n\nOtro cuerpo.")

    # Assert
    assert primera == repetida
    assert primera != distinta


def test_estimate_tokens_tiene_minimo_uno_y_es_proporcional() -> None:
    """El texto vacío estima un token y el cálculo crece ≈4 caracteres por token."""

    # Act
    vacio = estimate_tokens("")
    cuatrocientos = estimate_tokens("x" * 400)

    # Assert
    assert vacio == 1
    assert cuatrocientos == 100


@pytest.mark.parametrize(
    ("markdown", "seen", "expected"),
    [
        ("corto", {content_hash("corto")}, False),
        ("x" * 300, {content_hash("x" * 300)}, True),
        ("x" * 300, {content_hash("otro contenido")}, False),
    ],
)
def test_is_duplicate(markdown: str, seen: set[str], expected: bool) -> None:
    """Las páginas cortas nunca duplican; las largas solo si la huella ya existe."""

    # Act
    duplicate = is_duplicate(markdown, seen)

    # Assert
    assert duplicate is expected


def test_captured_page_conserva_los_valores_previos() -> None:
    """La entidad mantiene los mismos metadatos que la implementación anterior."""

    # Arrange
    home = CapturedPage(url="https://www.carvajal.com/", html="", markdown="x" * 8)
    inversionistas = CapturedPage(
        url="https://www.carvajal.com/inversionistas/inicio/", html="", markdown="x" * 8
    )
    tercero = CapturedPage(
        url="https://www.larepublica.co/empresas/nota-4099828", html="", markdown="x" * 8
    )

    # Assert
    assert home.slug == "home"
    assert home.section == "home"
    assert home.is_official is True
    assert home.domain == "carvajal.com"
    assert inversionistas.slug == "inicio"
    assert inversionistas.section == "inversionistas"
    assert tercero.slug == "nota_4099828"
    assert tercero.section == "prensa"
    assert tercero.is_official is False
    assert tercero.domain == "larepublica.co"
    assert home.content_hash == content_hash("x" * 8)
    assert home.tokens_estimate == estimate_tokens("x" * 8)
