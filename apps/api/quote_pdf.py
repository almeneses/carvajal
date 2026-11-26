"""PDF de una cotización emitida, con la marca de Carvajal (fpdf2, sin dependencias del sistema).

Se genera al vuelo desde el payload guardado en ``quotes`` (ver :mod:`apps.api.quote_view`):
encabezado con el logo, folio y fechas; producto; tabla de importes; vendedor; fuente del
precio y nota legal. Las cifras son las del payload: aquí no se calcula nada.

Recursos en ``apps/api/pdf/``: la marca recortada del logo ``assets/carvajal-logo.svg`` y la
tipografía institucional DM Sans (OFL), convertida a TTF para incrustarla.
"""

from collections.abc import Mapping
from contextlib import suppress
from functools import lru_cache
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from apps.api.quote_view import QuoteView

__all__ = ["DISCLAIMER", "build_quote_pdf"]

ASSETS = Path(__file__).resolve().parent / "pdf"
LOGO = ASSETS / "carvajal-logo-marca.png"
MARGIN = 18
THUMB_MM = 12  # miniatura por fila de la tabla de productos (spec 054)
PHOTO_MM = 34

NAVY = (0, 58, 93)  # #003A5D
CYAN = (0, 181, 229)  # #00B5E5
GREEN = (128, 164, 27)  # #80A41B
ORANGE = (237, 112, 4)  # #ED7004
GREY = (96, 104, 112)
LIGHT = (241, 245, 248)

DISCLAIMER = (
    "Cotización referencial elaborada con precios publicados por el vendedor indicado. "
    "No constituye pedido, reserva ni compromiso de venta. La existencia, el envío y la "
    "vigencia del precio se confirman directamente con el vendedor al momento de comprar."
)

CONVERTED_DISCLAIMER = (
    "Cotización referencial elaborada con un precio de referencia del catálogo. No constituye "
    "pedido, reserva ni compromiso de venta. La existencia, el envío y el precio final se "
    "confirman con un distribuidor al momento de comprar."
)


class _QuotePDF(FPDF):
    def footer(self) -> None:
        self.set_y(-18)
        self.set_draw_color(*CYAN)
        self.set_line_width(0.6)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.set_y(-15)
        self.set_font("DMSans", size=7.5)
        self.set_text_color(*GREY)
        self.cell(0, 5, "Carvajal Empaques · Asistente comercial", align="L")
        self.set_x(self.l_margin)
        self.cell(0, 5, f"Página {self.page_no()}", align="R")


def _units(value: int | None) -> str:
    return "—" if value is None else f"{value:,}".replace(",", ".")


def _rows(view: QuoteView) -> list[tuple[str, str]]:
    if view.lines:  # spec 054: el detalle por producto va en la tabla de productos
        return [
            ("Subtotal de productos", view.subtotal or "Pendiente"),
            ("IVA", view.tax),
            ("Envío", view.shipping),
        ]
    presentation = view.basis.capitalize() if view.basis else "Presentación"
    if view.units_per_presentation:
        presentation += f" x {_units(view.units_per_presentation)} unidades"
    rows = [
        ("Presentación", presentation),
        ("Precio por presentación", view.price_per_presentation or "—"),
        ("Unidades solicitadas", _units(view.units_requested)),
    ]
    if view.presentations is not None:
        rows.append(
            (
                "Presentaciones a comprar",
                f"{_units(view.presentations)} {view.presentations_label}".strip(),
            )
        )
    rows.append(("Unidades compradas", _units(view.units_bought)))
    if view.units_surplus:
        rows.append(("Unidades sobrantes", _units(view.units_surplus)))
    if view.unit_price:
        rows.append(("Precio unitario de referencia", view.unit_price))
    rows += [
        ("Subtotal", view.subtotal or "Pendiente"),
        ("IVA", view.tax),
        ("Envío", view.shipping),
    ]
    return rows


