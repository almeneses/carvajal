"""Pruebas de la pantalla de corpus con un proveedor falso: sin red ni base de datos."""

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from html import unescape

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from carvajal.domain.entities import Document


def make_document(
    index: int,
    *,
    section: str = "historia",
    layer: str = "core",
    title: str | None = None,
) -> Document:
    """Construye un documento de ejemplo con slug ordenable."""

    slug = f"doc-{index:03d}"
    return Document(
        id=f"id-{index:03d}",
        url=f"https://example.com/{slug}",
        title=title or f"Documento {index}",
        slug=slug,
        section=section,
        layer=layer,
        markdown=f"# Documento {index}",
        content_hash=f"hash-{index:03d}",
        captured_at=datetime(2026, 11, 1, 12, 30, tzinfo=UTC),
    )


class FakeCorpus:
    """Doble en memoria del proveedor de corpus inyectado en ``app.state``."""

    def __init__(
        self,
        documents: Iterable[Document],
        sections: Iterable[str] | None = None,
    ) -> None:
        self.documents = sorted(documents, key=lambda document: document.slug)
        self.calls: list[dict[str, object]] = []
        self._sections = (
            list(sections)
            if sections is not None
            else sorted({document.section for document in self.documents})
        )

    def list(
        self,
        *,
        layer: str | None = None,
        section: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Document]:
        """Filtra y recorta como el repositorio real."""

        if limit < 0 or offset < 0:
            raise ValueError(f"limit y offset deben ser no negativos: {limit!r}, {offset!r}")
        self.calls.append({"layer": layer, "section": section, "limit": limit, "offset": offset})
        filtered = [
            document
            for document in self.documents
            if (layer is None or document.layer == layer)
            and (section is None or document.section == section)
        ]
        return filtered[offset : offset + limit]

    def sections(self) -> Sequence[str]:
        """Devuelve las secciones configuradas para el formulario."""

        return self._sections


def make_client(corpus: FakeCorpus) -> TestClient:
    """Crea la app y le inyecta el proveedor falso tras el ``create_app``."""

    app = create_app()
    app.state.corpus = corpus
    return TestClient(app)


def test_la_primera_pagina_muestra_50_documentos_y_enlaza_la_siguiente() -> None:
    # Arrange
    corpus = FakeCorpus(make_document(index) for index in range(51))
    client = make_client(corpus)

    # Act
    response = client.get("/corpus")

    # Assert
    assert response.status_code == 200
    assert response.text.count('href="/documents/') == 50
    assert corpus.calls == [{"layer": None, "section": None, "limit": 51, "offset": 0}]
    assert "Siguiente" in response.text and "page=2" in response.text
    assert "Anterior" not in response.text


def test_la_segunda_pagina_usa_offset_50_y_muestra_el_resto() -> None:
    # Arrange
    corpus = FakeCorpus(make_document(index) for index in range(51))
    client = make_client(corpus)

    # Act
    response = client.get("/corpus?page=2")

    # Assert
    assert response.status_code == 200
    assert corpus.calls == [{"layer": None, "section": None, "limit": 51, "offset": 50}]
    assert response.text.count('href="/documents/') == 1
    assert 'href="/documents/id-050"' in response.text
    assert "Anterior" in response.text and "page=1" in response.text
    assert "Siguiente" not in response.text


def test_los_filtros_se_combinan_y_la_paginacion_los_conserva() -> None:
    # Arrange: 55 documentos del núcleo y 5 de la capa extendida.
    documents = [make_document(index) for index in range(55)]
    documents += [
        make_document(index, section="prensa", layer="extended") for index in range(100, 105)
    ]
    corpus = FakeCorpus(documents)
    client = make_client(corpus)

    # Act
    response = client.get("/corpus?section=historia&layer=core")

    # Assert
    assert response.status_code == 200
    assert corpus.calls == [{"layer": "core", "section": "historia", "limit": 51, "offset": 0}]
    assert response.text.count('href="/documents/') == 50
    assert "Documento 100" not in response.text

    match = re.search(r'href="([^"]*page=2[^"]*)"', response.text)
    assert match is not None
    next_url = unescape(match.group(1))
    assert "section=historia" in next_url
    assert "layer=core" in next_url
    assert "page=2" in next_url


@pytest.mark.parametrize("page", ["0", "-3", "abc", "", "  "])
def test_una_pagina_invalida_se_normaliza_a_la_primera(page: str) -> None:
    # Arrange
    corpus = FakeCorpus([make_document(0)])
    client = make_client(corpus)

    # Act
    response = client.get("/corpus", params={"page": page})

    # Assert
    assert response.status_code == 200
    assert corpus.calls == [{"layer": None, "section": None, "limit": 51, "offset": 0}]


def test_una_capa_desconocida_se_ignora() -> None:
    # Arrange
    corpus = FakeCorpus([make_document(0)])
    client = make_client(corpus)

    # Act
    response = client.get("/corpus?layer=otra")

    # Assert
    assert response.status_code == 200
    assert corpus.calls == [{"layer": None, "section": None, "limit": 51, "offset": 0}]
    assert "Documento 0" in response.text


def test_sin_documentos_muestra_el_estado_vacio() -> None:
    # Arrange
    corpus = FakeCorpus([], sections=["historia"])
    client = make_client(corpus)

    # Act
    response = client.get("/corpus")

    # Assert
    assert response.status_code == 200
    assert "No hay documentos que coincidan con los filtros." in response.text
    assert 'href="/documents/' not in response.text
    assert "Siguiente" not in response.text and "Anterior" not in response.text


def test_el_titulo_del_documento_se_escapa() -> None:
    # Arrange
    peligro = "<script>alert(1)</script>"
    corpus = FakeCorpus([make_document(0, title=peligro)])
    client = make_client(corpus)

    # Act
    response = client.get("/corpus")

    # Assert
    assert response.status_code == 200
    assert peligro not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_las_opciones_de_seccion_vienen_del_proveedor() -> None:
    # Arrange
    corpus = FakeCorpus([make_document(0, section="prensa")], sections=["historia", "prensa"])
    client = make_client(corpus)

    # Act
    response = client.get("/corpus?section=prensa")

    # Assert
    assert response.status_code == 200
    assert '<option value="historia">historia</option>' in response.text
    assert '<option value="prensa" selected>prensa</option>' in response.text
    assert "Fecha" in response.text and "2026-11-01" in response.text
