"""Pruebas HTTP del dictado por voz con un transcriptor falso: sin red."""

from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.security import CSRF_COOKIE, CSRF_FIELD
from apps.api.views import transcribe as transcribe_view
from apps.api.views.transcribe import TranscriptionRateLimiter
from carvajal.application.transcription import TranscriptionError

AUDIO = b"audio-de-prueba"
ARCHIVO = {"audio": ("pregunta.webm", AUDIO, "audio/webm")}


@dataclass
class FakeTranscriber:
    """Doble del servicio de voz: guarda las llamadas y devuelve texto fijo."""

    texto: str = "¿Cuándo nació Carvajal?"
    error: Exception | None = None
    llamadas: list[tuple[bytes, str, str, str]] = field(default_factory=list)

    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str,
        content_type: str,
        language: str = "es",
    ) -> str:
        self.llamadas.append((audio, filename, content_type, language))
        if self.error is not None:
            raise self.error
        return self.texto


def make_client(
    transcriber: FakeTranscriber | None = None,
    *,
    limit: int = 10,
) -> tuple[TestClient, FakeTranscriber]:
    """Cliente con transcriptor y limitador inyectados, sin tocar la base."""

    fake = transcriber or FakeTranscriber()
    app = create_app()
    app.state.transcriber = fake
    app.state.transcription_limits = TranscriptionRateLimiter(limit=limit, window_seconds=60)
    return TestClient(app), fake


def _token(client: TestClient) -> str:
    """Token CSRF emitido por una página pública."""

    if CSRF_COOKIE not in client.cookies:
        client.get("/faq")
    return client.cookies[CSRF_COOKIE]


def _post(client: TestClient, *, archivo: dict[str, tuple[str, bytes, str]] | None = None):
    return client.post(
        "/api/chat/transcribe",
        data={CSRF_FIELD: _token(client)},
        files=archivo or ARCHIVO,
    )


def test_transcribe_devuelve_texto_y_llama_al_servicio() -> None:
    # Arrange
    client, fake = make_client()

    # Act
    response = _post(client)

    # Assert
    assert response.status_code == 200
    assert response.json() == {"text": "¿Cuándo nació Carvajal?", "truncated": False}
    assert fake.llamadas == [(AUDIO, "pregunta.webm", "audio/webm", "es")]


def test_sin_csrf_no_transcribe() -> None:
    # Arrange
    client, fake = make_client()

    # Act
    response = client.post("/api/chat/transcribe", files=ARCHIVO)

    # Assert
    assert response.status_code == 403
    assert fake.llamadas == []


@pytest.mark.parametrize(
    "archivo",
    [
        {"audio": ("nota.txt", b"texto", "text/plain")},
        {"audio": ("pregunta.webm", b"", "audio/webm")},
    ],
)
def test_rechaza_formatos_no_admitidos_o_vacios(
    archivo: dict[str, tuple[str, bytes, str]],
) -> None:
    # Arrange
    client, fake = make_client()

    # Act
    response = _post(client, archivo=archivo)

    # Assert
    assert response.status_code in {415, 422}
    assert fake.llamadas == []


def test_rechaza_audio_demasiado_grande(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setenv("TRANSCRIPTION_MAX_BYTES", "4")
    client, fake = make_client()
    archivo = {"audio": ("pregunta.webm", b"12345", "audio/webm")}

    # Act
    response = _post(client, archivo=archivo)

    # Assert
    assert response.status_code == 413
    assert fake.llamadas == []


def test_sin_transcriptor_configurado_devuelve_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setattr(transcribe_view, "default_transcriber", lambda: None)
    app = create_app()
    app.state.transcriber = None
    app.state.transcription_limits = TranscriptionRateLimiter(limit=1, window_seconds=60)
    client = TestClient(app)

    # Act
    response = _post(client)

    # Assert
    assert response.status_code == 503


def test_un_fallo_del_servicio_no_filtra_detalles() -> None:
    # Arrange
    client, fake = make_client(FakeTranscriber(error=RuntimeError("detalle interno")))

    # Act
    response = _post(client)

    # Assert
    assert response.status_code == 503
    assert "detalle interno" not in response.text
    assert len(fake.llamadas) == 1


def test_un_error_de_transcripcion_devuelve_su_mensaje() -> None:
    # Arrange
    client, _ = make_client(FakeTranscriber(error=TranscriptionError("No se reconoció voz.")))

    # Act
    response = _post(client)

    # Assert
    assert response.status_code == 503
    assert response.json()["detail"] == "No se reconoció voz."


def test_una_transcripcion_vacia_devuelve_422() -> None:
    # Arrange
    client, _ = make_client(FakeTranscriber(texto="   "))

    # Act
    response = _post(client)

    # Assert
    assert response.status_code == 422


def test_marca_el_texto_que_supera_el_limite_de_la_pregunta() -> None:
    # Arrange
    client, _ = make_client(FakeTranscriber(texto="a" * 600))

    # Act
    response = _post(client)

    # Assert
    assert response.status_code == 200
    assert response.json()["truncated"] is True
    assert len(response.json()["text"]) == 600


def test_recorta_la_transcripcion_muy_larga_y_lo_avisa() -> None:
    # Arrange
    client, _ = make_client(FakeTranscriber(texto="a" * 2_500))

    # Act
    response = _post(client)

    # Assert
    assert response.status_code == 200
    assert response.json()["truncated"] is True
    assert len(response.json()["text"]) == 2_000


def test_un_texto_de_500_caracteres_no_se_marca_truncado() -> None:
    # Arrange
    client, _ = make_client(FakeTranscriber(texto="a" * 500))

    # Act
    response = _post(client)

    # Assert
    assert response.status_code == 200
    assert response.json()["truncated"] is False
    assert len(response.json()["text"]) == 500


def test_una_peticion_invalida_no_consume_el_cupo() -> None:
    # Arrange
    client, fake = make_client(limit=1)
    invalido = {"audio": ("nota.txt", b"texto", "text/plain")}

    # Act: el MIME inválido no debe gastar el único dictado disponible.
    rechazada = _post(client, archivo=invalido)
    aceptada = _post(client)

    # Assert
    assert rechazada.status_code == 415
    assert aceptada.status_code == 200
    assert len(fake.llamadas) == 1


def test_el_limite_por_ip_no_comparte_la_cuota_de_preguntas() -> None:
    # Arrange
    client, fake = make_client(limit=1)

    # Act
    primera = _post(client)
    segunda = _post(client)

    # Assert
    assert primera.status_code == 200
    assert segunda.status_code == 429
    assert segunda.headers["retry-after"]
    assert len(fake.llamadas) == 1


def test_el_limitador_se_reinicia_al_expirar_la_ventana() -> None:
    # Arrange
    limiter = TranscriptionRateLimiter(limit=1, window_seconds=60)

    # Act
    primera, _ = limiter.allow("ip")
    segunda, retry_after = limiter.allow("ip")

    # Assert
    assert primera is True
    assert segunda is False
    assert retry_after > 0
