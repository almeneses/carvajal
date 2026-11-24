"""Números de teléfono en formato E.164 para el webhook de mensajería.

La normalización quita separadores habituales (espacios, guiones, puntos y paréntesis) y
el prefijo ``whatsapp:`` de algunos relés, para que la allowlist y el remitente se
comparen igual aunque se escriban distinto. Los errores nunca repiten el número: los
mensajes pueden acabar en logs o en respuestas HTTP.
"""

import re
from typing import Final

__all__ = ["normalize_e164"]

_SEPARATORS: Final = re.compile(r"[\s\-().]")
# «+», un dígito distinto de cero y en total de 8 a 15 dígitos (ITU-T E.164).
_E164: Final = re.compile(r"\+[1-9][0-9]{7,14}")
_CHANNEL_PREFIX: Final = "whatsapp:"


def normalize_e164(value: str) -> str:
    """Devuelve el número canónico (``+573100000000``) o lanza ``ValueError``.

    ``+57 310-000 (0000)``, ``whatsapp:+573100000000`` y ``+573100000000`` dan el mismo
    resultado. Sin el ``+`` inicial el número se rechaza: sin indicativo de país no hay
    forma segura de completarlo.
    """

    raw = value.strip()
    if raw.lower().startswith(_CHANNEL_PREFIX):
        raw = raw[len(_CHANNEL_PREFIX) :]
    candidate = _SEPARATORS.sub("", raw)
    if not _E164.fullmatch(candidate):
        raise ValueError("El número no está en formato E.164 (+ y de 8 a 15 dígitos).")
    return candidate
