"""Codificación de eventos *server-sent events* compartida por chat y captura.

El formato de cable es el mismo en toda la aplicación: cada evento ocupa un
bloque con ``event: <nombre>`` y ``data: <json>`` terminado en línea en blanco.
"""

import json
from collections.abc import Mapping
from typing import Any

__all__ = ["SSE_HEADERS", "SSE_MEDIA_TYPE", "encode"]

SSE_MEDIA_TYPE = "text/event-stream"
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def encode(name: str, data: Mapping[str, Any]) -> str:
    """Serializa un evento al formato SSE."""

    payload = json.dumps(dict(data), ensure_ascii=False)
    return f"event: {name}\ndata: {payload}\n\n"
