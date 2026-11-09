"""Lectura del *front matter* YAML simple de los Markdown del corpus.

El bloque va delimitado por ``---`` al inicio y contiene pares ``clave: valor``:
el mismo formato que escribe la captura y el que refleja ``data/gold/index.jsonl``.
"""


def parse_front_matter(content: str) -> dict[str, str]:
    """Lee el *front matter* YAML simple del Markdown (clave: valor)."""

    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) != 3:
        return {}
    metadata: dict[str, str] = {}
    for line in parts[1].splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip():
            metadata[key.strip()] = value.strip()
    return metadata


def strip_front_matter(content: str) -> str:
    """Devuelve el cuerpo del Markdown sin el bloque de *front matter*."""

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) == 3:
            return parts[2].strip()
    return content.strip()
