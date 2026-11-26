"""Servidor MCP (Streamable HTTP) con una sola herramienta: ``consultar_carvajal``.

Lo consume el agente «ventas» de OpenClaw para responder por WhatsApp con el mismo agente
del chat web (variante 1: el turno completo como herramienta, con sus protecciones).

Seguridad, en capas:

- El puerto se publica solo en loopback (``127.0.0.1:8765`` en ``compose.prod.yml``) y se
  alcanza por un túnel SSH restringido a ese puerto.
- ``/mcp`` exige ``Authorization: Bearer <MCP_TOKEN>``, comparado en tiempo constante. Sin
  token configurado la aplicación no se construye: falla cerrada.
- Protección contra DNS rebinding: solo se aceptan cabeceras ``Host`` de loopback.
- ``/live`` responde sin token y sin tocar la base, para el healthcheck del contenedor.
- ``/health`` (con el mismo bearer) publica los contadores de la compuerta de afirmaciones
  de este proceso (bloque ``claims``, como el de ``/health`` de la app web): WhatsApp corre
  el agente aquí, en otro proceso, y sus veredictos no llegan a los de la app.

Voz: con ``voz=true`` (el cliente mandó una nota de voz y la pasarela responderá con TTS)
la herramienta sigue devolviendo un solo texto. Primero va un **resumen hablado** (unos
15-20 s, extractivo y sin LLM; ver :func:`carvajal.speech.spoken_summary`). Si el resumen
dejó algo fuera (o hay cotización), detrás va la línea :data:`~carvajal.speech.TEXT_HEADER`
y la respuesta escrita completa de WhatsApp, con sus enlaces, para enviarla como mensaje de
texto. Si no, y hay enlaces (PDF, foto, fuentes), van tras la línea
:data:`~carvajal.speech.LINKS_HEADER`, uno por línea y como ``rótulo: url``. La transcripción
llega con los miles al estilo colombiano (:func:`carvajal.speech.normalize_transcript`). Con
``voz=false`` (por defecto) la respuesta no cambia.
"""

import hmac
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import date
from functools import cache, partial
from typing import Annotated, Any
from urllib.parse import quote

import anyio
import structlog
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from apps.api.quote_links import link_expiry, pdf_url
from apps.mcp.bridge import (
    MAX_CHAT_ID_CHARS,
    MAX_MESSAGE_CHARS,
    WhatsAppBridge,
    read_inbound_photo,
)
from carvajal.speech import LINKS_HEADER, TEXT_HEADER

__all__ = [
    "INSTRUCTIONS",
    "TOOL_DESCRIPTION",
    "TOOL_NAME",
    "build_app",
    "build_server",
    "default_bridge",
]

logger = structlog.get_logger(__name__)

TOOL_NAME = "consultar_carvajal"
TOOL_DESCRIPTION = (
    "Responde un mensaje de un cliente por WhatsApp con el asistente oficial de Carvajal "
    "Empaques: información institucional del corpus, catálogo de empaques, cotizaciones "
    "con folio, sedes de distribuidores e indicadores financieros. Si el cliente pide "
    "el PDF de su cotización (p. ej. responde «PDF»), pásale ese mensaje igual: la "
    "herramienta devuelve el enlace. Devuelve el texto final, ya en formato de WhatsApp "
    "y con sus fuentes: envíalo al cliente tal cual. Si el cliente mandó una nota de voz, "
    "llama con voz=true: primero viene un resumen corto para leer en voz alta; si trae la "
    f"línea «{TEXT_HEADER}», lo que sigue es la respuesta completa para enviarla como "
    f"mensaje escrito, y si trae la línea «{LINKS_HEADER}», los enlaces para enviarlos "
    "como texto. Si el cliente mandó una foto de un alimento, pasa en `foto` la referencia "
    "del adjunto (media://inbound/<archivo>) y en `mensaje` el texto que la acompaña (puede "
    "ir vacío): la herramienta sugiere el empaque del catálogo."
)
INSTRUCTIONS = (
    "Usa consultar_carvajal para cada mensaje del cliente. Pasa el mensaje completo en "
    "`mensaje` y el identificador del chat (número o JID del remitente) en "
    "`conversacion`, siempre el mismo para el mismo chat. Responde al cliente exactamente "
    "con el texto que devuelve la herramienta, sin cambiar cifras, precios ni folios. Si "
    "el cliente mandó una nota de voz, pasa voz=true: lee en voz alta solo el resumen del "
    "principio y envía como texto, sin leerlo, lo que venga tras las líneas de TEXTO o de "
    "ENLACES. Si el cliente adjuntó una foto ([media attached: media://inbound/...]), pasa "
    "esa referencia en `foto`; nunca describas la foto tú mismo."
)

