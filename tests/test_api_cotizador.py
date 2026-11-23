"""Acceso heredado al Cotizador: redirecciones e historial autorizado (spec 043)."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from apps.api.chat.identity import COOKIE_NAME, SignedCookieIdentity
from apps.api.main import Handlers, HealthStatus, create_app
from apps.api.views.cotizador import SqlCotizadorStore, ThreadAccessError
from carvajal.adapters.storage.chat import ChatMessage, ConversationSummary

IDENTITY = SignedCookieIdentity(b"secreto-cotizador")
WHEN = datetime(2026, 11, 23, tzinfo=UTC)


@dataclass
class FakeStore:
    """Historial en memoria con el mismo contrato que el almacén SQL."""

    messages: list[ChatMessage] = field(default_factory=list)
    otros: set[str] = field(default_factory=set)
    historial_falla: bool = False

    def history(
        self, owner_key: str, thread_id: str | None
    ) -> tuple[str | None, Sequence[ChatMessage]]:
        if self.historial_falla:
            raise SQLAlchemyError("fallo simulado del historial")
        if thread_id in self.otros:
            raise ThreadAccessError(thread_id)
        return ("hilo-1" if self.messages else None), self.messages

    def conversations(self, owner_key: str) -> Sequence[ConversationSummary]:
        return []

    def new(self, owner_key: str) -> str:
        return "hilo-2"

    def append(self, *args: Any, **kwargs: Any) -> tuple[str, Any, Any]:
        raise AssertionError("La vista retirada no escribe el Cotizador.")

    def memory(self, owner_key: str) -> dict[str, Any]:
        return {}

    def remember(self, owner_key: str, profile: Any) -> dict[str, Any]:
        return {}

    def forget(self, owner_key: str) -> None:
        return None


def make_client(store: FakeStore | None = None) -> tuple[TestClient, FakeStore]:
    fake = store or FakeStore()
    app = create_app(Handlers(health=lambda: HealthStatus(1, 2)))
    app.state.cotizador = fake
    app.state.chat_identity = IDENTITY
    return TestClient(app), fake


def _payload(
    *,
    folio: str = "folio-1",
    simulated: bool = False,
    source: bool = True,
) -> dict[str, Any]:
    primary: dict[str, Any] = {
        "sku": "7704918100199",
        "name": "Vaso rígido",
        "currency": "COP",
        "captured_at": "2026-11-18" if source else None,
        "source_url": "https://example.com/precio" if source else None,
        "presentation": {"subtotal": "3751"},
    }
    if simulated:
        primary["price_simulated"] = True
    payload: dict[str, Any] = {
        "kind": "cotizacion",
        "city": "Bogotá",
        "primary": primary,
        "total": "3751",
        "quote": {"id": folio, "valid_until": "2026-11-30"},
    }
    if simulated:
        payload["simulated"] = True
    return payload


def _turno(
    pregunta: str,
    respuesta: str,
    *,
    folio: str = "folio-1",
    simulated: bool = False,
    source: bool = True,
) -> list[ChatMessage]:
    return [
        ChatMessage(f"u-{folio}", "hilo-1", "user", pregunta, None, WHEN, client_turn_id=None),
        ChatMessage(
            f"a-{folio}",
            "hilo-1",
            "assistant",
            respuesta,
            [_payload(folio=folio, simulated=simulated, source=source)],
            WHEN,
        ),
    ]


def _get(client: TestClient, url: str, owner_key: str = "dueno-a") -> Any:
    client.cookies.set(COOKIE_NAME, IDENTITY.issue(owner_key))
    return client.get(url, follow_redirects=False)


def test_el_acceso_general_redirige_al_chat() -> None:
    client, _ = make_client()

    respuesta = _get(client, "/cotizador")

    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/chat"


def test_el_panel_de_hilos_redirige_al_chat() -> None:
    client, _ = make_client()

    respuesta = _get(client, "/cotizador?panel=hilos")

    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/chat?panel=hilos"


def test_el_dueno_abre_su_historial_real_y_conserva_folio_y_fuente() -> None:
    client, store = make_client()
    store.messages = _turno("cotiza 20 vasos", "Son 3.751 COP.")

    respuesta = _get(client, "/cotizador?thread=hilo-1")

    assert respuesta.status_code == 200
    assert "Historial del Cotizador" in respuesta.text
    assert "cotiza 20 vasos" in respuesta.text
    assert "Son 3.751 COP." in respuesta.text
    assert "#folio-1" in respuesta.text
    assert "https://example.com/precio" in respuesta.text
    assert "Continuar en el chat" in respuesta.text
    assert "<form" not in respuesta.text


def test_el_historial_simulado_excluye_texto_tarjetas_y_contexto() -> None:
    client, store = make_client()
    store.messages = _turno(
        "cotiza 20 vasos", "Texto simulado que no debe publicarse.", simulated=True
    )

    respuesta = _get(client, "/cotizador?thread=hilo-1")

    assert respuesta.status_code == 200
    assert "Texto simulado que no debe publicarse." not in respuesta.text
    assert "cotiza 20 vasos" not in respuesta.text
    assert "no tiene contenido publicable" in respuesta.text


def test_el_historial_ambiguo_sin_fuente_no_se_publica() -> None:
    client, store = make_client()
    store.messages = _turno("cotiza 20 vasos", "Texto sin fuente atribuible.", source=False)

    respuesta = _get(client, "/cotizador?thread=hilo-1")

    assert respuesta.status_code == 200
    assert "Texto sin fuente atribuible." not in respuesta.text
    assert "no tiene contenido publicable" in respuesta.text


def test_un_hilo_mixto_publica_solo_los_turnos_reales() -> None:
    client, store = make_client()
    store.messages = [
        *_turno("turno real", "respuesta real", folio="folio-real"),
        *_turno(
            "turno simulado",
            "respuesta simulada",
            folio="folio-demo",
            simulated=True,
        ),
    ]

    respuesta = _get(client, "/cotizador?thread=hilo-1")

    assert "respuesta real" in respuesta.text
    assert "respuesta simulada" not in respuesta.text
    assert "turno simulado" not in respuesta.text


def test_otra_identidad_no_puede_abrir_el_historial() -> None:
    client, store = make_client()
    store.otros.add("ajeno")
    client.cookies.set(COOKIE_NAME, IDENTITY.issue("dueno-b"))

    respuesta = client.get("/cotizador?thread=ajeno")

    assert respuesta.status_code == 404


def test_un_fallo_de_historial_no_filtra_detalles() -> None:
    client, _ = make_client(FakeStore(historial_falla=True))

    respuesta = _get(client, "/cotizador?thread=hilo-1")

    assert respuesta.status_code == 503
    assert "fallo simulado" not in respuesta.text


def test_las_escrituras_antiguas_redirigen_al_chat() -> None:
    client, _ = make_client()

    for ruta in ("/cotizador", "/cotizador/stream", "/cotizador/nuevo"):
        respuesta = client.post(ruta, data={"question": "cotiza 20 vasos"}, follow_redirects=False)

        assert respuesta.status_code == 303, ruta
        assert respuesta.headers["location"] == "/chat", ruta


def test_la_ficha_generada_ya_no_se_sirve() -> None:
    client, _ = make_client()

    respuesta = client.get("/cotizador/ficha/7704918100199-p06")

    assert respuesta.status_code == 404


def test_el_chat_no_ofrece_enlaces_al_compositor_retirado_ni_a_faq() -> None:
    client, _ = make_client()

    respuesta = client.get("/chat")

    assert respuesta.status_code == 200
    assert 'href="/cotizador"' not in respuesta.text
    assert 'href="/faq"' not in respuesta.text
    assert 'href="/chat"' in respuesta.text


def test_el_almacen_sql_usa_un_espacio_propio_para_el_cotizador() -> None:
    almacen = SqlCotizadorStore(factory=None)

    assert almacen._owner("dueno") == "cot:dueno"
