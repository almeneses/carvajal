"""Extracción de texto de los formatos que admite la carga local.

Los PDF se leen con ``pdftotext`` (poppler-utils) conservando el número de
página; los datos estructurados se validan con la biblioteca estándar y se
guardan como texto íntegro delimitado por su formato. Solo HTML, Markdown y TXT
usan el decodificador tolerante.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import subprocess
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

from carvajal.domain.frontmatter import strip_front_matter

__all__ = [
    "EXTRACTED_EXTENSIONS",
    "HTML_EXTENSIONS",
    "TEXT_EXTENSIONS",
    "ExtractError",
    "extract_markdown",
]

EXTRACTED_EXTENSIONS = {".pdf", ".xml", ".json", ".jsonl", ".csv", ".tsv"}
HTML_EXTENSIONS = {".html", ".htm"}
TEXT_EXTENSIONS = {".md", ".markdown", ".txt"}
PDF_TIMEOUT_SECONDS = 20
PDF_MAX_TEXT_BYTES = 16 * 1024 * 1024

_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "div",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "hr",
    "li",
    "main",
    "p",
    "pre",
    "section",
    "table",
    "td",
    "th",
    "tr",
    "ul",
    "ol",
}


class ExtractError(ValueError):
    """Archivo no extraíble; el mensaje se muestra en el informe del lote."""


class _HtmlText(HTMLParser):
    """Extrae texto visible y título de un HTML sin dependencias externas."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._partes: list[str] = []
        self._ignorar = 0
        self._en_titulo = False
        self.titulo = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._ignorar += 1
            return
        if tag == "title":
            self._en_titulo = True
            return
        if tag in _BLOCK_TAGS:
            self._partes.append("\n")
        elif tag in {"span", "a", "strong", "em", "b", "i", "code"}:
            self._partes.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._ignorar = max(0, self._ignorar - 1)
            return
        if tag == "title":
            self._en_titulo = False
            return
        if tag in _BLOCK_TAGS:
            self._partes.append("\n")
        elif tag in {"span", "a", "strong", "em", "b", "i", "code"}:
            self._partes.append(" ")

    def handle_data(self, data: str) -> None:
        if self._ignorar:
            return
        if self._en_titulo:
            self.titulo += data
        else:
            self._partes.append(data)

    def texto(self) -> str:
        """Normaliza las líneas y conserva párrafos separados."""

        lineas = [linea.strip() for linea in "".join(self._partes).splitlines()]
        return "\n\n".join(linea for linea in lineas if linea)


def extract_markdown(filename: str, data: bytes) -> tuple[str, str]:
    """Devuelve ``(markdown, título)`` según la extensión del archivo.

    Raises:
        ExtractError: si el archivo no se puede validar o extraer.
    """

    suffix = Path(filename).suffix.lower()
    if suffix in HTML_EXTENSIONS:
        return _html(data, filename)
    if suffix == ".pdf":
        return _pdf(data), Path(filename).stem
    if suffix == ".xml":
        return _xml(data), Path(filename).stem
    if suffix == ".json":
        return _json(data), Path(filename).stem
    if suffix == ".jsonl":
        return _jsonl(data), Path(filename).stem
    if suffix in {".csv", ".tsv"}:
        return _tabular(data, suffix), Path(filename).stem
    if suffix in TEXT_EXTENSIONS:
        return _text(data, filename)
    raise ExtractError(f"formato no admitido ({suffix or 'sin extensión'})")


def _decode(data: bytes) -> str:
    """Decodifica UTF-8 con tolerancia para archivos de texto heredados."""

    return data.decode("utf-8-sig", errors="replace")


def _html(data: bytes, filename: str) -> tuple[str, str]:
    """Extrae el texto visible y el ``<title>`` de un HTML."""

    parser = _HtmlText()
    parser.feed(_decode(data))
    parser.close()
    markdown = parser.texto()
    titulo = html.unescape(parser.titulo).strip()
    if not titulo:
        titulo = _titulo_desde_markdown(markdown) or Path(filename).stem
    return markdown, titulo


