"""Tarjetas de producto de la ruta comercial (spec 036, T028-T029).

El nodo ``commercial`` deja su resultado estructurado en ``tool_result``; aquí se convierte en
el HTML de ``_productos.html``, que el servicio envía en el evento SSE ``products``. Se
renderiza en el servidor con el autoescape de Jinja para que el navegador solo inserte HTML
ya escapado.
"""

from typing import Any

import structlog

from apps.api.runtime import templates

__all__ = ["has_cards", "render_products", "safe_render_products"]

logger = structlog.get_logger(__name__)


def has_cards(result: dict[str, Any]) -> bool:
    """Indica si el resultado tiene algo que mostrar como tarjeta."""

    channel = result.get("channel") or {}
    return bool(
        result.get("options")
        or result.get("contacts")
        or result.get("catalog_note")
        or channel.get("distributors")
        or channel.get("cities_with_distributors")
    )


def render_products(result: dict[str, Any]) -> str:
    """HTML de las tarjetas para un resultado de la ruta comercial."""

    return templates.env.get_template("_productos.html").render(resultado=result).strip()


def safe_render_products(result: dict[str, Any]) -> str:
    """Como ``render_products``, pero ``""`` si el parcial falla.

    Las tarjetas son un adorno: un parcial roto no debe tumbar la respuesta de texto que
    se está transmitiendo. El fallo queda en el log para corregir la plantilla.
    """

    try:
        return render_products(result)
    except Exception as error:
        logger.warning("chat.products_render_failed", error=str(error))
        return ""
