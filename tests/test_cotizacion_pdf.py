"""Pruebas de la cotización en PDF y su formato en WhatsApp.

Sin red ni Postgres: el payload sale del motor real (``quote_request``) y se sella como lo
hace el servicio de chat. Cubren:

- vista de lectura (``quote_view``): importes, sobrantes, IVA, envío, vigencia, pendientes;
- bloque de WhatsApp con folio, total y vigencia, y la oferta del PDF;
- pedido de «PDF» por WhatsApp: enlace firmado a la última cotización del hilo, sin
  LLM pero consumiendo cupo;
- enlaces firmados: firma, vencimiento, alteración y otro dueño;
- ruta ``/cotizaciones/{folio}.pdf``: 200 con PDF válido y 404 en cualquier otro caso;
- repositorio: ``latest`` y ``by_folio`` sobre SQLite.
"""

import csv
import importlib.util
import io
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from apps.api.chat.service import _stamp_quote
from apps.api.chat.streaming import ChatEvent
from apps.api.main import create_app
from apps.api.quote_links import (
    LINK_LEEWAY_SECONDS,
    MAX_LINK_DAYS,
    link_expiry,
    pdf_url,
    sign,
    verify,
)
from apps.api.quote_pdf import build_quote_pdf
from apps.api.quote_view import format_date, format_money, quote_view
from apps.api.views import catalogo_imagenes
from apps.api.views import cotizacion_pdf as cotizacion_pdf_view
from apps.mcp.bridge import (
    LIMIT_REACHED,
    NO_QUOTE_FOR_PDF,
    PDF_OFFER,
    PDF_UNAVAILABLE,
    WhatsAppBridge,
    is_pdf_request,
    owner_key_for,
    whatsapp_quote,
)
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_session_factory
from carvajal.adapters.storage.quotes import SqlQuoteRepository
from carvajal.application.quota import QuotaResult
from carvajal.catalog.images import catalog_image, clear_image_cache
from carvajal.quotation import QuoteRequest, quote_request

SECRET = b"secreto-de-prueba"
MOMENT = datetime(2026, 11, 24, 15, 0, tzinfo=UTC)
BASE = "http://203.0.113.10:8080"
REPO = Path(__file__).resolve().parents[1]


def stamped(quantity: int = 450, folio: str = "Q-7F3A2C") -> dict[str, Any]:
    """Cotización real del motor (portacomidas J1 en Bogotá), sellada con folio."""

    quoted = quote_request(
        QuoteRequest(sku="CO-PCOM-J1", quantity=quantity, city="Bogotá", delivery=False)
    )
    payload, _record = _stamp_quote(quoted, folio=folio, moment=MOMENT)
    return payload


# --- vista ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [("204370", "$204.370"), ("510.93", "$510,93"), (None, None), ("x", None)],
)
def test_format_money(value: Any, expected: str | None) -> None:
    assert format_money(value) == expected


def test_format_date() -> None:
    assert format_date("2026-11-18") == "18 nov 2026"
    assert format_date(datetime(2026, 12, 1, 5, tzinfo=UTC)) == "1 dic 2026"
    assert format_date("no es fecha") is None


def test_quote_view_lee_el_payload_sin_calcular() -> None:
    view = quote_view(stamped())

    assert view is not None
    assert view.folio == "Q-7F3A2C"
    assert view.product.startswith("Portacomidas espumado J1")
    assert (view.city, view.seller) == ("Bogotá", "EDEXA")
    assert view.price_per_presentation == "$102.185"
    assert (view.units_requested, view.presentations, view.units_bought) == (450, 3, 600)
    assert view.units_surplus == 150
    assert view.subtotal == view.total == "$306.555"
    assert view.tax == "Incluido"
    assert view.shipping == "No solicitado"
    assert view.price_date == "18 nov 2026"
    assert view.valid_until_date == date(2026, 12, 1)


def test_quote_view_rechaza_lo_que_no_es_cotizacion() -> None:
    assert quote_view({"kind": "opciones"}) is None
    assert quote_view({"kind": "cotizacion"}) is None


def test_total_ausente_queda_pendiente() -> None:
    payload = {**stamped(), "total": None, "pending_lines": ["Falta el costo de envío."]}

    view = quote_view(payload)
    assert view is not None and view.total is None
    block = whatsapp_quote(view)
    assert "⏳ *Total pendiente*" in block
    assert "_Falta el costo de envío._" in block
    assert "💰" not in block


