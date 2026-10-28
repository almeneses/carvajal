"""Modelos del dominio del corpus web."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse


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

        path = urlparse(self.url).path.strip("/")
        segment = path.rsplit("/", 1)[-1] if path else "home"
        segment = segment.replace("-", "_").replace(".", "")
        return segment or "home"
