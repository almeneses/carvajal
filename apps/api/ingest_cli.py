"""Consola de ingesta de una carpeta local al corpus (spec 035, US3).

Recorre carpetas y subcarpetas, ignora las extensiones no admitidas y procesa
los archivos en lotes por número y bytes. Cada archivo pasa por Bronze, Silver y
Gold; al cerrar el lote, Gold se importa a PostgreSQL y se indexa en Qdrant.
``--solo-extraer`` se detiene en Gold para revisar la extracción antes de tocar
la base y el índice.

Uso:
    python -m apps.api.ingest_cli /ruta/a/carpeta --source-type oficial
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from apps.api.ingestion import ALLOWED_EXTENSIONS, IngestReport, UploadedDocument, ingest_uploads
from carvajal.infrastructure.settings import Settings

__all__ = ["DEFAULT_BATCH_FILES", "DEFAULT_MAX_FILE_MB", "main", "run"]

DEFAULT_BATCH_FILES = 25
DEFAULT_MAX_FILE_MB = 100
DEFAULT_BATCH_BYTES = 256 * 1024 * 1024
SOURCE_TYPES = ("oficial", "tercero", "colaborador")


@dataclass(frozen=True)
class Batch:
    """Archivos que se procesan en una sola pasada de las capas."""

    documents: list[UploadedDocument]
    errors: tuple[str, ...] = ()


def run(
    rutas: list[Path],
    *,
    solo_extraer: bool = False,
    source_type: str = "colaborador",
    max_file_bytes: int = DEFAULT_MAX_FILE_MB * 1024 * 1024,
    lote: int = DEFAULT_BATCH_FILES,
    settings: Settings | None = None,
    max_batch_bytes: int = DEFAULT_BATCH_BYTES,
) -> IngestReport:
    """Procesa las rutas y devuelve el acumulado de todos los lotes."""

    settings = settings or Settings()
    archivos, collect_errors = _collect(rutas)
    if not archivos:
        return IngestReport(errors=(*collect_errors, "No se encontraron archivos admitidos."))

    total = IngestReport(errors=tuple(collect_errors))
    for batch in _batches(archivos, lote, max_batch_bytes, max_file_bytes):
        report = ingest_uploads(
            batch.documents,
            settings=settings,
            max_file_bytes=max_file_bytes,
            solo_extraer=solo_extraer,
            source_type=source_type,
        )
        total = _sum(total, report, batch.errors)
        if batch.documents:
            print(
                f"Lote: {len(batch.documents)} archivos · Gold {report.gold} · "
                f"PostgreSQL {report.documents} · Qdrant {report.indexed}",
                flush=True,
            )
        for error in (*batch.errors, *report.errors):
            print(error, file=sys.stderr)
    return total


def main(argv: list[str] | None = None) -> int:
    """Punto de entrada de la consola; devuelve 0 solo si todo se procesó."""

    parser = argparse.ArgumentParser(
        prog="python -m apps.api.ingest_cli",
        description="Importa al corpus los archivos de una carpeta y sus subcarpetas.",
    )
    parser.add_argument("rutas", nargs="+", type=Path, help="Carpetas o archivos a procesar.")
    parser.add_argument(
        "--solo-extraer",
        action="store_true",
        help="Se detiene en Gold: no toca PostgreSQL ni Qdrant.",
    )
    parser.add_argument(
        "--source-type",
        choices=SOURCE_TYPES,
        default="colaborador",
        help="Procedencia que se escribe en el front matter de cada documento.",
    )
    parser.add_argument(
        "--max-file-mb",
        type=int,
        default=DEFAULT_MAX_FILE_MB,
        help=f"Límite por archivo en MB (por defecto {DEFAULT_MAX_FILE_MB}).",
    )
    parser.add_argument(
        "--lote",
        type=int,
        default=DEFAULT_BATCH_FILES,
        help=f"Archivos por lote (por defecto {DEFAULT_BATCH_FILES}).",
    )
    parser.add_argument(
        "--max-batch-mb",
        type=int,
        default=DEFAULT_BATCH_BYTES // 1024 // 1024,
        help=(
            f"Bytes acumulados por lote en MB (por defecto {DEFAULT_BATCH_BYTES // 1024 // 1024})."
        ),
    )
    args = parser.parse_args(argv)

    report = run(
        args.rutas,
        solo_extraer=args.solo_extraer,
        source_type=args.source_type,
        max_file_bytes=max(1, args.max_file_mb) * 1024 * 1024,
        lote=max(1, args.lote),
        max_batch_bytes=max(1, args.max_batch_mb) * 1024 * 1024,
    )
    print(
        f"\nResumen: {report.files} archivos · Bronze {report.bronze} · "
        f"Silver {report.silver} · Gold {report.gold} · "
        f"PostgreSQL {report.documents} · Qdrant {report.indexed}"
    )
    if report.errors:
        print(f"Errores: {len(report.errors)}", file=sys.stderr)
        return 1
    return 0 if report.gold else 1


def _collect(rutas: list[Path]) -> tuple[list[Path], list[str]]:
    """Lista los archivos admitidos en orden estable, sin duplicados."""

    archivos: list[Path] = []
    errores: list[str] = []
    for ruta in rutas:
        if ruta.is_file():
            candidatos = [ruta]
        elif ruta.is_dir():
            candidatos = sorted(path for path in ruta.rglob("*") if path.is_file())
        else:
            errores.append(f"{ruta}: la ruta no existe.")
            continue
        for path in candidatos:
            if path.suffix.lower() in ALLOWED_EXTENSIONS:
                archivos.append(path)
    return archivos, errores


def _batches(
    archivos: list[Path],
    lote: int,
    max_batch_bytes: int,
    max_file_bytes: int,
) -> Iterator[Batch]:
    """Genera lotes leyendo cada archivo al llegar a su lote.

    Es un generador a propósito: cargar de golpe el corpus completo mantiene
    en memoria todos los bytes a la vez y el contenedor muere por OOM (137)
    en cuanto la carpeta supera su límite. Así la memoria viva se acota al
    lote en curso.
    """

    actual: list[UploadedDocument] = []
    errores: list[str] = []
    bytes_lote = 0

    for path in archivos:
        try:
            data = path.read_bytes()
        except OSError as error:
            errores.append(f"{path.name}: no se pudo leer ({error.strerror or error}).")
            continue
        if len(data) > max_file_bytes:
            errores.append(f"{path.name}: supera el límite de {max_file_bytes // 1024 // 1024} MB.")
            continue
        if actual and (len(actual) >= lote or bytes_lote + len(data) > max_batch_bytes):
            yield Batch(documents=actual, errors=tuple(errores))
            actual, errores, bytes_lote = [], [], 0
        actual.append(UploadedDocument(filename=path.name, data=data))
        bytes_lote += len(data)

    if actual or errores:
        yield Batch(documents=actual, errors=tuple(errores))


def _sum(total: IngestReport, report: IngestReport, errors: tuple[str, ...]) -> IngestReport:
    """Acumula los conteos de un lote en el informe general."""

    return IngestReport(
        files=total.files + report.files,
        bronze=total.bronze + report.bronze,
        silver=total.silver + report.silver,
        gold=total.gold + report.gold,
        documents=total.documents + report.documents,
        chunks=total.chunks + report.chunks,
        indexed=total.indexed + report.indexed,
        errors=(*total.errors, *errors, *report.errors),
    )


if __name__ == "__main__":
    raise SystemExit(main())
