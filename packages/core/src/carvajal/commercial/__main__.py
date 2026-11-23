"""Consulta de línea de comandos del contrato comercial.

Ejemplos::

    uv run python -m carvajal.commercial validar
    uv run python -m carvajal.commercial sedes --ciudad "Bogotá"
    uv run python -m carvajal.commercial sedes --ciudad "Bogotá" --json
    uv run python -m carvajal.commercial ofertas --sku CO-CTT-08
    uv run python -m carvajal.commercial vincular --sku CO-CTT-08
    uv run python -m carvajal.commercial paridad-mx
    uv run python -m carvajal.commercial esquema \\
        --out docs/contratos/comerciales/contrato.schema.json

No escribe datos: solo lee el catálogo, los precios publicados y la evidencia versionada.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from carvajal.commercial import (
    CommercialBook,
    ContractDocument,
    ContractError,
    load_commercial_book,
    mexico_parity,
)

__all__ = ["main"]


def _json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _as_of(raw: str | None) -> date | None:
    return date.fromisoformat(raw) if raw else None


def _validar() -> int:
    book = load_commercial_book()
    summary = book.summary()
    print("Contrato comercial válido")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    for city in book.cities():
        directory = book.city_directory(city)
        print(
            f"  {city}: {directory.counts.get('contacto_directorio', 0)} sedes, "
            f"{directory.counts.get('oferta_publicada', 0)} ofertas publicadas, "
            f"{directory.counts.get('stock_verificado', 0)} con stock verificado"
        )
    return 0


def _sedes(book: CommercialBook, args: argparse.Namespace) -> int:
    if args.ciudad:
        directory = book.city_directory(args.ciudad, as_of=_as_of(args.as_of))
        if args.json:
            _json(directory.model_dump(mode="json"))
            return 0
        print(f"Directorio de {directory.city} ({len(directory.records)} registros)")
        for record in directory.records:
            if record.record_type != "contacto_directorio":
                continue
            site = record.site
            assert site is not None
            related = [
                item
                for item in directory.records
                if item.site and item.site.site_id == site.site_id
            ]
            kinds = ("oferta_publicada", "stock_verificado")
            counts = {
                kind: sum(1 for item in related if item.record_type == kind) for kind in kinds
            }
            precision = site.location.precision if site.location else "sin geocodificar"
            print(
                f"  {site.site_id} · {site.name} · {site.address or '(sin dirección)'} · "
                f"{site.phone} · mapa:{record.map.mode} ({precision}) · "
                f"ofertas:{counts['oferta_publicada']} · "
                f"stock verificado:{counts['stock_verificado']}"
            )
        return 0

    if args.json:
        _json(
            {
                "cities": book.cities(),
                "sedes": {
                    city: book.city_directory(city, as_of=_as_of(args.as_of)).model_dump(
                        mode="json"
                    )
                    for city in book.cities()
                },
            }
        )
        return 0
    print("Ciudades con sedes (la consulta no exige precio ni stock):")
    for city in book.cities():
        directory = book.city_directory(city)
        print(
            f"  {city}: {directory.counts.get('contacto_directorio', 0)} sedes, "
            f"{directory.counts.get('oferta_publicada', 0)} ofertas publicadas, "
            f"{directory.counts.get('stock_verificado', 0)} con stock verificado"
        )
    return 0


def _ofertas(book: CommercialBook, args: argparse.Namespace) -> int:
    records = book.offers_for_sku(args.sku, args.country, as_of=_as_of(args.as_of))
    if args.json:
        _json([item.model_dump(mode="json") for item in records])
        return 0 if records else 1
    if not records:
        print(f"{args.sku}: no hay ofertas en el contrato.")
        return 1
    for record in records:
        offer = record.offer
        assert offer is not None
        site = record.site
        print(
            f"{record.record_type} · {offer.offer_id} · {record.seller.name}"
            f" · {site.city if site else offer.city or 'sin sede'}"
            f" · {offer.presentation.label}"
            f" · {offer.amount} {offer.currency}"
            f" · IVA {offer.tax_status}"
            f" · disponibilidad {record.availability.status if record.availability else '-'}"
            f" · fuente {offer.source.name} ({offer.source.captured_at.isoformat()})"
        )
    return 0


def _vincular(book: CommercialBook, args: argparse.Namespace) -> int:
    links = book.sites_for_sku(args.sku, args.country, as_of=_as_of(args.as_of))
    if args.json:
        _json([item.model_dump(mode="json") for item in links])
        return 0 if links else 1
    if not links:
        print(f"{args.sku}: ninguna sede vinculada por oferta o stock.")
        return 1
    for link in links:
        site = book.site(link.site_id)
        assert site is not None
        print(
            f"{link.site_id} · {site.name} · {site.city} · {link.relationship}"
            f" · disponibilidad {link.availability.status}"
            f" · ofertas {', '.join(link.offer_ids) or '-'}"
        )
    return 0


def _paridad() -> int:
    report = mexico_parity()
    _json(report.model_dump(mode="json"))
    print("PARIDAD OK" if report.ok else "PARIDAD FALLIDA", file=sys.stderr)
    return 0 if report.ok else 1


def _esquema(args: argparse.Namespace) -> int:
    schema = ContractDocument.model_json_schema()
    text = json.dumps(schema, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"Esquema escrito en {path}")
    else:
        print(text, end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m carvajal.commercial")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validar", help="carga el libro y muestra conteos por ciudad")

    sedes = sub.add_parser("sedes", help="lista el directorio por ciudad, sin exigir precios")
    sedes.add_argument("--ciudad")
    sedes.add_argument("--json", action="store_true")
    sedes.add_argument("--as-of", help="fecha ISO para degradar evidencia vencida")

    ofertas = sub.add_parser("ofertas", help="lista todas las ofertas de un SKU")
    ofertas.add_argument("--sku", required=True)
    ofertas.add_argument("--country", choices=("CO", "MX"))
    ofertas.add_argument("--json", action="store_true")
    ofertas.add_argument("--as-of")

    vincular = sub.add_parser("vincular", help="sedes vinculadas a un SKU")
    vincular.add_argument("--sku", required=True)
    vincular.add_argument("--country", choices=("CO", "MX"))
    vincular.add_argument("--json", action="store_true")
    vincular.add_argument("--as-of")

    sub.add_parser("paridad-mx", help="compara la lectura inline de México con el contrato")
    esquema = sub.add_parser("esquema", help="imprime el JSON Schema del contrato")
    esquema.add_argument("--out")

    args = parser.parse_args(argv)
    try:
        if args.command == "validar":
            return _validar()
        book = load_commercial_book()
        if args.command == "sedes":
            return _sedes(book, args)
        if args.command == "ofertas":
            return _ofertas(book, args)
        if args.command == "vincular":
            return _vincular(book, args)
        if args.command == "paridad-mx":
            return _paridad()
        if args.command == "esquema":
            return _esquema(args)
    except ContractError as error:
        print(f"Contrato inválido: {error}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
