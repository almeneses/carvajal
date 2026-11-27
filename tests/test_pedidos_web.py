"""Pedido en Odoo por la web (spec 056): voz, POST sin JavaScript, ``/health`` y HTTPS.

El turno del pedido se guarda sin el nombre ni el correo de quien compra, pero la persona los
ve (y los oye) en el turno en vivo para comprobarlos. Servicio real sobre SQLite con el runner
``tools`` guionado, Odoo simulado y la app FastAPI, sin red. Cubren:

- la voz de la web: la confirmación dice los datos reales en el ``done`` y en la nota de voz
  del servidor de ese turno (:class:`apps.api.voice.LiveSpoken`); el historial, en tono
  neutro; el folio con CRM hace la pregunta del pedido (huecos 9 y 10);
- ``POST /chat`` sin JavaScript: la confirmación muestra los datos del turno (hueco 12);
- el bloque ``odoo`` de ``/health``: configurado, alcanzable y en caché, sin secretos ni
  cambiar el estado global (hueco 43);
- la redirección a HTTPS no toca el recibo ``/pedidos/{folio}.pdf`` (hueco 44).
"""

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from tests.test_api_voz import OpenAIFake, _runtime
from tests.test_chat_service_tools import assistant_rows
from tests.test_https_redirect import HTML
from tests.test_https_redirect import _client as https_client
from tests.test_pdf_web import _app as quote_app
from tests.test_pdf_web import _register, _turn, _visitor
from tests.test_pedidos_recibo import _order_app

from apps.api.main import Handlers, HealthStatus, create_app
from apps.api.security import CSRF_COOKIE, CSRF_FIELD
from apps.api.voice import LiveSpoken, live_spoken, spoken_message
from carvajal.adapters.crm.odoo import OdooProbe
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.chat import ChatMessage
from carvajal.infrastructure.settings import Settings
from carvajal.quotation.order import spoken_address

NAME = "Beatriz Gómez"
EMAIL = "beatriz.gomez@correo.com"
SPOKEN_EMAIL = spoken_address(EMAIL)
API_KEY = "clave-secreta-de-odoo"
ODOO_URL = "http://odoo-interno:8069"
VOICE_SUMMARY = re.compile(r'data-voz-resumen="([^"]*)"')


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


def _until_confirmation(client: TestClient) -> dict[str, dict[str, Any]]:
    """Registra el folio, acepta el pedido y da los datos: el último turno es la confirmación."""

    _register(client)
    _turn(client, "Sí, quiero hacer el pedido")
    return _turn(client, f"{NAME}, {EMAIL}")


def _post(client: TestClient, question: str) -> Any:
    """Un turno por ``POST /chat`` (el formulario sin JavaScript), sin seguir la redirección."""

    if CSRF_COOKIE not in client.cookies:
        client.get("/api/csrf")
    return client.post(
        "/chat",
        data={"question": question, CSRF_FIELD: client.cookies[CSRF_COOKIE]},
        follow_redirects=False,
    )


def _thread(page: str) -> str:
    return page.split('<ol class="hilo">', 1)[1].split('class="panel-hilos-cuerpo"', 1)[0]


# --- voz de la web (huecos 9 y 10) ---------------------------------------------------------


def test_la_voz_de_la_confirmacion_dice_el_nombre_y_el_correo_reales(engine: Any) -> None:
    # Arrange
    client = _visitor(_order_app(engine))

    # Act
    events = _until_confirmation(client)

    # Assert: la persona oye los datos que dictó para comprobarlos.
    summary = events["done"]["voz_resumen"]
    assert NAME in summary
    assert SPOKEN_EMAIL in summary
    assert summary.endswith("¿Lo confirmo?")
    for mark in ("(tu nombre)", "a…@", "el correo que escribiste", "**"):
        assert mark not in summary, mark
    # Lo guardado sigue sin los datos.
    stored = assistant_rows(engine)[-1]["content"]
    assert NAME not in stored and EMAIL not in stored


