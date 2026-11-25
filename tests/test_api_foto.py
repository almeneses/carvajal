"""``POST /chat/foto`` y la página del chat (spec 051, US1-US3 y US5).

El servicio de chat es el real sobre SQLite (turno guardado, reintentos); el clasificador es
un doble sin red. Cubren CSRF y límites antes de leer el cuerpo, el cupo cobrado solo si se
clasificó, la idempotencia sin volver a clasificar, el turno guardado con la referencia (para
cotizar después), las tarjetas, el botón y el aviso solo con clasificador, y ``/health``.
"""

import io
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.chat.identity import COOKIE_NAME, SignedCookieIdentity
from apps.api.chat.service import ChatService
from apps.api.main import Handlers, HealthStatus, create_app
from apps.api.security import CSRF_COOKIE
from apps.api.views.chat import LIMITE_ALCANZADO
from apps.api.views.foto import CSRF_HEADER
from carvajal.adapters.storage import tables
from carvajal.application.ports import JudgeCall, JudgeUnavailable
from carvajal.application.quota import QuotaResult
from carvajal.food.photo import PreparedPhoto
from carvajal.food.profile import FoodProfile
from carvajal.food.render import PHOTO_QUESTION, PHOTO_UNAVAILABLE
from carvajal.infrastructure.settings import Settings

IDENTITY = SignedCookieIdentity(b"secreto-de-prueba")
OWNER = "dueno-foto"
SOPA = FoodProfile(0.95, "sopas", 0.84, "plato_fuerte", "liquido", 0.83, "caliente", 1.0, 1.9, 0.85)


@dataclass
class FakeClassifier:
    """Doble del clasificador: anota cada foto y devuelve el perfil fijado."""

    profile: FoodProfile = SOPA
    status: str | None = None
    fail: bool = False
    photos: list[PreparedPhoto] = field(default_factory=list)

    def unavailable_status(self) -> str | None:
        return self.status

    def available(self) -> bool:
        return self.status is None

    def classify(self, photo: PreparedPhoto) -> tuple[FoodProfile, JudgeCall]:
        self.photos.append(photo)
        if self.fail:
            raise JudgeUnavailable("timeout")
        return self.profile, JudgeCall(kind="food", status="ok", latency_ms=200)


@pytest.fixture()
def session_factory() -> Iterator[Callable[[], Any]]:
    motor = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables.metadata.create_all(motor)
    with motor.begin() as conexion:
        conexion.execute(text("DROP INDEX IF EXISTS uq_messages_turn"))
        conexion.execute(text("DROP INDEX IF EXISTS uq_messages_turn_user"))
    try:
        yield sessionmaker(motor)
    finally:
        tables.metadata.drop_all(motor)
        motor.dispose()


@dataclass
class Harness:
    client: TestClient
    chat: ChatService
    classifier: FakeClassifier | None
    charges: list[str]


def _harness(
    session_factory: Callable[[], Any],
    classifier: FakeClassifier | None = None,
    *,
    allowed: bool = True,
) -> Harness:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    chat = ChatService(
        cast(Any, session_factory),
        embedder=cast(Any, None),
        llm=cast(Any, None),
        settings=settings,
    )
    charges: list[str] = []

    def quota(owner: str) -> QuotaResult:
        charges.append(owner)
        return QuotaResult(allowed=allowed, retry_after=0 if allowed else 60)

    app = create_app(Handlers(health=lambda: HealthStatus(1, 2)))
    app.state.chat = chat
    app.state.chat_identity = IDENTITY
    app.state.quota = quota
    app.state.food_classifier = classifier
    return Harness(TestClient(app), chat, classifier, charges)


def _session(client: TestClient, owner: str = OWNER) -> str:
    client.cookies.set(COOKIE_NAME, IDENTITY.issue(owner))
    client.get("/api/csrf")
    return client.cookies[CSRF_COOKIE]


def _jpeg(size: tuple[int, int] = (800, 600)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (180, 140, 60)).save(out, "JPEG")
    return out.getvalue()


def _send(
    client: TestClient,
    token: str,
    *,
    photo: bytes | None = None,
    media_type: str = "image/jpeg",
    note: str = "",
    turn: str = "",
    thread: str = "",
) -> Any:
    files = {"foto": ("plato.jpg", photo if photo is not None else _jpeg(), media_type)}
    return client.post(
        "/chat/foto",
        headers={CSRF_HEADER: token},
        files=files,
        data={"texto": note, "client_turn_id": turn, "thread_id": thread},
    )


