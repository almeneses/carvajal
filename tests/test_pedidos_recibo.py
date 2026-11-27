"""Recibo del pedido confirmado (spec 056): PDF de Odoo por la web y por WhatsApp.

Tras el «sí» final, el chat muestra el recibo (número, folio, fecha, líneas y total) y
termina en «📄 Te envío el recibo del pedido en PDF.». Cada canal cambia esa promesa: la web
por el enlace ``/pedidos/{folio}.pdf`` (cookie del dueño) y WhatsApp por el PDF adjunto
(``ADJUNTAR_PDF``) o un enlace firmado con su propio dominio (``ORDER_DOMAIN``). Cubren:

- el adaptador: ``OdooOrders.order_pdf`` lee el adjunto PDF del pedido confirmado;
- la plantilla: el recibo y ``receipt_offer`` (sin cruzarse con ``pdf_offer``);
- la ruta: dueño por cookie o firma, 404 sin prueba y 503 sin Odoo;
- la web (stream e historial) y WhatsApp (puente y de punta a punta) con el servicio real;
- ``apps.mcp.server``: el enlace que firma el puente real lo acepta la ruta.
"""

import base64
import json
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from tests.test_api_stream import parse_sse
from tests.test_chat_service_tools import assistant_rows, named, stored_quotes
from tests.test_pdf_web import _app as quote_app
from tests.test_pdf_web import _register, _turn, _visitor
from tests.test_pedidos_odoo import FakeOrders, _line, _service

from apps.api.chat.identity import SignedCookieIdentity
from apps.api.chat.streaming import ChatEvent
from apps.api.main import create_app
from apps.api.quote_links import (
    ORDER_DOMAIN,
    link_expiry,
    order_pdf_url,
    pdf_url,
    sign,
)
from apps.api.security import CSRF_COOKIE, CSRF_FIELD
from apps.api.views.pedido_pdf import NOT_FOUND
from apps.api.views.stream import _with_receipt_link
from apps.mcp.bridge import ATTACH_PDF_PREFIX, WhatsAppBridge, _Turn, owner_key_for
from carvajal.adapters.crm.odoo import OdooClient, OdooError, OdooOrders
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_session_factory
from carvajal.adapters.storage.quotes import SqlQuoteRepository
from carvajal.quotation import render
from carvajal.quotation.draft import QuoteDraft

SECRET = b"secreto-de-prueba"
IDENTITY = SignedCookieIdentity(SECRET)
PDF = b"%PDF-1.7\nrecibo del pedido S00012\n%%EOF"
FOLIO = "a3f09c12b7e4"
OWNER = "dueno-recibo"
ORDER = "S00012"
BASE = "https://carvajal.example"
MEDIA = "http://203.0.113.10:8080"
RECEIPT_LINK = re.compile(
    r"📄 \[Descarga el recibo de tu pedido (S\d+)\]\((http://testserver/pedidos/([0-9a-f]{12})\.pdf)\)"
)


# --- 1. adaptador: PDF del pedido en Odoo -------------------------------------------------


class AttachmentOdoo:
    """Odoo simulado para ``order_pdf``: el pedido confirmado y su adjunto, o nada.

    ``order`` es el pedido que devuelve la búsqueda en ``sale.order`` (``None``, ninguno);
    ``datas`` es el contenido en base64 del adjunto (``None``, sin adjunto; ``False``, como
    responde Odoo un binario vacío); ``status`` responde ese código a todo.
    """

    def __init__(
        self,
        *,
        order: dict[str, Any] | None = None,
        datas: str | bool | None = None,
        status: int = 200,
    ) -> None:
        self.order = order
        self.datas = datas
        self.status = status
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        _, _, model, method = request.url.path.strip("/").split("/")
        body = json.loads(request.content)
        self.calls.append((model, method, body))
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "detalle interno"})
        if model == "sale.order":
            return httpx.Response(200, json=[self.order] if self.order else [])
        if model == "ir.attachment":
            found = [] if self.datas is None else [{"id": 7, "datas": self.datas}]
            return httpx.Response(200, json=found)
        return httpx.Response(200, json=[])

    def bodies(self, model: str) -> list[dict[str, Any]]:
        return [body for name, _, body in self.calls if name == model]


def _odoo_orders(fake: AttachmentOdoo) -> OdooOrders:
    client = OdooClient(
        "http://odoo:8069", "carvajal", "clave", transport=httpx.MockTransport(fake)
    )
    return OdooOrders(client)


def test_order_pdf_devuelve_el_adjunto_del_pedido_confirmado() -> None:
    # Arrange
    fake = AttachmentOdoo(order={"id": 90}, datas=base64.b64encode(PDF).decode())

    # Act
    document = _odoo_orders(fake).order_pdf("COT-1")

    # Assert
    assert document == PDF


