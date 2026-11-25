"""Números dichos con palabras en español («dos mil quinientos» → 2.500), sin LLM.

Lo usan la normalización de las transcripciones de voz (:mod:`carvajal.speech`) y las
respuestas cortas del flujo de cotización (:mod:`carvajal.agent.followup`). Determinista:
«y» solo une decenas con unidades («treinta y cinco») y «quinientos y mil» son dos números.
"""

import re
import unicodedata
from typing import Final

__all__ = ["WORD_VALUES", "spoken_quantities", "word_numbers", "words_to_number"]


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


WORD_VALUES: Final = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12,
    "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiun": 21, "veintiuno": 21,
    "veintiuna": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24,
    "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28,
    "veintinueve": 29, "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90, "cien": 100, "ciento": 100,
    "doscientos": 200, "doscientas": 200, "trescientos": 300, "trescientas": 300,
    "cuatrocientos": 400, "cuatrocientas": 400, "quinientos": 500, "quinientas": 500,
    "seiscientos": 600, "seiscientas": 600, "setecientos": 700, "setecientas": 700,
    "ochocientos": 800, "ochocientas": 800, "novecientos": 900, "novecientas": 900,
}  # fmt: skip
_TENS: Final = frozenset(
    {"treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa"}
)
_UNITS: Final = frozenset(
    {"un", "uno", "una", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"}
)


def words_to_number(phrase: str) -> int | None:
    """Valor de «dos mil quinientos» (2.500), «trescientos» (300) o «mil» (1.000)."""

    total = 0
    partial = 0
    seen = False
    for word in _plain(phrase).split():
        if word == "y":
            continue
        if word == "mil":
            total += (partial or 1) * 1000
            partial = 0
            seen = True
            continue
        if word not in WORD_VALUES:
            return None
        partial += WORD_VALUES[word]
        seen = True
    return total + partial if seen else None


def word_numbers(words: list[str]) -> list[tuple[int, int, str]]:
    """``(inicio, fin, frase)`` de cada número dicho con palabras, por índice de palabra.

    «y» solo une decenas con unidades («treinta y cinco»): «quinientos y mil» son dos
    cantidades. Dos números seguidos sin «mil» entre ellos tampoco se suman si el segundo
    no completa al primero («cien quinientos»).
    """

    found: list[tuple[int, int, str]] = []
    index = 0
    while index < len(words):
        word = words[index]
        if word not in WORD_VALUES and word != "mil":
            index += 1
            continue
        start = index
        phrase = [word]
        index += 1
        while index < len(words):
            current = words[index]
            previous = phrase[-1]
            if (
                current == "y"
                and previous in _TENS
                and index + 1 < len(words)
                and (words[index + 1] in _UNITS)
            ):
                phrase += ["y", words[index + 1]]
                index += 2
                continue
            if current == "mil" or (current in WORD_VALUES and _smaller_order(previous, current)):
                phrase.append(current)
                index += 1
                continue
            break
        found.append((start, index, " ".join(phrase)))
    return found


def _smaller_order(previous: str, current: str) -> bool:
    """``current`` completa a ``previous``: «ciento veinte», «mil quinientos», «veinte»."""

    if previous == "mil":
        return True
    before = WORD_VALUES.get(previous, 0)
    after = WORD_VALUES.get(current, 0)
    # «ciento veinte», «quinientos treinta»; «treinta cinco» (sin «y») no es un número.
    return before >= 100 and after < 100


_UNITS_AFTER: Final = re.compile(
    r"^\s+(?:oz|onzas?|ml|mililitros?|litros?|unidades?|piezas?|pzas?|cajas?|paquetes?|pacas?|"
    r"vasos?|tapas?|domos?|contenedores?|recipientes?|portacomidas?|cazuelas?|bowls?|charolas?|"
    r"bandejas?|cubiertos?|tenedores?|cucharas?|cucharitas?|cuchillos?|envases?|platos?)\b",
    re.IGNORECASE,
)


def spoken_quantities(text: str) -> str:
    """«trescientos vasos» → «300 vasos», «dos mil quinientos platos» → «2.500 platos».

    Conservadora, como ``normalizarCantidades`` del navegador (``chat-voice.js``): solo
    convierte un número dicho con palabras si lo sigue una unidad o un producto; fechas,
    años, ordinales y texto suelto no cambian. «un vaso» o «una caja» tampoco.
    """

    tokens = list(re.finditer(r"\S+", text))
    words = [_plain(match.group(0)).strip(",.;:!¡¿?") for match in tokens]
    pieces: list[str] = []
    cursor = 0
    for start, end, phrase in word_numbers(words):
        if phrase in {"un", "una", "uno"}:
            continue
        begin = tokens[start].start()
        finish = tokens[end - 1].end()
        if not _UNITS_AFTER.match(text[finish:]) or text[finish - 1] in ",.;:":
            continue
        value = words_to_number(phrase)
        if not value:
            continue
        pieces.append(text[cursor:begin])
        pieces.append(f"{value:,}".replace(",", ".") if value >= 1000 else str(value))
        cursor = finish
    pieces.append(text[cursor:])
    return "".join(pieces)