def _fit(pdf: FPDF, text: str, width: float) -> str:
    """El texto recortado con «…» para que quepa en ``width`` mm con la fuente actual."""

    if pdf.get_string_width(text) <= width:
        return text
    while text and pdf.get_string_width(text + "…") > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _lines_table(pdf: FPDF, view: QuoteView, photos: Mapping[str, Path]) -> None:
    """Spec 054: una fila por producto (foto, nombre, cantidad, presentación y subtotal).

    Con alguna foto en ``photos`` (SKU → archivo del catálogo) la tabla abre una columna
    de miniaturas y las filas crecen a su alto; sin fotos queda como antes.
    """

    width = pdf.w - pdf.l_margin - pdf.r_margin
    thumbs = any(line.sku in photos for line in view.lines)
    thumb_w = THUMB_MM + 2 if thumbs else 0.0
    row_h = THUMB_MM + 2 if thumbs else 7.0
    rest = width - thumb_w
    columns = (("Producto", 0.46, "L"), ("Unidades", 0.14, "R"), ("Compras", 0.2, "R"),
               ("Subtotal", 0.2, "R"))  # fmt: skip
    pdf.set_fill_color(*NAVY)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("DMSans", "B", 9)
    if thumbs:
        pdf.cell(thumb_w, 7, "", fill=True)
    for label, share, align in columns:
        pdf.cell(rest * share, 7, f" {label} ", fill=True, align=align)
    pdf.ln(7)
    pdf.set_draw_color(220, 226, 232)
    pdf.set_line_width(0.2)
    for index, line in enumerate(view.lines):
        fill = index % 2 == 0
        pdf.set_fill_color(*LIGHT)
        pdf.set_text_color(30, 30, 30)
        pdf.set_font("DMSans", size=9)
        bought = f"{_units(line.presentations)} {line.presentations_label}".strip()
        if line.units_per_presentation:
            bought += f" x {_units(line.units_per_presentation)}"
        values = (
            line.product,
            _units(line.units_requested),
            bought,
            line.subtotal or "Pendiente",
        )
        if thumbs:
            x, y = pdf.get_x(), pdf.get_y()
            pdf.cell(thumb_w, row_h, "", border="B", fill=fill)
            photo = photos.get(line.sku)
            if photo is not None:
                # Foto ilegible: la celda queda vacía y el PDF sale igual.
                with suppress(OSError, RuntimeError, ValueError):
                    pdf.image(
                        str(photo), x=x + 1, y=y + 1, w=THUMB_MM, h=THUMB_MM, keep_aspect_ratio=True
                    )
        for (_, share, align), value in zip(columns, values, strict=True):
            text = _fit(pdf, value, rest * share - 3)
            pdf.cell(rest * share, row_h, f" {text} ", border="B", fill=fill, align=align)
        pdf.ln(row_h)