def test_la_nota_de_voz_del_servidor_del_turno_en_vivo_dice_los_datos(engine: Any) -> None:
    # Arrange
    fake = OpenAIFake()
    app = _order_app(engine)
    app.state.voice = _runtime(fake)
    client = _visitor(app)
    message_id = _until_confirmation(client)["done"]["message_id"]

    # Act
    response = client.post(
        "/api/voz/hablar",
        data={"message_id": message_id, CSRF_FIELD: client.cookies[CSRF_COOKIE]},
    )

    # Assert
    assert response.status_code == 200
    [spoken] = fake.tts_inputs()
    assert NAME in spoken and SPOKEN_EMAIL in spoken
    assert "(tu nombre)" not in spoken


def test_otro_visitante_no_oye_los_datos_del_turno_en_vivo(engine: Any) -> None:
    # Arrange
    fake = OpenAIFake()
    app = _order_app(engine)
    app.state.voice = _runtime(fake)
    message_id = _until_confirmation(_visitor(app))["done"]["message_id"]
    other = _visitor(app, "b" * 64)
    other.get("/api/csrf")

    # Act
    response = other.post(
        "/api/voz/hablar",
        data={"message_id": message_id, CSRF_FIELD: other.cookies[CSRF_COOKIE]},
    )

    # Assert: el mensaje es de otro dueño (404) y no se sintetiza nada.
    assert response.status_code == 404
    assert fake.tts_inputs() == []


def test_el_historial_lee_la_confirmacion_sin_datos_y_en_tono_neutro(engine: Any) -> None:
    # Arrange
    client = _visitor(_order_app(engine))
    _until_confirmation(client)

    # Act
    page = client.get("/chat")

    # Assert
    summaries = VOICE_SUMMARY.findall(_thread(page.text))
    confirmation = summaries[-1]
    assert (
        confirmation
        == "Voy a hacer el pedido a tu nombre, con el correo que escribiste. ¿Lo confirmo?"
    )
    assert NAME not in page.text and EMAIL not in page.text


def test_la_voz_del_recibo_no_lee_el_correo_enmascarado(engine: Any) -> None:
    # Arrange
    client = _visitor(_order_app(engine))
    _until_confirmation(client)

    # Act
    events = _turn(client, "sí")
    page = client.get("/chat")

    # Assert: ni en vivo ni en el historial se dice «a…@correo.com».
    live = events["done"]["voz_resumen"]
    stored = VOICE_SUMMARY.findall(_thread(page.text))[-1]
    for summary in (live, stored):
        assert summary.startswith("Listo, tu pedido quedó confirmado.")
        assert "…" not in summary and "@" not in summary and "arroba" not in summary


def test_la_voz_del_folio_con_crm_hace_la_pregunta_del_pedido(engine: Any) -> None:
    # Arrange
    client = _visitor(_order_app(engine))

    # Act
    events = _register(client)
    page = client.get("/chat")

    # Assert: tras la cotización corta, la oferta del pedido cierra la nota.
    live = events["done"]["voz_resumen"]
    assert live.startswith("Es 1 paquete de 50 unidades")
    assert live.endswith("¿Quieres hacer el pedido?")
    assert events["done"]["flow"]["order_offer"] is True
    assert VOICE_SUMMARY.findall(_thread(page.text))[-1].endswith("¿Quieres hacer el pedido?")


def test_la_voz_del_folio_sin_crm_no_ofrece_el_pedido(engine: Any) -> None:
    # Arrange
    client = _visitor(quote_app(engine))

    # Act
    events = _register(client)

    # Assert
    assert "pedido" not in events["done"]["voz_resumen"]


