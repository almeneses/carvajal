"""Webhook firmado para mensajería: ``POST /api/webhooks/whatsapp``.

Un relé de WhatsApp (u otro canal de mensajería) entrega cada mensaje entrante aquí y
recibe la respuesta en el cuerpo. El turno es el mismo que atiende el servidor MCP
(:class:`apps.mcp.bridge.WhatsAppBridge`): ``owner_key`` HMAC del número, formato de
WhatsApp, cotizaciones en texto y mensajes legibles ante fallos.

Seguridad, en el orden en que se comprueba (nada se procesa antes de la firma):

1. Sin ``WEBHOOK_SECRET`` el endpoint no existe: 404.
2. Cuerpo acotado a :data:`MAX_BODY_BYTES` (413), leído en crudo y sin decodificar.
3. ``X-Carvajal-Timestamp`` (segundos Unix) dentro de ±5 minutos (401).
4. ``X-Carvajal-Signature: sha256=<hex>``: HMAC-SHA256 de ``<timestamp>.<cuerpo crudo>``
   con ``WEBHOOK_SECRET``, comparado en tiempo constante (401). El timestamp va dentro
   de lo firmado para que una petición capturada no se pueda refrescar.
5. Esquema Pydantic (422, sin eco del cuerpo) y remitente en ``WEBHOOK_ALLOWED_NUMBERS``
   tras normalizar a E.164 (403; vacía rechaza todo).
6. Idempotencia: ``message_id`` se convierte en el ``client_turn_id`` del turno y se
   reserva de forma atómica antes de abrir hilo, cobrar cupo o llamar al LLM
   (``webhook_deliveries``). Un reintento que llega con el primer intento en curso recibe
   409 con ``Retry-After`` y no tiene efectos; uno posterior, o la misma petición firmada
   reenviada dentro de la ventana, reemite la misma respuesta sin duplicar turnos, cupo
   ni folios.

El número nunca se registra en claro: los logs llevan el ``owner_key`` que ata el puente.
"""

import hashlib
import hmac
import time
from typing import Final, cast

import anyio
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from apps.mcp.bridge import (
    MAX_MESSAGE_CHARS,
    SERVICE_UNAVAILABLE,
    TURN_IN_PROGRESS,
    WhatsAppBridge,
)
from carvajal.domain.phone import normalize_e164
from carvajal.infrastructure.settings import Settings

__all__ = [
    "MAX_BODY_BYTES",
    "MAX_SKEW_SECONDS",
    "RETRY_AFTER_SECONDS",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "TURN_IN_PROGRESS",
    "WhatsAppMessage",
    "WhatsAppReply",
    "allowed_numbers",
    "allows_any_sender",
    "invalid_allowlist_entries",
    "router",
    "signature_for",
    "turn_id_for",
]

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/webhooks")

SIGNATURE_HEADER: Final = "X-Carvajal-Signature"
TIMESTAMP_HEADER: Final = "X-Carvajal-Timestamp"
SIGNATURE_PREFIX: Final = "sha256="
MAX_SKEW_SECONDS: Final = 300
# Pausa sugerida al relé cuando el mismo ``message_id`` se está atendiendo (409).
RETRY_AFTER_SECONDS: Final = 5
# Un mensaje de 2 000 caracteres escapados en JSON (\uXXXX) cabe con holgura.
MAX_BODY_BYTES: Final = 16 * 1024
MAX_MESSAGE_ID_CHARS: Final = 200
# ``messages.client_turn_id`` admite 64 caracteres: prefijo + 40 hex del SHA-256.
TURN_PREFIX: Final = "wh_"

NOT_FOUND: Final = "Not Found"
UNAUTHORIZED: Final = "Firma o marca de tiempo inválida."
TOO_LARGE: Final = "El cuerpo supera el tamaño permitido."
INVALID_BODY: Final = "El cuerpo no cumple el esquema del webhook."
SENDER_NOT_ALLOWED: Final = "Remitente no autorizado."


