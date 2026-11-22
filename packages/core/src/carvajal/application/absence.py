"""Temas sin evidencia para el backlog del corpus (spec de prompts, 2026-11-21).

Cuando el RAG no encuentra respaldo, la pregunta se registra como un hueco de
cobertura. El tema se normaliza y se limpia de datos de contacto para poder
agruparlo sin guardar correos, teléfonos, URLs ni identificadores largos.
"""

import re
import unicodedata

__all__ = ["normalize_topic"]

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_PHONE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
_LONG_NUMBER = re.compile(r"\b\d{6,}\b")
_WORD = re.compile(r"[a-z0-9ñ]+")
_STOPWORDS = {
    "que",
    "cual",
    "cuales",
    "como",
    "cuando",
    "donde",
    "quien",
    "cuanto",
    "cuanta",
    "cuantos",
    "cuantas",
    "para",
    "por",
    "con",
    "del",
    "las",
    "los",
    "una",
    "uno",
    "unos",
    "unas",
    "sobre",
    "hay",
    "esta",
    "este",
    "son",
    "fue",
    "ser",
    "mas",
    "pero",
    "de",
    "en",
    "es",
    "la",
    "el",
    "se",
    "su",
    "al",
    "queda",
    "dice",
    "dicen",
    "tiene",
    "tienen",
    "puede",
    "pueden",
    "hace",
    "hacen",
    "era",
    "estan",
}


def normalize_topic(question: str, *, limit: int = 8) -> str:
    """Tema corto y comparable, sin datos de contacto ni palabras de pregunta."""

    text = _EMAIL.sub(" ", question)
    text = _URL.sub(" ", text)
    text = _PHONE.sub(" ", text)
    text = _LONG_NUMBER.sub(" ", text)
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    words = [word for word in _WORD.findall(plain) if word not in _STOPWORDS and len(word) > 1]
    return " ".join(words[:limit])
