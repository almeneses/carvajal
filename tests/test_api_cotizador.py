"""Pruebas HTTP del Cotizador (spec 042, FR-001, FR-016, FR-017)."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from apps.api.chat.identity import SignedCookieIdentity
from apps.api.main import Handlers, HealthStatus, create_app
from apps.api.views.cotizador import SqlCotizadorStore, ThreadAccessError
from carvajal.adapters.storage.chat import ChatMessage, ConversationSummary
from carvajal.application.quota import QuotaResult

IDENTITY = SignedCookieIdentity(b"secreto-cotizador")


@dataclass
class FakeStore:
    """Historial en memoria con el mismo contrato que el almacén SQL."""

    messages: list[ChatMessage] = field(default_factory=list)
    owners: list[str] = field(default_factory=list)
    otros: set[str] = field(default_factory=set)
    profile: dict[str, Any] = field(default_factory=dict)
    fallar: bool = False
    quotes: list[dict[str, Any]] = field(default_factory=list)

    def history(
        self, owner_key: str, thread_id: str | None
    ) -> tuple[str | None, Sequence[ChatMessage]]:
        self.owners.append(owner_key)
        if thread_id in self.otros:
            raise ThreadAccessError(thread_id)
        return ("hilo-1" if self.messages else None), self.messages

    def append(
        self,
        owner_key: str,
        *,
        question: str,
        answer: str,
        sources: Sequence[Mapping[str, Any]],
        thread_id: str | None,
    ) -> tuple[str, ChatMessage, ChatMessage]:
        self.owners.append(owner_key)
        if thread_id in self.otros:
            raise ThreadAccessError(thread_id)
        if self.fallar:
            raise SQLAlchemyError("fallo simulado")
        ahora = datetime.now(UTC)
        usuario = ChatMessage(
            "m-user", "hilo-1", "user", question, None, ahora, client_turn_id=None
        )
        asistente = ChatMessage(
            "m-bot", "hilo-1", "assistant", answer, [dict(item) for item in sources], ahora
        )
        self.messages = [*self.messages, usuario, asistente]
        return "hilo-1", usuario, asistente

    def new(self, owner_key: str) -> str:
        self.owners.append(owner_key)
        return "hilo-2"

    def conversations(self, owner_key: str) -> Sequence[ConversationSummary]:
        self.owners.append(owner_key)
        return []

    def memory(self, owner_key: str) -> dict[str, Any]:
        self.owners.append(owner_key)
        return dict(self.profile)

    def remember(self, owner_key: str, profile: Mapping[str, Any]) -> dict[str, Any]:
        self.owners.append(owner_key)
        self.profile = dict(profile)
        return self.profile

    def forget(self, owner_key: str) -> None:
        self.owners.append(owner_key)
        self.profile = {}

    def issue_quote(
        self,
        owner_key: str,
        *,
        quote_id: str,
        payload: Mapping[str, Any],
        total: Any,
        currency: str,
        valid_until: Any,
        conversation_id: str | None,
    ) -> None:
        self.quotes.append(
            {
                "owner": owner_key,
                "id": quote_id,
                "payload": dict(payload),
                "total": total,
                "currency": currency,
                "valid_until": valid_until,
                "conversation_id": conversation_id,
            }
        )


def make_client(store: FakeStore | None = None, quota: Any = None) -> tuple[TestClient, FakeStore]:
    fake = store or FakeStore()
    app = create_app(Handlers(health=lambda: HealthStatus(1, 2)))
    app.state.cotizador = fake
    app.state.chat_identity = IDENTITY
    app.state.quota = quota or (lambda owner: QuotaResult(allowed=True))
    return TestClient(app), fake


def _token(client: TestClient) -> str:
    client.get("/cotizador")
    return client.get("/api/csrf").json()["token"]


def test_la_seccion_se_abre_sin_aviso_general_de_demo() -> None:
    client, _ = make_client()

    respuesta = client.get("/cotizador")

    assert respuesta.status_code == 200
    assert "Cotizador" in respuesta.text
    assert "Modo demo" not in respuesta.text
    assert "Carvajal Empaques" in respuesta.text


def test_post_sin_csrf_se_rechaza_antes_del_motor() -> None:
    client, store = make_client()

    respuesta = client.post("/cotizador", data={"question": "cotiza 20 vasos en Bogotá"})

    assert respuesta.status_code == 403
    assert not store.messages


def test_turno_sin_ciudad_pide_ciudad_y_aisla_el_hilo() -> None:
    client, store = make_client()
    token = _token(client)

    respuesta = client.post(
        "/cotizador",
        data={"question": "cotízame 20 vasos para bebida caliente", "csrf_token": token},
    )

    assert respuesta.status_code == 200
    assert respuesta.headers["X-Thread"] == "hilo-1"
    assert "¿En qué ciudad" in respuesta.text
    assert store.owners and len(set(store.owners)) == 1


def test_turno_con_ciudad_pinta_tarjetas_y_sede() -> None:
    client, _ = make_client()
    token = _token(client)
    client.post(
        "/cotizador",
        data={"question": "cotízame 20 vasos para bebida caliente", "csrf_token": token},
    )

    respuesta = client.post(
        "/cotizador",
        data={
            "question": "en Bogotá",
            "csrf_token": token,
            "thread_id": "hilo-1",
        },
    )

    assert respuesta.status_code == 200
    assert "Cotización para Bogotá" in respuesta.text
    assert "3.751" in respuesta.text
    assert "EDEXA" in respuesta.text
    assert "Sobrantes" in respuesta.text
    assert "openstreetmap.org/export/embed.html" in respuesta.text
    assert "marker=4." in respuesta.text
    assert "coti-mapa-marco" in respuesta.text


def test_historial_se_repinta_con_la_cotizacion() -> None:
    client, store = make_client()
    token = _token(client)
    client.post(
        "/cotizador",
        data={"question": "cotiza 20 vasos calientes en Bogotá", "csrf_token": token},
    )

    respuesta = client.get("/cotizador?thread=hilo-1")

    assert respuesta.status_code == 200
    assert "Cotización para Bogotá" in respuesta.text
    assert "Producto de demostración" not in respuesta.text
    assert store.messages


def test_el_almacen_sql_usa_un_espacio_propio_para_el_cotizador() -> None:
    almacen = SqlCotizadorStore(factory=None)

    assert almacen._owner("dueno") == "cot:dueno"


def test_hilo_ajeno_responde_404() -> None:
    client, store = make_client()
    token = _token(client)
    store.otros.add("ajeno")
    client.post(
        "/cotizador",
        data={"question": "cotiza 20 vasos en Bogotá", "csrf_token": token},
    )

    respuesta = client.get("/cotizador?thread=ajeno")

    assert respuesta.status_code == 404


def test_cuota_agotada_responde_429() -> None:
    client, store = make_client(quota=lambda owner: QuotaResult(allowed=False, retry_after=120))
    token = _token(client)

    respuesta = client.post(
        "/cotizador",
        data={"question": "cotiza 20 vasos en Bogotá", "csrf_token": token},
    )

    assert respuesta.status_code == 429
    assert respuesta.headers["Retry-After"] == "120"
    assert not store.messages


def test_nuevo_hilo_redirige_a_la_vista() -> None:
    client, _ = make_client()
    token = _token(client)

    respuesta = client.post("/cotizador/nuevo", data={"csrf_token": token}, follow_redirects=False)

    assert respuesta.status_code == 303
    assert respuesta.headers["location"].startswith("/cotizador?thread=hilo-2")


def _eventos(cuerpo: str) -> list[tuple[str, dict[str, Any]]]:
    eventos = []
    for bloque in cuerpo.split("\n\n"):
        if not bloque.strip():
            continue
        nombre = "message"
        datos: list[str] = []
        for linea in bloque.splitlines():
            if linea.startswith("event:"):
                nombre = linea[6:].strip()
            elif linea.startswith("data:"):
                datos.append(linea[5:].lstrip())
        eventos.append((nombre, json.loads("\n".join(datos))))
    return eventos


def test_stream_emite_texto_y_tarjetas_y_persiste_una_vez() -> None:
    client, store = make_client()
    token = _token(client)
    client.post(
        "/cotizador",
        data={"question": "cotiza 20 vasos para bebida caliente", "csrf_token": token},
    )

    respuesta = client.post(
        "/cotizador/stream",
        data={"question": "en Bogotá", "csrf_token": token, "thread_id": "hilo-1"},
    )

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/event-stream")
    eventos = _eventos(respuesta.text)
    nombres = [nombre for nombre, _ in eventos]
    assert nombres[0] == "open"
    assert "token" in nombres
    assert "products" in nombres
    assert nombres[-1] == "done"
    texto = "".join(datos["text"] for nombre, datos in eventos if nombre == "token")
    assert "Bogotá" in texto
    html = next(datos["html"] for nombre, datos in eventos if nombre == "products")
    assert "Cotización para Bogotá" in html
    assert len(store.messages) == 4  # dos turnos: aclaración y cotización


def test_stream_sin_csrf_responde_error_sse() -> None:
    client, store = make_client()

    respuesta = client.post("/cotizador/stream", data={"question": "cotiza 20 vasos"})

    assert respuesta.status_code == 403
    assert respuesta.headers["content-type"].startswith("text/event-stream")
    nombre, datos = _eventos(respuesta.text)[0]
    assert nombre == "error"
    assert datos["incomplete"] is False
    assert not store.messages


def test_stream_con_cuota_agotada_responde_429() -> None:
    client, store = make_client(quota=lambda owner: QuotaResult(allowed=False, retry_after=60))
    token = _token(client)

    respuesta = client.post(
        "/cotizador/stream",
        data={"question": "cotiza 20 vasos en Bogotá", "csrf_token": token},
    )

    assert respuesta.status_code == 429
    assert respuesta.headers["Retry-After"] == "60"
    assert not store.messages


def test_la_memoria_silenciosa_usa_ciudad_domicilio_y_nombre() -> None:
    client, store = make_client()
    token = _token(client)
    client.post(
        "/cotizador",
        data={
            "question": (
                "Hola, me llamo Ana, trabajo en una cafetería en Bogotá y siempre pido a domicilio"
            ),
            "csrf_token": token,
        },
    )

    respuesta = client.post(
        "/cotizador",
        data={"question": "cotiza 20 vasos para bebida caliente", "csrf_token": token},
    )

    assert respuesta.status_code == 200
    assert "Listo, Ana." in respuesta.text
    assert "Cotización para Bogotá" in respuesta.text
    assert "Total conocido" in respuesta.text
    assert store.profile["city"] == "Bogotá"
    assert store.profile["delivery"] is True


def test_la_memoria_sobrevive_a_un_hilo_nuevo() -> None:
    client, store = make_client()
    token = _token(client)
    store.profile = {"name": "Ana", "city": "Bogotá", "delivery": False, "preferences": []}

    respuesta = client.post(
        "/cotizador/stream",
        data={"question": "cotiza 20 vasos para bebida caliente", "csrf_token": token},
    )

    assert "Cotización para Bogotá" in respuesta.text
    assert "¿En qué ciudad" not in respuesta.text


def test_olvidar_borra_la_memoria_y_vuelve_a_preguntar() -> None:
    client, store = make_client()
    token = _token(client)
    store.profile = {"name": "Ana", "city": "Bogotá", "delivery": False, "preferences": []}

    borrado = client.post("/cotizador", data={"question": "olvida mis datos", "csrf_token": token})
    respuesta = client.post(
        "/cotizador", data={"question": "cotiza 20 vasos calientes", "csrf_token": token}
    )

    assert store.profile == {}
    assert "Listo, borré lo que tenía guardado" in borrado.text
    assert "¿En qué ciudad" in respuesta.text


def test_el_perfil_no_viaja_en_el_payload_persistido() -> None:
    client, store = make_client()
    token = _token(client)
    store.profile = {"name": "Ana", "city": "Bogotá", "delivery": False, "preferences": []}

    client.post("/cotizador", data={"question": "cotiza 20 vasos calientes", "csrf_token": token})

    fuentes = store.messages[-1].sources or []
    assert fuentes
    assert "Ana" not in str(fuentes[0])
    assert "profile" not in fuentes[0]


def test_el_chat_conserva_su_navegacion_y_suma_el_cotizador() -> None:
    client, _ = make_client()

    respuesta = client.get("/chat")

    assert respuesta.status_code == 200
    assert 'href="/cotizador"' in respuesta.text
    assert 'aria-current="page"' in respuesta.text


def test_el_stream_avisa_si_la_persistencia_falla_a_mitad() -> None:
    client, store = make_client()
    token = _token(client)
    store.fallar = True

    respuesta = client.post(
        "/cotizador/stream",
        data={"question": "cotiza 20 vasos calientes en Bogotá", "csrf_token": token},
    )

    eventos = _eventos(respuesta.text)
    nombre, datos = eventos[-1]
    assert nombre == "error"
    assert datos["incomplete"] is True
    assert "token" in [n for n, _ in eventos]


def test_el_stream_tambien_olvida_y_no_usa_el_perfil() -> None:
    client, store = make_client()
    token = _token(client)
    store.profile = {"name": "Ana", "city": "Bogotá", "delivery": True, "preferences": []}
    client.post(
        "/cotizador",
        data={"question": "hola", "csrf_token": token},
    )

    respuesta = client.post(
        "/cotizador/stream",
        data={"question": "olvida mis datos", "csrf_token": token},
    )

    texto = "".join(d["text"] for n, d in _eventos(respuesta.text) if n == "token")
    assert "borré lo que tenía guardado" in texto
    assert store.profile == {}


def test_la_ficha_de_la_oferta_es_citable() -> None:
    client, _ = make_client()

    respuesta = client.get("/cotizador/ficha/7704918100199-p06")

    assert respuesta.status_code == 200
    assert "Vaso rígido traslúcido 9 oz Wau" in respuesta.text
    assert "EDEXA" in respuesta.text
    assert "stock, lead_days, ship_cost" in respuesta.text


def test_una_ficha_inexistente_o_invalida_responde_404() -> None:
    client, _ = make_client()

    assert client.get("/cotizador/ficha/no-existe").status_code == 404
    assert client.get("/cotizador/ficha/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_cada_cotizacion_emite_un_registro_auditable() -> None:
    client, store = make_client()
    token = _token(client)

    respuesta = client.post(
        "/cotizador",
        data={"question": "cotiza 20 vasos calientes en Bogotá", "csrf_token": token},
    )

    assert len(store.quotes) == 1
    registro = store.quotes[0]
    assert registro["id"] in respuesta.text
    assert registro["currency"] == "COP"
    assert registro["total"] == 3751
    assert registro["payload"]["quote"]["id"] == registro["id"]
    assert registro["owner"] and ":" not in registro["owner"]


def test_una_aclaracion_no_emite_cotizacion() -> None:
    client, store = make_client()
    token = _token(client)

    client.post(
        "/cotizador",
        data={"question": "cotiza 20 vasos calientes", "csrf_token": token},
    )

    assert store.quotes == []


def test_un_saludo_no_emite_otra_cotizacion() -> None:
    client, store = make_client()
    token = _token(client)
    client.post(
        "/cotizador",
        data={"question": "cotiza 20 vasos calientes en Bogotá", "csrf_token": token},
    )

    respuesta = client.post("/cotizador", data={"question": "hola", "csrf_token": token})

    assert respuesta.status_code == 200
    assert "Hola." in respuesta.text
    assert 'class="coti"' not in respuesta.text
    assert len(store.quotes) == 1