def test_spoken_message_resume_igual_un_turno_del_agente_con_la_plantilla() -> None:
    # Arrange: el texto del agente más el recordatorio del pedido no es solo plantilla.
    flow = {"text": "↩️ ¿Seguimos con el pedido?", "spoken": "¿Seguimos?", "awaiting": "pedido"}
    message = ChatMessage(
        id="m-1",
        conversation_id="hilo",
        role="assistant",
        content="Carvajal se fundó en 1904.\n\n↩️ ¿Seguimos con el pedido?",
        sources=[{"result_type": "commercial", "result": {"flow": flow}}],
        created_at=datetime(2026, 11, 26, tzinfo=UTC),
    )

    # Act
    summary = spoken_message(message, flow={**flow, "spoken": "Otra cosa."})

    # Assert: se resume el texto, no la plantilla en vivo.
    assert summary.startswith("Carvajal se fundó en 1904.")
    assert "Otra cosa." not in summary


def test_live_spoken_olvida_por_tiempo_y_por_tamano() -> None:
    # Arrange
    now = [0.0]
    store = LiveSpoken(ttl_s=60, max_entries=2, clock=lambda: now[0])
    store.remember("dueno", "m-1", "uno")
    store.remember("dueno", "m-2", "dos")
    store.remember("dueno", "m-3", "tres")

    # Act
    evicted = store.get("dueno", "m-1")
    other_owner = store.get("otro", "m-3")
    fresh = store.get("dueno", "m-3")
    now[0] = 61.0
    expired = store.get("dueno", "m-3")

    # Assert
    assert evicted is None and other_owner is None
    assert fresh == "tres"
    assert expired is None


def test_live_spoken_es_uno_por_app() -> None:
    # Arrange
    app = FastAPI()

    # Act
    first = live_spoken(app.state)
    second = live_spoken(app.state)

    # Assert
    assert first is second


# --- POST /chat sin JavaScript (hueco 12) --------------------------------------------------


def test_sin_javascript_la_confirmacion_muestra_los_datos_del_turno(engine: Any) -> None:
    # Arrange
    client = _visitor(_order_app(engine))
    _register(client)
    _turn(client, "Sí, quiero hacer el pedido")

    # Act
    response = _post(client, f"{NAME}, {EMAIL}")

    # Assert: se pinta el turno visible (no la redirección al guardado) y no se cachea.
    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    thread = _thread(response.text)
    assert NAME in thread and EMAIL in thread
    assert "(tu nombre)" not in thread
    # Lo guardado sigue sin los datos: al recargar se ve la versión enmascarada.
    reloaded = _thread(client.get("/chat").text)
    assert NAME not in reloaded and EMAIL not in reloaded


def test_sin_javascript_un_turno_sin_datos_redirige_como_siempre(engine: Any) -> None:
    # Arrange
    client = _visitor(_order_app(engine))
    _register(client)

    # Act
    response = _post(client, "Sí, quiero hacer el pedido")

    # Assert
    assert response.status_code == 303
    assert response.headers["location"] == "/chat"


def test_sin_javascript_el_recibo_enlaza_el_pdf_del_pedido(engine: Any) -> None:
    # Arrange
    client = _visitor(_order_app(engine))
    _until_confirmation(client)

    # Act
    response = _post(client, "sí")

    # Assert: el recibo con el correo real y el enlace del pedido, no la promesa.
    assert response.status_code == 200
    thread = _thread(response.text)
    assert EMAIL in thread
    assert "Descarga el recibo de tu pedido S00001" in thread
    assert "Te envío el recibo del pedido en PDF" not in thread


# --- /health: bloque odoo (hueco 43) -------------------------------------------------------


