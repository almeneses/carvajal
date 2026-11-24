"""Texto apto para voz (TTS) a partir del Markdown del modelo o del formato de WhatsApp.

Lo usa el canal de WhatsApp cuando el cliente habló (``consultar_carvajal`` con
``voz=true``): la pasarela convierte la respuesta en nota de voz y lo que aquí se devuelve
es lo que se oye. Todo es determinista y sin LLM; no se inventan ni se redondean cifras.

Reglas (español de Colombia), alineadas con la lectura en voz del chat web
(``apps/api/static/chat-voice.js``, ``limpiarTextoHablado``):

- sin citas ``[n]``, sin Markdown ni formato de WhatsApp, sin emojis;
- las URL nunca se leen: se separan en :attr:`SpokenReply.links` para enviarlas como texto;
  un enlace dentro de una frase se nombra como «el enlace»;
- las fuentes se resumen en una frase (:func:`spoken_sources`), no en una lista;
- unidades y abreviaturas en palabras («12 oz» → «12 onzas», «x 200 und» → «de 200
  unidades», «aprox.» → «aproximadamente»); los importes conservan los dígitos
  («$1.234.500» → «1.234.500 pesos»), que el TTS lee bien;
- el folio se deletrea por grupos (:func:`speak_folio`) y los códigos técnicos (SKU) se
  omiten, como en la web;
- las viñetas pasan a una frase («a, b y c») y las listas numeradas a «primero… luego…».
"""

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

__all__ = [
    "LINKS_HEADER",
    "SpeechLink",
    "SpokenReply",
    "combine",
    "speak_folio",
    "speak_money",
    "split_voice_reply",
    "spoken_sources",
    "to_speech",
]

LINKS_HEADER = "--- ENLACES (enviar como texto, no leer en voz alta) ---"
"""Separa el texto hablado de los enlaces en :meth:`SpokenReply.render`."""


@dataclass(frozen=True)
class SpeechLink:
    """Enlace que no se lee en voz alta: se envía aparte, como texto."""

    label: str
    url: str

    def line(self) -> str:
        return f"{self.label}: {self.url}" if self.label else self.url


@dataclass(frozen=True)
class SpokenReply:
    """Texto para el TTS y los enlaces que acompañan la nota de voz por escrito."""

    text: str
    links: tuple[SpeechLink, ...] = ()

    def render(self) -> str:
        """El texto hablado y, si hay enlaces, la sección :data:`LINKS_HEADER` con uno por línea."""

        if not self.links:
            return self.text
        listed = "\n".join(link.line() for link in self.links)
        return (
            f"{self.text}\n\n{LINKS_HEADER}\n{listed}" if self.text else f"{LINKS_HEADER}\n{listed}"
        )


def combine(parts: Iterable[SpokenReply]) -> SpokenReply:
    """Une varias respuestas: párrafos separados por línea en blanco y enlaces sin repetir."""

    texts: list[str] = []
    links: list[SpeechLink] = []
    seen: set[str] = set()
    for part in parts:
        if part.text:
            texts.append(part.text)
        for link in part.links:
            if link.url not in seen:
                seen.add(link.url)
                links.append(link)
    return SpokenReply("\n\n".join(texts), tuple(links))


def split_voice_reply(reply: str) -> tuple[str, list[str]]:
    """Inversa de :meth:`SpokenReply.render`: ``(texto hablado, líneas de enlaces)``."""

    marker = f"{LINKS_HEADER}\n"
    if reply.startswith(marker):
        return "", reply[len(marker) :].splitlines()
    spoken, found, links = reply.partition(f"\n\n{marker}")
    return spoken, links.splitlines() if found else []


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
_PRICE = re.compile(
    r"(?:\bCOP\s*)?\$\s?(?P<amount>\d{1,3}(?:\.\d{3})+|\d+)(?:,(?P<cents>\d{1,2}))?"
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
        cents = match["cents"]
        spoken = f"{match['amount']} {word}"
        if cents and cents.strip("0"):
            spoken += f" con {cents.ljust(2, '0').lstrip('0')} centavos"
        return spoken

    result = _PRICE.sub(price, text)
    return _BARE_CURRENCY.sub(
        lambda match: f"{match['amount']} {_CURRENCY_WORDS[match['currency'].upper()]}", result
    )


def _folio(match: re.Match[str]) -> str:
    lead = match["lead"].strip()
    code = match["code"]
    if lead.startswith("#"):
        # «#3» es un número; «cotización #a3f09c12b7e4» es un folio.
        if len(code) < 4:
            return f"número {code}"
        after_quote = match.string[: match.start()].rstrip().lower().endswith("cotización")
        return f"{'con folio' if after_quote else 'folio'} {speak_folio(code)}"
    word = "Folio" if lead.startswith("F") else "folio"
    return f"{word} {speak_folio(code)}"


def _date(match: re.Match[str]) -> str:
    return f"{match['day']} de {_MONTHS[match['month'].lower()]} de {match['year']}"


def _speak_inline(text: str) -> str:
    """Una línea ya sin URL: formato fuera y unidades, importes y folios en palabras."""

    result = _plain(text)
    result = _SKU.sub(" ", result)
    result = _FOLIO_REF.sub(_folio, result)
    result = _HEX_FOLIO.sub(lambda match: speak_folio(match[0]), result)
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


def to_speech(text: str) -> SpokenReply:
    """Markdown del modelo o texto de WhatsApp → texto para el TTS y enlaces aparte.

    Es determinista: no inventa ni redondea cifras, y deja los dígitos para que el TTS
    los lea. Los párrafos se separan con una línea en blanco.
    """

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
            paragraphs[-1].append(spoken)
    rendered = [_render_paragraph(parts) for parts in paragraphs if parts]
    if sources:
        rendered.append(spoken_sources(sources))
    unique: list[SpeechLink] = []
    seen: set[str] = set()
    for link in links:
        if link.url not in seen:
            seen.add(link.url)
            unique.append(link)
    return SpokenReply("\n\n".join(part for part in rendered if part), tuple(unique))