def _label_value(pdf: FPDF, label: str, value: str, *, width: float = 38) -> None:
    pdf.set_font("DMSans", "B", 9)
    pdf.set_text_color(*NAVY)
    pdf.cell(width, 5.5, label)
    pdf.set_font("DMSans", size=9)
    pdf.set_text_color(40, 40, 40)
    pdf.multi_cell(0, 5.5, value, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _section(pdf: FPDF, title: str) -> None:
    pdf.ln(3)
    pdf.set_font("DMSans", "B", 11)
    pdf.set_text_color(*NAVY)
    pdf.cell(0, 7, title.upper(), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(*GREEN)
    pdf.set_line_width(0.5)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + 18, pdf.get_y())
    pdf.ln(2.5)


@lru_cache(maxsize=128)
def _seller_and_source(pdf: FPDF, view: QuoteView) -> None:
    """Vendedor y fuente del precio de una cotización de un solo producto."""

    _section(pdf, "Precio de referencia" if view.converted else "Vendedor")

    if view.seller:
        _label_value(pdf, "Nombre", view.seller, width=24)
    if view.price_city:
        # El precio lo publica el vendedor en otra ciudad que la de la cotización.
        _label_value(pdf, "Ciudad", f"{view.price_city} (precio publicado allí)", width=24)
    if view.seller_address:
        _label_value(pdf, "Dirección", view.seller_address, width=24)
    if view.seller_phone:
        _label_value(pdf, "Teléfono", view.seller_phone, width=24)

    _section(pdf, "Fuente del precio")
    source = view.source_name or view.seller or "Vendedor"
    if view.price_date:
        source += f", {'del' if view.converted else 'publicado el'} {view.price_date}"
    _label_value(pdf, "Origen", source, width=24)
    if view.source_url:
        pdf.set_font("DMSans", "B", 9)
        pdf.set_text_color(*NAVY)
        pdf.cell(24, 5.5, "Enlace")
        pdf.set_font("DMSans", size=8)
        pdf.set_text_color(*CYAN)
        pdf.multi_cell(
            0, 5, view.source_url, link=view.source_url, new_x=XPos.LMARGIN, new_y=YPos.NEXT
        )


def _lines_sources(pdf: FPDF, view: QuoteView) -> None:
    """Spec 054: quién publica el precio de cada producto, con su enlace.

    Antes solo salía la fuente del primero, aunque el segundo fuera de otro vendedor.
    """

    _section(pdf, "Vendedor y fuente de cada producto")
    for index, line in enumerate(view.lines, start=1):
        if line.converted:
            origin = "Precio de referencia del catálogo"
            if line.price_date:
                origin += f", del {line.price_date}"
        else:
            origin = line.seller or "Vendedor"
            if line.price_city:
                origin += f" en {line.price_city}"
            if line.price_date:
                origin += f", publicado el {line.price_date}"
        pdf.set_font("DMSans", "B", 9)
        name = _fit(pdf, line.product, pdf.w - pdf.l_margin - pdf.r_margin - 8)
        pdf.set_text_color(*NAVY)
        pdf.multi_cell(0, 5, f"{index}. {name}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("DMSans", size=8.5)
        pdf.set_text_color(60, 60, 60)
        pdf.multi_cell(0, 4.5, f"   {origin}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if line.source_url:
            pdf.set_font("DMSans", size=8)
            pdf.set_text_color(*CYAN)
            pdf.set_x(pdf.l_margin + 3)
            pdf.multi_cell(
                0, 4.5, line.source_url, link=line.source_url,
                new_x=XPos.LMARGIN, new_y=YPos.NEXT,
            )  # fmt: skip
        pdf.ln(1)


def build_quote_pdf(
    view: QuoteView,
    *,
    photo: Path | None = None,
    line_photos: Mapping[str, Path] | None = None,
    compress: bool = True,
) -> bytes:
    """Bytes del PDF de la cotización (A4, una página en los casos habituales).

    ``photo`` es la foto del catálogo (``carvajal.catalog.images``); sin ella, el bloque
    del producto ocupa todo el ancho. Con varios productos, ``line_photos`` (SKU → foto)
    pone una miniatura en cada fila de la tabla.
    """

    pdf = _QuotePDF(format="A4", unit="mm")
    pdf.set_compression(compress)
    pdf.set_margins(MARGIN, 16, MARGIN)
    pdf.set_auto_page_break(auto=True, margin=22)
    pdf.add_font("DMSans", "", str(ASSETS / "DMSans-Regular.ttf"))
    pdf.add_font("DMSans", "B", str(ASSETS / "DMSans-Bold.ttf"))
    pdf.set_title(f"Cotización {view.folio}")
    pdf.set_author("Carvajal Empaques")
    pdf.set_creator("Asistente comercial Carvajal")
    pdf.add_page()

    # Encabezado: logo a la izquierda, folio y fechas a la derecha.
    top = pdf.get_y()
    pdf.image(str(LOGO), x=pdf.l_margin, y=top, w=52)
    pdf.set_xy(pdf.w - pdf.r_margin - 80, top)
    pdf.set_font("DMSans", "B", 16)
    pdf.set_text_color(*NAVY)
    pdf.cell(80, 8, "Cotización referencial", align="R", new_x=XPos.LEFT, new_y=YPos.NEXT)
    pdf.set_font("DMSans", "B", 11)
    pdf.set_text_color(*ORANGE)
    pdf.cell(80, 6, f"Folio #{view.folio}", align="R", new_x=XPos.LEFT, new_y=YPos.NEXT)
    pdf.set_font("DMSans", size=9)
    pdf.set_text_color(*GREY)
    if view.issued:
        pdf.cell(80, 5, f"Emitida: {view.issued}", align="R", new_x=XPos.LEFT, new_y=YPos.NEXT)
    if view.valid_until:
        pdf.cell(
            80, 5, f"Válida hasta: {view.valid_until}", align="R", new_x=XPos.LEFT, new_y=YPos.NEXT
        )
    pdf.set_y(max(pdf.get_y(), top + 22) + 4)
    pdf.set_draw_color(*CYAN)
    pdf.set_line_width(0.8)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(2)

    if view.lines:
        _section(pdf, f"Productos ({len(view.lines)})")
        _lines_table(pdf, view, line_photos or {})
        if view.city:
            pdf.ln(1.5)
            _label_value(pdf, "Ciudad", view.city, width=24)
    else:
        _section(pdf, "Producto")
    if not view.lines:
        start = pdf.get_y()
        text_x = pdf.l_margin
        if photo is not None:
            try:
                pdf.image(
                    str(photo),
                    x=pdf.l_margin,
                    y=start,
                    w=PHOTO_MM,
                    h=PHOTO_MM,
                    keep_aspect_ratio=True,
                )
                pdf.set_draw_color(220, 226, 232)
                pdf.set_line_width(0.2)
                pdf.rect(pdf.l_margin, start, PHOTO_MM, PHOTO_MM)
                text_x = pdf.l_margin + PHOTO_MM + 6
            except (OSError, RuntimeError, ValueError):  # foto ilegible: el PDF sale sin ella
                text_x = pdf.l_margin
        pdf.set_left_margin(text_x)
        pdf.set_xy(text_x, start)
        pdf.set_font("DMSans", "B", 12)
        pdf.set_text_color(30, 30, 30)
        pdf.multi_cell(0, 6.5, view.product, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        details = [
            ("SKU", view.sku),
            ("Marca", view.brand),
            ("Material", view.material),
            ("Ciudad", view.city),
        ]
        for label, value in details:
            if value:
                _label_value(pdf, label, value, width=24)
        pdf.set_left_margin(MARGIN)
        if text_x != MARGIN:
            pdf.set_y(max(pdf.get_y(), start + PHOTO_MM) + 1)

    # Importes.
    _section(pdf, "Detalle de la cotización")
    width = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.set_draw_color(220, 226, 232)
    pdf.set_line_width(0.2)
    for index, (label, value) in enumerate(_rows(view)):
        fill = index % 2 == 0
        pdf.set_fill_color(*LIGHT)
        pdf.set_font("DMSans", size=9.5)
        pdf.set_text_color(60, 60, 60)
        pdf.cell(width * 0.55, 7, f"  {label}", border="B", fill=fill)
        pdf.set_font("DMSans", "B", 9.5)
        pdf.set_text_color(30, 30, 30)
        pdf.cell(
            width * 0.45,
            7,
            f"{value}  ",
            border="B",
            fill=fill,
            align="R",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
    pdf.set_fill_color(*NAVY)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("DMSans", "B", 12)
    total = f"{view.total} {view.currency}" if view.total else "Pendiente"
    pdf.cell(width * 0.55, 10, "  TOTAL", fill=True)
    pdf.cell(
        width * 0.45, 10, f"{total}  ", fill=True, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT
    )
    if view.pending:
        pdf.ln(1.5)
        pdf.set_font("DMSans", size=8.5)
        pdf.set_text_color(*ORANGE)
        for line in view.pending:
            pdf.multi_cell(0, 4.5, f"• {line}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    if view.lines:
        _lines_sources(pdf, view)
    else:
        _seller_and_source(pdf, view)

    # Nota legal.
    pdf.ln(5)
    pdf.set_font("DMSans", size=8)
    pdf.set_text_color(*GREY)
    note = CONVERTED_DISCLAIMER if view.converted else DISCLAIMER
    if view.warning:
        note = f"{view.warning} {note}"
    pdf.multi_cell(0, 4.2, note, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    return bytes(pdf.output())
