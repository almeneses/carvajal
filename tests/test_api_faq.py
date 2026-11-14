"""Pruebas HTTP del generador de FAQ: SSE, CSRF, cupo y deduplicación."""

import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from apps.api.chat.identity import COOKIE_NAME, SignedCookieIdentity
from apps.api.main import create_app
from apps.api.security import CSRF_COOKIE, CSRF_FIELD
from carvajal.application.errors import LLMError
from carvajal.application.quota import QuotaResult
from carvajal.domain.entities import Document

IDENTITY = SignedCookieIdentity(b"secreto-faq")


def _document(slug: str, body: str, *, layer: str = "core") -> Document:
    """Documento mínimo para el corpus falso."""

    return Document(
        id=f"id-{slug}",
        url=f"https://carvajal.com/{slug}",
        title=slug.title(),
        slug=slug,
        section="empresa",
        layer=layer,
        markdown=f"---\ntitle: {slug}\n---\n{body}",
        content_hash="hash",
        captured_at=datetime(2026, 1, 1, tzinfo=UTC),
        source_type="oficial",
    )


@dataclass
class FakeCorpus:
    """Corpus en memoria para no tocar PostgreSQL."""

    docs: Sequence[Document]

    def documents(self) -> Sequence[Document]:
        return self.docs


@dataclass
class FakeLLM:
    """Proveedor de LLM falso: emite fragmentos o lanza un error."""

    chunks: tuple[str, ...] = ()
    text: str = ""
    error: Exception | None = None
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, *, system: str, prompt: str) -> str:
        self.calls.append((system, prompt))
        if self.error is not None:
            raise self.error
        return self.text

    def stream(self, *, system: str, prompt: str) -> Iterator[str]:
        self.calls.append((system, prompt))
        if self.error is not None:
            raise self.error
        yield from self.chunks


def make_client(
    llm: FakeLLM | None = None,
    corpus: FakeCorpus | None = None,
    quota: Callable[[str], QuotaResult] | None = None,
) -> tuple[TestClient, FakeLLM]:
    """Cliente con corpus, LLM, identidad y cuota inyectados."""

    provider = llm or FakeLLM(
        chunks=(
            "## Empresa\n",
            "**P:** ¿Qué es Carvajal?\n",
            "**R:** Una empresa colombiana.\n",
            "_Fuente:_ historia\n",
        )
    )
    app = create_app()
    app.state.faq_corpus = corpus or FakeCorpus([_document("historia", "Nació en Cali.")])
    app.state.faq_llm = provider
    app.state.chat_identity = IDENTITY
    app.state.quota = quota or (lambda owner: QuotaResult(allowed=True))
    return TestClient(app), provider