class OdooHealth:
    """Odoo simulado para la sonda: cuenta las llamadas y responde ``status``."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.status == 0:
            raise httpx.ConnectError("sin red", request=request)
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "detalle interno"})
        return httpx.Response(200, json=0)


def _odoo_settings(**values: Any) -> Settings:
    base: dict[str, Any] = {"odoo_url": ODOO_URL, "odoo_db": "carvajal", "odoo_api_key": API_KEY}
    return Settings(**{**base, **values})


def _health_app(probe: OdooProbe | None = None) -> TestClient:
    app = create_app(Handlers(health=lambda: HealthStatus(1, 2)))
    if probe is not None:
        app.state.odoo_probe = probe
    return TestClient(app)


def test_health_sin_odoo_dice_que_no_esta_configurado(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    for name in ("ODOO_URL", "ODOO_DB", "ODOO_API_KEY"):
        monkeypatch.setenv(name, "")
    client = _health_app()

    # Act
    response = client.get("/health")

    # Assert
    assert response.json()["odoo"] == {"configured": False, "status": "off"}


def test_health_con_odoo_alcanzable_lo_dice_sin_secretos() -> None:
    # Arrange
    fake = OdooHealth()
    client = _health_app(OdooProbe(_odoo_settings(), transport=httpx.MockTransport(fake)))

    # Act
    response = client.get("/health")

    # Assert
    assert response.status_code == 200
    assert response.json()["odoo"] == {"configured": True, "status": "ok", "reachable": True}
    assert API_KEY not in response.text and "odoo-interno" not in response.text
    [call] = fake.calls
    assert call.url.path == "/json/2/sale.order/search_count"
    assert call.headers["authorization"] == f"bearer {API_KEY}"


@pytest.mark.parametrize(("status", "reason"), [(0, "unreachable"), (401, "auth"), (500, "error")])
def test_health_con_odoo_caido_no_cambia_el_estado_global(status: int, reason: str) -> None:
    # Arrange
    fake = OdooHealth(status)
    client = _health_app(OdooProbe(_odoo_settings(), transport=httpx.MockTransport(fake)))

    # Act
    response = client.get("/health")

    # Assert
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["odoo"] == {
        "configured": True,
        "status": "unreachable",
        "reachable": False,
        "reason": reason,
    }
    assert "detalle interno" not in response.text


def test_la_sonda_de_odoo_tiene_cache() -> None:
    # Arrange
    now = [0.0]
    fake = OdooHealth(0)
    probe = OdooProbe(
        _odoo_settings(), transport=httpx.MockTransport(fake), ttl_s=60, clock=lambda: now[0]
    )

    # Act
    first = probe.status()
    cached = probe.status()
    fake.status = 200
    now[0] = 61.0
    renewed = probe.status()

    # Assert: un Odoo caído se vuelve a probar como mucho una vez por minuto.
    assert first["reachable"] is False and cached == first
    assert renewed["reachable"] is True
    assert len(fake.calls) == 2


def test_la_sonda_de_odoo_usa_un_plazo_corto() -> None:
    # Arrange
    seen: list[dict[str, Any]] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions.get("timeout") or {})
        return httpx.Response(200, json=0)

    probe = OdooProbe(_odoo_settings(odoo_timeout_s=20), transport=httpx.MockTransport(transport))

    # Act
    probe.status()

    # Assert
    assert seen[0]["read"] == pytest.approx(3.0)


def test_health_degradado_tambien_publica_odoo() -> None:
    # Arrange
    def down() -> HealthStatus:
        from sqlalchemy.exc import OperationalError

        raise OperationalError("select 1", {}, Exception("sin base"))

    app = create_app(Handlers(health=down))
    app.state.odoo_probe = OdooProbe(_odoo_settings(), transport=httpx.MockTransport(OdooHealth()))

    # Act
    response = TestClient(app).get("/health")

    # Assert
    assert response.status_code == 503
    assert response.json()["odoo"]["reachable"] is True


# --- HTTPS (hueco 44) ----------------------------------------------------------------------


def test_no_redirige_el_recibo_del_pedido(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: OpenClaw baja el recibo por MEDIA_BASE_URL, que puede ser http con IP.
    client = https_client(monkeypatch)

    # Act
    response = client.get("/pedidos/a3f09c12b7e4.pdf?t=1.abc", headers=HTML)

    # Assert
    assert response.status_code != 307