# --- WhatsApp ------------------------------------------------------------------------------


def test_bloque_de_whatsapp() -> None:
    view = quote_view(stamped())
    assert view is not None

    block = whatsapp_quote(view)

    assert block.startswith("🧾 *Cotización #Q-7F3A2C*\n📍 Bogotá · EDEXA")
    assert "• Presentación: caja x 200 a $102.185" in block
    assert "• Pides 450 → compras 3 cajas (600 und., sobran 150)" in block
    assert "• IVA: incluido" in block
    assert "💰 *Total: $306.555 COP*" in block
    assert "_Precio publicado por EDEXA el 18 nov 2026 · válida hasta 1 dic 2026_" in block


@pytest.mark.parametrize(
    "message",
    ["PDF", "pdf", "Sí, el PDF", "envíame la cotización en pdf por favor", "dale pdf!"],
)
def test_pedidos_de_pdf(message: str) -> None:
    assert is_pdf_request(message)


@pytest.mark.parametrize(
    "message",
    ["¿el PDF trae el IVA?", "cotiza 400 vasos y mándame el pdf", "hola", ""],
)
def test_no_son_pedidos_de_pdf(message: str) -> None:
    assert not is_pdf_request(message)


@dataclass
class FakeChat:
    events: list[ChatEvent] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)

    def stream(self, owner_key: str, question: str, **_: Any) -> Iterator[ChatEvent]:
        self.calls.append(question)
        yield from self.events

    def new_conversation(self, owner_key: str) -> Any:
        return object()


def _links(folio: str, owner: str, valid_until: date | None) -> str:
    return pdf_url(BASE, folio, owner, link_expiry(valid_until, now=MOMENT), SECRET)


def test_la_respuesta_con_cotizacion_ofrece_el_pdf() -> None:
    payload = stamped()
    chat = FakeChat(
        events=[
            ChatEvent("token", {"text": "Aquí está tu cotización."}),
            ChatEvent("done", {"quote_ids": ["Q-7F3A2C"]}),
        ]
    )
    bridge = WhatsAppBridge(
        chat=chat,
        secret=SECRET,
        quotes=lambda folio, owner: payload,
        latest_quote=lambda owner: payload,
        pdf_link=_links,
    )

    answer = bridge.consult("Cotiza 450 J1 en Bogotá", "+573100000000")

    assert "🧾 *Cotización #Q-7F3A2C*" in answer
    assert answer.endswith(PDF_OFFER)


def test_no_ofrece_pdf_si_la_cotizacion_no_es_del_hilo_activo() -> None:
    payload = stamped()
    chat = FakeChat(events=[ChatEvent("done", {"quote_ids": ["Q-7F3A2C"]})])
    bridge = WhatsAppBridge(
        chat=chat,
        secret=SECRET,
        quotes=lambda folio, owner: payload,
        latest_quote=lambda owner: None,
        pdf_link=_links,
    )

    answer = bridge.consult("Cotiza", "+573100000000")

    assert "🧾 *Cotización #Q-7F3A2C*" in answer
    assert PDF_OFFER not in answer


def test_sin_url_publica_no_se_ofrece_el_pdf() -> None:
    payload = stamped()
    chat = FakeChat(events=[ChatEvent("done", {"quote_ids": ["Q-7F3A2C"]})])
    bridge = WhatsAppBridge(
        chat=chat, secret=SECRET, quotes=lambda f, o: payload, latest_quote=lambda o: payload
    )

    answer = bridge.consult("Cotiza", "+573100000000")

    assert PDF_OFFER not in answer
    assert bridge.consult("PDF", "+573100000000") == PDF_UNAVAILABLE


def test_pdf_devuelve_el_enlace_sin_llamar_al_agente_pero_consume_cupo() -> None:
    payload = stamped()
    chat = FakeChat()
    charged: list[str] = []
    asked: list[str] = []

    def latest(owner: str) -> Mapping[str, Any]:
        asked.append(owner)
        return payload

    def quota(owner: str) -> QuotaResult:
        charged.append(owner)
        return QuotaResult(allowed=True)

    bridge = WhatsAppBridge(
        chat=chat, secret=SECRET, latest_quote=latest, pdf_link=_links, quota=quota
    )

    answer = bridge.consult("Sí, el PDF", "+573100000000")

    owner = owner_key_for("+573100000000", SECRET)
    assert asked == [owner]
    assert chat.calls == [] and charged == [owner]
    assert answer.startswith("📄 Aquí está tu cotización #Q-7F3A2C en PDF:")
    assert f"{BASE}/cotizaciones/Q-7F3A2C.pdf?e=" in answer
    assert "_El enlace funciona hasta el 1 dic 2026._" in answer


