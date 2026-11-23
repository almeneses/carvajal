"""Paridad de México antes de retirar la lectura de precios inline.

La spec 039 dejó el precio de México en ``referencias.csv`` (98 fichas) y el de Colombia en
``precios.csv``. El contrato nuevo expone ambos como ofertas, pero no retira la lectura
inline: esta comparación demuestra que la cobertura, la moneda, la presentación, el importe,
la fuente y la fecha siguen siendo los mismos antes de que alguien decida migrarla.

La comparación cuenta **referencias** y **ofertas**, no claves de un diccionario: si hubiera
dos ofertas MX del mismo SKU, la duplicación se reporta y ``ok`` falla. El denominador es el
total de referencias MX (98), no solo las que aún tienen precio, para que perder una ficha no
rebaje el listón.
"""

from collections import defaultdict

from carvajal.catalog.loader import Catalog, load_catalog
from carvajal.commercial.book import CommercialBook
from carvajal.commercial.loader import load_commercial_book
from carvajal.commercial.models import Offer, ParityReport

__all__ = ["mexico_parity"]


def _mx_offers(book: CommercialBook) -> list[Offer]:
    return [
        offer
        for offer in book.offers
        if offer.country == "MX" and offer.published and offer.source.type == "oficial"
    ]


def mexico_parity(
    *,
    book: CommercialBook | None = None,
    catalog: Catalog | None = None,
) -> ParityReport:
    """Compara las 98 referencias auditadas contra las ofertas del contrato."""

    data = catalog or load_catalog()
    commercial = book or load_commercial_book()
    mx_refs = {
        reference.sku: reference for reference in data.references if reference.country == "MX"
    }
    inline = {sku: reference for sku, reference in mx_refs.items() if reference.price is not None}
    offers = _mx_offers(commercial)
    by_sku: dict[str, list[Offer]] = defaultdict(list)
    for offer in offers:
        by_sku[offer.sku].append(offer)

    missing = tuple(sorted(mx_refs.keys() - by_sku.keys()))
    extra = tuple(sorted(by_sku.keys() - mx_refs.keys()))
    duplicate = tuple(sorted(sku for sku, items in by_sku.items() if len(items) > 1))
    currency: list[str] = []
    presentation: list[str] = []
    amount: list[str] = []
    tax: list[str] = []
    source: list[str] = []
    dates: list[str] = []
    validity: list[str] = []
    for sku, reference in inline.items():
        candidates = by_sku.get(sku, [])
        if not candidates:
            continue
        offer = candidates[0]
        if offer.currency != "MXN" or offer.currency != reference.currency:
            currency.append(f"{sku}: {offer.currency} vs {reference.currency}")
        if (
            offer.presentation.basis != "caja"
            or offer.presentation.units_in_basis != reference.units_per_box
        ):
            presentation.append(
                f"{sku}: {offer.presentation.label} vs caja de {reference.units_per_box}"
            )
        if offer.amount != reference.price:
            amount.append(f"{sku}: {offer.amount} vs {reference.price}")
        expected_tax = "incluido" if reference.tax_included else "no_incluido"
        if offer.tax_status != expected_tax:
            tax.append(f"{sku}: {offer.tax_status} vs {expected_tax}")
        if offer.source.url != reference.source_url:
            source.append(f"{sku}: {offer.source.url} vs {reference.source_url}")
        if offer.source.captured_at != reference.captured_at:
            dates.append(f"{sku}: {offer.source.captured_at} vs {reference.captured_at}")
        if offer.source.valid_until != reference.price_valid_until:
            validity.append(f"{sku}: {offer.source.valid_until} vs {reference.price_valid_until}")
    return ParityReport(
        audited_references=len(mx_refs),
        inline_offers=len(inline),
        contract_offers=len(offers),
        missing_skus=missing,
        extra_skus=extra,
        duplicate_skus=duplicate,
        currency_mismatches=tuple(currency),
        presentation_mismatches=tuple(presentation),
        amount_mismatches=tuple(amount),
        tax_mismatches=tuple(tax),
        source_mismatches=tuple(source),
        date_mismatches=tuple(dates),
        valid_until_mismatches=tuple(validity),
        details=(
            "La lectura inline de referencias.csv se conserva; el contrato la expone como "
            "oferta oficial por caja.",
            f"Referencias MX auditadas: {len(mx_refs)}.",
            f"Referencias MX con precio inline: {len(inline)}.",
            f"Ofertas MX oficiales en el contrato: {len(offers)} (SKU únicos: {len(by_sku)}).",
            "La comparación exige moneda MXN, presentación caja con las unidades de la "
            "referencia, importe idéntico, IVA declarado, URL de fuente, fecha de captura y "
            "vigencia idénticas.",
        ),
    )