def _events(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_la_foto_produce_un_turno_guardado_con_las_referencias(
    session_factory: Callable[[], Any],
) -> None:
    # Arrange
    harness = _harness(session_factory, FakeClassifier())
    token = _session(harness.client)

    # Act
    response = _send(harness.client, token, note="para domicilio", turn="t-1")

    # Assert
    assert response.status_code == 200
    events = _events(response.text)
    names = [name for name, _ in events]
    assert names[:3] == ["open", "meta", "fallback"]
    assert "products" in names and names[-1] == "done"
    answer = dict(events)["fallback"]["text"]
    assert "Veo una sopa" in answer and "(ref. " in answer
    trace = dict(events)["trace"]
    assert trace["path"] == "foto" and trace["foto"]["status"] == "ok"
    history = harness.chat.history(OWNER)
    assert [message.role for message in history] == ["user", "assistant"]
    assert history[0].content == f"{PHOTO_QUESTION}: para domicilio"
    assert history[1].content == answer
    assert any(
        isinstance(item, dict) and item.get("result_type") == "commercial"
        for item in history[1].sources or []
    )
    assert harness.charges == [OWNER]
    photo = cast(FakeClassifier, harness.classifier).photos[0]
    assert Image.open(io.BytesIO(photo.data)).getexif() == {}


def test_el_reintento_no_clasifica_ni_cobra_otra_vez(session_factory: Callable[[], Any]) -> None:
    # Arrange
    classifier = FakeClassifier()
    harness = _harness(session_factory, classifier)
    token = _session(harness.client)
    first = _events(_send(harness.client, token, turn="t-2").text)
    thread = dict(first)["done"]["thread_id"]

    # Act
    again = _events(_send(harness.client, token, turn="t-2", thread=thread).text)

    # Assert
    assert len(classifier.photos) == 1
    assert harness.charges == [OWNER]
    assert dict(again)["trace"]["route"] == "replay"
    assert len(harness.chat.history(OWNER)) == 2


def test_si_el_clasificador_falla_pide_texto_sin_guardar_ni_cobrar(
    session_factory: Callable[[], Any],
) -> None:
    # Arrange
    harness = _harness(session_factory, FakeClassifier(fail=True))
    token = _session(harness.client)

    # Act
    events = _events(_send(harness.client, token, turn="t-3").text)

    # Assert
    assert dict(events)["error"] == {"message": PHOTO_UNAVAILABLE, "incomplete": False}
    assert harness.charges == []
    assert harness.chat.history(OWNER) == []


def test_sin_cupo_no_guarda_el_turno(session_factory: Callable[[], Any]) -> None:
    # Arrange
    harness = _harness(session_factory, FakeClassifier(), allowed=False)
    token = _session(harness.client)

    # Act
    events = _events(_send(harness.client, token).text)

    # Assert
    assert dict(events)["error"]["message"] == LIMITE_ALCANZADO
    assert harness.chat.history(OWNER) == []


def test_sin_csrf_se_rechaza_sin_clasificar(session_factory: Callable[[], Any]) -> None:
    # Arrange
    classifier = FakeClassifier()
    harness = _harness(session_factory, classifier)
    _session(harness.client)

    # Act
    response = _send(harness.client, "token-falso")

    # Assert
    assert response.status_code == 403
    assert dict(_events(response.text))["error"]["reason"] == "csrf"
    assert classifier.photos == []


@pytest.mark.parametrize(
    ("classifier", "reason"),
    [(None, "off"), (FakeClassifier(status="cap"), "cap")],
)
def test_sin_clasificador_disponible_es_503(
    session_factory: Callable[[], Any], classifier: FakeClassifier | None, reason: str
) -> None:
    # Arrange
    harness = _harness(session_factory, classifier)
    token = _session(harness.client)

    # Act
    response = _send(harness.client, token)

    # Assert
    assert response.status_code == 503
    assert dict(_events(response.text))["error"]["reason"] == reason


@pytest.mark.parametrize(
    ("photo", "media_type", "status", "reason"),
    [
        (b"GIF89a", "image/gif", 415, "formato"),
        (b"no soy una imagen", "image/jpeg", 422, "ilegible"),
        (_jpeg((200, 200)), "image/jpeg", 422, "pequena"),
    ],
)
def test_rechaza_fotos_invalidas_sin_clasificar(
    session_factory: Callable[[], Any],
    photo: bytes,
    media_type: str,
    status: int,
    reason: str,
) -> None:
    # Arrange
    classifier = FakeClassifier()
    harness = _harness(session_factory, classifier)
    token = _session(harness.client)

    # Act
    response = _send(harness.client, token, photo=photo, media_type=media_type)

    # Assert
    assert response.status_code == status
    assert dict(_events(response.text))["error"]["reason"] == reason
    assert classifier.photos == [] and harness.charges == []


def test_el_tamano_declarado_se_comprueba_antes_de_leer(session_factory: Callable[[], Any]) -> None:
    # Arrange
    harness = _harness(session_factory, FakeClassifier())
    token = _session(harness.client)

    # Act
    response = harness.client.post(
        "/chat/foto",
        headers={CSRF_HEADER: token, "Content-Type": "multipart/form-data; boundary=x"},
        content=b"x" * (11 * 1024 * 1024),
    )

    # Assert
    assert response.status_code == 413


def test_la_pagina_ofrece_la_foto_solo_con_clasificador(session_factory: Callable[[], Any]) -> None:
    # Act
    con = _harness(session_factory, FakeClassifier()).client.get("/chat").text
    sin = _harness(session_factory, None).client.get("/chat").text
    tope = _harness(session_factory, FakeClassifier(status="cap")).client.get("/chat").text

    # Assert (FR-018, FR-022)
    assert "data-foto" in con and "TypeSafe" in con
    assert "data-foto" not in sin and "data-foto" not in tope


def test_health_informa_la_foto(session_factory: Callable[[], Any]) -> None:
    # Act
    body = _harness(session_factory, None).client.get("/health").json()

    # Assert
    assert body["food_photo"]["status"] == "off"
