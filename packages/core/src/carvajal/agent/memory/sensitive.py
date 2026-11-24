"""Filtro determinista de datos sensibles (spec 045, FR-012; research R8).

:func:`find_sensitive` dice qué tipos de dato sensible trae un texto y :func:`redact` los
sustituye por :data:`REDACTED`. Se aplica en todos los caminos de escritura de la memoria
(herramienta, atajo y migración) y antes de guardar el resumen de la conversación.

Decide por **forma**, no por conteo de dígitos: un teléfono es un celular o un fijo
colombiano, o un ``+`` seguido de 8 o más dígitos; un documento es un número precedido de su
palabra («cédula», «CC», «NIT»…); una tarjeta son 13 a 19 dígitos que pasan la suma de
Luhn. Así «1.000.000 de vasos», «$2.500.000», «2026-11-24», «tamaños 8 10 12 16 oz» y los
folios hexadecimales no se confunden con datos personales, y ``redact`` no rompe el
respaldo de las cifras que la persona dijo (R4). Las cifras seguidas de una unidad o de una
moneda, o precedidas de ``$``, quedan exentas aunque tengan forma de teléfono o tarjeta.
"""

import re
from collections.abc import Iterator
from typing import Final, Literal

__all__ = ["REDACTED", "SENSITIVE_KINDS", "SensitiveKind", "find_sensitive", "redact"]

SensitiveKind = Literal["documento", "telefono", "correo", "tarjeta", "cuenta", "clave"]
SENSITIVE_KINDS: Final[tuple[str, ...]] = (
    "documento",
    "telefono",
    "correo",
    "tarjeta",
    "cuenta",
    "clave",
)
REDACTED: Final = "[dato omitido]"

_FLAGS = re.IGNORECASE
# Fin de una cifra: ni letra ni dígito pegados, ni un decimal o grupo de miles que siga.
_END = r"(?![\w]|[.,]\d)"
_START = r"(?<![\w.+$])"

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", _FLAGS)
_MOBILE = re.compile(rf"{_START}(?:\+?57[ .-]?)?3\d{{2}}[ .-]?\d{{3}}[ .-]?\d{{4}}{_END}")
_LANDLINE = re.compile(rf"(?<![\w.+$(])(?:\+?57[ .-]?)?\(?60\d\)?[ .-]?\d{{3}}[ .-]?\d{{4}}{_END}")
_INTERNATIONAL = re.compile(rf"(?<![\w])\+\d(?:[ .-]?\d){{7,}}{_END}")
_DOCUMENT = re.compile(
    r"\b(?:c[ée]dula|c\.?\s?c\.?|documento|nit|pasaporte|dni|identificaci[óo]n)"
    r"(?:\s*(?:de\s+identidad|de\s+ciudadan[íi]a|de\s+extranjer[íi]a|n[úu]mero|no\.|nro\.?"
    r"|es|:|#))*"
    rf"\s*(?:[a-z]{{1,3}}\s?)?(?:\d{{1,3}}(?:\.\d{{3}})+|\d{{5,}})(?:-\d)?{_END}",
    _FLAGS,
)
_CARD = re.compile(rf"{_START}(?:\d[ -]?){{12,18}}\d{_END}")
_CARD_WORDS = re.compile(
    r"\b(?:cvv|cvc|c[óo]digo\s+de\s+seguridad)\b\s*(?:es\b|:)?\s*\d{3,4}\b"
    r"|\bfecha\s+de\s+vencimiento\b\s*(?:es\b|:)?\s*\d{1,2}\s*/\s*\d{2,4}\b",
    _FLAGS,
)
_ACCOUNT = re.compile(
    r"\b(?:cuenta\s+(?:de\s+)?(?:ahorros|corriente|bancaria)|n[úu]mero\s+de\s+cuenta)\b"
    r"\s*(?:es\b|:|#|no\.)?\s*\d[\d -]{3,}[^,;\n]*",
    _FLAGS,
)
_SECRET = re.compile(
    r"\b(?:contrase[ñn]a|password|clave|pin|token)\b\s*(?:es\b|:|=)\s*\S[^,;\n]*", _FLAGS
)
# Exenciones de las cifras con forma de teléfono o tarjeta (R8): una unidad o una moneda
# después, o ``$``/``COP`` antes.
_UNIT_AFTER = re.compile(
    r"\s*(?:de\s+)?(?:vasos?|unidades?|cajas?|paquetes?|pacas?|oz|ml|g|gr|gramos|kg|pesos"
    r"|cop|usd|mxn)\b",
    _FLAGS,
)
_MONEY_BEFORE = re.compile(r"(?:\$|\bcop|\busd|\bmxn)\s*$", _FLAGS)

_PATTERNS: Final[tuple[tuple[SensitiveKind, re.Pattern[str], bool], ...]] = (
    # (tipo, patrón, si aplica las exenciones de cantidades y montos)
    ("correo", _EMAIL, False),
    ("documento", _DOCUMENT, False),
    ("tarjeta", _CARD_WORDS, False),
    ("cuenta", _ACCOUNT, False),
    ("clave", _SECRET, False),
    ("tarjeta", _CARD, True),
    ("telefono", _MOBILE, True),
    ("telefono", _LANDLINE, True),
    ("telefono", _INTERNATIONAL, True),
)


def _luhn(digits: str) -> bool:
    """Suma de Luhn de una cadena de dígitos."""

    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _exempt(text: str, start: int, end: int) -> bool:
    """Cifra con una unidad o moneda después, o con ``$``/``COP`` antes: no es sensible."""

    return bool(_UNIT_AFTER.match(text, end)) or bool(_MONEY_BEFORE.search(text[:start]))


def _matches(text: str) -> Iterator[tuple[int, int, SensitiveKind]]:
    """Cada dato sensible del texto como ``(inicio, fin, tipo)``."""

    for kind, pattern, exemptable in _PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span()
            if exemptable and _exempt(text, start, end):
                continue
            if pattern is _CARD:
                digits = re.sub(r"\D", "", match.group())
                if not 13 <= len(digits) <= 19 or not _luhn(digits):
                    continue
            yield start, end, kind


def find_sensitive(text: str) -> list[SensitiveKind]:
    """Tipos de dato sensible del texto, sin repetir y en el orden en que aparecen."""

    found: list[SensitiveKind] = []
    for _start, _end, kind in sorted(_matches(text or "")):
        if kind not in found:
            found.append(kind)
    return found


def redact(text: str) -> str:
    """El texto con cada dato sensible sustituido por :data:`REDACTED`.

    Los tramos que se solapan se unen; lo demás queda igual (espacios incluidos).
    """

    spans = sorted((start, end) for start, end, _kind in _matches(text or ""))
    if not spans:
        return text
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    pieces: list[str] = []
    cursor = 0
    for start, end in merged:
        pieces.append(text[cursor:start])
        pieces.append(REDACTED)
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)
