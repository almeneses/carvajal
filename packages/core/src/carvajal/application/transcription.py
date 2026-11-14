"""Voz a texto: puerto que implementa el servicio de transcripción del chat.

El caso de uso no conoce el motor concreto (faster-whisper, un servicio HTTP o
un doble en pruebas). Solo recibe bytes de audio y devuelve el texto reconocido.
"""

from typing import Protocol, runtime_checkable

__all__ = ["Transcriber", "TranscriptionError"]


class TranscriptionError(Exception):
    """El servicio de transcripción no pudo devolver texto utilizable."""


@runtime_checkable
class Transcriber(Protocol):
    """Convierte audio en texto para rellenar la pregunta del chat."""

    def transcribe(
        self,
        audio: bytes,
        *,
        filename: str,
        content_type: str,
        language: str = "es",
    ) -> str:
        """Devuelve el texto reconocido o lanza ``TranscriptionError``.

        Args:
            audio: Contenido binario de la grabación.
            filename: Nombre original; el proveedor lo usa para inferir el formato.
            content_type: MIME del audio (por ejemplo ``audio/webm``).
            language: Código ISO-639-1 del idioma esperado.

        Raises:
            TranscriptionError: si el servicio falla, tarda demasiado o no
                reconoce voz.
        """

        ...
