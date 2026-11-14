"""Pruebas del frontend propio del M2: shell SPA y contrato de integración."""

import re

from fastapi.testclient import TestClient

from apps.web.main import API_BASE, app

cliente = TestClient(app)

RUTAS_SPA = ("/", "/corpus", "/documents", "/documents/abc")


def test_rutas_spa_devuelven_el_mismo_html() -> None:
    # Arrange
    index = cliente.get("/")

    # Act / Assert
    for ruta in RUTAS_SPA:
        respuesta = cliente.get(ruta)
        assert respuesta.status_code == 200, ruta
        assert respuesta.headers["content-type"].startswith("text/html"), ruta
        assert respuesta.headers["cache-control"] == "no-store", ruta
        assert respuesta.text == index.text, ruta


def test_csp_estricta_y_cabeceras_de_seguridad() -> None:
    # Arrange
    respuesta = cliente.get("/")

    # Act
    csp = respuesta.headers["content-security-policy"]

    # Assert
    assert "default-src 'self'" in csp
    assert f"connect-src 'self' {API_BASE}" in csp
    assert "img-src 'self' data:" in csp
    assert "style-src 'self'" in csp
    assert "script-src 'self'" in csp
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert respuesta.headers["x-content-type-options"] == "nosniff"


def test_index_carga_scripts_y_hojas_en_orden() -> None:
    # Arrange
    html = cliente.get("/").text

    # Act
    etiquetas = re.findall(r"<script\b[^>]*>", html)
    fuentes = [re.search(r'src="([^"]+)"', etiqueta) for etiqueta in etiquetas]

    # Assert
    assert [encontrado.group(1) for encontrado in fuentes if encontrado] == [
        "/config.js",
        "/static/lectura.js",
        "/static/app.js",
    ]
    assert all("defer" in etiqueta for etiqueta in etiquetas)
    assert html.index("/static/styles.css") < html.index("/static/lectura.css")
    assert 'rel="stylesheet"' in html


def test_index_no_usa_scripts_ni_estilos_inline() -> None:
    # Arrange
    html = cliente.get("/").text

    # Act
    etiquetas = re.findall(r"<script\b[^>]*>", html)

    # Assert
    assert all("src=" in etiqueta for etiqueta in etiquetas)
    assert " style=" not in html
    assert "javascript:" not in html


def test_markup_del_contrato_de_lectura() -> None:
    # Arrange
    html = cliente.get("/").text

    # Assert
    assert '<nav class="pestanas" aria-label="Pantallas de la aplicación">' in html
    assert 'aria-current="page"' in html
    assert '<section id="vista-corpus" data-vista="corpus" hidden' in html
    assert '<h1 id="titulo-corpus" tabindex="-1">Explora el corpus</h1>' in html
    assert '<form id="corpus-filtros" class="filtros" role="search"' in html
    for identificador in (
        "corpus-q",
        "corpus-section",
        "corpus-source",
        "corpus-layer",
        "corpus-limpiar",
        "corpus-conteo",
        "corpus-lista",
        "corpus-estado",
        "corpus-paginacion",
    ):
        assert f'id="{identificador}"' in html
    assert 'type="search"' in html
    assert 'id="corpus-source" name="source_type"' in html
    assert '<ul id="corpus-lista"' in html
    assert 'id="corpus-conteo" class="corpus-conteo" role="status"' in html
    assert 'id="corpus-estado" class="corpus-estado" role="status"' in html
    assert 'id="vista-documento"' in html
    assert 'data-vista="documento"' in html
    assert '<div id="documento-contenido"></div>' in html
    assert '<div id="anuncios"' in html


def test_config_js_y_healthz_se_mantienen() -> None:
    # Arrange / Act
    config = cliente.get("/config.js")
    salud = cliente.get("/healthz")

    # Assert
    assert config.status_code == 200
    assert config.headers["content-type"].startswith("application/javascript")
    assert f'window.CARVAJAL_API = "{API_BASE}";' in config.text
    assert salud.status_code == 200
    assert salud.json() == {"status": "ok", "api": API_BASE}


def test_la_base_local_sigue_el_host_para_conservar_la_cookie() -> None:
    # Arrange: el navegador entra por 127.0.0.1, no por localhost.
    cliente_local = TestClient(app, base_url="http://127.0.0.1:8001")

    # Act
    config = cliente_local.get("/config.js")
    respuesta = cliente_local.get("/")

    # Assert
    assert 'window.CARVAJAL_API = "http://127.0.0.1:8000";' in config.text
    assert (
        "connect-src 'self' http://127.0.0.1:8000" in respuesta.headers["content-security-policy"]
    )
    assert cliente_local.get("/healthz").json()["api"] == "http://127.0.0.1:8000"


def test_un_host_ajeno_no_se_interpola_en_la_csp() -> None:
    # Arrange: Host manipulado que no pertenece a la lista blanca local.
    cliente_ajeno = TestClient(app, base_url="http://localhost.evil.com")

    # Act
    config = cliente_ajeno.get("/config.js")
    respuesta = cliente_ajeno.get("/")

    # Assert
    assert f'window.CARVAJAL_API = "{API_BASE}";' in config.text
    assert f"connect-src 'self' {API_BASE}" in respuesta.headers["content-security-policy"]
    assert "evil.com" not in respuesta.headers["content-security-policy"]


def test_estaticos_no_se_marcan_como_no_store() -> None:
    # Arrange / Act
    respuesta = cliente.get("/static/app.js")

    # Assert
    assert respuesta.status_code == 200
    assert respuesta.headers.get("cache-control") == "no-cache"


def test_logo_institucional_se_sirve() -> None:
    # Arrange / Act
    respuesta = cliente.get("/static/logo.svg")

    # Assert
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("image/svg+xml")
    assert respuesta.text.lstrip().startswith("<svg")
