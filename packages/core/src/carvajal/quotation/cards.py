"""Tarjetas del flujo de cotización dentro del texto del modelo (ensayo del 2026-11-27).

Las tarjetas («✅ Agregué…», «🛒 Tu cotización…», «Total parcial…», «🔎 Opciones…») las
escribe solo el flujo (:mod:`carvajal.quotation.render`), después del texto del agente. El
modelo ve esas tarjetas en el historial y una vez escribió una propia: «✅ Agregué *1
producto*… Total parcial: $54.990» sin haber agregado nada, con un total inventado.

:func:`strip_imitated_cards` quita del texto del modelo los bloques con forma de tarjeta antes
de emitirlo, y :func:`compact_cards` resume las tarjetas del historial en una línea neutra para
que el modelo no tenga un formato que imitar.
"""

from __future__ import annotations

import re
from typing import Final

__all__ = ["CARD_NOTE", "compact_cards", "strip_imitated_cards"]

# Comienzos de tarjeta, ya sin negritas ni espacios iniciales. Los emojis pueden llegar con o
# sin el selector de variante (U+FE0F).
_STARTS: Final = (
    "✅ agregué",
    "🔄 actualicé",
    "🛒 tu cotización",
    "↩ tu cotización",
    "🔎 opciones",
    "⭐ recomendada",
    "usé la recomendada",
    "🧾 pedido",
    # La nota con que :func:`compact_cards` resume una tarjeta en el historial: el modelo la
    # copió al cliente en el ensayo tras la PR #288.
    "(el sistema mostró",
)
# «Total parcial: $54.990» o «💰 Total: $37.510 COP»: solo con la cifra (una frase del modelo
# que empieza con «Total parcial…» no es una tarjeta).
_TOTAL: Final = re.compile(r"^(?:💰\s*)?total(?:\s+parcial)?:\s*\$?\s*\d")
# Renglones que siguen dentro de una tarjeta (los que escribe ``render``): líneas numeradas,
# la ciudad, el precio, la presentación, las alternativas y el aviso de las sin precio.
_CONTINUES: Final = re.compile(
    r"^(?:\d+\.\s|📍|\$|alternativas\b|sin precio|precio por confirmar|iva\b|"
    r"\d[\d.,]*\s+(?:paquetes?|cajas?|pacas?|bolsas?|unidad(?:es)?|und)\b)"
)
# La pregunta con que cierra la tarjeta: lo que sigue ya no es parte de ella.
_CLOSES: Final = re.compile(r"^(?:¿algo más|¿cuál te cotizo|di esa\b|¿la confirmo)")
# Comienzo de la nota del historial; empieza como una tarjeta (:data:`_STARTS`).
CARD_NOTE: Final = "(El sistema mostró aquí su tarjeta de la cotización:"
_MARKUP: Final = re.compile(r"[*_]+")
_BLANKS: Final = re.compile(r"\n{3,}")
_ITEM: Final = re.compile(r"^\d+\.\s+")
_EMOJI_LEAD: Final = re.compile(r"^[^\wÁÉÍÓÚÑáéíóúñ¿(]+")
# Renglones de una tarjeta que se conservan, como texto plano, al compactarla.
_MAX_ITEMS: Final = 6


def _plain(line: str) -> str:
    return _MARKUP.sub("", line).replace("️", "").strip().lower()


def _starts_card(plain: str) -> bool:
    return plain.startswith(_STARTS) or _TOTAL.match(plain) is not None


def _blocks(text: str) -> list[tuple[bool, list[str]]]:
    """Renglones agrupados en ``(es_tarjeta, renglones)`` consecutivos, en orden.

    Una tarjeta empieza en un renglón de :data:`_STARTS` (o un total con cifra), sigue con
    renglones en blanco, sangrados o de :data:`_CONTINUES` y termina en su pregunta de cierre
    (:data:`_CLOSES`) o en el primer renglón que no es suyo.
    """

    blocks: list[tuple[bool, list[str]]] = []
    in_card = False
    for line in text.split("\n"):
        plain = _plain(line)
        closing = False
        if _starts_card(plain):
            in_card = True
        elif in_card and _CLOSES.match(plain) is not None:
            closing = True
        elif in_card and plain and not line[:1].isspace() and _CONTINUES.match(plain) is None:
            in_card = False
        if blocks and blocks[-1][0] == in_card:
            blocks[-1][1].append(line)
        else:
            blocks.append((in_card, [line]))
        if closing:
            in_card = False
    return blocks


def _join(parts: list[str]) -> str:
    return _BLANKS.sub("\n\n", "\n".join(parts)).strip()


def strip_imitated_cards(text: str) -> tuple[str, int]:
    """``(texto sin las tarjetas, renglones quitados)``; el texto sin tarjetas vuelve igual."""

    kept: list[str] = []
    removed = 0
    for is_card, lines in _blocks(text):
        if is_card:
            removed += sum(1 for line in lines if line.strip())
        else:
            kept.extend(lines)
    if not removed:
        return text, 0
    return _join(kept), removed


def compact_cards(text: str) -> str:
    """Cada tarjeta del historial, como una línea neutra con sus productos en texto plano."""

    parts: list[str] = []
    changed = False
    for is_card, lines in _blocks(text):
        if not is_card:
            parts.extend(lines)
            continue
        changed = True
        items = [
            _EMOJI_LEAD.sub("", _MARKUP.sub("", _ITEM.sub("", line.strip()))).strip()
            for line in lines
            if _ITEM.match(line.strip()) or _plain(line).startswith("⭐")
        ][:_MAX_ITEMS]
        head = _EMOJI_LEAD.sub("", _MARKUP.sub("", lines[0]).replace("️", "")).strip()
        summary = "; ".join(items) if items else head
        parts.append(f"{CARD_NOTE} {summary}.)")
    return _join(parts) if changed else text
