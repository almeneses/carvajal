"""Reglas puras del corpus web: derivación de metadatos y deduplicación.

Este módulo no hace IO ni conoce el pipeline: solo transforma cadenas. Así las
reglas se pueden probar y reutilizar desde la fachada legacy, la CLI o los
puertos de persistencia sin arrastrar clientes HTTP, archivos ni LLMs.
"""

import hashlib
from urllib.parse import urlparse

OFFICIAL_DOMAIN = "carvajal.com"
"""Dominio raíz del sitio oficial de Carvajal."""

DEFAULT_MIN_DEDUPE_CHARS = 200
"""Umbral por defecto bajo el cual una página se considera casi vacía."""


def slug_from_url(url: str) -> str:
    """Deriva el slug legible de una URL.

    La portada (sin ruta) devuelve ``home``; una ruta anidada usa su último
    segmento. Los guiones se vuelven guiones bajos y los puntos se eliminan.
    """

    path = urlparse(url).path.strip("/")
    segment = path.rsplit("/", 1)[-1] if path else "home"
    segment = segment.replace("-", "_").replace(".", "")
    return segment or "home"


def domain_from_url(url: str) -> str:
    """Devuelve el dominio de la URL en minúsculas y sin el prefijo ``www.``."""

    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def is_official_domain(domain: str) -> bool:
    """Indica si el dominio es el sitio oficial o un subdominio suyo."""

    return domain == OFFICIAL_DOMAIN or domain.endswith(f".{OFFICIAL_DOMAIN}")


def section_from_url(url: str) -> str:
    """Deriva la sección editorial a partir de la URL.

    En el sitio oficial es el primer segmento de la ruta (``inversionistas``,
    ``quienes-somos``…) y ``home`` para la portada. En fuentes de terceros la
    sección es ``prensa``: el medio se conserva aparte, en el dominio.
    """

    if not is_official_domain(domain_from_url(url)):
        return "prensa"
    segments = [segment for segment in urlparse(url).path.split("/") if segment]
    return segments[0] if segments else "home"


def content_hash(markdown: str) -> str:
    """Calcula la huella SHA-256 del Markdown, usada para deduplicar el corpus."""

    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def estimate_tokens(markdown: str) -> int:
    """Estima los tokens del Markdown (≈4 caracteres por token, mínimo 1)."""

    return max(1, round(len(markdown) / 4))


def is_duplicate(
    markdown: str,
    seen_hashes: set[str],
    min_chars: int = DEFAULT_MIN_DEDUPE_CHARS,
) -> bool:
    """Indica si el Markdown repite una huella ya vista.

    Las páginas casi vacías (menos de ``min_chars`` caracteres) nunca cuentan
    como duplicadas: su contenido es demasiado pobre para descartarlas.
    """

    return len(markdown) >= min_chars and content_hash(markdown) in seen_hashes