def test_pdf_no_elude_el_cupo() -> None:
    chat = FakeChat()
    charged: list[str] = []

    def quota(owner: str) -> QuotaResult:
        charged.append(owner)
        return QuotaResult(allowed=False)

    bridge = WhatsAppBridge(
        chat=chat,
        secret=SECRET,
        latest_quote=lambda owner: stamped(),
        pdf_link=_links,
        quota=quota,
    )

    answer = bridge.consult("PDF", "+573100000000")

    assert answer == LIMIT_REACHED
    assert chat.calls == [] and charged


def test_pdf_responde_si_falla_la_consulta_del_hilo() -> None:
    def broken(owner: str) -> Mapping[str, Any]:
        raise RuntimeError("base")

    bridge = WhatsAppBridge(chat=FakeChat(), secret=SECRET, latest_quote=broken, pdf_link=_links)

    assert bridge.consult("PDF", "+573100000000") == PDF_UNAVAILABLE


def test_pdf_sin_cotizacion_previa() -> None:
    bridge = WhatsAppBridge(
        chat=FakeChat(), secret=SECRET, latest_quote=lambda owner: None, pdf_link=_links
    )

    assert bridge.consult("pdf", "+573100000000") == NO_QUOTE_FOR_PDF


def test_reintento_del_pedido_de_pdf_devuelve_el_mismo_enlace_sin_cobrar_de_nuevo() -> None:
    # Arrange
    payload = stamped()
    chat = FakeChat()
    charged: list[str] = []
    asked: list[str] = []

    def latest(owner: str) -> Mapping[str, Any]:
        asked.append(owner)
        return payload

    def quota(owner: str) -> QuotaResult:
        charged.append(owner)
        return QuotaResult(allowed=True)

    bridge = WhatsAppBridge(
        chat=chat, secret=SECRET, latest_quote=latest, pdf_link=_links, quota=quota
    )

    # Act
    first = bridge.respond("PDF", "+573100000000", client_turn_id="wh-pdf-1")
    second = bridge.respond("PDF", "+573100000000", client_turn_id="wh-pdf-1")

    # Assert
    assert first.text.startswith("📄 Aquí está tu cotización #Q-7F3A2C en PDF:")
    assert second == first
    assert len(charged) == 1 and len(asked) == 1
    assert chat.calls == []


def test_si_no_se_pudo_generar_el_pdf_el_reintento_lo_vuelve_a_intentar() -> None:
    # Arrange
    payload = stamped()
    failures = [RuntimeError("base")]

    def latest(owner: str) -> Mapping[str, Any]:
        if failures:
            raise failures.pop()
        return payload

    bridge = WhatsAppBridge(chat=FakeChat(), secret=SECRET, latest_quote=latest, pdf_link=_links)

    # Act
    first = bridge.respond("PDF", "+573100000000", client_turn_id="wh-pdf-2")
    second = bridge.respond("PDF", "+573100000000", client_turn_id="wh-pdf-2")

    # Assert
    assert first.text == PDF_UNAVAILABLE
    assert second.text.startswith("📄 Aquí está tu cotización #Q-7F3A2C en PDF:")


def test_el_servicio_mcp_recibe_la_url_publica() -> None:
    """El enlace al PDF lo arma el proceso MCP: su contenedor debe recibir PUBLIC_BASE_URL.

    ``mcp`` hereda el entorno de ``app`` con ``<<: *app-environment``; esta prueba evita
    que un cambio en esa fusión deje a WhatsApp sin oferta de PDF en producción.
    """

    # Arrange
    compose = yaml.safe_load((REPO / "compose.prod.yml").read_text())

    # Act
    services = compose["services"]

    # Assert
    for name in ("app", "mcp"):
        assert services[name]["environment"]["PUBLIC_BASE_URL"] == "${PUBLIC_BASE_URL:-}"


# --- enlaces -------------------------------------------------------------------------------