def _data(client: TestClient, exclude: str = "[]") -> dict[str, str]:
    """Datos del POST con el token CSRF emitido por la portada."""

    if CSRF_COOKIE not in client.cookies:
        client.get("/")
    return {CSRF_FIELD: client.cookies[CSRF_COOKIE], "exclude": exclude}


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Extrae ``(evento, datos)`` de un cuerpo SSE terminado en línea en blanco."""

    events: list[tuple[str, dict[str, object]]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        name = "message"
        data: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data.append(line.removeprefix("data:").lstrip())
        events.append((name, json.loads("\n".join(data))))
    return events


def test_faq_stream_emite_open_tokens_y_done() -> None:
    # Arrange
    client, _ = make_client()

    # Act
    response = client.post("/api/faq", data=_data(client))

    # Assert
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    assert [name for name, _ in events] == ["open", "token", "token", "token", "token", "done"]
    assert events[0][1] == {"stream": "faq"}
    assert events[-1][1]["entries"] == [
        {
            "theme": "Empresa",
            "question": "¿Qué es Carvajal?",
            "answer": "Una empresa colombiana.",
            "source": "historia",
        }
    ]
    assert events[-1][1]["truncated"] is False


def test_faq_limita_el_lote_a_diez_preguntas() -> None:
    # Arrange
    entries = "".join(
        f"**P:** ¿Pregunta {numero}?\n**R:** Respuesta.\n_Fuente:_ historia\n\n"
        for numero in range(1, 13)
    )
    client, _ = make_client(FakeLLM(chunks=("## Empresa\n" + entries,)))

    # Act
    response = client.post("/api/faq", data=_data(client))

    # Assert
    done = parse_sse(response.text)[-1][1]
    assert len(done["entries"]) == 10


def test_faq_excluye_preguntas_ya_mostradas() -> None:
    # Arrange
    text = (
        "## Empresa\n"
        "**P:** ¿Qué es Carvajal?\n"
        "**R:** Repetida.\n"
        "_Fuente:_ historia\n\n"
        "**P:** ¿Dónde nació?\n"
        "**R:** En Cali.\n"
        "_Fuente:_ historia\n"
    )
    client, llm = make_client(FakeLLM(chunks=(text,)))

    # Act
    response = client.post(
        "/api/faq",
        data=_data(client, json.dumps(["¿Qué es Carvajal?"])),
    )

    # Assert
    done = parse_sse(response.text)[-1][1]
    assert [entry["question"] for entry in done["entries"]] == ["¿Dónde nació?"]
    assert "¿Qué es Carvajal?" in llm.calls[0][1]


def test_faq_rechaza_csrf_invalido_sin_llamar_al_modelo() -> None:
    # Arrange
    client, llm = make_client()

    # Act
    response = client.post(
        "/api/faq",
        data={CSRF_FIELD: "token-ajeno", "exclude": "[]"},
    )

    # Assert
    assert response.status_code == 403
    assert parse_sse(response.text)[0][0] == "error"
    assert llm.calls == []


def test_faq_respeta_el_cupo_diario() -> None:
    # Arrange
    client, llm = make_client(quota=lambda owner: QuotaResult(allowed=False, retry_after=42))

    # Act
    response = client.post("/api/faq", data=_data(client))

    # Assert
    assert response.status_code == 429
    assert response.headers["retry-after"] == "42"
    assert parse_sse(response.text)[0][0] == "error"
    assert llm.calls == []


def test_faq_emite_cookie_si_el_visitante_no_tenia_identidad() -> None:
    # Arrange
    client, _ = make_client()

    # Act
    response = client.post("/api/faq", data=_data(client))

    # Assert
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    owner_key = IDENTITY.verify(cookie[COOKIE_NAME].value)
    assert owner_key is not None


def test_faq_traduce_error_del_llm_a_evento_error() -> None:
    # Arrange
    client, _ = make_client(
        FakeLLM(error=LLMError("El proveedor falló.")),
    )

    # Act
    response = client.post("/api/faq", data=_data(client))

    # Assert
    assert response.status_code == 200
    events = parse_sse(response.text)
    assert events[0][0] == "open"
    assert events[-1][0] == "error"
    assert events[-1][1]["incomplete"] is False


def test_faq_responde_422_si_el_corpus_no_tiene_nucleo() -> None:
    # Arrange
    corpus = FakeCorpus([_document("prensa", "Terceros.", layer="extended")])
    client, _ = make_client(corpus=corpus)

    # Act
    response = client.post("/api/faq", data=_data(client))

    # Assert
    assert response.status_code == 422
    assert "capa núcleo" in str(parse_sse(response.text)[0][1]["message"])


def test_faq_responde_422_si_exclude_no_es_json() -> None:
    # Arrange
    client, _ = make_client()

    # Act
    response = client.post("/api/faq", data=_data(client, "no-es-json"))

    # Assert
    assert response.status_code == 422
    assert parse_sse(response.text)[0][0] == "error"


def test_faq_responde_503_si_la_cuota_no_puede_consultarse() -> None:
    # Arrange
    def quota(owner: str) -> QuotaResult:
        raise SQLAlchemyError("sin base")

    client, _ = make_client(quota=quota)

    # Act
    response = client.post("/api/faq", data=_data(client))

    # Assert
    assert response.status_code == 503
    assert parse_sse(response.text)[0][0] == "error"
