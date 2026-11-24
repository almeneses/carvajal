"""Enlaces firmados al PDF de una cotización: ``/cotizaciones/{folio}.pdf?e=…&t=…``.

La firma es HMAC-SHA256 con ``SESSION_SECRET`` sobre el folio, el dueño de la cotización y
el vencimiento. El dueño no viaja en la URL: la ruta lee la cotización por folio y vuelve a
firmar con su ``owner_key`` guardado, así un folio adivinado o un enlace alterado no abre
nada. El enlace vence con la vigencia de la cotización y nunca después de
``MAX_LINK_DAYS``.
"""

import hmac
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from urllib.parse import quote

__all__ = ["MAX_LINK_DAYS", "link_expiry", "pdf_url", "sign", "verify"]

MAX_LINK_DAYS = 7
# Tolerancia de reloj entre el proceso MCP y el proceso web al validar el enlace.
LINK_LEEWAY_SECONDS = 300
_DOMAIN = b"carvajal:quote-pdf:v1\n"


def sign(folio: str, owner_key: str, expires: int, secret: bytes) -> str:
    """Firma HMAC-SHA256 truncada a 128 bits (32 caracteres hexadecimales)."""

    message = _DOMAIN + f"{folio}\n{owner_key}\n{expires}".encode()
    return hmac.new(secret, message, sha256).hexdigest()[:32]


def verify(
    folio: str,
    owner_key: str,
    expires: int,
    token: str,
    secret: bytes,
    *,
    now: datetime | None = None,
) -> bool:
    """``True`` si la firma corresponde y el enlace no ha vencido."""

    moment = now or datetime.now(UTC)
    now_ts = int(moment.timestamp())
    if expires + LINK_LEEWAY_SECONDS < now_ts:
        return False
    max_expiry = int((moment + timedelta(days=MAX_LINK_DAYS)).timestamp())
    if expires > max_expiry + LINK_LEEWAY_SECONDS:
        return False
    expected = sign(folio, owner_key, expires, secret)
    # Un enlace copiado puede venir con el hexadecimal en mayúsculas.
    return hmac.compare_digest(expected.encode(), token.strip().lower().encode())


def link_expiry(valid_until: date | None, *, now: datetime | None = None) -> int:
    """Vencimiento (epoch) = fin del día de vigencia, tope ``MAX_LINK_DAYS`` desde ahora."""

    moment = now or datetime.now(UTC)
    limit = moment + timedelta(days=MAX_LINK_DAYS)
    if valid_until is not None:
        end = datetime.combine(valid_until, time(23, 59, 59), tzinfo=UTC)
        limit = min(limit, end)
    return int(limit.timestamp())


def pdf_url(base_url: str, folio: str, owner_key: str, expires: int, secret: bytes) -> str:
    """URL pública del PDF firmada para ``owner_key``."""

    token = sign(folio, owner_key, expires, secret)
    return f"{base_url.rstrip('/')}/cotizaciones/{quote(folio, safe='')}.pdf?e={expires}&t={token}"
