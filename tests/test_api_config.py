"""Pruebas de la pestaña de configuración y la carga de documentos."""

import pytest
from fastapi.testclient import TestClient

from apps.api.ingestion import ALLOWED_EXTENSIONS, IngestReport, UploadedDocument
from apps.api.main import create_app
from apps.api.security import (
    CSRF_COOKIE,
    CSRF_FIELD,
    OPERATOR_COOKIE,
    issue_operator_cookie,
)
from carvajal.infrastructure.settings import Settings

OPERATOR_TOKEN = "token-de-prueba"


@pytest.fixture(autouse=True)
def _operator_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Habilita la escritura web con un token conocido en estas pruebas."""

    monkeypatch.setenv("OPERATOR_TOKEN", OPERATOR_TOKEN)
    monkeypatch.delenv("CONFIG_TOKEN", raising=False)


def make_client() -> TestClient:
    """Cliente de la app con la carga real desactivada por defecto."""

    app = create_app()
    app.state.ingest = lambda documents, **kwargs: IngestReport(files=len(documents))
    return TestClient(app)


def test_configuracion_muestra_el_formulario_y_las_etapas() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get(
        "/configuracion",
        headers={"X-Operator-Token": OPERATOR_TOKEN},
    )

    # Assert
    assert response.status_code == 200
    assert 'enctype="multipart/form-data"' in response.text
    assert 'name="archivos"' in response.text
    assert 'name="carpeta"' in response.text
    assert "Bronze" in response.text and "Qdrant" in response.text
    assert "Configuración" in response.text
    assert 'href="/configuracion"' not in response.text
    assert 'name="token_admin"' not in response.text
    assert "carvajal sync --layer core" in response.text


def test_subir_documento_pasa_los_archivos_al_ingestor() -> None:
    # Arrange
    recibidos: list[UploadedDocument] = []

    def ingestor(documents: list[UploadedDocument], **kwargs: object) -> IngestReport:
        recibidos.extend(documents)
        return IngestReport(
            files=len(documents),
            bronze=1,
            silver=1,
            gold=1,
            documents=12,
            chunks=34,
            indexed=56,
        )

    app = create_app()
    app.state.ingest = ingestor
    client = TestClient(app)
    client.get("/configuracion")
    token = client.cookies[CSRF_COOKIE]

    # Act
    response = client.post(
        "/configuracion",
        data={CSRF_FIELD: token, "token_admin": OPERATOR_TOKEN},
        files={"archivos": ("nota.md", b"# Nota\n\nTexto de prueba.", "text/markdown")},
    )

    # Assert
    assert response.status_code == 200
    assert recibidos and recibidos[0].filename == "nota.md"
    assert "Documentos en Gold para revisión" in response.text
    assert "carvajal sync --layer core" in response.text
    assert "56" in response.text


def test_subir_sin_archivos_devuelve_422() -> None:
    # Arrange
    client = make_client()
    client.get("/configuracion")
    token = client.cookies[CSRF_COOKIE]

    # Act
    response = client.post(
        "/configuracion", data={CSRF_FIELD: token, "token_admin": OPERATOR_TOKEN}
    )

    # Assert
    assert response.status_code == 422
    assert "Selecciona al menos un documento" in response.text


def test_subir_sin_csrf_devuelve_403() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.post(
        "/configuracion",
        data={CSRF_FIELD: "ajeno"},
        files={"archivos": ("nota.md", b"# Nota", "text/markdown")},
    )

    # Assert
    assert response.status_code == 403
    assert "La sesión del formulario expiró" in response.text


def test_subir_desde_una_ip_remota_sin_token_no_ejecuta_ingesta() -> None:
    # Arrange
    llamadas: list[list[UploadedDocument]] = []
    app = create_app()
    app.state.ingest = lambda documents, **kwargs: (
        llamadas.append(list(documents)) or IngestReport(files=len(documents))
    )
    client = TestClient(app, client=("203.0.113.10", 12345))
    client.get("/configuracion")
    token = client.cookies[CSRF_COOKIE]

    # Act
    response = client.post(
        "/configuracion",
        data={CSRF_FIELD: token},
        files={"archivos": ("nota.md", b"# Nota\n\nTexto.", "text/markdown")},
    )

    # Assert
    assert response.status_code == 403
    assert "token de operador" in response.text
    assert llamadas == []


def test_subir_desde_una_ip_remota_con_token_funciona() -> None:
    # Arrange
    app = create_app()
    app.state.ingest = lambda documents, **kwargs: IngestReport(
        files=len(documents),
        bronze=1,
        silver=1,
        gold=1,
        documents=1,
        chunks=1,
        indexed=1,
    )
    client = TestClient(app, client=("203.0.113.10", 12345))
    client.get("/configuracion")
    token = client.cookies[CSRF_COOKIE]

    # Act
    response = client.post(
        "/configuracion",
        data={CSRF_FIELD: token, "token_admin": OPERATOR_TOKEN},
        files={"archivos": ("nota.md", b"# Nota\n\nTexto.", "text/markdown")},
    )

    # Assert
    assert response.status_code == 200
    assert "Documentos en Gold para revisión" in response.text


def test_configuracion_anuncia_los_mismos_formatos_que_acepta_el_backend() -> None:
    # Arrange
    client = make_client()
    aceptados = ",".join(sorted(ALLOWED_EXTENSIONS))

    # Act
    response = client.get(
        "/configuracion",
        headers={"X-Operator-Token": OPERATOR_TOKEN},
    )

    # Assert
    assert response.text.count(aceptados) == 2


def test_subir_un_json_llega_al_ingestor() -> None:
    # Arrange
    recibidos: list[UploadedDocument] = []

    def ingestor(documents: list[UploadedDocument], **kwargs: object) -> IngestReport:
        recibidos.extend(documents)
        return IngestReport(files=len(documents), gold=1, documents=1, chunks=1, indexed=1)

    app = create_app()
    app.state.ingest = ingestor
    client = TestClient(app)
    client.get("/configuracion")
    token = client.cookies[CSRF_COOKIE]

    # Act
    response = client.post(
        "/configuracion",
        data={CSRF_FIELD: token, "token_admin": OPERATOR_TOKEN},
        files={"archivos": ("datos.json", b'{"a": 1}', "application/json")},
    )

    # Assert
    assert response.status_code == 200
    assert recibidos and recibidos[0].filename == "datos.json"


def test_configuracion_pide_token_y_no_filtra_su_valor() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/configuracion")

    # Assert
    assert response.status_code == 200
    assert response.text.count('name="token_admin"') == 1
    assert 'action="/configuracion/acceso"' in response.text
    assert 'type="password"' in response.text
    assert 'name="archivos"' not in response.text
    assert 'name="urls"' not in response.text
    assert OPERATOR_TOKEN not in response.text


def test_acceso_con_token_abre_la_consola_y_emite_cookie() -> None:
    # Arrange
    client = make_client()
    client.get("/configuracion")
    token = client.cookies[CSRF_COOKIE]

    # Act
    response = client.post(
        "/configuracion/acceso",
        data={CSRF_FIELD: token, "token_admin": OPERATOR_TOKEN},
        follow_redirects=False,
    )
    consola = client.get("/configuracion")

    # Assert
    assert response.status_code == 303
    assert response.headers["location"] == "/configuracion"
    assert "carvajal_operator=" in response.headers["set-cookie"]
    assert consola.status_code == 200
    assert 'name="archivos"' in consola.text
    assert 'name="token_admin"' not in consola.text


def test_operador_por_cabecera_abre_la_pagina_sin_pedir_token() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get(
        "/configuracion",
        headers={"X-Operator-Token": OPERATOR_TOKEN},
    )

    # Assert
    assert response.status_code == 200
    assert 'name="token_admin"' not in response.text
    assert 'name="archivos"' in response.text


def test_operador_valido_emite_cookie_y_oculta_el_campo() -> None:
    # Arrange
    app = create_app()
    app.state.ingest = lambda documents, **kwargs: IngestReport(files=len(documents))
    client = TestClient(app)
    client.get("/configuracion")
    token = client.cookies[CSRF_COOKIE]

    # Act
    response = client.post(
        "/configuracion",
        data={CSRF_FIELD: token, "token_admin": OPERATOR_TOKEN},
        files={"archivos": ("nota.md", b"# Nota\n\nTexto.", "text/markdown")},
    )
    segunda = client.get("/configuracion")

    # Assert
    cookie = response.headers["set-cookie"]
    assert "carvajal_operator=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Max-Age=28800" in cookie
    assert "Path=/" in cookie
    assert segunda.status_code == 200
    assert 'name="token_admin"' not in segunda.text


def test_operador_valido_con_csrf_invalido_no_ejecuta_ingesta() -> None:
    # Arrange
    llamadas: list[list[UploadedDocument]] = []

    def ingestor(documents: list[UploadedDocument], **kwargs: object) -> IngestReport:
        llamadas.append(list(documents))
        return IngestReport(files=len(documents))

    app = create_app()
    app.state.ingest = ingestor
    client = TestClient(app)
    client.get("/configuracion")

    # Act
    response = client.post(
        "/configuracion",
        data={CSRF_FIELD: "ajeno", "token_admin": OPERATOR_TOKEN},
        files={"archivos": ("nota.md", b"# Nota\n\nTexto.", "text/markdown")},
    )

    # Assert
    assert response.status_code == 403
    assert "La sesión del formulario expiró" in response.text
    assert llamadas == []


def test_cerrar_sesion_de_operador_borra_la_cookie() -> None:
    # Arrange
    client = make_client()
    client.cookies.set(OPERATOR_COOKIE, issue_operator_cookie())
    client.get("/configuracion")
    token = client.cookies[CSRF_COOKIE]

    # Act
    response = client.post(
        "/configuracion/salir",
        data={CSRF_FIELD: token},
        follow_redirects=False,
    )

    # Assert
    assert response.status_code == 303
    assert response.headers["location"] == "/chat"
    assert "carvajal_operator=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_el_hook_inyectado_tambien_recibe_solo_extraer() -> None:
    # Arrange
    capturado: dict[str, object] = {}

    def ingestor(documents: list[UploadedDocument], **kwargs: object) -> IngestReport:
        capturado.update(kwargs)
        return IngestReport(files=len(documents), gold=len(documents))

    app = create_app()
    app.state.ingest = ingestor
    client = TestClient(app)
    client.get("/configuracion")
    token = client.cookies[CSRF_COOKIE]

    # Act
    response = client.post(
        "/configuracion",
        data={CSRF_FIELD: token, "token_admin": OPERATOR_TOKEN},
        files={"archivos": ("nota.md", b"# Nota\n\nTexto.", "text/markdown")},
    )

    # Assert
    assert response.status_code == 200
    assert capturado["solo_extraer"] is True


def test_subir_sin_token_de_operador_no_ejecuta_ingesta() -> None:
    # Arrange
    llamadas: list[list[UploadedDocument]] = []

    def ingestor(documents: list[UploadedDocument], **kwargs: object) -> IngestReport:
        llamadas.append(list(documents))
        return IngestReport(files=len(documents))

    app = create_app()
    app.state.ingest = ingestor
    client = TestClient(app)
    client.get("/configuracion")
    token = client.cookies[CSRF_COOKIE]

    # Act
    response = client.post(
        "/configuracion",
        data={CSRF_FIELD: token},
        files={"archivos": ("nota.md", b"# Nota\n\nTexto.", "text/markdown")},
    )

    # Assert
    assert response.status_code == 403
    assert "token de operador" in response.text
    assert llamadas == []


def test_subir_con_token_incorrecto_no_ejecuta_ingesta() -> None:
    # Arrange
    llamadas: list[list[UploadedDocument]] = []

    def ingestor(documents: list[UploadedDocument], **kwargs: object) -> IngestReport:
        llamadas.append(list(documents))
        return IngestReport(files=len(documents))

    app = create_app()
    app.state.ingest = ingestor
    client = TestClient(app)
    client.get("/configuracion")
    token = client.cookies[CSRF_COOKIE]

    # Act
    response = client.post(
        "/configuracion",
        data={CSRF_FIELD: token, "token_admin": "otro-token"},
        files={"archivos": ("nota.md", b"# Nota\n\nTexto.", "text/markdown")},
    )

    # Assert
    assert response.status_code == 403
    assert llamadas == []


def test_configuracion_sin_operador_configurado_devuelve_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setattr(
        "apps.api.views.config.Settings",
        lambda: Settings(_env_file=None, operator_token=""),
    )
    client = make_client()
    client.get("/configuracion")
    token = client.cookies[CSRF_COOKIE]

    # Act
    response = client.get("/configuracion")
    carga = client.post(
        "/configuracion",
        data={CSRF_FIELD: token, "token_admin": "cualquiera"},
        files={"archivos": ("nota.md", b"# Nota\n\nTexto.", "text/markdown")},
    )

    # Assert
    assert response.status_code == 403
    assert "deshabilitadas" in response.text
    assert 'name="archivos"' not in response.text
    assert 'name="token_admin"' not in response.text
    assert "carvajal capture" in response.text
    assert carga.status_code == 403
    assert "deshabilitadas" in carga.text


def test_la_carga_web_solo_extrae_a_gold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    capturado: dict[str, object] = {}

    def ingestor(documents: list[UploadedDocument], **kwargs: object) -> IngestReport:
        capturado["documents"] = documents
        capturado.update(kwargs)
        return IngestReport(files=len(documents), gold=len(documents))

    monkeypatch.setattr("apps.api.views.config.ingest_uploads", ingestor)
    app = create_app()
    client = TestClient(app)
    client.get("/configuracion")
    token = client.cookies[CSRF_COOKIE]

    # Act
    response = client.post(
        "/configuracion",
        data={CSRF_FIELD: token, "token_admin": OPERATOR_TOKEN},
        files={"archivos": ("nota.md", b"# Nota\n\nTexto.", "text/markdown")},
    )

    # Assert
    assert response.status_code == 200
    assert capturado["solo_extraer"] is True
    assert capturado["documents"]
    assert "carvajal sync --layer core" in response.text
    assert "PostgreSQL" in response.text and "Qdrant" in response.text