def test_order_pdf_busca_el_pedido_confirmado_del_folio_y_su_pdf() -> None:
    # Arrange
    fake = AttachmentOdoo(order={"id": 90}, datas=base64.b64encode(PDF).decode())

    # Act
    _odoo_orders(fake).order_pdf("COT-1")

    # Assert: solo el pedido en «sale» y, de él, el primer PDF que generó quien lo creó (el
    # bot, al confirmar): no un documento que el equipo subió después al chatter.
    (order_query,) = fake.bodies("sale.order")
    assert order_query["domain"] == [["client_order_ref", "=", "COT-1"], ["state", "=", "sale"]]
    (attachment_query,) = fake.bodies("ir.attachment")
    assert [model_method[:2] for model_method in fake.calls] == [
        ("sale.order", "search_read"),
        ("ir.attachment", "search_read"),
    ]
    domain = attachment_query["domain"]
    assert ["res_model", "=", "sale.order"] in domain
    assert ["res_id", "=", 90] in domain
    assert ["mimetype", "=", "application/pdf"] in domain
    assert attachment_query["order"] == "id asc"
    assert attachment_query["limit"] == 1
    assert attachment_query["fields"] == ["datas"]


def test_order_pdf_solo_toma_el_pdf_de_quien_creo_el_pedido() -> None:
    # Arrange: el pedido lo creó el bot (uid 5).
    fake = AttachmentOdoo(
        order={"id": 90, "create_uid": [5, "Asistente Carvajal"]},
        datas=base64.b64encode(PDF).decode(),
    )

    # Act
    _odoo_orders(fake).order_pdf("COT-1")

    # Assert
    (attachment_query,) = fake.bodies("ir.attachment")
    assert ["create_uid", "=", 5] in attachment_query["domain"]


def test_order_pdf_sin_pedido_confirmado_no_busca_adjuntos() -> None:
    # Arrange: un borrador o un cancelado no aparecen con ``state = sale``.
    fake = AttachmentOdoo(order=None, datas=base64.b64encode(PDF).decode())

    # Act
    document = _odoo_orders(fake).order_pdf("COT-1")

    # Assert
    assert document is None
    assert fake.bodies("ir.attachment") == []


@pytest.mark.parametrize("datas", [None, False, ""], ids=["sin_adjunto", "binario_false", "vacio"])
def test_order_pdf_sin_adjunto_devuelve_none(datas: str | bool | None) -> None:
    # Arrange
    fake = AttachmentOdoo(order={"id": 90}, datas=datas)

    # Act
    document = _odoo_orders(fake).order_pdf("COT-1")

    # Assert
    assert document is None


def test_order_pdf_con_odoo_caido_lanza_odoo_error() -> None:
    # Arrange: la ruta lo convierte en 503 para que el enlace se pueda reintentar.
    fake = AttachmentOdoo(status=500)

    # Act / Assert
    with pytest.raises(OdooError):
        _odoo_orders(fake).order_pdf("COT-1")


# --- 2. plantilla: recibo y receipt_offer ---------------------------------------------------


class FixedDatetime(datetime):
    """``datetime.now`` fijo: la fecha del recibo no depende del reloj."""

    @classmethod
    def now(cls, tz: Any = None) -> "FixedDatetime":
        return cls(2026, 11, 26, 10, 0, tzinfo=tz)


def _two_lines(shipping: dict[str, Any] | None = None) -> QuoteDraft:
    first = _line("V-12")
    second = _line("V-16", shipping=shipping)
    second["primary"] = {**second["primary"], "name": "Vaso 16 oz"}
    return QuoteDraft(lines=(first, second), state="registrada", folio="COT-1")


def test_el_recibo_trae_numero_folio_fecha_lineas_total_correo_y_la_promesa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setattr(render, "datetime", FixedDatetime)
    draft = _two_lines()

    # Act
    message = render.order_placed(ORDER, "ana@correo.com", "COT-1", draft)

    # Assert
    lines = message.text.splitlines()
    assert lines[0] == f"🧾 **Pedido {ORDER} confirmado** · cotización #COT-1"
    assert lines[1] == f"Fecha: {render.format_date(date(2026, 11, 26))}"
    assert lines[2].startswith("1. Vaso 12 oz × 100 · $50.000")  # noqa: RUF001
    assert lines[3].startswith("2. Vaso 16 oz × 100 · $50.000")  # noqa: RUF001
    assert lines[4].startswith("Total **$100.000** · IVA")
    assert "La confirmación del pedido llega a **ana@correo.com**" in message.text
    assert lines[-1] == render.RECEIPT_PROMISE
    # La voz no lee el correo ni las cifras: el detalle va por escrito.
    assert "ana@correo.com" not in message.spoken
    assert "recibo" in message.spoken


