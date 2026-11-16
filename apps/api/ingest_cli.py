"""Carga documentos y directorios locales desde la consola."""

import argparse
import sys
from collections.abc import Iterator
from pathlib import Path

from apps.api.ingestion import ALLOWED_EXTENSIONS, MAX_FILES, UploadedDocument, ingest_uploads

MAX_LOCAL_MB = 100
MAX_LOCAL_BYTES = MAX_LOCAL_MB * 1024 * 1024


def _files(path: Path) -> Iterator[Path]:
    if not path.is_dir():
        yield path
        return
    yield from (
        item
        for item in sorted(path.rglob("*"))
        if item.is_file() and item.suffix.lower() in ALLOWED_EXTENSIONS
    )


def _process(batch: list[UploadedDocument], solo_extraer: bool) -> bool:
    report = ingest_uploads(batch, solo_extraer=solo_extraer, max_file_bytes=MAX_LOCAL_BYTES)
    print(
        f"Bronze: {report.bronze} | Silver: {report.silver} | Gold: {report.gold} | "
        f"PostgreSQL: {report.documents} | Qdrant: {report.indexed}"
    )
    for error in report.errors:
        print(error, file=sys.stderr)
    return not report.ok


def main(argv: list[str] | None = None) -> int:
    """Recorre carpetas y procesa lotes de tamaño acotado."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rutas", type=Path, nargs="+", help="Archivos o carpetas (recursivas).")
    parser.add_argument("--solo-extraer", action="store_true", help="Escribe hasta Gold.")
    args = parser.parse_args(argv)

    files = dict.fromkeys(file for path in args.rutas for file in _files(path))
    if not files:
        print("No se encontraron archivos admitidos.", file=sys.stderr)
        return 1

    batch: list[UploadedDocument] = []
    size = 0
    failed = False
    for file in files:
        if file.suffix.lower() not in ALLOWED_EXTENSIONS:
            print(f"{file}: formato no admitido.", file=sys.stderr)
            failed = True
            continue
        try:
            with file.open("rb") as source:
                data = source.read(MAX_LOCAL_BYTES + 1)
        except OSError as error:
            print(f"{file}: {error.strerror}", file=sys.stderr)
            failed = True
            continue
        if len(data) > MAX_LOCAL_BYTES:
            print(f"{file}: supera el límite de {MAX_LOCAL_MB} MB.", file=sys.stderr)
            failed = True
            continue
        if len(batch) == MAX_FILES or size + len(data) > MAX_LOCAL_BYTES:
            failed |= _process(batch, args.solo_extraer)
            batch.clear()
            size = 0
        batch.append(UploadedDocument(file.name, data))
        size += len(data)

    if batch:
        failed |= _process(batch, args.solo_extraer)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
