"""Pedido en Odoo por la web (spec 056): voz, POST sin JavaScript, ``/health`` y HTTPS.

El turno del pedido se guarda sin el nombre ni el correo de quien compra, pero la persona los
ve (y los oye) en el turno en vivo para comprobarlos. Servicio real sobre SQLite con el runner
``tools`` guionado, Odoo simulado y la app FastAPI, sin red. Cubren:

- la voz de la web: la confirmación dice los datos reales en el ``done`` (voz del navegador);
  la nota de voz del servidor (TTS de OpenAI) nunca los recibe y lee lo guardado en tono
  neutro, como el historial; el folio con CRM hace la pregunta del pedido (huecos 9 y 10 y
  hallazgo #9 de la revisión);
- ``POST /chat`` sin JavaScript: PRG (303) y el ``GET`` siguiente muestra una sola vez los
  datos del turno (hueco 12 y hallazgos #8 y #18);
- el bloque ``odoo`` de ``/health``: configurado, alcanzable, en caché y con plazo total de
  un segundo; el motivo y el usuario de ventas solo para el operador (hueco 43 y hallazgos
  #7 y #11);
- la navegación al recibo ``/pedidos/{folio}.pdf`` por http se redirige a HTTPS (hallazgo
  #12).
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

from apps.api.ephemeral import EphemeralTexts, shown_turns
from apps.api.main import Handlers, HealthStatus, create_app
from apps.api.security import CSRF_COOKIE, CSRF_FIELD
from apps.api.voice import spoken_message
from carvajal.adapters.crm.odoo import PROBE_TIMEOUT_S, OdooProbe
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


def test_la_nota_de_voz_del_servidor_nunca_manda_los_datos_a_openai(engine: Any) -> None:
    # Arrange
    fake = OpenAIFake()
    app = _order_app(engine)
    app.state.voice = _runtime(fake)
    client = _visitor(app)
    message_id = _until_confirmation(client)["done"]["message_id"]

    # Act: «Escuchar» en el mismo turno en vivo de la confirmación.
    response = client.post(
        "/api/voz/hablar",
        data={"message_id": message_id, CSRF_FIELD: client.cookies[CSRF_COOKIE]},
    )

    # Assert: el TTS de OpenAI recibe lo guardado en tono neutro, sin nombre ni correo
    # (FR-019); los datos reales solo los dice la voz del navegador (``voz_resumen``).
    assert response.status_code == 200
    [spoken] = fake.tts_inputs()
    assert (
        spoken == "Voy a hacer el pedido a tu nombre, con el correo que escribiste. ¿Lo confirmo?"
    )
    for secret in (NAME, EMAIL, SPOKEN_EMAIL, "beatriz", "gomez"):
        assert secret not in spoken.lower(), secret


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


def test_los_textos_de_un_solo_uso_olvidan_por_tiempo_por_tamano_y_al_mostrarse() -> None:
    # Arrange
    now = [0.0]
    store = EphemeralTexts(ttl_s=60, max_entries=2, clock=lambda: now[0])
    store.remember("dueno", "m-1", "uno")
    store.remember("dueno", "m-2", "dos")
    store.remember("dueno", "m-3", "tres")

    # Act
    evicted = store.get("dueno", "m-1")
    other_owner = store.pop("otro", "m-3")
    peeked = store.get("dueno", "m-3")
    shown = store.pop("dueno", "m-3")
    again = store.pop("dueno", "m-3")
    store.remember("dueno", "m-4", "cuatro")
    now[0] = 61.0
    expired = store.pop("dueno", "m-4")

    # Assert
    assert evicted is None and other_owner is None
    assert peeked == shown == "tres"
    assert again is None
    assert expired is None


def test_los_textos_de_un_solo_uso_son_uno_por_app() -> None:
    # Arrange
    app = FastAPI()

    # Act
    first = shown_turns(app.state)
    second = shown_turns(app.state)

    # Assert
    assert first is second


# --- POST /chat sin JavaScript (hueco 12) --------------------------------------------------


def test_sin_javascript_la_confirmacion_redirige_y_muestra_los_datos_una_vez(
    engine: Any,
) -> None:
    # Arrange
    client = _visitor(_order_app(engine))
    _register(client)
    _turn(client, "Sí, quiero hacer el pedido")

    # Act
    response = _post(client, f"{NAME}, {EMAIL}")
    shown = client.get(response.headers["location"])
    reloaded = client.get(response.headers["location"])

    # Assert: PRG (recargar no reenvía el formulario con los datos)…
    assert response.status_code == 303
    assert NAME not in response.text and EMAIL not in response.text
    # …el GET que sigue a la redirección muestra el turno visible, sin caché…
    assert "no-store" in shown.headers["cache-control"]
    thread = _thread(shown.text)
    assert NAME in thread and EMAIL in thread
    assert "(tu nombre)" not in thread
    # …y una recarga muestra lo guardado, sin los datos.
    assert NAME not in _thread(reloaded.text) and EMAIL not in _thread(reloaded.text)


def test_sin_javascript_otro_visitante_no_ve_los_datos_del_turno(engine: Any) -> None:
    # Arrange
    app = _order_app(engine)
    client = _visitor(app)
    _register(client)
    _turn(client, "Sí, quiero hacer el pedido")
    response = _post(client, f"{NAME}, {EMAIL}")
    other = _visitor(app, "b" * 64)

    # Act
    page = other.get(response.headers["location"])

    # Assert
    assert NAME not in page.text and EMAIL not in page.text


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
    shown = client.get(response.headers["location"])

    # Assert: el recibo con el correo real y el enlace del pedido, no la promesa.
    assert response.status_code == 303
    thread = _thread(shown.text)
    assert EMAIL in thread
    assert "Descarga el recibo de tu pedido S00001" in thread
    assert "Te envío el recibo del pedido en PDF" not in thread


# --- /health: bloque odoo (hueco 43) -------------------------------------------------------


class OdooHealth:
    """Odoo simulado para la sonda: cuenta las llamadas y responde ``status``.

    ``users`` es lo que devuelve la búsqueda del usuario de ventas (``res.users``).
    """

    def __init__(self, status: int = 200, users: list[dict[str, Any]] | None = None) -> None:
        self.status = status
        self.users = [{"id": 2}] if users is None else users
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.status == 0:
            raise httpx.ConnectError("sin red", request=request)
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "detalle interno"})
        if request.url.path == "/json/2/res.users/search_read":
            return httpx.Response(200, json=self.users)
        return httpx.Response(200, json=0)

    def probes(self) -> list[httpx.Request]:
        return [call for call in self.calls if call.url.path.endswith("/search_count")]


def _odoo_settings(**values: Any) -> Settings:
    base: dict[str, Any] = {"odoo_url": ODOO_URL, "odoo_db": "carvajal", "odoo_api_key": API_KEY}
    return Settings(**{**base, **values})


OPERATOR = "token-de-operador-de-prueba"


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
    [call] = fake.probes()
    assert call.url.path == "/json/2/sale.order/search_count"
    assert call.headers["authorization"] == f"bearer {API_KEY}"


@pytest.mark.parametrize(("status", "reason"), [(0, "unreachable"), (401, "auth"), (500, "error")])
def test_health_con_odoo_caido_no_cambia_el_estado_global(
    monkeypatch: pytest.MonkeyPatch, status: int, reason: str
) -> None:
    # Arrange
    monkeypatch.setenv("OPERATOR_TOKEN", OPERATOR)
    fake = OdooHealth(status)
    client = _health_app(OdooProbe(_odoo_settings(), transport=httpx.MockTransport(fake)))

    # Act
    public = client.get("/health")
    operator = client.get("/health", headers={"X-Operator-Token": OPERATOR})

    # Assert: el estado global no cambia; el motivo solo lo ve el operador.
    assert public.status_code == 200
    body = public.json()
    assert body["status"] == "ok"
    assert body["odoo"] == {"configured": True, "status": "unreachable", "reachable": False}
    assert operator.json()["odoo"] == {
        "configured": True,
        "status": "unreachable",
        "reachable": False,
        "reason": reason,
    }
    assert "detalle interno" not in public.text + operator.text


@pytest.mark.parametrize(
    ("users", "login", "expected"),
    [([{"id": 2}], "admin", "ok"), ([], "admin", "missing"), ([{"id": 2}], "", "off")],
)
def test_health_dice_al_operador_si_existe_el_usuario_de_ventas(
    monkeypatch: pytest.MonkeyPatch, users: list[dict[str, Any]], login: str, expected: str
) -> None:
    # Arrange
    monkeypatch.setenv("OPERATOR_TOKEN", OPERATOR)
    fake = OdooHealth(users=users)
    settings = _odoo_settings(odoo_sales_login=login)
    client = _health_app(OdooProbe(settings, transport=httpx.MockTransport(fake)))

    # Act
    public = client.get("/health").json()["odoo"]
    operator = client.get("/health", headers={"X-Operator-Token": OPERATOR}).json()["odoo"]

    # Assert: ``sales_user`` (hallazgo #11) solo para el operador; el login no sale nunca.
    assert "sales_user" not in public
    assert operator["sales_user"] == expected
    assert operator["reachable"] is True


def test_la_sonda_de_odoo_nunca_bloquea_health_mas_del_plazo() -> None:
    # Arrange: un Odoo que no contesta hasta que la prueba lo suelta.
    import threading
    import time

    release = threading.Event()

    def hung(request: httpx.Request) -> httpx.Response:
        release.wait(5)
        return httpx.Response(200, json=0)

    probe = OdooProbe(_odoo_settings(), transport=httpx.MockTransport(hung), timeout_s=0.2)

    # Act
    started = time.monotonic()
    first = probe.status()
    elapsed = time.monotonic() - started
    again = probe.status()
    release.set()

    # Assert: responde en el plazo con ``timeout``, y la siguiente usa la caché sin esperar.
    assert elapsed < 1.0
    assert first == {
        "configured": True,
        "status": "unreachable",
        "reachable": False,
        "reason": "timeout",
    }
    assert again == first


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
    assert len(fake.probes()) == 2


def test_la_sonda_de_odoo_usa_un_plazo_corto() -> None:
    # Arrange
    seen: list[dict[str, Any]] = []

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions.get("timeout") or {})
        return httpx.Response(200, json=0)

    probe = OdooProbe(_odoo_settings(odoo_timeout_s=20), transport=httpx.MockTransport(transport))

    # Act
    probe.status()

    # Assert: un segundo por fase; el plazo total lo impone ``status()`` (también 1 s).
    assert seen[0]["read"] == pytest.approx(1.0)
    assert PROBE_TIMEOUT_S <= 1.0


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


def test_la_navegacion_al_recibo_por_http_se_redirige_a_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: el recibo lleva el nombre y el correo; un enlace http que llegue a una
    # persona no se sirve en claro (hallazgo #12).
    client = https_client(monkeypatch)

    # Act
    browser = client.get("/pedidos/a3f09c12b7e4.pdf?t=1.abc", headers=HTML)
    openclaw = client.get("/pedidos/a3f09c12b7e4.pdf?t=1.abc")

    # Assert: el navegador va a HTTPS; un cliente sin ``Accept: text/html`` no se redirige.
    assert browser.status_code == 307
    assert browser.headers["location"].startswith("https://")
    assert openclaw.status_code != 307
