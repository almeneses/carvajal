"""Comprobaciones deterministas de evidencia para el RAG.

Los embeddings pequeños dan scores altos incluso a preguntas fuera del corpus.
Esta comprobación léxica es barata: si el usuario nombra un lugar o una entidad
que ningún fragmento recuperado contiene, no hay evidencia suficiente aunque el
score coseno sea alto.
"""

__all__ = ["proper_entities_supported"]

_QUESTION_WORDS = {
    "cuál",
    "cual",
    "qué",
    "que",
    "cómo",
    "como",
    "cuándo",
    "cuando",
    "dónde",
    "donde",
    "cuántos",
    "cuantos",
    "cuántas",
    "cuantas",
    "quién",
    "quien",
    "cuáles",
    "cuales",
    "en",
    "de",
    "la",
    "el",
    "los",
    "las",
}


def proper_entities_supported(question: str, texts: list[str]) -> bool:
    """Indica si las entidades nombradas de la pregunta aparecen en el texto.

    Detecta dos formas: palabras capitalizadas y términos largos después de
    preposiciones («en tokio», «de liquidezya»). Así funciona también si el
    usuario escribe todo en minúsculas, sin convertir palabras comunes en
    entidades.
    """

    context = " ".join(texts).casefold()
    entities = _entities(question)
    return all(entity.casefold() in context for entity in entities)


_PREPOSITIONS = {"en", "de", "para", "sobre", "desde", "hasta", "con"}


def _entities(question: str) -> list[str]:
    """Extrae candidatos a entidad de una pregunta en español."""

    words = [raw.strip("¿?¡!,.;:()[]«»\"'") for raw in question.split()]
    entities: list[str] = []
    for position, word in enumerate(words):
        if not word or word.casefold() in _QUESTION_WORDS:
            continue
        capitalized = word[:1].isupper() and position != 0
        after_preposition = (
            position > 0
            and words[position - 1].casefold() in _PREPOSITIONS
            and len(word) >= 5
            and word.casefold() not in _QUESTION_WORDS
        )
        if (capitalized and len(word) >= 3) or after_preposition:
            entities.append(word)
    return entities