def test_el_recibo_con_envio_lo_suma_al_total(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setattr(render, "datetime", FixedDatetime)
    draft = _two_lines({"requested": True, "status": "confirmed", "cost": "8000"})

    # Act
    message = render.order_placed(ORDER, "ana@correo.com", "COT-1", draft)

    # Assert
    assert "Total **$108.000**" in message.text
    assert "envío por confirmar" not in message.text
    assert message.text.endswith(f"\n{render.RECEIPT_PROMISE}")


def test_el_recibo_con_envio_sin_costo_avisa_que_falta_confirmarlo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    monkeypatch.setattr(render, "datetime", FixedDatetime)
    draft = _two_lines({"requested": True, "status": "pending"})

    # Act
    message = render.order_placed(ORDER, "ana@correo.com", "COT-1", draft)

    # Assert
    assert "Total **$100.000** (envío por confirmar)" in message.text


def test_el_recibo_sin_folio_no_promete_el_pdf() -> None:
    # Act
    message = render.order_placed(ORDER, "ana@correo.com", None, _two_lines())

    # Assert
    assert message.text.splitlines()[0] == f"🧾 **Pedido {ORDER} confirmado**"
    assert render.RECEIPT_PROMISE not in message.text
    assert "PDF" not in message.text


def _receipt(folio: str = FOLIO) -> str:
    return render.order_placed(ORDER, "ana@correo.com", folio, _two_lines()).text


def test_receipt_offer_cambia_la_promesa_por_el_enlace_con_el_numero() -> None:
    # Arrange
    asked: list[str] = []

    def link(folio: str) -> str:
        asked.append(folio)
        return f"{BASE}/pedidos/{folio}.pdf"

    # Act
    offered = render.receipt_offer(_receipt(), link)

    # Assert: el enlace es del folio (la ruta busca por él) y nombra el pedido.
    assert asked == [FOLIO]
    assert render.RECEIPT_PROMISE not in offered
    assert offered.endswith(
        f"\n📄 [Descarga el recibo de tu pedido {ORDER}]({BASE}/pedidos/{FOLIO}.pdf)"
    )
    assert offered.startswith(f"🧾 **Pedido {ORDER} confirmado** · cotización #{FOLIO}\n")


def test_receipt_offer_sin_url_quita_la_promesa() -> None:
    # Arrange
    receipt = _receipt()

    # Act
    offered = render.receipt_offer(receipt, lambda _folio: None)

    # Assert
    assert offered == receipt.removesuffix(f"\n{render.RECEIPT_PROMISE}")
    assert "PDF" not in offered


@pytest.mark.parametrize(
    "message",
    [
        "Te envío el recibo del pedido en PDF.",  # prosa sin el encabezado del recibo
        f"🧾 **Pedido {ORDER} confirmado** · cotización #{FOLIO}\nSin la promesa.",
        "✅ **Cotización #a3f09c12b7e4 registrada**\n1 producto\n📄 Te envío el PDF.",
    ],
)
def test_receipt_offer_no_toca_otros_mensajes(message: str) -> None:
    # Act
    offered = render.receipt_offer(message, lambda _folio: f"{BASE}/pedidos/x.pdf")

    # Assert
    assert offered == message


def test_receipt_offer_y_pdf_offer_no_se_cruzan() -> None:
    # Arrange
    registered = render.registered(_two_lines(), FOLIO, None).text
    receipt = _receipt()

    def quote_link(folio: str) -> str:
        return f"{BASE}/cotizaciones/{folio}.pdf"

    def order_link(folio: str) -> str:
        return f"{BASE}/pedidos/{folio}.pdf"

    # Act
    receipt_with_quote_link = render.pdf_offer(receipt, quote_link)
    registered_with_order_link = render.receipt_offer(registered, order_link)
    both = render.receipt_offer(render.pdf_offer(receipt, quote_link), order_link)

    # Assert
    assert receipt_with_quote_link == receipt
    assert registered_with_order_link == registered
    assert "/cotizaciones/" not in both
    assert f"/pedidos/{FOLIO}.pdf" in both


# --- 3. ruta GET /pedidos/{folio}.pdf -------------------------------------------------------


class FakeOrderPdf:
    """``app.state.order_pdf``: el PDF, ``None`` o un fallo de Odoo; anota los folios."""

    def __init__(self, document: bytes | None = PDF, *, error: Exception | None = None) -> None:
        self.document = document
        self.error = error
        self.calls: list[str] = []

    def __call__(self, folio: str) -> bytes | None:
        self.calls.append(folio)
        if self.error is not None:
            raise self.error
        return self.document


def _route_app(order_pdf: Any = None, *, owner: str = OWNER) -> FastAPI:
    """App real con la cotización ``FOLIO`` de ``owner`` y el PDF del pedido inyectado."""

    def lookup(folio: str) -> tuple[str, dict[str, Any]] | None:
        return (owner, {}) if folio == FOLIO else None

    app = create_app()
    app.state.chat_identity = IDENTITY
    app.state.quote_lookup = lookup
    app.state.quote_link_secret = SECRET
    if order_pdf is not None:
        app.state.order_pdf = order_pdf
    return app


def _owner_client(app: FastAPI, owner: str | None = OWNER) -> TestClient:
    client = TestClient(app)
    if owner is not None:
        client.cookies.set(IDENTITY.cookie_name, IDENTITY.issue(owner))
    return client


def _path(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}" if parts.query else parts.path


def _assert_not_found(response: Any) -> None:
    assert response.status_code == 404
    assert response.json() == {"detail": NOT_FOUND}
    assert "no-store" in response.headers["cache-control"]


def test_el_dueno_descarga_el_recibo_con_su_cookie() -> None:
    # Arrange
    reader = FakeOrderPdf()
    client = _owner_client(_route_app(reader))

    # Act
    response = client.get(f"/pedidos/{FOLIO}.pdf")

    # Assert
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert f'filename="pedido-{FOLIO}.pdf"' in response.headers["content-disposition"]
    assert "no-store" in response.headers["cache-control"]
    assert response.content == PDF
    assert reader.calls == [FOLIO]


def test_la_cookie_de_otro_no_abre_el_recibo() -> None:
    # Arrange
    reader = FakeOrderPdf()
    client = _owner_client(_route_app(reader), "otro-dueno")

    # Act
    response = client.get(f"/pedidos/{FOLIO}.pdf")

    # Assert: ni siquiera se consulta Odoo.
    _assert_not_found(response)
    assert reader.calls == []


def test_sin_cookie_ni_firma_no_se_abre_el_recibo() -> None:
    # Arrange
    reader = FakeOrderPdf()
    client = _owner_client(_route_app(reader), None)

    # Act
    response = client.get(f"/pedidos/{FOLIO}.pdf")

    # Assert
    _assert_not_found(response)
    assert reader.calls == []


def test_un_folio_que_no_existe_responde_igual_que_uno_ajeno() -> None:
    # Arrange
    client = _owner_client(_route_app(FakeOrderPdf()))

    # Act
    missing = client.get("/pedidos/ffffffffffff.pdf")
    too_long = client.get(f"/pedidos/{'f' * 65}.pdf")

    # Assert
    _assert_not_found(missing)
    _assert_not_found(too_long)


def test_el_enlace_firmado_del_pedido_abre_el_recibo_sin_cookie() -> None:
    # Arrange
    reader = FakeOrderPdf()
    client = _owner_client(_route_app(reader), None)
    url = order_pdf_url(BASE, FOLIO, OWNER, link_expiry(None), SECRET)

    # Act
    response = client.get(_path(url))

    # Assert
    assert response.status_code == 200
    assert response.content == PDF
    assert reader.calls == [FOLIO]


def test_el_enlace_firmado_usa_el_dominio_del_pedido() -> None:
    # Arrange
    expires = link_expiry(None)

    # Act
    url = order_pdf_url(BASE, FOLIO, OWNER, expires, SECRET)

    # Assert
    token = sign(FOLIO, OWNER, expires, SECRET, domain=ORDER_DOMAIN)
    assert url == f"{BASE}/pedidos/{FOLIO}.pdf?t={expires}.{token}"
    assert token != sign(FOLIO, OWNER, expires, SECRET)


def test_la_firma_de_la_cotizacion_no_abre_el_recibo() -> None:
    # Arrange: el mismo folio, dueño y vencimiento, firmados para el PDF de la cotización.
    reader = FakeOrderPdf()
    client = _owner_client(_route_app(reader), None)
    quote = urlsplit(pdf_url(BASE, FOLIO, OWNER, link_expiry(None), SECRET))

    # Act
    response = client.get(f"/pedidos/{FOLIO}.pdf?{quote.query}")

    # Assert
    _assert_not_found(response)
    assert reader.calls == []


def test_una_firma_ajena_tampoco_se_salva_con_la_cookie_del_dueno() -> None:
    # Arrange: con ``t``, solo cuenta la firma (como en el PDF de la cotización).
    client = _owner_client(_route_app(FakeOrderPdf()))
    quote = urlsplit(pdf_url(BASE, FOLIO, OWNER, link_expiry(None), SECRET))

    # Act
    response = client.get(f"/pedidos/{FOLIO}.pdf?{quote.query}")

    # Assert
    _assert_not_found(response)


def test_el_enlace_firmado_para_otro_dueno_no_abre_el_recibo() -> None:
    # Arrange
    client = _owner_client(_route_app(FakeOrderPdf()), None)
    url = order_pdf_url(BASE, FOLIO, "otro-dueno", link_expiry(None), SECRET)

    # Act
    response = client.get(_path(url))

    # Assert
    _assert_not_found(response)


@pytest.mark.parametrize(
    "expires",
    [
        int((datetime.now(UTC) - timedelta(hours=1)).timestamp()),
        int((datetime.now(UTC) + timedelta(days=30)).timestamp()),
    ],
    ids=["vencido", "mas_alla_del_tope"],
)
def test_un_enlace_vencido_o_demasiado_largo_no_abre_el_recibo(expires: int) -> None:
    # Arrange
    reader = FakeOrderPdf()
    client = _owner_client(_route_app(reader), None)
    url = order_pdf_url(BASE, FOLIO, OWNER, expires, SECRET)

    # Act
    response = client.get(_path(url))

    # Assert
    _assert_not_found(response)
    assert reader.calls == []


def test_sin_pdf_en_odoo_responde_404() -> None:
    # Arrange: el pedido no está confirmado o Odoo aún no adjuntó el PDF.
    client = _owner_client(_route_app(FakeOrderPdf(None)))

    # Act
    response = client.get(f"/pedidos/{FOLIO}.pdf")

    # Assert
    _assert_not_found(response)


@pytest.mark.parametrize(
    "error",
    [OdooError("Odoo no responde (ConnectError)."), httpx.ReadTimeout("lento")],
    ids=["odoo_error", "timeout"],
)
def test_si_odoo_falla_responde_503(error: Exception) -> None:
    # Arrange
    client = _owner_client(_route_app(FakeOrderPdf(error=error)))

    # Act
    response = client.get(f"/pedidos/{FOLIO}.pdf")

    # Assert: el enlace se puede reintentar; el mensaje de Odoo no sale.
    assert response.status_code == 503
    assert "Odoo" not in response.text


def test_sin_odoo_configurado_responde_503() -> None:
    # Arrange: sin ``order_pdf`` inyectado, la ruta construye el lector con los ajustes.
    app = _route_app()
    app.state.settings = app.state.settings.model_copy(
        update={"odoo_url": "", "odoo_db": "", "odoo_api_key": SecretStr("")}
    )
    client = _owner_client(app)

    # Act
    response = client.get(f"/pedidos/{FOLIO}.pdf")

    # Assert
    assert not app.state.settings.odoo_configured
    assert response.status_code == 503
    assert getattr(app.state, "order_pdf", None) is None


# --- 4. web: stream e historial ---------------------------------------------------------


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


def _order_app(engine: Any, reader: FakeOrderPdf | None = None) -> FastAPI:
    """La app de ``tests/test_pdf_web.py`` con pedidos en un Odoo simulado."""

    app = quote_app(engine)
    app.state.chat, _ = _service(engine, FakeOrders())
    app.state.order_pdf = reader or FakeOrderPdf()
    return app


def _web_order(client: TestClient) -> dict[str, dict[str, Any]]:
    """Registra la cotización y hace el pedido: el último turno es el recibo."""

    _register(client)
    _turn(client, "Sí, quiero hacer el pedido")
    _turn(client, "Ana Pérez, ana@correo.com")
    return _turn(client, "sí")


def test_el_stream_del_pedido_confirmado_enlaza_el_recibo(engine: Any) -> None:
    # Arrange
    client = _visitor(_order_app(engine))

    # Act
    events = _web_order(client)

    # Assert
    (row,) = stored_quotes(engine)
    answer = events["fallback"]["text"]
    found = RECEIPT_LINK.search(answer)
    assert found is not None, answer
    assert found.group(1) == "S00001"
    assert found.group(3) == row["id"]
    assert render.RECEIPT_PROMISE not in answer
    assert "**Pedido S00001 confirmado**" in answer
    # Lo guardado conserva el texto común: WhatsApp lee el mismo servicio.
    assert assistant_rows(engine)[-1]["content"].endswith(render.RECEIPT_PROMISE)


def test_el_enlace_del_stream_descarga_el_recibo_con_la_cookie(engine: Any) -> None:
    # Arrange
    reader = FakeOrderPdf()
    app = _order_app(engine, reader)
    found = RECEIPT_LINK.search(_web_order(_visitor(app))["fallback"]["text"])
    assert found is not None

    # Act
    mine = _visitor(app).get(found.group(2))
    other = _visitor(app, "b" * 64).get(found.group(2))

    # Assert
    assert mine.status_code == 200
    assert mine.content == PDF
    _assert_not_found(other)
    (row,) = stored_quotes(engine)
    assert reader.calls == [row["id"]]


def test_la_voz_de_la_web_no_lee_el_enlace_del_recibo(engine: Any) -> None:
    # Arrange
    client = _visitor(_order_app(engine))

    # Act
    events = _web_order(client)

    # Assert
    summary = events["done"].get("voz_resumen") or ""
    for mark in ("http", "pedidos/", ".pdf", "["):
        assert mark not in summary, mark


def test_el_historial_de_la_web_enlaza_el_recibo(engine: Any) -> None:
    # Arrange
    client = _visitor(_order_app(engine))
    _web_order(client)
    (row,) = stored_quotes(engine)

    # Act
    page = client.get("/chat")

    # Assert: el turno guardado (con los datos enmascarados) enlaza el recibo, sin la promesa.
    assert page.status_code == 200
    thread = page.text.split('<ol class="hilo">', 1)[1].split('class="panel-hilos-cuerpo"', 1)[0]
    assert f'href="http://testserver/pedidos/{row["id"]}.pdf"' in thread
    assert "Descarga el recibo de tu pedido S00001" in thread
    assert "Te envío el recibo del pedido en PDF" not in thread
    # Y el PDF de la cotización sigue enlazado en su turno.
    assert f'href="http://testserver/cotizaciones/{row["id"]}.pdf"' in thread


def test_un_reintento_del_si_final_en_la_web_sigue_enlazando_el_recibo(engine: Any) -> None:
    # Arrange: el navegador reintenta el último turno con el mismo ``client_turn_id``.
    client = _visitor(_order_app(engine))
    _register(client)
    _turn(client, "Sí, quiero hacer el pedido")
    _turn(client, "Ana Pérez, ana@correo.com")
    form = {"question": "sí", "client_turn_id": "turno-final", CSRF_FIELD: ""}
    form[CSRF_FIELD] = client.cookies[CSRF_COOKIE]
    first = client.post("/chat/stream", data=form)
    assert first.status_code == 200

    # Act
    retry = client.post("/chat/stream", data=form)

    # Assert: la misma respuesta enlazada, no la promesa de un PDF que no llega.
    assert retry.status_code == 200
    shown = "".join(
        str(data.get("text", ""))
        for name, data in parse_sse(retry.text)
        if name in {"token", "fallback"}
    )
    assert "**Pedido S00001 confirmado**" in shown
    assert "Descarga el recibo de tu pedido S00001" in shown
    assert render.RECEIPT_PROMISE not in shown


def test_with_receipt_link_solo_cambia_el_texto_del_recibo() -> None:
    # Arrange
    receipt = ChatEvent("fallback", {"text": _receipt(), "sources": [{"id": 1}]})
    other = ChatEvent("fallback", {"text": "Hola", "sources": []})

    def link(folio: str) -> str:
        return f"/pedidos/{folio}.pdf"

    # Act
    offered = _with_receipt_link(receipt, link)
    untouched = _with_receipt_link(other, link)

    # Assert
    assert offered.name == "fallback"
    assert offered.data["sources"] == [{"id": 1}]
    assert (
        f"[Descarga el recibo de tu pedido {ORDER}](/pedidos/{FOLIO}.pdf)" in offered.data["text"]
    )
    assert untouched is other


# --- 5. WhatsApp ---------------------------------------------------------------------------


class NoChat:
    """El puente no llama al chat en ``_render_flow``."""

    def stream(
        self,
        owner_key: str,
        question: str,
        *,
        client_turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> Iterator[ChatEvent]:  # pragma: no cover
        raise AssertionError("no se esperaba un turno del chat")

    def new_conversation(self, owner_key: str) -> Any:  # pragma: no cover
        raise AssertionError("no se esperaba un hilo nuevo")


def _flow_turn(order: dict[str, Any] | None = None) -> _Turn:
    template = _receipt()
    flow: dict[str, Any] = {"text": template, "spoken": "Listo, tu pedido quedó confirmado."}
    if order is not None:
        flow["order"] = order
    return _Turn(template, [], [], None, flow)


def _bridge(**links: Any) -> WhatsAppBridge:
    return WhatsAppBridge(chat=NoChat(), secret=SECRET, **links)


ORDER_FLOW = {"name": ORDER, "folio": FOLIO}


def test_whatsapp_adjunta_el_recibo_con_el_numero_del_pedido() -> None:
    # Arrange
    asked: list[tuple[str, str]] = []

    def attachment(folio: str, owner: str) -> str:
        asked.append((folio, owner))
        return f"{MEDIA}/pedidos/{folio}.pdf?t=1.firma"

    bridge = _bridge(order_attachment=attachment, order_link=lambda f, o: f"{BASE}/pedidos/{f}.pdf")

    # Act
    reply = bridge._render_flow(_flow_turn(ORDER_FLOW), "wa_dueno")

    # Assert: con adjunto, el enlace no hace falta.
    assert asked == [(FOLIO, "wa_dueno")]
    assert (
        f"📄 Te envío el recibo de tu pedido {ORDER} en PDF.\n"
        f"{ATTACH_PDF_PREFIX}{MEDIA}/pedidos/{FOLIO}.pdf?t=1.firma"
    ) in reply.text
    assert reply.text.count(ATTACH_PDF_PREFIX) == 1
    assert render.RECEIPT_PROMISE not in reply.text
    assert BASE not in reply.text
    assert f"*Pedido {ORDER} confirmado*" in reply.text


def test_whatsapp_sin_adjunto_enlaza_el_recibo() -> None:
    # Arrange
    bridge = _bridge(order_link=lambda folio, owner: f"{BASE}/pedidos/{folio}.pdf?t=1.firma")

    # Act
    reply = bridge._render_flow(_flow_turn(ORDER_FLOW), "wa_dueno")

    # Assert: el enlace va solo en su línea para que WhatsApp lo detecte entero.
    assert f"{ORDER} en PDF. Ábrelo aquí:\n\n{BASE}/pedidos/{FOLIO}.pdf?t=1.firma" in reply.text
    assert ATTACH_PDF_PREFIX not in reply.text
    assert render.RECEIPT_PROMISE not in reply.text
    assert "](" not in reply.text


@pytest.mark.parametrize(
    "links",
    [
        {},
        {"order_attachment": lambda folio, owner: (_ for _ in ()).throw(RuntimeError("x"))},
    ],
    ids=["sin_enlaces", "enlace_falla"],
)
def test_whatsapp_sin_como_entregar_el_recibo_no_lo_promete(links: dict[str, Any]) -> None:
    # Arrange
    bridge = _bridge(**links)

    # Act
    reply = bridge._render_flow(_flow_turn(ORDER_FLOW), "wa_dueno")

    # Assert
    assert render.RECEIPT_PROMISE not in reply.text
    assert "recibo" not in reply.text
    assert ATTACH_PDF_PREFIX not in reply.text
    assert f"*Pedido {ORDER} confirmado*" in reply.text


def test_whatsapp_sin_order_en_el_flujo_deja_el_texto_igual() -> None:
    # Arrange
    bridge = _bridge(order_attachment=lambda folio, owner: f"{MEDIA}/pedidos/{folio}.pdf")
    template = _receipt()

    # Act
    written = bridge._with_receipt(template, {"text": template}, "wa_dueno")

    # Assert
    assert written == template


# --- 5b. WhatsApp de punta a punta con el servicio real -----------------------------------


CHAT = "+573001112233"


def _quote_readers(engine: Any) -> dict[str, Any]:
    factory = build_session_factory(engine)

    def quotes(folio: str, owner: str) -> dict[str, Any] | None:
        with factory() as session:
            record = SqlQuoteRepository(session).get(folio, owner)
            return None if record is None else dict(record.payload)

    def latest(owner: str) -> dict[str, Any] | None:
        with factory() as session:
            record = SqlQuoteRepository(session).latest(owner)
            return None if record is None else dict(record.payload)

    def lookup(folio: str) -> tuple[str, dict[str, Any]] | None:
        with factory() as session:
            record = SqlQuoteRepository(session).by_folio(folio)
            if record is None:
                return None
            return str(record.get("owner_key") or ""), dict(record.payload)

    return {"quotes": quotes, "latest": latest, "lookup": lookup}


def test_el_si_final_por_whatsapp_trae_el_pedido_en_el_flujo(engine: Any) -> None:
    # Arrange
    service, _ = _service(engine, FakeOrders())
    owner = owner_key_for(CHAT, SECRET)
    list(service.stream(owner, "cotiza 50 vasos en Bogotá"))
    list(service.stream(owner, "no"))
    list(service.stream(owner, "sí"))
    list(service.stream(owner, "hagamos el pedido"))
    list(service.stream(owner, "Ana Pérez, ana@correo.com"))

    # Act
    events = list(service.stream(owner, "sí"))

    # Assert
    (row,) = stored_quotes(engine)
    flow = named(events, "done").data["flow"]
    assert owner.startswith("wa_")
    assert flow["order"] == {"name": "S00001", "folio": row["id"]}
    assert flow["text"].endswith(render.RECEIPT_PROMISE)
    assert named(events, "done").data["quote_ids"] == []


def test_whatsapp_de_punta_a_punta_adjunta_el_recibo_que_abre_la_ruta(engine: Any) -> None:
    # Arrange: el puente con adjuntos (como con MEDIA_BASE_URL) sobre el servicio real.
    service, _ = _service(engine, FakeOrders())
    readers = _quote_readers(engine)

    def pdf_attachment(folio: str, owner: str, valid_until: date | None) -> str:
        return pdf_url(MEDIA, folio, owner, link_expiry(valid_until), SECRET)

    def order_attachment(folio: str, owner: str) -> str:
        return order_pdf_url(MEDIA, folio, owner, link_expiry(None), SECRET)

    bridge = WhatsAppBridge(
        chat=service,
        secret=SECRET,
        quotes=readers["quotes"],
        latest_quote=readers["latest"],
        pdf_attachment=pdf_attachment,
        order_attachment=order_attachment,
    )
    for message in ("cotiza 50 vasos en Bogotá", "no", "sí", "hagamos el pedido"):
        bridge.respond(message, CHAT)
    bridge.respond("Ana Pérez, ana@correo.com", CHAT)

    # Act
    reply = bridge.respond("sí", CHAT)

    # Assert: un solo adjunto, el del pedido (no el de la cotización otra vez).
    (row,) = stored_quotes(engine)
    folio = row["id"]
    assert "*Pedido S00001 confirmado*" in reply.text
    assert "Te envío el recibo de tu pedido S00001 en PDF." in reply.text
    assert reply.text.count(ATTACH_PDF_PREFIX) == 1
    (url,) = [
        line.removeprefix(ATTACH_PDF_PREFIX)
        for line in reply.text.splitlines()
        if line.startswith(ATTACH_PDF_PREFIX)
    ]
    assert url.startswith(f"{MEDIA}/pedidos/{folio}.pdf?t=")
    assert "/cotizaciones/" not in reply.text
    assert render.RECEIPT_PROMISE not in reply.text
    # La persona ve su correo en el turno; lo guardado lo enmascara.
    assert "ana@correo.com" in reply.text

    # Y la ruta de la web abre ese adjunto (OpenClaw lo descarga sin cookie).
    app = create_app()
    app.state.quote_lookup = readers["lookup"]
    app.state.quote_link_secret = SECRET
    reader = FakeOrderPdf()
    app.state.order_pdf = reader
    response = TestClient(app).get(_path(url))
    assert response.status_code == 200
    assert response.content == PDF
    assert reader.calls == [folio]


def test_el_reintento_del_si_final_por_whatsapp_vuelve_a_adjuntar_sin_otro_pedido(
    engine: Any,
) -> None:
    # Arrange: OpenClaw reintenta el mismo turno (mismo ``client_turn_id``).
    orders = FakeOrders()
    service, _ = _service(engine, orders)
    owner = owner_key_for(CHAT, SECRET)
    for message in (
        "cotiza 50 vasos en Bogotá",
        "no",
        "sí",
        "hagamos el pedido",
        "Ana Pérez, ana@correo.com",
    ):
        list(service.stream(owner, message))
    first = list(service.stream(owner, "sí", client_turn_id="wa-final"))

    # Act
    retry = list(service.stream(owner, "sí", client_turn_id="wa-final"))

    # Assert: el turno guardado conserva el pedido (el puente vuelve a adjuntar el recibo)
    # y no se crea un segundo pedido; lo reemitido lleva el correo enmascarado.
    (row,) = stored_quotes(engine)
    flow = named(retry, "done").data["flow"]
    assert named(first, "done").data["flow"]["order"] == flow["order"]
    assert flow["order"] == {"name": "S00001", "folio": row["id"]}
    assert flow["text"].endswith(render.RECEIPT_PROMISE)
    assert "ana@correo.com" not in flow["text"]
    assert len(orders.requests) == 1


# --- 6. apps.mcp.server: el puente real firma con ORDER_DOMAIN --------------------------


@pytest.fixture()
def real_bridge(monkeypatch: pytest.MonkeyPatch, engine: Any) -> Iterator[WhatsAppBridge]:
    """``default_bridge`` con URL pública y de adjuntos, sin base ni modelo reales."""

    from apps.api import runtime
    from apps.api.chat import service as chat_service
    from apps.mcp import server
    from carvajal.adapters import judge

    monkeypatch.setenv("SESSION_SECRET", SECRET.decode())
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE)
    monkeypatch.setenv("MEDIA_BASE_URL", MEDIA)
    monkeypatch.setenv("FOOD_PHOTO_MEDIA_DIR", "")
    factory: sessionmaker[Any] = build_session_factory(engine)
    monkeypatch.setattr(runtime, "session_factory", lambda: factory)
    monkeypatch.setattr(chat_service, "default_chat_service", NoChat)
    monkeypatch.setattr(judge, "build_food_classifier", lambda settings: None)
    server.default_bridge.cache_clear()
    try:
        yield server.default_bridge()
    finally:
        server.default_bridge.cache_clear()


def test_el_puente_real_firma_el_recibo_y_la_ruta_lo_acepta(real_bridge: WhatsAppBridge) -> None:
    # Arrange
    assert real_bridge.order_attachment is not None
    assert real_bridge.order_link is not None
    reader = FakeOrderPdf()
    client = _owner_client(_route_app(reader, owner="wa_dueno"), None)

    # Act
    attached = real_bridge.order_attachment(FOLIO, "wa_dueno")
    linked = real_bridge.order_link(FOLIO, "wa_dueno")
    responses = [client.get(_path(url)) for url in (attached, linked)]
    as_quote = client.get(_path(attached).replace("/pedidos/", "/cotizaciones/", 1))

    # Assert: adjunto con MEDIA_BASE_URL, enlace con PUBLIC_BASE_URL; los dos abren el
    # recibo y ninguno abre el PDF de la cotización del mismo folio.
    assert attached.startswith(f"{MEDIA}/pedidos/{FOLIO}.pdf?t=")
    assert linked.startswith(f"{BASE}/pedidos/{FOLIO}.pdf?t=")
    assert [response.status_code for response in responses] == [200, 200]
    assert reader.calls == [FOLIO, FOLIO]
    assert as_quote.status_code == 404


def test_un_enlace_de_pedido_no_abre_el_pdf_de_la_cotizacion(engine: Any) -> None:
    # Arrange: una cotización real registrada en la web, con su dueño.
    app = _order_app(engine)
    _register(_visitor(app))
    (row,) = stored_quotes(engine)
    folio, owner = row["id"], row["owner_key"]
    expires = link_expiry(None)
    anonymous = TestClient(app)

    # Act
    quote_ok = anonymous.get(_path(pdf_url(BASE, folio, owner, expires, SECRET)))
    order_as_quote = urlsplit(order_pdf_url(BASE, folio, owner, expires, SECRET)).query
    crossed = anonymous.get(f"/cotizaciones/{folio}.pdf?{order_as_quote}")

    # Assert: la firma de la cotización sí abre su PDF; la del pedido, no.
    assert quote_ok.status_code == 200
    assert quote_ok.content.startswith(b"%PDF-")
    assert crossed.status_code == 404


# --- revisión de la PR #279 --------------------------------------------------------------


def test_chat_provider_sigue_siendo_comprobable_en_tiempo_de_ejecucion() -> None:
    # Arrange
    from apps.api.chat.service import ChatProvider

    # Act / Assert: ``isinstance`` con un protocolo sin ``@runtime_checkable`` lanza TypeError.
    assert isinstance(object(), ChatProvider) is False


def test_los_enlaces_solo_se_aplican_al_turno_reemitido() -> None:
    # Arrange
    from apps.api.views.stream import _sse

    text = render.order_placed("S00001", "ana@correo.com", "COT-1")

    class Provider:
        def __init__(self, replay: bool) -> None:
            self.replay = replay

        def stream(self, *args: Any, **kwargs: Any) -> Any:
            data: dict[str, Any] = {"text": text.text}
            if self.replay:
                data["replay"] = True
            yield ChatEvent("token", data)
            yield ChatEvent("done", {})

    def link(folio: str) -> str:
        return f"/pedidos/{folio}.pdf"

    # Act
    fresh = "".join(_sse(cast(Any, Provider(False)), "dueno", "sí", receipt_link=link))
    replayed = "".join(_sse(cast(Any, Provider(True)), "dueno", "sí", receipt_link=link))

    # Assert
    assert "/pedidos/COT-1.pdf" not in fresh
    assert "/pedidos/COT-1.pdf" in replayed


def test_sin_odoo_el_lector_del_recibo_se_arma_una_sola_vez(monkeypatch: Any) -> None:
    # Arrange
    from types import SimpleNamespace

    from apps.api.views import pedido_pdf
    from carvajal.adapters import crm

    calls: list[Any] = []

    def fake_build(settings: Any) -> None:
        calls.append(settings)

    monkeypatch.setattr(crm, "build_orders", fake_build)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=object())))

    # Act
    first = pedido_pdf._order_pdf(cast(Any, request))
    second = pedido_pdf._order_pdf(cast(Any, request))

    # Assert
    assert first is None and second is None
    assert len(calls) == 1


def test_si_falla_armar_el_lector_del_recibo_no_se_queda_en_cache(monkeypatch: Any) -> None:
    # Arrange
    from types import SimpleNamespace

    from apps.api.views import pedido_pdf
    from carvajal.adapters import crm

    calls: list[Any] = []

    def broken(settings: Any) -> None:
        calls.append(settings)
        raise ValueError("configuración rota")

    monkeypatch.setattr(crm, "build_orders", broken)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=object())))

    # Act
    first = pedido_pdf._order_pdf(cast(Any, request))
    second = pedido_pdf._order_pdf(cast(Any, request))

    # Assert: 503 (None) y se vuelve a intentar en la siguiente descarga.
    assert first is None and second is None
    assert len(calls) == 2
