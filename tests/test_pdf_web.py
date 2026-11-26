"""La web entrega el PDF de la cotización registrada (spec 054, T6).

T6 termina en «📄 Te envío el PDF.» para todos los canales: WhatsApp la cambia por su enlace
firmado o el adjunto, y la web la cambia por el enlace ``/cotizaciones/{folio}.pdf``, que el
dueño abre con la cookie del chat. Servicio real sobre SQLite con el runner ``tools``
guionado y la app FastAPI, sin red. Cubren:

- la plantilla: la promesa pasa a ser el enlace, o se quita si no hay enlace;
- el stream y el historial: texto y tarjeta con el enlace; la voz no lo lee;
- la ruta: PDF para el dueño, 404 para otro visitante o sin cookie ni firma;
- WhatsApp: el mismo servicio sigue entregando su enlace firmado, sin el de la web.
"""

import re
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from tests.test_api_stream import parse_sse
from tests.test_chat_service_tools import (
    FakeToolRunner,
    assistant_rows,
    build_service,
    commercial_final,
    scripted_events,
    stored_quotes,
)

from apps.api.chat.identity import VISITOR_COOKIE, SignedCookieIdentity
from apps.api.main import create_app
from apps.api.quote_links import link_expiry, pdf_url
from apps.api.security import CSRF_COOKIE, CSRF_FIELD
from apps.mcp.bridge import WhatsAppBridge, owner_key_for
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_session_factory
from carvajal.adapters.storage.quotes import SqlQuoteRepository
from carvajal.application.quota import QuotaResult
from carvajal.quotation import render
from carvajal.quotation.draft import QuoteDraft

SECRET = b"secreto-de-prueba"
IDENTITY = SignedCookieIdentity(SECRET)
VISITOR = "a" * 64
OTHER_VISITOR = "b" * 64
T6 = (
    "✅ **Cotización #a3f09c12b7e4 registrada**\n"
    "1 producto · Total **$102.185** · válida hasta 2 dic\n"
    "📄 Te envío el PDF."
)
WEB_LINK = re.compile(r"📄 \[Descarga el PDF de tu cotización\]\((http://testserver/cotizaciones/"
                      r"([0-9a-f]{12})\.pdf)\)")  # fmt: skip


@pytest.fixture()
def engine() -> Iterator[Any]:
    motor = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables.metadata.create_all(motor)
    with motor.begin() as conexion:
        conexion.execute(text("DROP INDEX IF EXISTS uq_messages_turn"))
        conexion.execute(text("DROP INDEX IF EXISTS uq_messages_turn_user"))
    try:
        yield motor
    finally:
        motor.dispose()


def _app(engine: Any) -> FastAPI:
    """App con el servicio real, la identidad de prueba y la cotización leída de SQLite."""

    factory = build_session_factory(engine)

    def lookup(folio: str) -> tuple[str, dict[str, Any]] | None:
        with factory() as session:
            record = SqlQuoteRepository(session).by_folio(folio)
            if record is None:
                return None
            return str(record.get("owner_key") or ""), dict(record.payload)

    app = create_app()
    app.state.chat = build_service(engine, FakeToolRunner(scripted_events(commercial_final())))
    app.state.chat_identity = IDENTITY
    app.state.quota = lambda owner: QuotaResult(allowed=True)
    app.state.quote_lookup = lookup
    app.state.quote_link_secret = SECRET
    return app


def _visitor(app: FastAPI, visitor: str | None = VISITOR) -> TestClient:
    client = TestClient(app)
    if visitor is not None:
        client.cookies.set(VISITOR_COOKIE, visitor)
    return client


def _turn(client: TestClient, question: str) -> dict[str, dict[str, Any]]:
    """Un turno por ``/chat/stream``: el último evento de cada nombre."""

    if CSRF_COOKIE not in client.cookies:
        client.get("/api/csrf")
    response = client.post(
        "/chat/stream",
        data={"question": question, CSRF_FIELD: client.cookies[CSRF_COOKIE]},
    )
    assert response.status_code == 200
    return dict(parse_sse(response.text))


def _register(client: TestClient) -> dict[str, dict[str, Any]]:
    """Cotiza, cierra el carrito y confirma: el tercer turno registra el folio."""

    _turn(client, "cotiza 50 vasos en Bogotá")
    _turn(client, "no")
    return _turn(client, "sí")


