"""Pruebas de los endpoints JSON de lectura del M2 (corpus y documento).

No tocan red ni base de datos: la app recibe dobles en ``app.state.corpus`` y
``app.state.documents``. El HTML saneado se comprueba sobre el render real de
``apps.api.rendering``.
"""

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from apps.api.main import create_app
from carvajal.domain.entities import Document, DocumentVersion

CAPTURED_AT = datetime(2024, 5, 1, 10, 30, tzinfo=UTC)
CREATED_AT = datetime(2024, 6, 1, 12, 0, tzinfo=UTC)
CURRENT_HASH = "a1b2c3d4e5f6" + "0" * 52
VERSION_HASH = "b" * 64

DOCUMENT = Document(
    id="doc-1",
    url="https://www.carvajal.com/historia/",
    title="Historia de Carvajal",
    slug="historia",
    section="historia",
    layer="core",
    markdown="# Trayectoria\n\nTexto <script>alert(1)</script> y [enlace](javascript:alert(1)).",
    content_hash=CURRENT_HASH,
    captured_at=CAPTURED_AT,
    source_type="oficial",
)

VERSION = DocumentVersion(
    id="ver-1",
    document_id="doc-1",
    markdown="## Historia anterior\n\nContenido archivado.",
    content_hash=VERSION_HASH,
    captured_at=datetime(2023, 1, 2, 3, 4, tzinfo=UTC),
    created_at=CREATED_AT,
)

UNSAFE_DOCUMENT = Document(
    id="doc-inseguro",
    url="javascript:alert(1)",
    title="Documento con URL insegura",
    slug="inseguro",
    section="historia",
    layer="core",
    markdown="Texto seguro.",
    content_hash="c" * 64,
    captured_at=CAPTURED_AT,
    source_type="tercero",
)


def make_document(
    index: int,
    *,
    section: str = "historia",
    layer: str = "core",
    source_type: str = "oficial",
    title: str | None = None,
) -> Document:
    """Documento de ejemplo con slug ordenable y URL con dominio."""

    slug = f"doc-{index:03d}"
    return Document(
        id=f"id-{index:03d}",
        url=f"https://www.carvajal.com/{section}/{slug}/",
        title=title or f"Documento {index}",
        slug=slug,
        section=section,
        layer=layer,
        markdown=f"# Documento {index}",
        content_hash=f"{index:064d}",
        captured_at=CAPTURED_AT,
        source_type=source_type,
    )