class WhatsAppMessage(BaseModel):
    """Mensaje entrante. ``from`` es el número del remitente en E.164."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    message_id: str = Field(min_length=1, max_length=MAX_MESSAGE_ID_CHARS)
    sender: str = Field(alias="from", min_length=1, max_length=40)
    text: str = Field(max_length=MAX_MESSAGE_CHARS * 4)
    nueva: bool = False

    @field_validator("message_id")
    @classmethod
    def _message_id_sin_espacios(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("message_id vacío")
        return cleaned

    @field_validator("sender")
    @classmethod
    def _sender_e164(cls, value: str) -> str:
        return normalize_e164(value)


class WhatsAppReply(BaseModel):
    """Respuesta del webhook: texto para enviar tal cual y folios emitidos."""

    reply: str
    quote_ids: list[str]


def signature_for(secret: str, timestamp: str, body: bytes) -> str:
    """Valor de ``X-Carvajal-Signature`` para ``timestamp`` y el cuerpo crudo."""

    payload = timestamp.encode("ascii") + b"." + body
    return SIGNATURE_PREFIX + hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def turn_id_for(message_id: str) -> str:
    """``client_turn_id`` estable, opaco y de largo fijo para un ``message_id``."""

    digest = hashlib.sha256(message_id.encode("utf-8")).hexdigest()
    return f"{TURN_PREFIX}{digest[:40]}"


OPEN_ALLOWLIST = "*"


def allows_any_sender(settings: Settings) -> bool:
    """``WEBHOOK_ALLOWED_NUMBERS=*``: modo abierto, cualquier remitente E.164 válido.

    La protección del endpoint no es la lista sino la firma con ``WEBHOOK_SECRET`` (solo el
    relé la conoce); el cupo diario por número y el tope de Jev limitan el gasto.
    """

    return any(entry.strip() == OPEN_ALLOWLIST for entry in settings.webhook_allowed_numbers)


def _normalized(entries: tuple[str, ...]) -> tuple[frozenset[str], int]:
    valid: set[str] = set()
    invalid = 0
    for entry in entries:
        if entry.strip() == OPEN_ALLOWLIST:
            continue
        try:
            valid.add(normalize_e164(entry))
        except ValueError:
            invalid += 1
    return frozenset(valid), invalid


def allowed_numbers(settings: Settings) -> frozenset[str]:
    """Números autorizados, normalizados a E.164; las entradas inválidas se ignoran."""

    return _normalized(settings.webhook_allowed_numbers)[0]


def invalid_allowlist_entries(settings: Settings) -> int:
    """Cuántas entradas de ``WEBHOOK_ALLOWED_NUMBERS`` no son E.164 (para el aviso)."""

    return _normalized(settings.webhook_allowed_numbers)[1]


def _fresh(timestamp: str, now: float) -> bool:
    if not timestamp.isascii() or not timestamp.isdigit() or len(timestamp) > 12:
        return False
    return abs(now - int(timestamp)) <= MAX_SKEW_SECONDS


def _signed(secret: str, timestamp: str, body: bytes, supplied: str) -> bool:
    expected = signature_for(secret, timestamp, body).encode("ascii")
    # ``compare_digest`` con bytes: una cabecera no ASCII no rompe la comparación.
    return hmac.compare_digest(expected, supplied.strip().lower().encode("utf-8", "replace"))


async def _read_body(request: Request) -> bytes | None:
    """Cuerpo crudo o ``None`` si supera el límite, sin leer más de la cuenta."""

    declared = request.headers.get("content-length", "")
    if declared.isascii() and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            return None
    return bytes(body)


def _settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else Settings()


def _bridge(request: Request) -> WhatsAppBridge:
    injected = getattr(request.app.state, "webhook_bridge", None)
    if injected is not None:
        return cast(WhatsAppBridge, injected)
    from apps.mcp.server import default_bridge

    return default_bridge()


def _reject(status_code: int, detail: str, reason: str) -> JSONResponse:
    # Solo el motivo: ni el número, ni el cuerpo, ni las cabeceras de firma.
    logger.warning("webhook.rejected", channel="whatsapp", reason=reason, status=status_code)
    return JSONResponse({"detail": detail}, status_code=status_code)


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request) -> JSONResponse:
    """Atiende un mensaje firmado y devuelve ``{reply, quote_ids}``."""

    settings = _settings(request)
    secret = settings.webhook_secret.strip()
    if not secret:
        return JSONResponse({"detail": NOT_FOUND}, status_code=404)

    body = await _read_body(request)
    if body is None:
        return _reject(413, TOO_LARGE, "body_too_large")
    timestamp = request.headers.get(TIMESTAMP_HEADER, "").strip()
    if not _fresh(timestamp, time.time()):
        return _reject(401, UNAUTHORIZED, "stale_timestamp")
    if not _signed(secret, timestamp, body, request.headers.get(SIGNATURE_HEADER, "")):
        return _reject(401, UNAUTHORIZED, "bad_signature")

    try:
        message = WhatsAppMessage.model_validate_json(body)
    except ValidationError as error:
        # Solo la ubicación de cada error: el ``input`` llevaría el número o el texto.
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        logger.warning("webhook.invalid_body", channel="whatsapp", fields=fields)
        return JSONResponse({"detail": INVALID_BODY}, status_code=422)
    if not allows_any_sender(settings) and message.sender not in allowed_numbers(settings):
        return _reject(403, SENDER_NOT_ALLOWED, "sender_not_allowed")

    try:
        bridge = _bridge(request)
    except Exception as error:  # p. ej. falta SESSION_SECRET para el owner_key
        logger.error("webhook.bridge_unavailable", channel="whatsapp", error=repr(error))
        return JSONResponse({"detail": SERVICE_UNAVAILABLE}, status_code=503)

    turn_id = turn_id_for(message.message_id)
    # El turno es síncrono (SQLAlchemy y el grafo): se ejecuta fuera del event loop.
    reply = await anyio.to_thread.run_sync(
        lambda: bridge.respond(
            message.text,
            message.sender,
            new_conversation=message.nueva,
            client_turn_id=turn_id,
        )
    )
    if reply.in_progress:
        logger.info("webhook.turn_in_progress", channel="whatsapp")
        return JSONResponse(
            {"detail": TURN_IN_PROGRESS},
            status_code=409,
            headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
        )
    payload = WhatsAppReply(reply=reply.text, quote_ids=list(reply.quote_ids))
    return JSONResponse(payload.model_dump())