# --- plantilla -----------------------------------------------------------------------------


def test_la_plantilla_registrada_sigue_terminando_en_la_promesa_comun() -> None:
    # Arrange
    draft = QuoteDraft()

    # Act
    message = render.registered(draft, "a3f09c12b7e4", None)

    # Assert: WhatsApp la sustituye por su enlace firmado; no cambia para ningún canal.
    assert message.text.endswith(f"\n{render.PDF_PROMISE}")


def test_pdf_offer_cambia_la_promesa_por_el_enlace_del_folio() -> None:
    # Arrange
    asked: list[str] = []

    def link(folio: str) -> str:
        asked.append(folio)
        return f"https://carvajal.example/cotizaciones/{folio}.pdf"

    # Act
    offered = render.pdf_offer(T6, link)

    # Assert
    assert asked == ["a3f09c12b7e4"]
    assert render.PDF_PROMISE not in offered
    assert offered.endswith(
        "\n📄 [Descarga el PDF de tu cotización]"
        "(https://carvajal.example/cotizaciones/a3f09c12b7e4.pdf)"
    )
    assert offered.startswith("✅ **Cotización #a3f09c12b7e4 registrada**\n1 producto")


def test_pdf_offer_sin_enlace_no_promete_el_pdf() -> None:
    # Act
    offered = render.pdf_offer(T6, lambda _folio: None)

    # Assert
    assert offered == T6.removesuffix(f"\n{render.PDF_PROMISE}")
    assert "PDF" not in offered


@pytest.mark.parametrize(
    "message",
    [
        "🧾 **Resumen de tu cotización**\n¿La confirmo y te envío el PDF? Responde **sí**.",
        "Te envío el PDF.",  # prosa sin el encabezado de T6
        "Esa cotización ya quedó registrada con el folio **#a3f09c12b7e4**.",
    ],
)
def test_pdf_offer_no_toca_otros_mensajes(message: str) -> None:
    # Act
    offered = render.pdf_offer(message, lambda _folio: "https://x.example/c.pdf")

    # Assert
    assert offered == message


# --- web: stream, historial y ruta ---------------------------------------------------------


def test_al_registrar_en_la_web_el_texto_y_la_tarjeta_enlazan_el_pdf(engine: Any) -> None:
    # Arrange
    client = _visitor(_app(engine))

    # Act
    events = _register(client)

    # Assert: el enlace es del folio registrado y reemplaza la promesa.
    (row,) = stored_quotes(engine)
    folio = row["id"]
    answer = events["fallback"]["text"]
    found = WEB_LINK.search(answer)
    assert found is not None, answer
    assert found.group(2) == folio
    assert render.PDF_PROMISE not in answer
    assert f'href="/cotizaciones/{folio}.pdf"' in events["products"]["html"]
    assert events["done"]["quote_ids"] == [folio]
    # El turno guardado conserva el texto común (WhatsApp lee el mismo servicio).
    assert assistant_rows(engine)[-1]["content"].endswith(render.PDF_PROMISE)


def test_con_url_publica_el_enlace_de_la_web_no_depende_del_host_de_la_peticion(
    engine: Any,
) -> None:
    # Arrange: detrás de un proxy, ``Host`` puede ser el interno; manda PUBLIC_BASE_URL.
    app = _app(engine)
    app.state.settings = app.state.settings.model_copy(
        update={"public_base_url": "https://carva.example/"}
    )

    # Act
    answer = _register(_visitor(app))["fallback"]["text"]

    # Assert
    (row,) = stored_quotes(engine)
    assert f"(https://carva.example/cotizaciones/{row['id']}.pdf)" in answer
    assert "testserver" not in answer


def test_la_voz_de_la_web_no_lee_el_enlace_del_pdf(engine: Any) -> None:
    # Arrange
    client = _visitor(_app(engine))

    # Act
    events = _register(client)

    # Assert
    summary = events["done"]["voz_resumen"]
    assert summary
    for mark in ("http", "cotizaciones/", ".pdf", "[", "📄"):
        assert mark not in summary, mark


