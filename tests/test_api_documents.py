"""Pruebas de la pantalla de documento con un proveedor falso.

No tocan red ni base de datos: ``app.state.documents`` recibe un doble en
memoria que cumple el contrato ``get`` / ``list_versions``.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from apps.api.main import create_app
from carvajal.domain.entities import Document, DocumentVersion

CAPTURED_AT = datetime(2024, 5, 1, 10, 30, tzinfo=UTC)
CURRENT_HASH = "a1b2c3d4e5f6" + "0" * 52

DOCUMENT = Document(
    id="doc-1",
    url="https://www.carvajal.com/historia/",
    title="Historia de Carvajal",
    slug="historia",
    section="historia",
    layer="core",
    markdown="## Trayectoria\n\nInició operaciones en 1904.",
    content_hash=CURRENT_HASH,
    captured_at=CAPTURED_AT,
    source_type="oficial",
)

VERSION = DocumentVersion(
    id="ver-1",
    document_id="doc-1",
    markdown="## Historia anterior\n\nContenido archivado.",
    content_hash="b" * 64,
    captured_at=datetime(2023, 1, 2, 3, 4, tzinfo=UTC),
    created_at=datetime(2024, 6, 1, 12, 0, tzinfo=UTC),
)


class FakeDocuments:
    """Doble en memoria del repositorio de documentos."""

    def __init__(
        self,
        documents: dict[str, Document] | None = None,
        versions: dict[str, list[DocumentVersion]] | None = None,
    ) -> None:
        self._documents = documents if documents is not None else {DOCUMENT.id: DOCUMENT}
        self._versions = versions or {}

    def get(self, document_id: str) -> Document | None:
        """Devuelve el documento del diccionario, si existe."""

        return self._documents.get(document_id)

    def list_versions(self, document_id: str) -> Sequence[DocumentVersion]:
        """Devuelve las versiones asociadas al documento."""

        return self._versions.get(document_id, [])


def make_client(documents: FakeDocuments) -> TestClient:
    """Cliente con la vista de documentos apuntando al doble."""

    app = create_app()
    app.state.documents = documents
    return TestClient(app)


def test_muestra_metadatos_y_contenido_actual() -> None:
    # Arrange
    client = make_client(FakeDocuments())

    # Act
    response = client.get("/documents/doc-1")

    # Assert
    assert response.status_code == 200
    assert '<h1 id="titulo-documento">Historia de Carvajal</h1>' in response.text
    assert "<dd>historia</dd>" in response.text
    assert "<dd>core</dd>" in response.text
    assert "<dd>oficial</dd>" in response.text
    assert "https://www.carvajal.com/historia/" in response.text
    assert '<time datetime="2024-05-01T10:30:00+00:00">' in response.text
    assert "2024-05-01 10:30 UTC" in response.text
    assert CURRENT_HASH[:12] in response.text
    assert CURRENT_HASH not in response.text
    assert "<h2>Trayectoria</h2>" in response.text
    assert "Inició operaciones en 1904." in response.text


def test_version_archivada_muestra_aviso_y_volver_a_la_actual() -> None:
    # Arrange
    client = make_client(FakeDocuments(versions={DOCUMENT.id: [VERSION]}))

    # Act
    response = client.get("/documents/doc-1?version=ver-1")

    # Assert
    assert response.status_code == 200
    assert "versión archivada" in response.text
    assert "Volver a la actual" in response.text
    assert 'href="/documents/doc-1"' in response.text
    assert "Contenido archivado." in response.text
    assert "Inició operaciones en 1904." not in response.text
    assert "2023-01-02 03:04 UTC" in response.text
    assert VERSION.content_hash[:12] in response.text
    assert "Versión anterior" in response.text
    assert 'href="?version=ver-1"' in response.text


def test_version_de_otro_documento_devuelve_404() -> None:
    # Arrange: la versión existe, pero pertenece a otro documento.
    ajena = DocumentVersion(
        id="ver-2",
        document_id="doc-2",
        markdown="Contenido de otro documento.",
        content_hash="c" * 64,
        captured_at=CAPTURED_AT,
        created_at=CAPTURED_AT,
    )
    client = make_client(FakeDocuments(versions={"doc-2": [ajena]}))

    # Act
    response = client.get("/documents/doc-1?version=ver-2")

    # Assert
    assert response.status_code == 404
    assert "Contenido de otro documento." not in response.text


def test_documento_inexistente_devuelve_404() -> None:
    # Arrange
    client = make_client(FakeDocuments())

    # Act
    response = client.get("/documents/no-existe")

    # Assert
    assert response.status_code == 404


def test_sin_versiones_no_muestra_enlace_anterior() -> None:
    # Arrange
    client = make_client(FakeDocuments())

    # Act
    response = client.get("/documents/doc-1")

    # Assert
    assert response.status_code == 200
    assert "Versión anterior" not in response.text
    assert "?version=" not in response.text


def test_con_versiones_enlaza_la_version_anterior() -> None:
    # Arrange
    client = make_client(FakeDocuments(versions={DOCUMENT.id: [VERSION]}))

    # Act
    response = client.get("/documents/doc-1")

    # Assert
    assert response.status_code == 200
    assert "Versión anterior" in response.text
    assert 'href="?version=ver-1"' in response.text


def test_el_titulo_se_escapa() -> None:
    # Arrange
    peligroso = Document(
        id="doc-peligroso",
        url="https://www.carvajal.com/peligroso/",
        title='<script>alert("título")</script>',
        slug="peligroso",
        section="historia",
        layer="core",
        markdown="Texto seguro.",
        content_hash=CURRENT_HASH,
        captured_at=CAPTURED_AT,
        source_type="oficial",
    )
    client = make_client(FakeDocuments({peligroso.id: peligroso}))

    # Act
    response = client.get("/documents/doc-peligroso")

    # Assert
    assert response.status_code == 200
    assert "&lt;script&gt;alert" in response.text
    assert "<script>alert" not in response.text
