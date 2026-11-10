"""Lectura del *front matter* YAML simple de los Markdown del corpus.

El bloque va delimitado por líneas ``---`` al inicio y contiene pares
``clave: valor``: el mismo formato que escribe la captura y el que refleja
``data/gold/index.jsonl``. Es deliberadamente simple: no soporta YAML
multilínea, listas ni claves con espacios.
"""


def _split_front_matter(content: str) -> tuple[dict[str, str], str]:
    """Separa los metadatos del cuerpo exigiendo líneas delimitadoras completas.

    Un ``---`` embebido en un valor o al inicio del cuerpo no cierra el bloque;
    solo cuenta una línea cuyo contenido, sin espacios, sea ``---``. Si no hay
    delimitador de cierre, no hay metadatos y se devuelve el contenido íntegro.
    """

    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content.strip()
    for index in range(1, len(lines)):
        if lines[index].strip() != "---":
            continue
        metadata: dict[str, str] = {}
        for line in lines[1:index]:
            key, separator, value = line.partition(":")
            if separator and key.strip():
                metadata[key.strip()] = value.strip()
        return metadata, "\n".join(lines[index + 1 :]).strip()
    return {}, content.strip()


def parse_front_matter(content: str) -> dict[str, str]:
    """Lee el *front matter* YAML simple del Markdown (clave: valor)."""

    return _split_front_matter(content)[0]


def strip_front_matter(content: str) -> str:
    """Devuelve el cuerpo del Markdown sin el bloque de *front matter*."""

    return _split_front_matter(content)[1]