def _text(data: bytes, filename: str) -> tuple[str, str]:
    """Quita el *front matter* de Markdown y TXT y elige el título."""

    markdown = strip_front_matter(_decode(data)).strip()
    return markdown, _titulo_desde_markdown(markdown) or Path(filename).stem


def _titulo_desde_markdown(markdown: str) -> str:
    """Toma el primer encabezado del Markdown si existe."""

    match = re.search(r"^\s*#{1,6}\s+(.+)$", markdown, re.MULTILINE)
    return match.group(1).strip()[:140] if match else ""


def _pdf(data: bytes) -> str:
    """Extrae un PDF por página con ``pdftotext`` y conserva el número de página."""

    try:
        result = subprocess.run(
            ["pdftotext", "-layout", "-enc", "UTF-8", "-", "-"],
            input=data,
            capture_output=True,
            timeout=PDF_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as error:
        raise ExtractError(
            "falta pdftotext (paquete poppler-utils) para leer PDF; "
            "instálalo con `apt-get install poppler-utils`."
        ) from error
    except subprocess.TimeoutExpired as error:
        raise ExtractError(f"el PDF superó {PDF_TIMEOUT_SECONDS} s de extracción.") from error
    if result.returncode != 0:
        raise ExtractError("el PDF está dañado, protegido o no se pudo leer.")
    texto = result.stdout.decode("utf-8", errors="replace")
    if len(result.stdout) > PDF_MAX_TEXT_BYTES:
        raise ExtractError(f"el texto del PDF supera {PDF_MAX_TEXT_BYTES // 1024 // 1024} MB.")

    # pdftotext separa las páginas con un salto de formulario (\f).
    paginas = texto.split("\f")
    if paginas and not paginas[-1].strip():
        paginas.pop()
    secciones = [
        f"## Página {numero}\n\n{contenido.strip()}"
        for numero, contenido in enumerate(paginas, start=1)
        if contenido.strip()
    ]
    if not secciones:
        raise ExtractError("el PDF no tiene texto extraíble; puede requerir OCR previo.")
    return "\n\n".join(secciones)


def _xml(data: bytes) -> str:
    """Valida un XML sin resolver DTD ni entidades y lo conserva como texto."""

    lowered = data.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ExtractError("el XML declara DTD o entidades; no se admiten.")
    try:
        ET.fromstring(data)
    except ET.ParseError as error:
        raise ExtractError("el XML no está bien formado.") from error
    return f"```xml\n{_decode(data).strip()}\n```"


def _json(data: bytes) -> str:
    """Valida un JSON y lo guarda legible sin alterar los datos."""

    try:
        payload = json.loads(data.decode("utf-8-sig"))
        contenido = json.dumps(payload, ensure_ascii=False, indent=2)
        contenido.encode("utf-8")
    except (UnicodeDecodeError, json.JSONDecodeError, UnicodeEncodeError) as error:
        raise ExtractError("el JSON no está bien formado.") from error
    return f"```json\n{contenido}\n```"


def _jsonl(data: bytes) -> str:
    """Valida un JSONL línea a línea y conserva las líneas originales."""

    try:
        texto = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ExtractError("el JSONL no es UTF-8 válido.") from error
    lineas = [line for line in texto.splitlines() if line.strip()]
    if not lineas:
        raise ExtractError("el JSONL está vacío.")
    for numero, line in enumerate(lineas, start=1):
        try:
            json.loads(line)
        except json.JSONDecodeError as error:
            raise ExtractError(f"el JSONL no está bien formado (línea {numero}).") from error
    return "```jsonl\n" + "\n".join(lineas) + "\n```"


def _tabular(data: bytes, suffix: str) -> str:
    """Valida un CSV o TSV y lo conserva como texto delimitado."""

    try:
        texto = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ExtractError(f"el {suffix[1:].upper()} no es UTF-8 válido.") from error
    delimiter = "\t" if suffix == ".tsv" else ","
    try:
        list(csv.reader(io.StringIO(texto), delimiter=delimiter, strict=True))
    except csv.Error as error:
        raise ExtractError(f"el {suffix[1:].upper()} no está bien formado.") from error
    if not texto.strip():
        raise ExtractError(f"el {suffix[1:].upper()} está vacío.")
    return f"```{suffix[1:]}\n{texto.strip()}\n```"
