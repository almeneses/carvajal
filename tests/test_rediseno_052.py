"""Rediseño 052: modo de voz del chat, Corpus legible y consola de Configuración."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.test_api_corpus import FakeCorpus, make_document

from apps.api import runtime
from apps.api.main import create_app

TEMPLATES = Path(__file__).resolve().parents[1] / "apps" / "api" / "templates"
OPERATOR_TOKEN = "token-de-prueba-052"


def test_la_escena_de_voz_es_decorativa_y_cubre_cada_estado() -> None:
    # Arrange / Act
    chat = (TEMPLATES / "chat.html").read_text(encoding="utf-8")

    # Assert: el lector de pantalla oye data-voz-estado, no la escena ni el chip.
    assert '<div class="voz-escena" aria-hidden="true">' in chat
    assert '<p class="voz-chip" aria-hidden="true">' in chat
    for estado in ("escucha", "transcribe", "responde", "manos"):
        assert chat.count(f'data-escena="{estado}"') == 2  # título y guía
    assert chat.index("voz-escena") < chat.index("data-chat-stream")


def test_la_marca_por_voz_del_turno_nace_oculta() -> None:
    # Arrange / Act
    plantilla = (TEMPLATES / "_chat_stream.html").read_text(encoding="utf-8")

    # Assert: chat-stream.js la muestra solo si chat-voice.js marcó el envío como dictado.
    assert '<span class="turno-por-voz" data-turno-por-voz hidden>por voz</span>' in plantilla


def test_corpus_muestra_secciones_legibles_y_filtra_por_slug() -> None:
    # Arrange
    corpus = FakeCorpus(
        [make_document(1, section="canal-clientes"), make_document(2, section="historia")]
    )
    app = create_app()
    app.state.corpus = corpus
    client = TestClient(app)

    # Act
    response = client.get("/corpus", params={"section": "canal-clientes", "layer": "core"})

    # Assert
    assert response.status_code == 200
    assert '<option value="canal-clientes" selected>Canal clientes</option>' in response.text
    assert '<span class="doc-seccion">Canal clientes</span>' in response.text
    assert '<li class="chip-filtro">Sección: Canal clientes</li>' in response.text
    assert '<li class="chip-filtro">Capa: Núcleo</li>' in response.text
    assert "<strong>1</strong>\n      documento en la página 1" in response.text
    assert corpus.calls[-1]["section"] == "canal-clientes"


def test_corpus_sin_filtros_no_muestra_chips() -> None:
    # Arrange
    app = create_app()
    app.state.corpus = FakeCorpus(make_document(index) for index in range(3))
    client = TestClient(app)

    # Act
    response = client.get("/corpus")

    # Assert
    assert "chip-filtro" not in response.text
    assert "documentos en la página 1" in response.text


def test_la_consola_del_operador_explica_el_flujo_sin_aviso_de_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setenv("OPERATOR_TOKEN", OPERATOR_TOKEN)
    monkeypatch.delenv("CONFIG_TOKEN", raising=False)
    client = TestClient(create_app())

    # Act
    response = client.get("/configuracion", headers={"X-Operator-Token": OPERATOR_TOKEN})

    # Assert
    assert response.status_code == 200
    assert 'class="config-flujo"' in response.text
    assert response.text.count('class="config-paso ') == 3
    assert "Sesión de operador activa" in response.text
    assert 'class="boton secundario">Cerrar sesión de operador' in response.text
    assert '<p class="aviso">' not in response.text


@pytest.mark.parametrize(
    ("slug", "esperado"),
    [
        pytest.param("canal-clientes", "Canal clientes", id="guiones"),
        pytest.param("informe_ESG", "Informe ESG", id="sigla-intacta"),
        pytest.param("historia-de-Carvajal", "Historia de Carvajal", id="nombre-propio"),
        pytest.param("120", "120", id="numero"),
        pytest.param(None, "", id="vacio"),
    ],
)
def test_legible_solo_sube_la_primera_letra(slug: object, esperado: str) -> None:
    # Arrange / Act
    texto = runtime._readable_label(slug)

    # Assert
    assert texto == esperado