class FakeCorpus:
    """Doble en memoria con la misma semántica de filtros que el proveedor real."""

    def __init__(
        self,
        documents: Iterable[Document],
        sections: Iterable[str] | None = None,
        source_types: Iterable[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.documents = sorted(documents, key=lambda document: document.slug)
        self.calls: list[dict[str, Any]] = []
        self.count_calls: list[dict[str, Any]] = []
        self._sections = (
            list(sections)
            if sections is not None
            else sorted({document.section for document in self.documents})
        )
        self._source_types = (
            list(source_types)
            if source_types is not None
            else sorted({document.source_type for document in self.documents})
        )
        self.error = error

    def _filtered(
        self,
        *,
        layer: str | None,
        section: str | None,
        source_type: str | None,
        q: str | None,
    ) -> list[Document]:
        if self.error is not None:
            raise self.error
        needle = (q or "").casefold()
        return [
            document
            for document in self.documents
            if (layer is None or document.layer == layer)
            and (section is None or document.section == section)
            and (source_type is None or document.source_type == source_type)
            and (
                not needle
                or needle
                in " ".join(
                    (document.title, document.section, document.slug, document.url)
                ).casefold()
            )
        ]

    def list(
        self,
        *,
        layer: str | None = None,
        section: str | None = None,
        source_type: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Document]:
        self.calls.append(
            {
                "layer": layer,
                "section": section,
                "source_type": source_type,
                "q": q,
                "limit": limit,
                "offset": offset,
            }
        )
        return self._filtered(layer=layer, section=section, source_type=source_type, q=q)[
            offset : offset + limit
        ]

    def count(
        self,
        *,
        layer: str | None = None,
        section: str | None = None,
        source_type: str | None = None,
        q: str | None = None,
    ) -> int:
        self.count_calls.append(
            {"layer": layer, "section": section, "source_type": source_type, "q": q}
        )
        return len(self._filtered(layer=layer, section=section, source_type=source_type, q=q))

    def sections(self) -> Sequence[str]:
        if self.error is not None:
            raise self.error
        return self._sections

    def source_types(self) -> Sequence[str]:
        if self.error is not None:
            raise self.error
        return self._source_types


class FakeDocuments:
    """Doble en memoria del repositorio de documentos, con búsqueda por slug."""

    def __init__(
        self,
        documents: Iterable[Document] | None = None,
        versions: dict[str, list[DocumentVersion]] | None = None,
        error: Exception | None = None,
    ) -> None:
        rows = list(documents) if documents is not None else [DOCUMENT]
        self._by_id = {document.id: document for document in rows}
        self._by_slug = {document.slug: document for document in rows}
        self._versions = versions or {}
        self.error = error

    def get(self, document_id: str) -> Document | None:
        if self.error is not None:
            raise self.error
        return self._by_id.get(document_id)

    def get_by_slug(self, slug: str) -> Document | None:
        if self.error is not None:
            raise self.error
        return self._by_slug.get(slug)

    def list_versions(self, document_id: str) -> Sequence[DocumentVersion]:
        return self._versions.get(document_id, [])


def make_client(
    *,
    corpus: FakeCorpus | None = None,
    documents: FakeDocuments | None = None,
) -> TestClient:
    """Inyecta los dobles después de crear la app."""

    app = create_app()
    if corpus is not None:
        app.state.corpus = corpus
    if documents is not None:
        app.state.documents = documents
    return TestClient(app)


def test_corpus_pagina_y_devuelve_facetas() -> None:
    # Arrange
    documents = [make_document(index) for index in range(30)]
    corpus = FakeCorpus(documents)
    client = make_client(corpus=corpus)

    # Act
    response = client.get("/api/corpus", params={"page": "2", "page_size": "10"})

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 30
    assert data["page"] == 2
    assert data["page_size"] == 10
    assert data["has_next"] is True
    assert [document["slug"] for document in data["documents"]] == [
        f"doc-{index:03d}" for index in range(10, 20)
    ]
    assert data["sections"] == ["historia"]
    assert data["source_types"] == ["oficial"]
    assert corpus.count_calls == [{"layer": None, "section": None, "source_type": None, "q": None}]
    assert corpus.calls == [
        {
            "layer": None,
            "section": None,
            "source_type": None,
            "q": None,
            "limit": 10,
            "offset": 10,
        }
    ]
    first = data["documents"][0]
    assert set(first) == {
        "id",
        "url",
        "title",
        "slug",
        "section",
        "layer",
        "source_type",
        "captured_at",
    }
    assert first["captured_at"].startswith("2024-05-01T10:30:00")


def test_corpus_filtra_sobre_el_total_antes_de_paginar() -> None:
    # Arrange: 7 del núcleo oficial, 5 externos de prensa.
    documents = [make_document(index) for index in range(7)]
    documents += [
        make_document(index, section="prensa", layer="extended", source_type="tercero")
        for index in range(100, 105)
    ]
    corpus = FakeCorpus(documents)
    client = make_client(corpus=corpus)

    # Act
    response = client.get(
        "/api/corpus",
        params={
            "section": "historia",
            "layer": "core",
            "source_type": "oficial",
            "page_size": "3",
        },
    )

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 7
    assert len(data["documents"]) == 3
    assert data["has_next"] is True
    assert all(document["section"] == "historia" for document in data["documents"])
    assert corpus.count_calls[0] == {
        "layer": "core",
        "section": "historia",
        "source_type": "oficial",
        "q": None,
    }
    assert corpus.calls[0]["limit"] == 3
    assert corpus.calls[0]["offset"] == 0

    # Act: última página del conjunto filtrado.
    last_page = client.get(
        "/api/corpus",
        params={
            "section": "historia",
            "layer": "core",
            "source_type": "oficial",
            "page_size": "3",
            "page": "3",
        },
    )

    # Assert
    assert last_page.status_code == 200
    last = last_page.json()
    assert last["total"] == 7
    assert len(last["documents"]) == 1
    assert last["has_next"] is False


def test_corpus_busqueda_insensible_recorta_y_pasa_el_texto_literal() -> None:
    # Arrange
    documents = [
        make_document(0, title="Informe 100% Real"),
        make_document(1, title="Informe 100X Real"),
        make_document(2, section="Historia", title="Otra página"),
    ]
    corpus = FakeCorpus(documents)
    client = make_client(corpus=corpus)

    # Act: el guion bajo y el porcentaje no deben tratarse como comodines.
    response = client.get("/api/corpus", params={"q": "  100%  "})

    # Assert
    assert response.status_code == 200
    assert [document["slug"] for document in response.json()["documents"]] == ["doc-000"]
    assert corpus.calls[0]["q"] == "100%"

    # Act: el texto se busca sin distinguir mayúsculas.
    section = client.get("/api/corpus", params={"q": "PÁGINA"})

    # Assert
    assert section.status_code == 200
    assert section.json()["total"] == 1
    assert section.json()["documents"][0]["slug"] == "doc-002"

    # Act: la búsqueda se limita a 200 caracteres.
    larga = client.get("/api/corpus", params={"q": "a" * 250})

    # Assert
    assert larga.status_code == 200
    assert corpus.calls[-1]["q"] == "a" * 200


def test_corpus_ignora_filtros_desconocidos_y_normaliza_parametros() -> None:
    # Arrange
    corpus = FakeCorpus([make_document(0)])
    client = make_client(corpus=corpus)

    # Act
    response = client.get(
        "/api/corpus",
        params={
            "section": "no-existe",
            "source_type": "otro",
            "layer": "otra",
            "page": "0",
            "page_size": "999",
        },
    )

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 1
    assert data["page_size"] == 24
    assert len(data["documents"]) == 1
    assert corpus.calls[0] == {
        "layer": None,
        "section": None,
        "source_type": None,
        "q": None,
        "limit": 24,
        "offset": 0,
    }


def test_corpus_con_base_caida_devuelve_503_sin_detalles() -> None:
    # Arrange
    error = SQLAlchemyError("password=secreto host=interno")
    client = make_client(corpus=FakeCorpus([], error=error))

    # Act
    response = client.get("/api/corpus")

    # Assert
    assert response.status_code == 503
    assert response.json() == {"detail": "La base de datos no responde."}
    assert "secreto" not in response.text
    assert "interno" not in response.text


def test_documento_devuelve_html_saneado_y_ficha() -> None:
    # Arrange
    documents = FakeDocuments(versions={DOCUMENT.id: [VERSION]})
    client = make_client(documents=documents)

    # Act
    response = client.get("/api/documents/doc-1")

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {
        "document",
        "document_url",
        "content_html",
        "content_hash",
        "captured_display",
        "captured_iso",
        "version",
        "versions",
    }
    assert "markdown" not in data
    assert data["document"]["id"] == "doc-1"
    assert data["document"]["source_type"] == "oficial"
    assert data["document_url"] == "https://www.carvajal.com/historia/"
    assert data["content_html"].startswith("<h1>Trayectoria</h1>")
    assert "<script" not in data["content_html"]
    assert "javascript:" not in data["content_html"]
    assert "alert(1)" not in data["content_html"]
    assert data["content_hash"] == CURRENT_HASH[:12]
    assert CURRENT_HASH not in response.text
    assert data["captured_display"] == "2024-05-01 10:30 UTC"
    assert data["captured_iso"] == "2024-05-01T10:30:00+00:00"
    assert data["version"] is None
    assert data["versions"] == [
        {
            "id": "ver-1",
            "captured_at": "2023-01-02T03:04:00+00:00",
            "created_at": "2024-06-01T12:00:00+00:00",
            "content_hash": VERSION_HASH[:12],
        }
    ]


def test_documento_version_archivada_cambia_contenido_y_huella() -> None:
    # Arrange
    client = make_client(documents=FakeDocuments(versions={DOCUMENT.id: [VERSION]}))

    # Act
    response = client.get("/api/documents/doc-1", params={"version": "ver-1"})

    # Assert
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "ver-1"
    assert "Contenido archivado." in data["content_html"]
    assert "Trayectoria" not in data["content_html"]
    assert data["content_hash"] == VERSION_HASH[:12]
    assert data["captured_display"] == "2023-01-02 03:04 UTC"


def test_version_ajena_devuelve_404() -> None:
    # Arrange: el doble entrega bajo el documento una versión de otro documento.
    ajena = DocumentVersion(
        id="ver-2",
        document_id="doc-2",
        markdown="Contenido de otro documento.",
        content_hash="d" * 64,
        captured_at=CAPTURED_AT,
        created_at=CREATED_AT,
    )
    client = make_client(documents=FakeDocuments(versions={DOCUMENT.id: [ajena]}))

    # Act
    response = client.get("/api/documents/doc-1", params={"version": "ver-2"})

    # Assert
    assert response.status_code == 404
    assert "Contenido de otro documento." not in response.text
    assert response.json()["detail"]


def test_documento_inexistente_devuelve_404() -> None:
    # Arrange
    client = make_client(documents=FakeDocuments())

    # Act
    response = client.get("/api/documents/no-existe")

    # Assert
    assert response.status_code == 404
    assert response.json()["detail"] == "El documento solicitado no existe."


def test_documento_por_slug_resuelve_la_cita() -> None:
    # Arrange
    client = make_client(documents=FakeDocuments())

    # Act
    response = client.get("/api/documents/by-slug/historia")

    # Assert
    assert response.status_code == 200
    assert response.json()["document"]["id"] == "doc-1"
    assert response.json()["document"]["slug"] == "historia"


def test_slug_inexistente_devuelve_404() -> None:
    # Arrange
    client = make_client(documents=FakeDocuments())

    # Act
    response = client.get("/api/documents/by-slug/no-existe")

    # Assert
    assert response.status_code == 404
    assert response.json()["detail"] == "El documento solicitado no existe."


def test_url_insegura_no_se_expone_como_enlace() -> None:
    # Arrange
    client = make_client(documents=FakeDocuments([UNSAFE_DOCUMENT]))

    # Act
    response = client.get("/api/documents/doc-inseguro")

    # Assert
    assert response.status_code == 200
    assert response.json()["document_url"] is None
    assert response.json()["document"]["url"] == ""


def test_documento_con_base_caida_devuelve_503_sin_detalles() -> None:
    # Arrange
    error = SQLAlchemyError("password=secreto")
    client = make_client(documents=FakeDocuments(error=error))

    # Act
    response = client.get("/api/documents/doc-1")

    # Assert
    assert response.status_code == 503
    assert response.json() == {"detail": "La base de datos no responde."}
    assert "secreto" not in response.text
