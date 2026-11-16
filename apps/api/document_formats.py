"""Extracción de PDF y formatos de datos para las cargas del corpus."""

import csv
import json
import os
import re
import selectors
import subprocess
from io import StringIO
from tempfile import TemporaryFile
from time import monotonic
from xml.etree import ElementTree

EXTRACTED_EXTENSIONS = {".csv", ".json", ".jsonl", ".pdf", ".tsv", ".xml"}
MAX_PDF_TEXT_BYTES = 16 * 1024 * 1024
PDF_TIMEOUT_SECONDS = 20


class _NoDoctype(ElementTree.TreeBuilder):
    def doctype(self, _name: str, _pubid: str | None, _system: str | None) -> None:
        raise ValueError("DTD no admitida.")


def extract_markdown(suffix: str, data: bytes) -> str:
    """Extrae texto por página o representa los datos estructurados en Markdown."""

    if suffix == ".pdf":
        return _pdf_markdown(data)
    try:
        text = _structured_text(suffix, data)
        text.encode("utf-8")  # JSON puede contener un sustituto Unicode sin pareja.
    except (ValueError, LookupError, RecursionError, csv.Error, ElementTree.ParseError) as error:
        raise ValueError(f"El archivo {suffix} no es válido.") from error

    if not text.strip():
        return ""
    fence_length = max((len(match[0]) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, fence_length + 1)
    return f"{fence}{suffix[1:]}\n{text}\n{fence}"


def _structured_text(suffix: str, data: bytes) -> str:
    if suffix == ".xml":
        parser = ElementTree.XMLParser(target=_NoDoctype())
        root = ElementTree.fromstring(data, parser)
        return ElementTree.tostring(root, encoding="unicode")
    if suffix == ".json":
        return json.dumps(json.loads(data), ensure_ascii=False)

    text = data.decode("utf-8-sig")
    if suffix == ".jsonl":
        return "\n".join(
            json.dumps(json.loads(line), ensure_ascii=False)
            for line in text.splitlines()
            if line.strip()
        )
    for _ in csv.reader(
        StringIO(text, newline=""), delimiter="\t" if suffix == ".tsv" else ",", strict=True
    ):
        pass
    return text.strip("\r\n")


def _pdf_text(data: bytes) -> bytes:
    # Un archivo de entrada evita bloquear la salida mientras Poppler lee.
    with TemporaryFile() as source:
        source.write(data)
        source.seek(0)
        with subprocess.Popen(
            ["pdftotext", "-layout", "-enc", "UTF-8", "-", "-"],
            stdin=source,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ) as process:
            try:
                return _read_pdf_output(process)
            finally:
                if process.poll() is None:
                    process.kill()


def _read_pdf_output(process: subprocess.Popen[bytes]) -> bytes:
    deadline = monotonic() + PDF_TIMEOUT_SECONDS
    output = bytearray()
    assert process.stdout is not None
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise TimeoutError("Se agotó el tiempo de extracción del PDF.")
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > MAX_PDF_TEXT_BYTES:
                raise ValueError("El texto extraído del PDF supera el límite de 16 MB.")

    if process.wait(timeout=max(0, deadline - monotonic())):
        raise ValueError("No se pudo leer el PDF. Puede estar dañado o protegido.")
    return bytes(output)


def _pdf_markdown(data: bytes) -> str:
    try:
        pages = _pdf_text(data).decode("utf-8", errors="replace").split("\f")
    except FileNotFoundError as error:
        raise ValueError("Falta pdftotext para extraer el PDF. Instala poppler-utils.") from error
    except (TimeoutError, subprocess.TimeoutExpired) as error:
        raise ValueError("Se agotó el tiempo de extracción del PDF.") from error

    # shortcut: extrae la capa de texto, añade OCR si se necesita indexar PDF escaneado.
    markdown = "\n\n".join(
        f"## Página {number}\n\n{page.strip()}"
        for number, page in enumerate(pages, 1)
        if page.strip()
    )
    if not markdown:
        raise ValueError("El PDF no tiene texto extraíble. Puede requerir OCR.")
    return markdown
