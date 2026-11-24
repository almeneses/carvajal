"""Texto apto para voz (TTS) a partir del Markdown del modelo o del formato de WhatsApp.

Lo usan el canal de WhatsApp cuando el cliente habló (``consultar_carvajal`` con
``voz=true``) y la lectura en voz del chat web: lo que aquí se devuelve es lo que se oye.
Todo es determinista y sin LLM; no se inventan ni se redondean cifras.

- :func:`to_speech` pasa **toda** la respuesta a texto hablable;
- :func:`spoken_summary` (spec 048) la **resume** en unas 55 palabras (15-20 s) sin LLM,
  solo con frases de la propia respuesta: la primera frase, la lista en una frase, la
  pregunta final y las fuentes, con un aviso de que el detalle está en pantalla o por
  escrito. Es lo que se lee en la web y en las notas de voz de WhatsApp;
- :func:`normalize_transcript` pasa los miles de una transcripción al estilo colombiano
  («1,800» → «1.800»).

Reglas (español de Colombia), alineadas con la lectura en voz del chat web
(``apps/api/static/chat-voice.js``, ``limpiarTextoHablado``):

- sin citas ``[n]``, sin Markdown ni formato de WhatsApp, sin emojis;
- las URL nunca se leen: se separan en :attr:`SpokenReply.links` para enviarlas como texto;
  un enlace dentro de una frase se nombra como «el enlace»;
- las fuentes se resumen en una frase (:func:`spoken_sources`), no en una lista;
- unidades y abreviaturas en palabras («12 oz» → «12 onzas», «x 200 und» → «de 200
  unidades», «aprox.» → «aproximadamente»); los importes conservan los dígitos
  («$1.234.500» → «1.234.500 pesos»), que el TTS lee bien;
- del folio solo se dicen los últimos cuatro caracteres, deletreados («tu cotización
  terminada en B 7 E 4», :func:`speak_folio_tail`); el folio completo va por escrito. Los
  códigos técnicos (SKU) se omiten, como en la web;
- las viñetas pasan a una frase («a, b y c») y las listas numeradas a «primero… luego…».
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final, Literal
from urllib.parse import urlsplit

__all__ = [
    "LINKS_HEADER",
    "SUMMARY_POINTERS",
    "SUMMARY_WORDS",
    "TEXT_HEADER",
    "Channel",
    "SpeechLink",
    "SpokenReply",
    "VoiceReplyParts",
    "combine",
    "normalize_transcript",
    "parse_voice_reply",
    "speak_folio",
    "speak_folio_tail",
    "speak_money",
    "split_voice_reply",
    "spoken_sources",
    "spoken_summary",
    "to_speech",
]

LINKS_HEADER = "--- ENLACES (enviar como texto, no leer en voz alta) ---"
"""Separa el texto hablado de los enlaces en :meth:`SpokenReply.render`."""

TEXT_HEADER = "--- TEXTO (enviar como mensaje escrito, no leer en voz alta) ---"
"""Separa el texto hablado de la respuesta escrita completa en :meth:`SpokenReply.render`.

