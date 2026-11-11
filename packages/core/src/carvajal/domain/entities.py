"""Entidades del dominio del corpus web, sin dependencias de IO.

``CapturedPage`` es la página capturada por el pipeline. ``Document``, ``Chunk``
y ``DocumentVersion`` son el modelo mínimo que consumen los contratos de
persistencia y recuperación de W0.4 y W1.3.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from carvajal.domain.rules import (
    OFFICIAL_DOMAIN,
    content_hash,
    domain_from_url,
    estimate_tokens,
    is_official_domain,
    section_from_url,
    slug_from_url,
)

__all__ = ["OFFICIAL_DOMAIN", "CapturedPage", "Chunk", "Document", "DocumentVersion"]


@dataclass(frozen=True)
class CapturedPage:
    """Página pública capturada del sitio, con su Markdown listo para curar."""

    url: str
    html: str
    markdown: str
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def slug(self) -> str:
        """Devuelve el slug legible de la página para el entregable Gold."""

        return slug_from_url(self.url)

    @property
    def domain(self) -> str:
        """Dominio de la página, sin el prefijo ``www.``."""

        return domain_from_url(self.url)

    @property
    def is_official(self) -> bool:
        """Indica si la página pertenece al sitio oficial de Carvajal."""

        return is_official_domain(self.domain)

    @property
    def section(self) -> str:
        """Sección editorial derivada de la URL (``home`` o ``prensa`` si es tercero)."""

        return section_from_url(self.url)

    @property
    def content_hash(self) -> str:
        """Huella SHA-256 del Markdown, usada para deduplicar el corpus."""

        return content_hash(self.markdown)

    @property
    def tokens_estimate(self) -> int:
        """Estimación burda de tokens (≈4 caracteres por token)."""

        return estimate_tokens(self.markdown)


@dataclass(frozen=True)
class Document:
    """Documento del corpus listo para persistir o recuperar.

    Modelo mínimo que usarán los contratos de persistencia/recuperación; no
    incluye HTML crudo ni lógica de captura.
    """

    id: str
    url: str
    title: str
    slug: str
    section: str
    layer: str
    markdown: str
    content_hash: str
    captured_at: datetime
    source_type: str = "oficial"


@dataclass(frozen=True)
class Chunk:
    """Fragmento de un documento, unidad mínima de recuperación.

    Modelo mínimo que usarán los contratos de persistencia/recuperación:
    identifica el documento de origen y su posición dentro de él.
    """

    id: str
    document_id: str
    text: str
    position: int
    section: str
    layer: str


@dataclass(frozen=True)
class DocumentVersion:
    """Copia inmutable del contenido anterior de un documento.

    Se archiva cuando un ``upsert`` cambia el ``content_hash``; conserva el
    Markdown, la huella y la fecha de captura que tenía el documento entonces.
    """

    id: str
    document_id: str
    markdown: str
    content_hash: str
    captured_at: datetime
    created_at: datetime
