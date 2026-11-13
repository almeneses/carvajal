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
    """Indica si todos los nombres propios de la pregunta aparecen en el texto."""

    context = " ".join(texts).casefold()
    entities = []
    for position, raw in enumerate(question.split()):
        word = raw.strip("¿?¡!,.;:()[]«»\"'")
        if not word or word.casefold() in _QUESTION_WORDS:
            continue
        if position == 0 and word[:1].isupper():
            # La primera palabra suele ser el interrogativo capitalizado.
            continue
        if word[:1].isupper() and len(word) >= 3:
            entities.append(word)
    return all(entity.casefold() in context for entity in entities)
