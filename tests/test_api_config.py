"""Pruebas de la pestaña de configuración y la carga de documentos."""

from fastapi.testclient import TestClient

from apps.api.ingestion import IngestReport, UploadedDocument
from apps.api.main import create_app
from apps.api.security import CSRF_COOKIE, CSRF_FIELD


def make_client() -> TestClient:
    """Cliente de la app con la carga real desactivada por defecto."""

    app = create_app()
    app.state.ingest = lambda documents: IngestReport(files=len(documents))
    return TestClient(app)


def test_configuracion_muestra_el_formulario_y_las_etapas() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/configuracion")

    # Assert
    assert response.status_code == 200
    assert 'enctype="multipart/form-data"' in response.text
    assert 'name="archivos"' in response.text
    assert 'name="carpeta"' in response.text
    assert "Bronze" in response.text and "Qdrant" in response.text
    assert "Configuración" in response.text
    assert 'href="/configuracion"' in response.text
    assert response.text.count('aria-current="page"') == 1
    assert 'name="token_admin"' not in response.text


def test_subir_documento_pasa_los_archivos_al_ingestor() -> None:
    # Arrange
    recibidos: list[UploadedDocument] = []

    def ingestor(documents: list[UploadedDocument]) -> IngestReport:
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
        data={CSRF_FIELD: token},
        files={"archivos": ("nota.md", b"# Nota\n\nTexto de prueba.", "text/markdown")},
    )

    # Assert
    assert response.status_code == 200
    assert recibidos and recibidos[0].filename == "nota.md"
    assert "Corpus actualizado" in response.text
    assert "56" in response.text


def test_subir_sin_archivos_devuelve_422() -> None:
    # Arrange
    client = make_client()
    client.get("/configuracion")
    token = client.cookies[CSRF_COOKIE]

    # Act
    response = client.post("/configuracion", data={CSRF_FIELD: token})

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


def test_subir_desde_una_ip_remota_no_exige_token() -> None:
    # Arrange
    app = create_app()
    app.state.ingest = lambda documents: IngestReport(
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
        data={CSRF_FIELD: token},
        files={"archivos": ("nota.md", b"# Nota\n\nTexto.", "text/markdown")},
    )

    # Assert
    assert response.status_code == 200
    assert "Corpus actualizado" in response.text