Aparece cuando la nota de voz es un resumen (:attr:`SpokenReply.omitted`): lo que sigue es
la respuesta normal de WhatsApp, con sus enlaces, fuentes y oferta del PDF, para enviarla
como mensaje de texto y que el cliente no pierda el detalle.
"""

Channel = Literal["web", "whatsapp"]
"""Dónde queda el detalle que el resumen hablado no dice: en pantalla o por escrito."""

SUMMARY_WORDS: Final = 55
"""Presupuesto del resumen hablado: unas 55 palabras son 15 a 20 segundos de voz."""

SUMMARY_POINTERS: Final[dict[str, str]] = {
    "web": "El detalle completo está en pantalla.",
    "whatsapp": "Te dejo el detalle por escrito.",
}
"""Frase final cuando el resumen deja fuera parte de la respuesta, por canal."""


@dataclass(frozen=True)
class SpeechLink:
    """Enlace que no se lee en voz alta: se envía aparte, como texto."""

    label: str
    url: str

    def line(self) -> str:
        return f"{self.label}: {self.url}" if self.label else self.url


@dataclass(frozen=True)
class SpokenReply:
    """Texto para el TTS y los enlaces que acompañan la nota de voz por escrito.

    ``omitted`` indica que el texto hablado es un resumen: dejó fuera parte de la
    respuesta escrita (frases, el detalle de una lista, la cotización completa).
    """

    text: str
    links: tuple[SpeechLink, ...] = ()
    omitted: bool = False

    def render(self, written: str = "") -> str:
        """El texto hablado y, aparte, lo que va por escrito.

        Sin ``written``: si hay enlaces, la sección :data:`LINKS_HEADER` con uno por línea
        (contrato de siempre). Con ``written`` (la respuesta escrita completa): la sección
        :data:`TEXT_HEADER` con ese texto, que ya lleva los enlaces; la de enlaces solo
        aparece si alguno no está en ``written``.
        """

        sections: list[str] = [self.text] if self.text else []
        written = written.strip()
        links = self.links
        if written:
            sections.append(f"{TEXT_HEADER}\n{written}")
            links = tuple(link for link in links if link.url not in written)
        if links:
            listed = "\n".join(link.line() for link in links)
            sections.append(f"{LINKS_HEADER}\n{listed}")
        return "\n\n".join(sections)


def combine(parts: Iterable[SpokenReply]) -> SpokenReply:
    """Une varias respuestas: párrafos separados por línea en blanco y enlaces sin repetir."""

    texts: list[str] = []
    links: list[SpeechLink] = []
    omitted = False
    for part in parts:
        if part.text:
            texts.append(part.text)
        links.extend(part.links)
        omitted = omitted or part.omitted
    return SpokenReply("\n\n".join(texts), _unique_links(links), omitted)


@dataclass(frozen=True)
class VoiceReplyParts:
    """Las tres partes de una respuesta con ``voz=true`` (ver :meth:`SpokenReply.render`)."""

    spoken: str
    written: str = ""
    links: tuple[str, ...] = ()


def _section(reply: str, header: str) -> int:
    """Posición donde empieza la sección ``header`` (al inicio o tras línea en blanco)."""

    if reply.startswith(f"{header}\n"):
        return 0
    found = reply.find(f"\n\n{header}\n")
    return -1 if found < 0 else found


def parse_voice_reply(reply: str) -> VoiceReplyParts:
    """Inversa de :meth:`SpokenReply.render`: texto hablado, texto escrito y enlaces."""

    marks = sorted(
        (position, header)
        for header in (TEXT_HEADER, LINKS_HEADER)
        if (position := _section(reply, header)) >= 0
    )
    if not marks:
        return VoiceReplyParts(reply)
    spoken = reply[: marks[0][0]]
    pieces: dict[str, str] = {}
    for index, (position, header) in enumerate(marks):
        begin = reply.index(f"{header}\n", position) + len(header) + 1
        finish = marks[index + 1][0] if index + 1 < len(marks) else len(reply)
        pieces[header] = reply[begin:finish]
    links = tuple(line for line in pieces.get(LINKS_HEADER, "").splitlines() if line.strip())
    return VoiceReplyParts(spoken, pieces.get(TEXT_HEADER, "").strip(), links)


def split_voice_reply(reply: str) -> tuple[str, list[str]]:
    """``(texto hablado, líneas de enlaces)``, sin la sección escrita.

    Para las tres partes, :func:`parse_voice_reply`.
    """

    parts = parse_voice_reply(reply)
    return parts.spoken, list(parts.links)


# --- Piezas sueltas ------------------------------------------------------------------------

_SMALL_NUMBERS = {
    2: "dos",
    3: "tres",
    4: "cuatro",
    5: "cinco",
    6: "seis",
    7: "siete",
    8: "ocho",
    9: "nueve",
    10: "diez",
}
_CURRENCY_WORDS = {"COP": "pesos", "USD": "dólares", "MXN": "pesos mexicanos"}


def speak_money(amount: str, currency: str = "COP") -> str:
    """``$1.234.500`` → ``1.234.500 pesos``; los dígitos se conservan.

    ``currency`` (``COP``, ``USD`` o ``MXN``) da la palabra; un texto que no es un importe
    reconocible se devuelve igual.
    """

    return _money(f"{amount} {currency}".strip())


def speak_folio(folio: str) -> str:
    """Folio legible en voz alta, deletreado por grupos.

    ``C-2026-0042`` → ``C, 2026, 0 0 4 2`` (un año de cuatro cifras se lee como número) y
    ``a3f09c12b7e4`` → ``A 3 F 0, 9 C 1 2, B 7 E 4`` (bloques de cuatro).
    """

    groups: list[str] = []
    for group in re.split(r"[-\s]+", folio.strip().lstrip("#").strip()):
        if not group:
            continue
        if group.isdigit() and len(group) == 4 and group[0] != "0":
            groups.append(group)
            continue
        size = 4 if len(group) > 6 else len(group)
        chunks = [group[index : index + size] for index in range(0, len(group), size)]
        groups.extend(" ".join(chunk.upper()) for chunk in chunks)
    return ", ".join(groups)


FOLIO_TAIL_CHARS = 4
"""Caracteres del final del folio que se dicen en voz alta (:func:`speak_folio_tail`)."""


def speak_folio_tail(folio: str, size: int = FOLIO_TAIL_CHARS) -> str:
    """Los últimos ``size`` caracteres del folio, deletreados uno a uno.

    ``a3f09c12b7e4`` → ``B 7 E 4`` y ``C-2026-0042`` → ``0 0 4 2``: se ignoran guiones,
    espacios y ``#``. Un folio de ``size`` caracteres o menos se deletrea completo. Basta
    para reconocer la cotización en una nota de voz; el folio completo va por escrito.
    """

    characters = re.sub(r"[^0-9A-Za-z]", "", folio)
    return " ".join(characters[-size:].upper())


_CARVAJAL_HOST = re.compile(r"(?:^|\.)carvajal[a-z]*\.(?:com|co)(?:\.[a-z]{2})?$")


def spoken_sources(sources: Sequence[tuple[str, str]]) -> str:
    """Una frase corta con la procedencia, en lugar de la lista ``[n] título — url``.

    ``sources`` son pares ``(título, url)`` ya sin repetidos. Si todas son páginas de
    Carvajal se dice «Según el sitio de Carvajal…»; si no, se nombra el documento (una
    fuente) o se cuentan («Lo saqué de dos documentos»).
    """

    cleaned = [(_plain(title).strip(), url.strip()) for title, url in sources]
    cleaned = [(title, url) for title, url in cleaned if title or url]
    if not cleaned:
        return ""
    hosts = [urlsplit(url).hostname or "" for _, url in cleaned]
    count = len(cleaned)
    if all(hosts) and all(_CARVAJAL_HOST.search(host) for host in hosts):
        site = (
            "el sitio de Carvajal Empaques"
            if all("empaques" in host for host in hosts)
            else "el sitio de Carvajal"
        )
        if count == 1:
            return f"Según {site}."
        return f"Lo saqué de {_SMALL_NUMBERS.get(count, str(count))} páginas del {site[3:]}."
    if count == 1:
        title = cleaned[0][0]
        if title and len(title) <= 60:
            return f"Lo saqué del documento «{to_speech(title).text.rstrip('.')}»."
        return "Lo saqué de un documento consultado."
    return f"Lo saqué de {_SMALL_NUMBERS.get(count, str(count))} documentos."


# --- Limpieza del formato ------------------------------------------------------------------

_MD_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_URL = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]]+", re.IGNORECASE)
_PAREN_URL = re.compile(r"[ \t]*\(\s*((?:https?://|www\.)[^\s()]+)\s*\)", re.IGNORECASE)
_URL_TRAILING = ".,;:!?»”'\""
_CITATION = re.compile(r"[ \t]*\[\d{1,2}(?:\s*[,\u2013-]\s*\d{1,2})*\]")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_QUOTE_MARK = re.compile(r"^\s*>\s?")
_BULLET = re.compile(r"^\s*(?:[-*+•]|(?P<number>\d{1,2})[.)])\s+(?P<item>.+)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_EMPHASIS = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_UNDERSCORE = re.compile(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])")
_STRIKE = re.compile(r"(?<![\w~])~(?=\S)([^~\n]+?)(?<=\S)~(?![\w~])")
_CODE = re.compile(r"`+([^`\n]*)`+")
_SOURCES_TITLE = re.compile(r"^fuentes\s*:?$", re.IGNORECASE)
_SOURCE_LINE = re.compile(
    r"^\[(\d{1,2})\]\s*(?P<title>.*?)(?:\s+[\u2014\u2013-]\s+(?P<url>https?://\S+))?$"
)


def _strip_emoji(text: str) -> str:
    kept = []
    for character in text:
        if character in "\ufe0f\u200d\u20e3":  # selector de variante, ZWJ y tecla
            continue
        if unicodedata.category(character) == "So" and character != "°":
            continue
        kept.append(character)
    return "".join(kept)


def _plain(text: str) -> str:
    """Sin citas, Markdown, formato de WhatsApp ni emojis; no toca cifras ni palabras."""

    result = _CITATION.sub("", text)
    result = _CODE.sub(r"\1", result)
    result = _BOLD.sub(lambda match: match[1] or match[2], result)
    result = _EMPHASIS.sub(r"\1", result)
    result = _UNDERSCORE.sub(r"\1", result)
    result = _STRIKE.sub(r"\1", result)
    result = result.replace("**", "").replace("*", "")
    result = _strip_emoji(result)
    return re.sub(r"[ \t]+", " ", result).strip()


def _trim_url(url: str) -> str:
    while url and url[-1] in _URL_TRAILING:
        url = url[:-1]
    return url


# --- Unidades, importes, fechas y abreviaturas ---------------------------------------------

_NUMBER = r"\d+(?:[.,]\d+)*"
# El punto de una abreviatura se consume solo si la frase sigue; si no, cierra la frase.
_ABBR_DOT = r"(?:\.(?=\s*[),;:]|(?-i:\s+[a-záéíóúñ0-9])))?"
# Importe con signo: miles con punto y centavos con coma (``$1.234.500,50``, como los
# formatea el proyecto), miles con coma y centavos con punto (``$1,234,500.50``, como a
# veces los escribe el modelo) o sin separador de miles (``$500``, ``$12,50``). Si sigue
# otro dígito o separador con dígito, la cifra no encaja en ninguna forma y queda tal cual:
# nunca se parte un importe en dos.
_PRICE = re.compile(
    r"(?:\bCOP\s*)?\$\s?(?:"
    r"(?P<dotted>\d{1,3}(?:\.\d{3})+)(?:,(?P<dotted_cents>\d{1,2}))?"
    r"|(?P<commas>\d{1,3}(?:,\d{3})+)(?:\.(?P<commas_cents>\d{1,2}))?"
    r"|(?P<plain>\d+)(?:[.,](?P<plain_cents>\d{1,2}))?"
    r")(?!\d|[.,]\d)"
    r"(?:\s?(?P<currency>COP|USD|MXN)\b)?"
)
_BARE_CURRENCY = re.compile(r"\b(?P<amount>\d{1,3}(?:\.\d{3})+|\d+)\s?(?P<currency>COP|USD|MXN)\b")
_PRESENTATIONS = (
    "caja|cajas|paquete|paquetes|paca|pacas|bolsa|bolsas|display|displays|fardo|fardos|"
    "rollo|rollos|presentación|presentaciones|empaque|empaques"
)
_UNIT_WORD = rf"(?:und|unds|uds|ud|unid|unidades|unidad|u)\b{_ABBR_DOT}"
_PRESENTATION_X = re.compile(
    rf"\b(?P<what>{_PRESENTATIONS})\s*x\s*(?P<count>{_NUMBER})(?:\s*{_UNIT_WORD})?",
    re.IGNORECASE,
)
_X_UNITS = re.compile(rf"(?<!\w)x\s?(?P<count>{_NUMBER})\s*{_UNIT_WORD}", re.IGNORECASE)
_DIMENSION = re.compile(r"(?<=\d)\s?[x\u00d7]\s?(?=\d)")
_UNITS = {
    "oz": ("onza", "onzas"),
    "onz": ("onza", "onzas"),
    "ml": ("mililitro", "mililitros"),
    "cc": ("centímetro cúbico", "centímetros cúbicos"),
    "lt": ("litro", "litros"),
    "lts": ("litro", "litros"),
    "l": ("litro", "litros"),
    "g": ("gramo", "gramos"),
    "gr": ("gramo", "gramos"),
    "grs": ("gramo", "gramos"),
    "kg": ("kilo", "kilos"),
    "cm": ("centímetro", "centímetros"),
    "mm": ("milímetro", "milímetros"),
    "und": ("unidad", "unidades"),
    "unds": ("unidad", "unidades"),
    "uds": ("unidad", "unidades"),
    "ud": ("unidad", "unidades"),
    "unid": ("unidad", "unidades"),
}
_MEASURE = re.compile(
    rf"(?P<count>{_NUMBER})\s?(?P<unit>{'|'.join(sorted(_UNITS, key=len, reverse=True))})\b"
    rf"{_ABBR_DOT}",
    re.IGNORECASE,
)
_LONE_UNIT = re.compile(rf"\b(?:und|unds|uds)\b{_ABBR_DOT}", re.IGNORECASE)
_PERCENT = re.compile(r"(?<=\d)\s?%")
_MONTHS = {
    "ene": "enero",
    "feb": "febrero",
    "mar": "marzo",
    "abr": "abril",
    "may": "mayo",
    "jun": "junio",
    "jul": "julio",
    "ago": "agosto",
    "sep": "septiembre",
    "oct": "octubre",
    "nov": "noviembre",
    "dic": "diciembre",
}
_SHORT_DATE = re.compile(
    rf"\b(?P<day>\d{{1,2}})\s+(?P<month>{'|'.join(_MONTHS)})\.?\s+(?P<year>\d{{4}})\b",
    re.IGNORECASE,
)
_ABBREVIATIONS = [
    (re.compile(rf"\baprox\b{_ABBR_DOT}", re.IGNORECASE), "aproximadamente"),
    (re.compile(rf"\bp\.\s?ej\b{_ABBR_DOT}", re.IGNORECASE), "por ejemplo"),
    (re.compile(rf"\betc\b{_ABBR_DOT}", re.IGNORECASE), "etcétera"),
    (re.compile(rf"\bmáx\b{_ABBR_DOT}", re.IGNORECASE), "máximo"),
    (re.compile(rf"\bmín\b{_ABBR_DOT}", re.IGNORECASE), "mínimo"),
    (re.compile(r"\bc/u\b", re.IGNORECASE), "cada uno"),
    (re.compile(r"\btel\.\s?", re.IGNORECASE), "teléfono "),
    (re.compile(r"\bcra\.\s?", re.IGNORECASE), "carrera "),
    (re.compile(r"\bav\.\s?", re.IGNORECASE), "avenida "),
    (re.compile(r"\bcl\.\s?", re.IGNORECASE), "calle "),
    (re.compile(r"\bref\.\s?", re.IGNORECASE), "referencia "),
    (re.compile(r"\bN[°º]\s?(?=\d)"), "número "),
    (re.compile(r"\bNo\.\s?(?=\d)"), "número "),
]
_FOLIO_REF = re.compile(
    r"(?P<lead>\b[Ff]olio\s*(?:[Nn][°º.]\s*)?:?\s*#?\s*|#\s?)"
    r"(?P<code>(?=[A-Za-z0-9-]*\d)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)"
)
_HEX_FOLIO = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{12}\b")
_SKU = re.compile(r"\bSKU\b\s*:?\s*(?:[A-Za-z0-9][A-Za-z0-9._/-]*)?", re.IGNORECASE)
# Mismo patrón que ``limpiarTextoHablado`` en la web: códigos técnicos con guion y dígitos.
_TECH_CODE = re.compile(r"\b(?=[A-Za-z0-9-]*\d)[A-Za-z]{2,}(?:-[A-Za-z0-9]+)+\b")


def _plural(count: str, forms: tuple[str, str]) -> str:
    return forms[0] if count in {"1", "1,0"} else forms[1]


def _money(text: str) -> str:
    def price(match: re.Match[str]) -> str:
        word = _CURRENCY_WORDS.get((match["currency"] or "COP").upper(), "pesos")
        if match["commas"]:
            # Miles con coma: se dicen con punto, como el resto de las cifras en español
            # (la voz leería «1,234» como «uno coma dos tres cuatro»). Mismos dígitos.
            amount, cents = match["commas"].replace(",", "."), match["commas_cents"]
        elif match["dotted"]:
            amount, cents = match["dotted"], match["dotted_cents"]
        else:
            amount, cents = match["plain"], match["plain_cents"]
        spoken = f"{amount} {word}"
        if cents and cents.strip("0"):
            spoken += f" con {cents.ljust(2, '0').lstrip('0')} centavos"
        return spoken

    result = _PRICE.sub(price, text)
    return _BARE_CURRENCY.sub(
        lambda match: f"{match['amount']} {_CURRENCY_WORDS[match['currency'].upper()]}", result
    )


def _folio(match: re.Match[str]) -> str:
    """Solo el final del folio: «folio terminado en B 7 E 4», «cotización terminada en…»."""

    lead = match["lead"].strip()
    code = match["code"]
    tail = speak_folio_tail(code)
    if lead.startswith("#"):
        # «#3» es un número; «cotización #a3f09c12b7e4» es un folio.
        if len(code) < 4:
            return f"número {code}"
        after_quote = match.string[: match.start()].rstrip().lower().endswith("cotización")
        return f"terminada en {tail}" if after_quote else f"folio terminado en {tail}"
    word = "Folio" if lead.startswith("F") else "folio"
    return f"{word} terminado en {tail}"


def _date(match: re.Match[str]) -> str:
    return f"{match['day']} de {_MONTHS[match['month'].lower()]} de {match['year']}"


def _speak_inline(text: str) -> str:
    """Una línea ya sin URL: formato fuera y unidades, importes y folios en palabras."""

    result = _plain(text)
    result = _SKU.sub(" ", result)
    result = _FOLIO_REF.sub(_folio, result)
    result = _HEX_FOLIO.sub(
        lambda match: f"el folio terminado en {speak_folio_tail(match[0])}", result
    )
    result = _TECH_CODE.sub(" ", result)
    result = _money(result)
    result = _PRESENTATION_X.sub(
        lambda match: (
            f"{match['what']} de {match['count']} {_plural(match['count'], ('unidad', 'unidades'))}"
        ),
        result,
    )
    result = _X_UNITS.sub(
        lambda match: f"de {match['count']} {_plural(match['count'], ('unidad', 'unidades'))}",
        result,
    )
    result = _DIMENSION.sub(" por ", result)
    result = _MEASURE.sub(
        lambda match: f"{match['count']} {_plural(match['count'], _UNITS[match['unit'].lower()])}",
        result,
    )
    result = _LONE_UNIT.sub("unidades", result)
    result = _PERCENT.sub(" por ciento", result)
    result = _SHORT_DATE.sub(_date, result)
    for pattern, replacement in _ABBREVIATIONS:
        result = pattern.sub(replacement, result)
    result = re.sub(r"\s*(?:→|·|•|\|)\s*", ", ", result)
    result = re.sub(r"\(\s*\)", " ", result)
    result = re.sub(r"[ \t]+", " ", result)
    result = re.sub(r"\s+([,.;:!?)])", r"\1", result)
    result = re.sub(r"\(\s+", "(", result)
    result = re.sub(r",(?:\s*,)+", ",", result)
    return result.strip(" ,;")


# --- Estructura: líneas, listas y fuentes --------------------------------------------------


@dataclass
class _List:
    ordered: bool
    items: list[str]


def _sentence(text: str, *, last: bool = False) -> str:
    text = text.strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    if last and text.endswith(":"):
        return text[:-1].rstrip() + "."
    return text if text[-1] in ".!?…:;" else f"{text}."


def _and(items: Sequence[str]) -> str:
    if len(items) == 1:
        return items[0]
    last = items[-1]
    conjunction = "e" if re.match(r"h?i(?!e)", last, re.IGNORECASE) else "y"
    return f"{', '.join(items[:-1])} {conjunction} {last}"


_ORDINALS = ("Primero", "Luego", "Después")


def _spoken_list(block: _List) -> str:
    items = [item.rstrip(" .;,") for item in block.items if item.strip(" .;,")]
    if not items:
        return ""
    if len(items) == 1:
        return _sentence(items[0])
    if block.ordered:
        sentences = []
        for index, item in enumerate(items):
            if index == len(items) - 1 and len(items) > 2:
                lead = "Por último"
            else:
                lead = _ORDINALS[min(index, len(_ORDINALS) - 1)]
            sentences.append(f"{lead}, {item}.")
        return " ".join(sentences)
    if all(len(item) <= 60 and ":" not in item for item in items):
        return _sentence(_and(items))
    return " ".join(_sentence(item) for item in items)


def _render_paragraph(parts: list[str | _List]) -> str:
    rendered: list[str] = []
    for index, part in enumerate(parts):
        if isinstance(part, _List):
            spoken = _spoken_list(part)
            if spoken:
                rendered.append(spoken)
        else:
            next_is_list = index + 1 < len(parts) and isinstance(parts[index + 1], _List)
            spoken = _sentence(part, last=not next_is_list)
            if spoken:
                rendered.append(spoken)
    return " ".join(rendered)


def _line_links(line: str, previous: str) -> tuple[str, list[SpeechLink]]:
    """Quita las URL de una línea: ``(lo que se lee, enlaces)``.

    Una línea que solo rotula su enlace («📷 Foto: url», «Título — url») deja de leerse y
    su rótulo pasa al enlace; una URL sola toma el rótulo de la línea anterior si esta
    termina en «:». Dentro de una frase, la URL se nombra «el enlace», y la que va entre
    paréntesis (formato de WhatsApp) simplemente se quita.
    """

    links: list[SpeechLink] = []

    def markdown(match: re.Match[str]) -> str:
        links.append(SpeechLink(_plain(match[1]), match[2]))
        return match[1]

    line = _MD_LINK.sub(markdown, line)
    urls: list[str] = []

    def collect(match: re.Match[str]) -> str:
        urls.append(_trim_url(match[1] if match.lastindex else match[0]))
        return "" if match.lastindex else "\x00"

    # «la ficha (url)» (formato de WhatsApp) → «la ficha»; una URL suelta → marcador.
    line = _URL.sub(collect, _PAREN_URL.sub(collect, line))
    if not urls:
        return line, links
    label = _plain(line.replace("\x00", " ")).strip(" :\u2014\u2013-·|,(")
    before, _, after = line.partition("\x00")
    prefix = _plain(before).rstrip()
    is_label = not prefix or prefix.endswith((":", "\u2014", "\u2013", " -"))
    if len(urls) == 1 and "\x00" in line and is_label and not _plain(after).strip(" .,;"):
        if not label and previous.rstrip().endswith(":"):
            label = _plain(previous).rstrip(" :")
        links.append(SpeechLink(label, urls[0]))
        return "", links
    links.extend(SpeechLink("Enlace", url) for url in urls)
    return line.replace("\x00", "el enlace"), links


@dataclass
class _Parsed:
    """Respuesta ya pasada a frases: párrafos (líneas y listas), fuentes y enlaces."""

    paragraphs: list[list[str | _List]]
    sources: list[tuple[str, str]]
    links: tuple[SpeechLink, ...]


class _Heading(str):
    """Línea que era un encabezado: se lee igual, pero no abre un resumen hablado."""


def _unique_links(links: Iterable[SpeechLink]) -> tuple[SpeechLink, ...]:
    unique: list[SpeechLink] = []
    seen: set[str] = set()
    for link in links:
        if link.url not in seen:
            seen.add(link.url)
            unique.append(link)
    return tuple(unique)


def _parse(text: str) -> _Parsed:
    """Markdown o texto de WhatsApp → párrafos hablables, fuentes y enlaces aparte."""

    paragraphs: list[list[str | _List]] = [[]]
    links: list[SpeechLink] = []
    sources: list[tuple[str, str]] = []
    in_sources = False
    previous = ""
    for raw in text.replace("\r\n", "\n").split("\n"):
        stripped = raw.strip()
        if not stripped or _RULE.match(stripped):
            in_sources = False
            if paragraphs[-1]:
                paragraphs.append([])
            continue
        if _SOURCES_TITLE.match(_plain(stripped)):
            in_sources = True
            continue
        if in_sources:
            match = _SOURCE_LINE.match(stripped)
            if match:
                title = _plain(match["title"] or "")
                url = _trim_url(match["url"] or "")
                sources.append((title, url))
                if url:
                    links.append(SpeechLink(title or "Fuente", url))
                continue
            in_sources = False
        line, found = _line_links(raw, previous)
        links.extend(found)
        previous = raw
        if not line.strip():
            continue
        heading = _HEADING.match(line)
        if heading:
            line = heading[1]
        line = _QUOTE_MARK.sub("", line)
        bullet = _BULLET.match(line)
        if bullet:
            item = _speak_inline(bullet["item"])
            current = paragraphs[-1]
            ordered = bullet["number"] is not None
            if current and isinstance(current[-1], _List) and current[-1].ordered == ordered:
                current[-1].items.append(item)
            elif item:
                current.append(_List(ordered, [item]))
            continue
        spoken = _speak_inline(line)
        if spoken:
            paragraphs[-1].append(_Heading(spoken) if heading else spoken)
    return _Parsed([parts for parts in paragraphs if parts], sources, _unique_links(links))


def to_speech(text: str) -> SpokenReply:
    """Markdown del modelo o texto de WhatsApp → texto para el TTS y enlaces aparte.

    Es determinista: no inventa ni redondea cifras, y deja los dígitos para que el TTS
    los lea. Los párrafos se separan con una línea en blanco. Lee **toda** la respuesta;
    para la versión corta, :func:`spoken_summary`.
    """

    parsed = _parse(text)
    rendered = [_render_paragraph(parts) for parts in parsed.paragraphs]
    if parsed.sources:
        rendered.append(spoken_sources(parsed.sources))
    return SpokenReply("\n\n".join(part for part in rendered if part), parsed.links)


# --- Resumen hablado -----------------------------------------------------------------------

# Fin de frase: signo de cierre y espacio antes de una frase nueva. «1.800», «12,5» o
# «10:30» no llevan espacio tras el signo, así que nunca parten una cifra.
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])\s+(?=[¿¡«\"(\[A-ZÁÉÍÓÚÑÜ0-9])")
# Abreviaturas que terminan en punto sin cerrar la frase («S.A. Tiene…», «Sr. López»).
_NOT_AN_END = re.compile(r"(?:\bS\.A|\bS\.A\.S|\b(?:Sr|Sra|Dr|Dra|Ltda|Cía|Ing|Lic))\.$")
# Cabeza de un ítem de lista: lo que va antes de la primera coma, dos puntos, punto y coma,
# raya o paréntesis con espacio. Una coma decimal («1,5 litros») no lleva espacio detrás.
_HEAD_BREAK = re.compile(r",\s|:\s|;\s|\.\s|\s[\u2014\u2013-]\s|\s\(")
# Una introducción de lista así de corta («Estas son las opciones:») no aporta nada que no
# diga ya la frase de la lista; una más larga sí se dice.
_LEAD_IN_WORDS = 6
_LIST_HEADS = 4
_HEAD_WORDS = 8
_MAX_SPOKEN_SOURCES = 5


def _words(text: str) -> int:
    return len(text.split())


def _sentences(text: str) -> list[str]:
    """Frases completas de una línea ya hablable, sin partir cifras ni abreviaturas."""

    pieces: list[str] = []
    start = 0
    for match in _SENTENCE_BREAK.finditer(text):
        piece = text[start : match.start()]
        if _NOT_AN_END.search(piece):
            continue
        pieces.append(piece)
        start = match.end()
    pieces.append(text[start:])
    return [piece.strip() for piece in pieces if piece.strip()]


def _closing(sentence: str) -> str:
    """Una frase que termina en «:» (sin lista detrás) se cierra con punto."""

    return sentence[:-1].rstrip() + "." if sentence.endswith(":") else sentence


def _options(count: int) -> str:
    if count == 1:
        return "una opción"
    return f"{_SMALL_NUMBERS.get(count, str(count))} opciones"


def _item_head(item: str) -> str:
    return _HEAD_BREAK.split(item, maxsplit=1)[0].strip(" .,;:")


def _list_phrase(block: _List, channel: Channel, *, with_heads: bool) -> str:
    """«Te muestro tres opciones en pantalla: A, B y C.» o solo cuántas hay."""

    items = [item for item in block.items if item.strip(" .;,")]
    heads = [_item_head(item) for item in items]
    count = _options(len(items))
    if with_heads:
        listed = _and(heads)
        if channel == "web":
            return f"Te muestro {count} en pantalla: {listed}."
        return f"Te dejo por escrito {count}: {listed}."
    return f"Te dejo {count} en pantalla." if channel == "web" else f"Te dejo {count} por escrito."


def _heads_fit(block: _List) -> bool:
    items = [item for item in block.items if item.strip(" .;,")]
    if not items or len(items) > _LIST_HEADS:
        return False
    return all(1 <= _words(_item_head(item)) <= _HEAD_WORDS for item in items)


def _merge_sources(*groups: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    merged: list[tuple[str, str]] = []
    seen: set[str] = set()
    for group in groups:
        for title, url in group:
            key = (url or title).strip()
            if key and key not in seen:
                seen.add(key)
                merged.append((title, url))
    return merged


def spoken_summary(
    text: str,
    *,
    channel: Channel = "web",
    sources: Sequence[tuple[str, str]] = (),
    budget: int = SUMMARY_WORDS,
) -> SpokenReply:
    """Resumen hablado **extractivo** de una respuesta: unas ``budget`` palabras (~15-20 s).

    Determinista y sin LLM: solo reutiliza frases y cifras que ya están en la respuesta,
    pasadas por las mismas reglas de :func:`to_speech`. Nunca inventa ni parte una cifra.

    - Una respuesta que ya cabe en el presupuesto se lee completa (abstenciones, mensajes
      cortos): ``omitted`` queda en ``False``.
    - Si no cabe: la primera frase del primer párrafo de prosa (siempre, aunque sea larga),
      luego la frase de la lista, la pregunta final al cliente y más frases del primer
      párrafo mientras quepan, en ese orden de prioridad y siempre frases completas.
    - La primera lista no se lee ítem por ítem: con hasta cuatro ítems cortos se nombran
      sus cabezas («Te muestro tres opciones en pantalla: A, B y C.»; en WhatsApp «Te dejo
      por escrito…»); con más, solo cuántas hay. Una introducción corta de la lista
      («Estas son las opciones:») se omite; una larga se dice con punto.
    - Si la respuesta termina con una pregunta al cliente, esa pregunta cierra el resumen.
    - Las fuentes van en una frase (:func:`spoken_sources`, también las de ``sources``,
      pares ``(título, url)``) y las URL nunca se dicen: quedan en ``links``.
    - Si se omitió algo más que el detalle de la lista (que su frase ya remite a la
      pantalla o al texto), se avisa con :data:`SUMMARY_POINTERS` antes de la pregunta.

    El presupuesto cuenta todo lo que se dice; solo la primera frase y la frase corta de la
    lista entran aunque no quepan, así que el resumen nunca pasa del presupuesto en más de
    una frase.
    """

    if channel not in SUMMARY_POINTERS:
        raise ValueError(f"Canal desconocido: {channel!r}")
    parsed = _parse(text)
    listed = _merge_sources(parsed.sources, sources)
    links = _unique_links(
        [*parsed.links, *(SpeechLink(title or "Fuente", url) for title, url in sources if url)]
    )
    source_phrase = spoken_sources(listed[:_MAX_SPOKEN_SOURCES]) if listed else ""
    body = [_render_paragraph(parts) for parts in parsed.paragraphs]
    body = [paragraph for paragraph in body if paragraph]
    if _words(" ".join(body)) <= budget:
        return SpokenReply("\n\n".join([*body, source_phrase]).strip(), links)

    # Elementos en orden: (párrafo, posición, línea de prosa o lista).
    elements = [
        (paragraph, position, part)
        for paragraph, parts in enumerate(parsed.paragraphs)
        for position, part in enumerate(parts)
    ]
    prose = [
        index
        for index, (_, _, part) in enumerate(elements)
        if isinstance(part, str) and not isinstance(part, _Heading)
    ]
    lists = [index for index, (_, _, part) in enumerate(elements) if isinstance(part, _List)]

    # Apertura: las líneas de prosa seguidas del primer párrafo que tiene prosa.
    opening_lines: list[int] = []
    if prose:
        first_paragraph = elements[prose[0]][0]
        for index in range(prose[0], len(elements)):
            paragraph, _, part = elements[index]
            if paragraph != first_paragraph or not isinstance(part, str):
                break
            if not isinstance(part, _Heading):
                opening_lines.append(index)
    opening: list[str] = []
    for index in opening_lines:
        opening.extend(_sentences(_sentence(str(elements[index][2]))))
    next_index = (opening_lines[-1] + 1) if opening_lines else -1
    introduces_list = 0 <= next_index < len(elements) and isinstance(elements[next_index][2], _List)
    if opening and opening[-1].endswith(":") and introduces_list:
        lead_in = opening.pop()
        if _words(lead_in) > _LEAD_IN_WORDS:
            opening.append(_closing(lead_in))
    opening = [_closing(sentence) for sentence in opening]

    # Pregunta final: la última frase de la última línea de prosa, si es una pregunta.
    question = ""
    question_line = -1
    if prose and prose[-1] == len(elements) - 1:
        question_line = prose[-1]
        last = _sentences(_sentence(str(elements[question_line][2])))
        if last and last[-1].endswith("?"):
            question = last[-1]
            if question_line in opening_lines and opening and opening[-1] == question:
                opening.pop()
            extra_in_question_line = len(last) > 1 and question_line not in opening_lines
        else:
            question_line = -1
    if question_line < 0:
        extra_in_question_line = False

    # La frase de la lista ya remite a la pantalla o al texto escrito: con lista, el aviso
    # final sobra. Sin lista, se reservan sus palabras por si hace falta.
    first_list = elements[lists[0]][2] if lists else None
    pointer = SUMMARY_POINTERS[channel] if first_list is None else ""
    used = _words(source_phrase) + _words(pointer)
    chosen: list[str] = []
    if opening:
        chosen.append(opening[0])
        used += _words(opening[0])
    spoken_question = ""
    if question and used + _words(question) <= budget:
        spoken_question = question
        used += _words(question)
    list_text = ""
    if isinstance(first_list, _List):
        full_list = (
            _list_phrase(first_list, channel, with_heads=True) if _heads_fit(first_list) else ""
        )
        list_text = (
            full_list
            if full_list and used + _words(full_list) <= budget
            else _list_phrase(first_list, channel, with_heads=False)
        )
        used += _words(list_text)
    for sentence in opening[1:]:
        if used + _words(sentence) > budget:
            break
        chosen.append(sentence)
        used += _words(sentence)

    other_prose = any(index not in opening_lines and index != question_line for index in prose)
    omitted_more = (
        len(chosen) < len(opening)
        or bool(question and not spoken_question)
        or other_prose
        or extra_in_question_line
        or len(lists) > 1
    )
    if not omitted_more and first_list is None:
        # Nada que resumir (una sola frase larga, por ejemplo): se lee completa.
        return SpokenReply("\n\n".join([*body, source_phrase]).strip(), links)
    list_first = bool(lists) and (not opening_lines or lists[0] < opening_lines[0])
    spoken_body = [list_text, *chosen] if list_first else [*chosen, list_text]
    paragraphs = [
        " ".join(part for part in spoken_body if part),
        source_phrase,
        pointer if omitted_more else "",
        spoken_question,
    ]
    return SpokenReply("\n\n".join(part for part in paragraphs if part), links, omitted=True)


# --- Transcripciones -----------------------------------------------------------------------

# Miles al estilo de EE. UU. («1,800», «12,500,000», «2,500.50»): grupos de tres cifras tras
# la coma, sin espacio, y una primera cifra distinta de cero. Así no se tocan decimales
# («1,5», «0,75», «12,5 onzas»), horas («10:30») ni listas («2, 3 y 4»).
_US_THOUSANDS = re.compile(
    r"(?<![\d.,])(?P<integer>[1-9]\d{0,2}(?:,\d{3})+)(?:\.(?P<decimals>\d+))?(?!\d|[.,]\d)"
)


def normalize_transcript(text: str) -> str:
    """Transcripción del STT → cifras al estilo colombiano, sin tocar nada más.

    Los transcriptores (OpenAI) escriben «mil ochocientos» como «1,800», que en Colombia se
    leería como uno coma ocho. Se reescribe el separador: «1,800» → «1.800», «12,500,000» →
    «12.500.000», «2,500.50» → «2.500,50». Decimales («1,5», «0,75»), horas («10:30») y
    listas con espacio tras la coma («2, 3 y 4») quedan igual. Determinista y sin LLM.
    """

    def colombian(match: re.Match[str]) -> str:
        integer = match["integer"].replace(",", ".")
        return f"{integer},{match['decimals']}" if match["decimals"] else integer

    return _US_THOUSANDS.sub(colombian, text)