ASGIApp = Callable[
    [MutableMapping[str, Any], Callable[[], Awaitable[Any]], Callable[..., Awaitable[None]]],
    Awaitable[None],
]

_LOOPBACK_HOSTS = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*", "[::1]", "[::1]:*"]


def build_server(bridge: WhatsAppBridge | Callable[[], WhatsAppBridge]) -> MCPServer:
    """``MCPServer`` con la herramienta del puente; ``bridge`` puede ser perezoso."""

    def resolve() -> WhatsAppBridge:
        return bridge if isinstance(bridge, WhatsAppBridge) else bridge()

    server = MCPServer(name="carvajal", instructions=INSTRUCTIONS)

    @server.tool(
        name=TOOL_NAME,
        description=TOOL_DESCRIPTION,
        annotations=ToolAnnotations(
            title="Asistente de Carvajal",
            read_only_hint=False,  # guarda el turno y la cotización emitida
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    async def consultar_carvajal(
        mensaje: Annotated[
            str, Field(max_length=MAX_MESSAGE_CHARS * 4, description="Mensaje del cliente.")
        ],
        conversacion: Annotated[
            str,
            Field(
                min_length=1,
                max_length=MAX_CHAT_ID_CHARS,
                description="Identificador estable del chat de WhatsApp (número o JID).",
            ),
        ],
        nueva: Annotated[
            bool,
            Field(description="True para empezar una conversación nueva (comando /new)."),
        ] = False,
        voz: Annotated[
            bool,
            Field(
                description=(
                    "True si el cliente mandó una nota de voz: la respuesta llega como un "
                    "resumen apto para TTS y, aparte, el texto escrito completo (línea de "
                    "TEXTO) o los enlaces (línea de ENLACES)."
                )
            ),
        ] = False,
        foto: Annotated[
            str,
            Field(
                max_length=200,
                description=(
                    "Referencia del adjunto si el cliente mandó una foto de un alimento "
                    "(media://inbound/<archivo>.jpg); vacío si no hay foto."
                ),
            ),
        ] = "",
    ) -> str:
        # El turno es síncrono (SQLAlchemy y el grafo): se ejecuta fuera del event loop.
        return await anyio.to_thread.run_sync(
            lambda: resolve().consult(
                mensaje, conversacion, new_conversation=nueva, voice=voz, photo=foto
            )
        )

    return server


def _bearer_guard(app: ASGIApp, token: str) -> ASGIApp:
    expected = f"Bearer {token}".encode()

    async def guarded(
        scope: MutableMapping[str, Any],
        receive: Callable[[], Awaitable[Any]],
        send: Callable[..., Awaitable[None]],
    ) -> None:
        if scope["type"] == "http" and scope.get("path") != "/live":
            headers = dict(scope.get("headers") or [])
            supplied = headers.get(b"authorization", b"")
            if not hmac.compare_digest(supplied, expected):
                logger.warning("mcp.unauthorized", path=scope.get("path"))
                response: Response = JSONResponse({"detail": "No autorizado."}, 401)
                await response(scope, receive, send)
                return
        await app(scope, receive, send)

    return guarded


def build_app(server: MCPServer, token: str) -> ASGIApp:
    """Aplicación ASGI: ``/mcp`` y ``/health`` con bearer y anti DNS rebinding, ``/live``
    abierto."""

    if not token.strip():
        raise RuntimeError("Define MCP_TOKEN: el servidor MCP no arranca sin token.")

    @server.custom_route("/live", methods=["GET"], include_in_schema=False)
    async def live(_request: Request) -> Response:
        return PlainTextResponse("ok")

    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_LOOPBACK_HOSTS,
        allowed_origins=[],
    )
    # El transporte solo valida ``Host`` y ``Origin`` en ``/mcp``: las rutas propias lo
    # piden aparte con las mismas reglas.
    host_check = TransportSecurityMiddleware(security)

    @server.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(request: Request) -> Response:
        rejected = await host_check.validate_request(request)
        if rejected is not None:
            return rejected
        # Diferido: el bloque importa el gatekeeper, que arrastra el grafo del agente.
        from apps.api.observability import claims_block
        from carvajal.infrastructure.settings import Settings

        return JSONResponse({"status": "ok", "claims": claims_block(Settings())})

    app: Starlette = server.streamable_http_app(transport_security=security)
    return _bearer_guard(app, token.strip())


@cache
def default_bridge() -> WhatsAppBridge:
    """Puente real: chat, cotizaciones y su PDF, cupo, reintentos y reservas, al primer uso.

    Lo comparten el servidor MCP y el webhook firmado (``apps.api.views.webhooks``): el
    mismo número cae en el mismo ``owner_key`` por cualquiera de los dos canales.
    """

    from apps.api import runtime
    from apps.api.chat.service import default_chat_service
    from carvajal.adapters.judge import build_food_classifier
    from carvajal.adapters.storage.chat import SqlChatRepository
    from carvajal.adapters.storage.deliveries import SqlDeliveryStore
    from carvajal.adapters.storage.quotes import SqlQuoteRepository
    from carvajal.adapters.storage.usage import SqlUsageRepository
    from carvajal.application.quota import DailyQuota, QuotaResult
    from carvajal.catalog.images import catalog_image
    from carvajal.infrastructure.settings import Settings

    settings = Settings()
    if not settings.session_secret:
        raise RuntimeError("Define SESSION_SECRET: firma el identificador de cada chat.")
    factory = runtime.session_factory()

    def quotes(folio: str, owner_key: str) -> dict[str, Any] | None:
        with factory() as session:
            record = SqlQuoteRepository(session).get(folio, owner_key)
            return None if record is None else dict(record.payload)

    def turns(owner_key: str, client_turn_id: str) -> str | None:
        with factory() as session:
            return SqlChatRepository(session).find_turn_thread(owner_key, client_turn_id)

    def quota(owner_key: str) -> QuotaResult:
        with factory.begin() as session:
            return DailyQuota(
                SqlUsageRepository(session),
                limit=settings.daily_requests_limit,
                timezone=settings.quota_timezone,
            ).consume(owner_key)

    def quota_peek(owner_key: str) -> QuotaResult:
        with factory() as session:
            return DailyQuota(
                SqlUsageRepository(session),
                limit=settings.daily_requests_limit,
                timezone=settings.quota_timezone,
            ).peek(owner_key)

    def latest_quote(owner_key: str) -> dict[str, Any] | None:
        with factory() as session:
            conversation = SqlChatRepository(session).active(owner_key)
            if conversation is None:
                return None
            record = SqlQuoteRepository(session).latest(owner_key, conversation_id=conversation.id)
            return None if record is None else dict(record.payload)

    secret = settings.session_secret.encode("utf-8")
    base_url = settings.public_base_url.strip()
    media_dir = settings.food_photo_media_dir.strip()

    media_base = settings.media_base_url.strip()

    def pdf_link(folio: str, owner_key: str, valid_until: date | None) -> str:
        return pdf_url(base_url, folio, owner_key, link_expiry(valid_until), secret)

    def pdf_attachment(folio: str, owner_key: str, valid_until: date | None) -> str:
        return pdf_url(media_base, folio, owner_key, link_expiry(valid_until), secret)

    def photo_url(sku: str) -> str | None:
        # El manifiesto usa SKU simples; no se construye una URL que la ruta no
        # pueda volver a resolver (por ejemplo, un SKU con ``/``).
        if (
            not sku
            or len(sku) > 64
            or any(not (character.isalnum() or character in "._-") for character in sku)
        ):
            return None
        if catalog_image(sku) is None:
            return None
        return f"{base_url.rstrip('/')}/catalogo/imagenes/{quote(sku, safe='')}.jpg"

    return WhatsAppBridge(
        chat=default_chat_service(),
        secret=secret,
        quotes=quotes,
        quota=quota,
        quota_peek=quota_peek,
        turns=turns,
        latest_quote=latest_quote,
        # Sin URL pública no hay a dónde enlazar: el chat no ofrece el PDF.
        pdf_link=pdf_link if base_url else None,
        # Con MEDIA_BASE_URL, OpenClaw adjunta el PDF como documento en vez de enlazarlo.
        pdf_attachment=pdf_attachment if media_base else None,
        photo_url=photo_url if base_url else None,
        # Reserva compartida entre workers: un reintento en vuelo no repite el turno.
        deliveries=SqlDeliveryStore(factory),
        # Spec 051: fotos de WhatsApp desde la carpeta de adjuntos de OpenClaw (solo lectura).
        photo_reader=partial(read_inbound_photo, directory=media_dir) if media_dir else None,
        food_classifier=build_food_classifier(settings),
    )
