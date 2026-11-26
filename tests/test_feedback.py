"""Pruebas de las marcas del visitante (spec 038, US4).

Cubren el endpoint con CSRF y sin cuota, la idempotencia de la fila y que una marca nunca
fuerce una traza (FR-012). Desde el 2026-11-26 la web ya no muestra los botones: el endpoint
y la tabla ``chat_feedback`` se conservan sin interfaz que los use.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.pool import StaticPool

from apps.api.chat.feedback import SqlFeedbackRepository
from apps.api.main import HealthStatus, create_app
from apps.api.security import CSRF_COOKIE, CSRF_FIELD
from carvajal.adapters.storage import tables
from carvajal.application.quota import QuotaResult
from carvajal.infrastructure.settings import Settings


@pytest.fixture()
def engine() -> Iterator[Any]:
    motor = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables.metadata.create_all(motor)
    try:
        yield motor
    finally:
        tables.metadata.drop_all(motor)
        motor.dispose()


class CuotaContada:
    def __init__(self) -> None:
        self.llamadas = 0

    def __call__(self, owner_key: str) -> QuotaResult:
        self.llamadas += 1
        return QuotaResult(allowed=True)


def make_client(engine: Any, *, monitoring: bool = True) -> tuple[TestClient, CuotaContada]:
    """Cliente con la marca y la cuota inyectadas; sin Postgres."""

    from sqlalchemy.orm import Session

    cuota = CuotaContada()

    def marcar(request_id: str, useful: bool) -> None:
        with Session(bind=engine) as session:
            SqlFeedbackRepository(session).record(request_id, useful)
            session.commit()

    app = create_app()
    app.state.feedback = marcar
    app.state.quota = cuota
    app.state.monitoring = monitoring
    app.state.health = lambda: HealthStatus(1, 2)
    return TestClient(app), cuota


def csrf(client: TestClient) -> str:
    if CSRF_COOKIE not in client.cookies:
        client.get("/faq")
    return client.cookies[CSRF_COOKIE]


def filas(engine: Any) -> int:
    from sqlalchemy.orm import Session

    with Session(bind=engine) as session:
        return int(session.scalar(select(func.count()).select_from(tables.chat_feedback)) or 0)


def test_sin_csrf_responde_403(engine: Any) -> None:
    """La marca exige el mismo token que el resto de los POST (FR-012)."""

    cliente, cuota = make_client(engine)

    respuesta = cliente.post("/chat/feedback", data={"request_id": "req-1", "useful": "1"})

    assert respuesta.status_code == 403
    assert filas(engine) == 0
    assert cuota.llamadas == 0


def test_marca_valida_responde_204_y_no_cobra_cuota(engine: Any) -> None:
    cliente, cuota = make_client(engine)

    respuesta = cliente.post(
        "/chat/feedback",
        data={"request_id": "req-1", "useful": "1", CSRF_FIELD: csrf(cliente)},
    )

    assert respuesta.status_code == 204
    assert respuesta.text == ""
    assert filas(engine) == 1
    assert cuota.llamadas == 0


@pytest.mark.parametrize(
    "datos",
    [
        {"request_id": "", "useful": "1"},
        {"request_id": "con espacios", "useful": "1"},
        {"request_id": "x" * 65, "useful": "1"},
        {"request_id": "req-1", "useful": "quizá"},
    ],
)
def test_entradas_invalidas_responden_400(engine: Any, datos: dict[str, str]) -> None:
    cliente, _ = make_client(engine)

    respuesta = cliente.post("/chat/feedback", data={**datos, CSRF_FIELD: csrf(cliente)})

    assert respuesta.status_code == 400
    assert filas(engine) == 0


def test_acepta_el_request_id_del_middleware(engine: Any) -> None:
    """El id que admite el middleware de logs también vale para la marca."""

    from apps.api.observability import REQUEST_ID_PATTERN

    cliente, _ = make_client(engine)
    identificador = "req.2026.1119.abc"

    assert REQUEST_ID_PATTERN.fullmatch(identificador) is not None
    respuesta = cliente.post(
        "/chat/feedback",
        data={"request_id": identificador, "useful": "1", CSRF_FIELD: csrf(cliente)},
    )

    assert respuesta.status_code == 204
    assert filas(engine) == 1


def test_marcar_dos_veces_no_duplica_la_fila(engine: Any) -> None:
    """El upsert por ``request_id`` es idempotente (T024, T027)."""

    cliente, _ = make_client(engine)
    token = csrf(cliente)
    datos = {"request_id": "req-2", CSRF_FIELD: token}

    primera = cliente.post("/chat/feedback", data={**datos, "useful": "1"})
    segunda = cliente.post("/chat/feedback", data={**datos, "useful": "0"})

    assert (primera.status_code, segunda.status_code) == (204, 204)
    assert filas(engine) == 1
    from sqlalchemy.orm import Session

    with Session(bind=engine) as session:
        guardada = SqlFeedbackRepository(session).get("req-2")
    assert guardada is not None
    assert guardada.useful is False


@pytest.mark.parametrize("monitoring", [False, True])
def test_el_chat_ya_no_ofrece_los_botones_de_marca(engine: Any, monitoring: bool) -> None:
    """Ensayo del 2026-11-26: la web retiró «¿Te sirvió esta respuesta? [Útil] [No útil]».

    Con o sin monitoreo, ni la plantilla del turno ni el JavaScript del chat ofrecen la marca;
    el endpoint se conserva (pruebas de arriba) para las páginas abiertas antes del despliegue.
    """

    # Arrange
    cliente, _ = make_client(engine, monitoring=monitoring)

    # Act
    html = cliente.get("/chat").text
    js = cliente.get("/static/chat-stream.js").text

    # Assert
    assert "plantilla-turno" in html
    assert "data-marcas" not in html
    assert "data-marca=" not in html
    assert "Te sirvió esta respuesta" not in html
    assert "data-monitoring" not in html
    assert "/chat/feedback" not in js
    assert "crearMarcas" not in js


def test_una_marca_no_fuerza_una_traza(engine: Any) -> None:
    """T028: marcar un turno no muestreado no crea raíz y conserva la fila."""

    from sqlalchemy.orm import Session

    from carvajal.infrastructure.monitoring import build_tracer

    cliente, _ = make_client(engine)
    construcciones: list[int] = []

    def fabrica(_settings: Settings) -> Any:
        construcciones.append(1)
        return None

    tracer = build_tracer(
        Settings(
            _env_file=None,
            monitoring_enabled=True,
            langsmith_api_key="lsv2_pt_x",
            monitoring_sample_rate=0.0,
        ),
        client_factory=fabrica,
        random_fn=lambda: 0.0,
    )
    # El turno real del chat con un tracer que no muestrea.
    with tracer.turn(
        request_id="req-sin-muestra",
        thread_hash="0123456789abcdef",
        router="rules",
        question="hola",
    ) as turno:
        turno.add_metadata(route="rag")
    respuesta = cliente.post(
        "/chat/feedback",
        data={"request_id": "req-sin-muestra", "useful": "1", CSRF_FIELD: csrf(cliente)},
    )

    assert respuesta.status_code == 204
    assert construcciones == []
    with Session(bind=engine) as session:
        guardada = SqlFeedbackRepository(session).get("req-sin-muestra")
    assert guardada is not None and guardada.useful is True


def test_la_marca_sobrevive_sin_traza(engine: Any) -> None:
    """Un turno no muestreado conserva su marca con el ``request_id`` (US4.3)."""

    cliente, _ = make_client(engine)

    respuesta = cliente.post(
        "/chat/feedback",
        data={"request_id": "req-sin-traza", "useful": "0", CSRF_FIELD: csrf(cliente)},
    )

    assert respuesta.status_code == 204
    from sqlalchemy.orm import Session

    with Session(bind=engine) as session:
        guardada = SqlFeedbackRepository(session).get("req-sin-traza")
    assert guardada is not None
    assert guardada.useful is False


def test_el_endpoint_no_guarda_texto(engine: Any) -> None:
    """Solo se persisten ``request_id``, ``useful`` y la fecha (FR-012)."""

    cliente, _ = make_client(engine)

    cliente.post(
        "/chat/feedback",
        data={
            "request_id": "req-texto",
            "useful": "1",
            "question": "mi teléfono es 300 123 4567",
            "texto": "Ana Pérez",
            CSRF_FIELD: csrf(cliente),
        },
    )

    from sqlalchemy.orm import Session

    with Session(bind=engine) as session:
        fila = session.execute(select(tables.chat_feedback)).mappings().one()
    assert set(fila) == {"request_id", "useful", "created_at"}
    assert "300 123 4567" not in repr(dict(fila))
