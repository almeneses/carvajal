"""Higiene de los mensajes que se publican en informes y consola.

Los informes de captura, carga y sincronización llegan a la consola y a la web;
un error crudo puede arrastrar la contraseña de una URL de conexión o una ruta
absoluta del servidor. Estas funciones dejan el diagnóstico útil (host, nombre
del archivo, causa) sin exponer secretos ni la topología interna.
"""

from __future__ import annotations

import re

__all__ = ["redact_credentials", "safe_report_error"]

_CREDENTIALS = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<user>[^/@:\s]+):[^@\s]+@")
_ABSOLUTE_PATH = re.compile(r"(?<![\w:/])/(?:[\w.@+\-]+/)+[\w.@+\-]+")
_WINDOWS_PATH = re.compile(r"(?<![\w:])[A-Za-z]:\\(?:[\w.@+\- ]+\\)*[\w.@+\- ]+")


def redact_credentials(message: str) -> str:
    """Oculta la contraseña de una URL sin perder usuario, host ni diagnóstico."""

    return _CREDENTIALS.sub(r"\g<scheme>\g<user>:***@", message)


def safe_report_error(error: object) -> str:
    """Mensaje de error apto para un informe: sin credenciales ni rutas internas.

    Las rutas absolutas se reducen a su último componente: el informe conserva
    el nombre del archivo, que es lo que el operador necesita para corregirlo.
    """

    text = redact_credentials(str(error))
    text = _ABSOLUTE_PATH.sub(_basename, text)
    return _WINDOWS_PATH.sub(_basename, text)


def _basename(match: re.Match[str]) -> str:
    """Último segmento de una ruta, sin separadores."""

    return re.split(r"[\\/]", match.group(0))[-1]
