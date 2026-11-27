"""Preparación de la foto antes de enviarla al clasificador (spec 051, research R3).

La foto se endereza según su orientación, se escala a los límites de TypeSafe (lado mayor
≤ 1568 px, lado menor ≥ 280 px) y se re-codifica como JPEG **sin** metadatos: ni EXIF
(ubicación, dispositivo, fecha) ni perfil de color. Todo ocurre en memoria; nada se escribe
en disco (FR-002, FR-003).
"""

import base64
import io
import warnings
from dataclasses import dataclass
from typing import Final, Literal

from PIL import Image, ImageOps, UnidentifiedImageError

__all__ = [
    "MAX_PHOTO_BYTES",
    "MAX_SIDE_PX",
    "MIN_SIDE_PX",
    "THUMBNAIL_SIDE_PX",
    "PhotoRejected",
    "PreparedPhoto",
    "prepare_photo",
    "thumbnail_data_url",
]

MAX_PHOTO_BYTES: Final = 10 * 1024 * 1024
MAX_SIDE_PX: Final = 1568
MIN_SIDE_PX: Final = 280
# Defensa contra bombas de descompresión: una foto de cámara ronda 12-50 Mpx.
_MAX_SOURCE_PIXELS: Final = 60_000_000
_JPEG_QUALITY: Final = 85
# Spec 053 (FR-017): la miniatura del historial; la foto original nunca se guarda.
THUMBNAIL_SIDE_PX: Final = 320
_THUMBNAIL_QUALITY: Final = 70
# WebP: el formato en que WhatsApp entrega muchas fotos; se convierte a JPEG como las demás.
_FORMATS: Final = frozenset({"JPEG", "PNG", "MPO", "WEBP"})

RejectReason = Literal["formato", "pequena", "grande", "alargada", "ilegible"]

MESSAGES: Final[dict[RejectReason, str]] = {
    "formato": "La foto debe ser JPEG o PNG (sin animación).",
    "pequena": f"La foto es muy pequeña: cada lado debe medir al menos {MIN_SIDE_PX} píxeles.",
    "grande": "La foto supera los 10 MB.",
    "alargada": "La foto es demasiado alargada; recórtala alrededor del alimento.",
    "ilegible": "No se pudo leer la foto. Prueba con otra.",
}


class PhotoRejected(ValueError):
    """La foto no se puede analizar; ``message`` se le puede mostrar a la persona."""

    def __init__(self, reason: RejectReason) -> None:
        super().__init__(MESSAGES[reason])
        self.reason: RejectReason = reason
        self.message = MESSAGES[reason]


@dataclass(frozen=True)
class PreparedPhoto:
    """JPEG listo para el clasificador: enderezado, escalado y sin metadatos."""

    data: bytes
    width: int
    height: int

    def __repr__(self) -> str:  # nunca los bytes en un log
        return f"PreparedPhoto({self.width}x{self.height}, {len(self.data)} B)"


def prepare_photo(raw: bytes) -> PreparedPhoto:
    """Valida y normaliza la foto; lanza :class:`PhotoRejected` con el motivo."""

    if len(raw) > MAX_PHOTO_BYTES:
        raise PhotoRejected("grande")
    if not raw:
        raise PhotoRejected("ilegible")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(raw))
            if image.format not in _FORMATS:
                raise PhotoRejected("formato")
            if getattr(image, "is_animated", False) and image.format in {"PNG", "WEBP"}:
                raise PhotoRejected("formato")
            width, height = image.size
            if width * height > _MAX_SOURCE_PIXELS:
                raise PhotoRejected("grande")
            image.load()
            upright = ImageOps.exif_transpose(image)
    except PhotoRejected:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise PhotoRejected("grande") from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        raise PhotoRejected("ilegible") from None
    if min(upright.size) < MIN_SIDE_PX:
        raise PhotoRejected("pequena")
    rgb = upright.convert("RGB")
    rgb.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX), Image.Resampling.LANCZOS)
    if min(rgb.size) < MIN_SIDE_PX:
        raise PhotoRejected("alargada")
    out = io.BytesIO()
    # Sin ``exif`` ni ``icc_profile``: el JPEG nuevo no lleva metadatos de la original.
    rgb.save(out, "JPEG", quality=_JPEG_QUALITY, optimize=True)
    return PreparedPhoto(out.getvalue(), rgb.width, rgb.height)


def thumbnail_data_url(photo: PreparedPhoto) -> str:
    """Miniatura de la foto ya preparada como ``data:`` URL (spec 053, FR-017).

    Parte de la foto preparada, así hereda la orientación correcta y la ausencia de
    metadatos; el lado mayor queda en :data:`THUMBNAIL_SIDE_PX` como máximo. La CSP admite
    ``data:`` en ``img-src``, así que el historial la pinta sin una ruta nueva.
    """

    with Image.open(io.BytesIO(photo.data)) as image:
        small = image.convert("RGB")
        small.thumbnail((THUMBNAIL_SIDE_PX, THUMBNAIL_SIDE_PX))
        out = io.BytesIO()
        small.save(out, "JPEG", quality=_THUMBNAIL_QUALITY, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode("ascii")
