"""Adaptador HTTP para Speaches (API compatible con OpenAI).

Speaches ejecuta ``faster-whisper`` y expone ``POST /v1/audio/transcriptions``.
Se usa como servicio aparte para no cargar el modelo ni su memoria en el
proceso web: el contenedor ``whisper`` mantiene la caché de Hugging Face.
"""

import httpx

from carvajal.application.transcription import TranscriptionError

__all__ = ["SpeachesTranscriber"]


class SpeachesTranscriber:
    """Cliente mínimo del endpoint de transcripción de Speaches."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "",
        timeout_seconds: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._transport = transport

    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str,
        content_type: str,
        language: str = "es",
    ) -> str:
        """Envía el audio y devuelve el texto, sin registrarlo ni persistirlo."""

        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        files = {"file": (filename, audio, content_type)}
        data = {
            "model": self._model,
            "language": language,
            "response_format": "json",
        }
        timeout = httpx.Timeout(self._timeout, connect=10.0)
        try:
            with httpx.Client(
                base_url=self._base_url,
                timeout=timeout,
                transport=self._transport,
            ) as client:
                response = client.post(
                    "/v1/audio/transcriptions",
                    data=data,
                    files=files,
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise TranscriptionError("El servicio de transcripción no respondió bien.") from error

        if not isinstance(payload, dict):
            raise TranscriptionError("El servicio de transcripción devolvió un formato inesperado.")
        text = str(payload.get("text") or "").strip()
        if not text:
            raise TranscriptionError("No se reconoció voz en la grabación.")
        return text