def test_el_enlace_de_la_web_descarga_el_pdf_para_su_dueno(engine: Any) -> None:
    # Arrange
    client = _visitor(_app(engine))
    url = WEB_LINK.search(_register(client)["fallback"]["text"])
    assert url is not None

    # Act
    response = client.get(url.group(1))

    # Assert
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "no-store" in response.headers["cache-control"]
    assert response.content.startswith(b"%PDF-")


@pytest.mark.parametrize("visitor", [OTHER_VISITOR, None], ids=["otro_visitante", "sin_cookie"])
def test_nadie_mas_abre_el_pdf_sin_la_firma(engine: Any, visitor: str | None) -> None:
    # Arrange
    app = _app(engine)
    url = WEB_LINK.search(_register(_visitor(app))["fallback"]["text"])
    assert url is not None

    # Act
    response = _visitor(app, visitor).get(url.group(1))

    # Assert: la misma respuesta que un folio que no existe.
    assert response.status_code == 404
    assert response.json() == {"detail": "La cotización no existe o el enlace venció."}


@pytest.mark.parametrize("visitor", [VISITOR, OTHER_VISITOR], ids=["dueño", "otro_visitante"])
@pytest.mark.parametrize(
    "token",
    ["1.firma", "4102444800.", "firma", "%20"],
    ids=["manipulado", "truncado", "sin_vence", "espacio"],
)
def test_una_firma_invalida_no_se_salva_con_la_cookie(
    engine: Any, visitor: str, token: str
) -> None:
    # Arrange
    app = _app(engine)
    url = WEB_LINK.search(_register(_visitor(app))["fallback"]["text"])
    assert url is not None

    # Act
    response = _visitor(app, visitor).get(f"{url.group(1)}?t={token}")

    # Assert: ni la cookie del dueño rescata un enlace vencido o manipulado; la web enlaza
    # sin ``t`` y la cookie solo cuenta cuando no llega ningún token.
    assert response.status_code == 404


def test_el_historial_de_la_web_enlaza_el_pdf_de_la_cotizacion(engine: Any) -> None:
    # Arrange
    client = _visitor(_app(engine))
    _register(client)
    (row,) = stored_quotes(engine)
    folio = row["id"]

    # Act
    page = client.get("/chat")

    # Assert: el texto y la tarjeta del turno registrado llevan el enlace, sin la promesa.
    assert page.status_code == 200
    # Solo el hilo: el panel lateral resume cada hilo con el texto guardado.
    thread = page.text.split('<ol class="hilo">', 1)[1].split('class="panel-hilos-cuerpo"', 1)[0]
    assert f'href="http://testserver/cotizaciones/{folio}.pdf"' in thread
    assert f'href="/cotizaciones/{folio}.pdf"' in thread
    assert "Te envío el PDF" not in thread


# --- WhatsApp ------------------------------------------------------------------------------


def test_whatsapp_sigue_entregando_su_enlace_firmado_sin_el_de_la_web(engine: Any) -> None:
    # Arrange: el mismo servicio de chat detrás del puente, con URL pública.
    service = build_service(engine, FakeToolRunner(scripted_events(commercial_final())))
    factory = build_session_factory(engine)
    chat = "+573001112233"
    base = "https://carvajal.example"

    def quotes(folio: str, owner: str) -> dict[str, Any] | None:
        with factory() as session:
            record = SqlQuoteRepository(session).get(folio, owner)
            return None if record is None else dict(record.payload)

    def latest(owner: str) -> dict[str, Any] | None:
        with factory() as session:
            record = SqlQuoteRepository(session).latest(owner)
            return None if record is None else dict(record.payload)

    def link(folio: str, owner: str, valid_until: date | None) -> str:
        return pdf_url(base, folio, owner, link_expiry(valid_until), SECRET)

    bridge = WhatsAppBridge(
        chat=service, secret=SECRET, quotes=quotes, latest_quote=latest, pdf_link=link
    )
    bridge.respond("cotiza 50 vasos en Bogotá", chat)
    bridge.respond("no", chat)

    # Act
    reply = bridge.respond("sí", chat)

    # Assert
    (row,) = stored_quotes(engine)
    assert row["owner_key"] == owner_key_for(chat, SECRET)
    assert reply.quote_ids == (row["id"],)
    assert f"{base}/cotizaciones/{row['id']}.pdf?t=" in reply.text
    assert "Te envío el PDF" not in reply.text
    assert "Descarga el PDF de tu cotización" not in reply.text
    assert "](" not in reply.text