def test_firma_y_vencimiento() -> None:
    expires = link_expiry(date(2026, 12, 1), now=MOMENT)
    token = sign("Q-1", "wa_dueno", expires, SECRET)

    assert verify("Q-1", "wa_dueno", expires, token, SECRET, now=MOMENT)
    assert not verify("Q-2", "wa_dueno", expires, token, SECRET, now=MOMENT)
    assert not verify("Q-1", "wa_otro", expires, token, SECRET, now=MOMENT)
    assert not verify("Q-1", "wa_dueno", expires + 1, token, SECRET, now=MOMENT)
    assert not verify("Q-1", "wa_dueno", expires, token, b"otro", now=MOMENT)
    # Tolerancia de reloj entre MCP y web; pasados unos minutos ya no vale.
    near = datetime.fromtimestamp(expires + 1, UTC)
    assert verify("Q-1", "wa_dueno", expires, token, SECRET, now=near)
    late = datetime.fromtimestamp(expires + LINK_LEEWAY_SECONDS + 1, UTC)
    assert not verify("Q-1", "wa_dueno", expires, token, SECRET, now=late)
    assert verify("Q-1", "wa_dueno", expires, token.upper(), SECRET, now=MOMENT)


def test_firma_rechaza_un_vencimiento_mas_alla_del_tope() -> None:
    too_far = int((MOMENT + timedelta(days=MAX_LINK_DAYS + 1)).timestamp())
    token = sign("Q-1", "wa_dueno", too_far, SECRET)

    assert not verify("Q-1", "wa_dueno", too_far, token, SECRET, now=MOMENT)


def test_el_enlace_no_pasa_del_tope_ni_de_la_vigencia() -> None:
    far = link_expiry(date(2027, 6, 1), now=MOMENT)
    near = link_expiry(date(2026, 11, 25), now=MOMENT)

    assert far == int((MOMENT + timedelta(days=MAX_LINK_DAYS)).timestamp())
    assert near == int(datetime(2026, 11, 25, 23, 59, 59, tzinfo=UTC).timestamp())


# --- PDF y ruta ----------------------------------------------------------------------------


def test_build_quote_pdf_genera_un_pdf() -> None:
    view = quote_view(stamped())
    assert view is not None

    document = build_quote_pdf(view)

    assert document.startswith(b"%PDF-")
    assert len(document) > 10_000  # logo y tipografía incrustados


def _client(payload: Mapping[str, Any] | None, owner: str = "wa_dueno") -> TestClient:
    app = create_app()
    app.state.quote_link_secret = SECRET
    app.state.quote_lookup = lambda folio: None if payload is None else (owner, payload)
    return TestClient(app)


def _path(folio: str, owner: str, expires: int, secret: bytes = SECRET) -> str:
    return pdf_url("", folio, owner, expires, secret)


