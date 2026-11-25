"""PDF de una cotización emitida, con la marca de Carvajal (fpdf2, sin dependencias del sistema).

Se genera al vuelo desde el payload guardado en ``quotes`` (ver :mod:`apps.api.quote_view`):
encabezado con el logo, folio y fechas; producto; tabla de importes; vendedor; fuente del
precio y nota legal. Las cifras son las del payload: aquí no se calcula nada.

Recursos en ``apps/api/pdf/``: la marca recortada del logo ``assets/carvajal-logo.svg`` y la
tipografía institucional DM Sans (OFL), convertida a TTF para incrustarla.
"""

from functools import lru_cache
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from apps.api.quote_view import QuoteView

__all__ = ["DISCLAIMER", "build_quote_pdf"]

ASSETS = Path(__file__).resolve().parent / "pdf"
LOGO = ASSETS / "carvajal-logo-marca.png"
MARGIN = 18
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
    "Cotización referencial elaborada con un precio convertido desde el catálogo de México. "
    "No constituye pedido, reserva ni compromiso de venta."
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
def build_quote_pdf(view: QuoteView, *, photo: Path | None = None, compress: bool = True) -> bytes:
    """Bytes del PDF de la cotización (A4, una página en los casos habituales).

    ``photo`` es la foto del catálogo (``carvajal.catalog.images``); sin ella, el bloque
    del producto ocupa todo el ancho.
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

    # Producto: foto a la izquierda (si hay) y datos a la derecha.
    _section(pdf, "Producto")
    start = pdf.get_y()
    text_x = pdf.l_margin
    if photo is not None:
        try:
            pdf.image(
                str(photo), x=pdf.l_margin, y=start, w=PHOTO_MM, h=PHOTO_MM, keep_aspect_ratio=True
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

    # Vendedor y fuente.
    _section(pdf, "Precio de referencia" if view.converted else "Vendedor")
    if view.seller:
        _label_value(pdf, "Nombre", view.seller, width=24)
    if view.seller_address:
        _label_value(pdf, "Dirección", view.seller_address, width=24)
    if view.seller_phone:
        _label_value(pdf, "Teléfono", view.seller_phone, width=24)

    _section(pdf, "Fuente del precio")
    source = view.source_name or view.seller or "Vendedor"
    if view.price_date:
        source += f", {'convertido' if view.converted else 'publicado'} el {view.price_date}"
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

    # Nota legal.
    pdf.ln(5)
    pdf.set_font("DMSans", size=8)
    pdf.set_text_color(*GREY)
    note = CONVERTED_DISCLAIMER if view.converted else DISCLAIMER
    if view.warning:
        note = f"{view.warning} {note}"
    pdf.multi_cell(0, 4.2, note, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    return bytes(pdf.output())
