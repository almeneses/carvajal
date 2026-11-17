"""Identidad estable de los documentos del corpus.

La identidad se deriva de la fuente canónica —URL normalizada para capturas,
ruta relativa a la raíz importada para la consola y nombre de archivo para las
subidas web— y nunca del contenido. La huella del texto queda como versión:
reimportar un archivo modificado actualiza el mismo documento en lugar de crear
otro.

Este módulo solo transforma cadenas: no escribe archivos ni consulta el índice.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from pathlib import PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from carvajal.domain.rules import slug_from_url

URL_PREFIX = "url:"
FILE_PREFIX = "file:"
UPLOAD_PREFIX = "upload:"

FALLBACK_SLUG = "documento"
"""Slug que se usa cuando el nombre no aporta ningún carácter legible."""


def normalize_url(url: str) -> str:
    """Normaliza una URL para usarla como identidad.

    Fija el esquema en ``https``, quita ``www.``, pasa el host a minúsculas,
    elimina la barra final —conservando ``/`` en la portada— y descarta el
    fragmento. La cadena de consulta se conserva: dos rutas iguales con
    parámetros distintos son páginas distintas.
    """

    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, parts.query, ""))


def source_key_for_url(url: str) -> str:
    """Identidad de una captura del sitio: ``url:<normalizada>``."""

    return f"{URL_PREFIX}{normalize_url(url)}"


def source_key_for_file(relative: str | PurePosixPath, root: str | None = None) -> str:
    """Identidad de un archivo importado: ``file:<raíz>/<ruta relativa>``.

    La ruta se normaliza con ``/`` y sin ``.`` para que el resultado no dependa
    del sistema de archivos. Para un archivo suelto —sin raíz importada— la
    identidad es ``file:<nombre>``.

    Raises:
        ValueError: si la ruta queda vacía o intenta salir de la raíz.
    """

    path = _relative_path(relative)
    if not path:
        raise ValueError("La ruta relativa de un archivo no puede quedar vacía.")
    if root is None:
        return f"{FILE_PREFIX}{PurePosixPath(path).name}"
    name = PurePosixPath(str(root).replace("\\", "/")).name
    return f"{FILE_PREFIX}{name}/{path}" if name else f"{FILE_PREFIX}{path}"


def source_key_for_upload(filename: str) -> str:
    """Identidad de una subida web: ``upload:<nombre de archivo>``.

    Raises:
        ValueError: si el nombre queda vacío.
    """

    name = PurePosixPath(str(filename).replace("\\", "/")).name
    if not name:
        raise ValueError("El nombre de un archivo subido no puede quedar vacío.")
    return f"{UPLOAD_PREFIX}{name}"


def slug_for_url(url: str) -> str:
    """Slug legible de una captura, derivado del último segmento de su URL."""

    return slug_from_url(url)


def slug_for_file(filename: str) -> str:
    """Slug legible de un archivo: nombre sin extensión, plegado y en minúsculas.

    Los acentos se despliegan (``Cambio climático.pdf`` → ``cambio-climatico``)
    y cualquier otro carácter no alfanumérico separa palabras.
    """

    stem = _fold(PurePosixPath(str(filename).replace("\\", "/")).stem).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    return slug or FALLBACK_SLUG


def assign_slug(base: str, source_key: str, taken: Mapping[str, str]) -> str:
    """Asigna a ``source_key`` un slug libre a partir de ``base``.

    Si ``base`` ya pertenece a la misma fuente se reutiliza —un documento
    existente nunca se renombra—; si pertenece a otra, se prueba ``base-2``,
    ``base-3``… en orden hasta encontrar uno libre. El resultado depende solo
    del contenido de ``taken``, así que dos corridas iguales asignan lo mismo.
    """

    if taken.get(base) in (None, source_key):
        return base
    suffix = 2
    while True:
        candidate = f"{base}-{suffix}"
        if taken.get(candidate) in (None, source_key):
            return candidate
        suffix += 1


def _relative_path(relative: str | PurePosixPath) -> str:
    """Normaliza una ruta relativa a segmentos separados por ``/``."""

    parts = PurePosixPath(str(relative).replace("\\", "/")).parts
    segments = [part for part in parts if part not in ("", "/")]
    if ".." in segments:
        raise ValueError("La ruta relativa no puede salir de la raíz importada.")
    return "/".join(segments)


def _fold(text: str) -> str:
    """Despliega los acentos para que el slug no dependa de ellos."""

    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(char for char in decomposed if not unicodedata.combining(char))
