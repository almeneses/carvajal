"""Pruebas del adaptador HTTP de voz a texto (sin red ni servicio real)."""

from collections.abc import Callable

import httpx
import pytest

from carvajal.adapters.transcription import SpeachesTranscriber
from carvajal.application.transcription import TranscriptionError

BASE_URL = "http://whisper:8000"
MODEL = "Systran/faster-whisper-small"


def _adapter(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    api_key: str = "",
) -> SpeachesTranscriber:
    return SpeachesTranscriber(
        BASE_URL,
        MODEL,
        api_key=api_key,
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )


def test_envia_modelo_idioma_y_devuelve_el_texto() -> None:
    # Arrange
    visto: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto["url"] = str(request.url)
        visto["body"] = request.content
        visto["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"text": "  ¿Cuándo nació Carvajal?  "})

    adapter = _adapter(handler)

    # Act
    texto = adapter.transcribe(
        b"audio-de-prueba",
        filename="pregunta.webm",
        content_type="audio/webm",
        language="es",
    )

    # Assert
    assert texto == "¿Cuándo nació Carvajal?"
    assert str(visto["url"]).endswith("/v1/audio/transcriptions")
    cuerpo = visto["body"]
    assert isinstance(cuerpo, bytes)
    assert MODEL.encode() in cuerpo
    assert b"es" in cuerpo
    assert b"audio/webm" in cuerpo
    assert visto["auth"] is None


def test_envia_la_clave_si_esta_configurada() -> None:
    # Arrange
    visto: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"text": "Hola"})

    adapter = _adapter(handler, api_key="secreto")

    # Act
    adapter.transcribe(b"audio", filename="a.webm", content_type="audio/webm")

    # Assert
    assert visto["auth"] == "Bearer secreto"


@pytest.mark.parametrize(
    "respuesta",
    [
        httpx.Response(500, json={"detail": "fallo"}),
        httpx.Response(200, json={"text": "   "}),
        httpx.Response(200, text="no es json"),
    ],
)
def test_las_respuestas_invalidas_se_traducen_a_transcription_error(
    respuesta: httpx.Response,
) -> None:
    # Arrange
    adapter = _adapter(lambda request: respuesta)

    # Act / Assert
    with pytest.raises(TranscriptionError):
        adapter.transcribe(b"audio", filename="a.webm", content_type="audio/webm")


def test_un_timeout_se_traduce_a_transcription_error() -> None:
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    adapter = _adapter(handler)

    # Act / Assert
    with pytest.raises(TranscriptionError):
        adapter.transcribe(b"audio", filename="a.webm", content_type="audio/webm")