def test_ruta_usa_el_secreto_de_los_settings_sin_inyeccion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En producción nadie inyecta ``quote_link_secret``: sale de ``app.state.settings``."""

    monkeypatch.setenv("SESSION_SECRET", "secreto-de-produccion")
    app = create_app()
    app.state.quote_lookup = lambda folio: ("wa_dueno", stamped())
    client = TestClient(app)
    expires = int((datetime.now(UTC) + timedelta(days=1)).timestamp())

    response = client.get(_path("Q-7F3A2C", "wa_dueno", expires, b"secreto-de-produccion"))

    assert response.status_code == 200
    assert app.state.settings.session_secret == "secreto-de-produccion"


def test_ruta_entrega_el_pdf_con_firma_valida() -> None:
    expires = int((datetime.now(UTC) + timedelta(days=1)).timestamp())
    client = _client(stamped())

    response = client.get(_path("Q-7F3A2C", "wa_dueno", expires))

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "cotizacion-Q-7F3A2C.pdf" in response.headers["content-disposition"]
    assert "no-store" in response.headers["cache-control"]
    assert response.content.startswith(b"%PDF-")


@pytest.mark.parametrize("case", ["otro_dueno", "vencido", "sin_firma", "otro_secreto"])
def test_ruta_responde_404_si_la_firma_no_vale(case: str) -> None:
    future = int((datetime.now(UTC) + timedelta(days=1)).timestamp())
    past = int((datetime.now(UTC) - timedelta(seconds=LINK_LEEWAY_SECONDS + 1)).timestamp())
    paths = {
        "otro_dueno": _path("Q-7F3A2C", "wa_otro", future),
        "vencido": _path("Q-7F3A2C", "wa_dueno", past),
        "sin_firma": f"/cotizaciones/Q-7F3A2C.pdf?e={future}",
        "otro_secreto": _path("Q-7F3A2C", "wa_dueno", future, b"otro"),
    }

    response = _client(stamped()).get(paths[case])

    assert response.status_code == 404


def test_ruta_responde_404_si_el_folio_no_existe() -> None:
    expires = int((datetime.now(UTC) + timedelta(days=1)).timestamp())

    response = _client(None).get(_path("Q-NOEXISTE", "wa_dueno", expires))

    assert response.status_code == 404


def test_ruta_responde_404_si_el_pdf_no_se_puede_generar(monkeypatch: pytest.MonkeyPatch) -> None:
    expires = int((datetime.now(UTC) + timedelta(days=1)).timestamp())

    def explode(*args: Any, **kwargs: Any) -> bytes:
        raise RuntimeError("fpdf2")

    monkeypatch.setattr(cotizacion_pdf_view, "build_quote_pdf", explode)
    response = _client(stamped()).get(_path("Q-7F3A2C", "wa_dueno", expires))

    assert response.status_code == 404
    assert "fpdf2" not in response.text


# --- repositorio ---------------------------------------------------------------------------


def test_repositorio_latest_y_by_folio() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables.metadata.create_all(engine)
    factory = build_session_factory(engine)
    try:
        with factory() as session:
            repo = SqlQuoteRepository(session)
            records = [
                ("Q-1", "wa_a", "c-1"),
                ("Q-2", "wa_a", "c-2"),
                ("Q-3", "wa_b", "c-1"),
            ]
            for index, (folio, owner, conversation) in enumerate(records):
                repo.record(
                    folio,
                    owner,
                    payload=stamped(folio=folio),
                    total=None,
                    currency="COP",
                    valid_until=date(2026, 12, 1),
                    issued_at=MOMENT + timedelta(minutes=index),
                    conversation_id=conversation,
                )
            session.commit()
        with factory() as session:
            repo = SqlQuoteRepository(session)
            latest = repo.latest("wa_a")
            assert latest is not None and latest.folio in {"Q-1", "Q-2"}
            by_c1 = repo.latest("wa_a", conversation_id="c-1")
            by_c2 = repo.latest("wa_a", conversation_id="c-2")
            assert by_c1 is not None and by_c1.folio == "Q-1"
            assert by_c2 is not None and by_c2.folio == "Q-2"
            assert repo.latest("wa_nadie") is None
            found = repo.by_folio("Q-3")
            assert found is not None and found.get("owner_key") == "wa_b"
    finally:
        tables.metadata.drop_all(engine)
        engine.dispose()


# --- fotos del catálogo --------------------------------------------------------------------


def _photo_dir(tmp_path: Path, rows: list[dict[str, str]], files: list[str]) -> Path:
    base = tmp_path / "empaques"
    (base / "imagenes").mkdir(parents=True)
    for name in files:
        Image.new("RGB", (60, 40), (200, 210, 220)).save(base / "imagenes" / name)
    with (base / "imagenes.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sku", "archivo", "fuente_url", "tipo_fuente"])
        writer.writeheader()
        writer.writerows(rows)
    clear_image_cache()
    return base


def test_catalog_image_lee_el_manifiesto_y_rechaza_rutas(tmp_path: Path) -> None:
    base = _photo_dir(
        tmp_path,
        [
            {"sku": "CO-PCOM-J1", "archivo": "CO-PCOM-J1.jpg", "tipo_fuente": "oficial"},
            {"sku": "MALO", "archivo": "../secreto.jpg"},
            {"sku": "SIN-ARCHIVO", "archivo": "no-existe.jpg"},
        ],
        ["CO-PCOM-J1.jpg"],
    )

    image = catalog_image("CO-PCOM-J1", (tmp_path / "vacio", base))

    assert image is not None and image.path.name == "CO-PCOM-J1.jpg"
    assert image.source_type == "oficial"
    assert catalog_image("MALO", (base,)) is None
    assert catalog_image("SIN-ARCHIVO", (base,)) is None
    assert catalog_image("OTRO", (base,)) is None
    clear_image_cache()


def test_ruta_de_fotos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = _photo_dir(
        tmp_path, [{"sku": "CO-PCOM-J1", "archivo": "CO-PCOM-J1.jpg"}], ["CO-PCOM-J1.jpg"]
    )
    monkeypatch.setattr(catalogo_imagenes, "catalog_image", lambda sku: catalog_image(sku, (base,)))
    client = TestClient(create_app())

    found = client.get("/catalogo/imagenes/CO-PCOM-J1.jpg")
    missing = client.get("/catalogo/imagenes/OTRO.jpg")

    assert found.status_code == 200
    assert found.headers["content-type"] == "image/jpeg"
    assert missing.status_code == 404
    clear_image_cache()


def test_el_pdf_incluye_la_foto_y_sobrevive_a_una_ilegible(tmp_path: Path) -> None:
    view = quote_view(stamped())
    assert view is not None
    photo = tmp_path / "foto.jpg"
    Image.new("RGB", (600, 450), (120, 140, 160)).save(photo)
    broken = tmp_path / "rota.jpg"
    broken.write_bytes(b"no es una imagen")

    plain = build_quote_pdf(view)
    with_photo = build_quote_pdf(view, photo=photo)
    with_broken = build_quote_pdf(view, photo=broken)

    assert with_photo.startswith(b"%PDF-") and len(with_photo) > len(plain)
    assert with_broken.startswith(b"%PDF-")


def test_whatsapp_enlaza_la_foto() -> None:
    payload = stamped()
    chat = FakeChat(events=[ChatEvent("done", {"quote_ids": ["Q-7F3A2C"]})])
    bridge = WhatsAppBridge(
        chat=chat,
        secret=SECRET,
        quotes=lambda folio, owner: payload,
        photo_url=lambda sku: f"{BASE}/catalogo/imagenes/{sku}.jpg",
    )

    answer = bridge.consult("Cotiza", "+573100000000")

    assert f"📷 Foto: {BASE}/catalogo/imagenes/CO-PCOM-J1.jpg" in answer


def test_una_foto_que_falla_no_tumba_la_cotizacion() -> None:
    payload = stamped()
    chat = FakeChat(events=[ChatEvent("done", {"quote_ids": ["Q-7F3A2C"]})])

    def broken(sku: str) -> str | None:
        raise OSError("disco")

    bridge = WhatsAppBridge(
        chat=chat, secret=SECRET, quotes=lambda folio, owner: payload, photo_url=broken
    )

    answer = bridge.consult("Cotiza", "+573100000000")

    assert "🧾 *Cotización #Q-7F3A2C*" in answer and "📷" not in answer


def _script() -> Any:
    path = Path(__file__).resolve().parents[1] / "scripts" / "curar_imagenes_co.py"
    spec = importlib.util.spec_from_file_location("curar_imagenes_co", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_toma_og_image_y_si_falta_la_foto_de_producto() -> None:
    script = _script()
    page = "https://colombia.carvajalempaques.com/producto/portacomidas-j1/"
    with_og = (
        '<meta property="og:image" content="/cms/wp-content/uploads/2400027005.png">'
        '<img src="/cms/wp-content/uploads/otra.jpg">'
    )
    without_og = (
        '<img src="/cms/wp-content/uploads/logo-white.png">'
        '<img src="/cms/wp-content/themes/site/public/images/icons/apilable-ico.png">'
        '<img data-src="/cms/wp-content/uploads/portacomidas-j1.jpg">'
    )

    assert (
        script.image_url(with_og, page)
        == "https://colombia.carvajalempaques.com/cms/wp-content/uploads/2400027005.png"
    )
    assert script.image_url(without_og, page).endswith("/uploads/portacomidas-j1.jpg")
    assert script.image_url("<p>sin fotos</p>", page) is None


def test_script_guarda_jpeg_reducido_y_sin_transparencia(tmp_path: Path) -> None:
    script = _script()
    buffer = io.BytesIO()
    Image.new("RGBA", (1600, 900), (0, 0, 0, 0)).save(buffer, "PNG")
    target = tmp_path / "x.jpg"

    script.save_jpeg(buffer.getvalue(), target, 600)

    with Image.open(target) as saved:
        assert saved.format == "JPEG"
        assert max(saved.size) == 600
        assert saved.getpixel((0, 0)) == (255, 255, 255)
