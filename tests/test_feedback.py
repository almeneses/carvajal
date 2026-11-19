"""Pruebas de las marcas del visitante (spec 038, US4).

Cubren el endpoint con CSRF y sin cuota, la idempotencia de la fila, la
visibilidad de los botones y que una marca nunca fuerce una traza (FR-012).
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


def test_sin_monitoreo_los_botones_no_aparecen(engine: Any) -> None:
    """La bandera ``data-monitoring`` apaga los botones (US4.2)."""

    cliente, _ = make_client(engine, monitoring=False)

    html = cliente.get("/chat").text

    assert 'data-monitoring="0"' in html
    assert "plantilla-turno" in html


def test_con_monitoreo_los_botones_estan_en_la_plantilla(engine: Any) -> None:
    """Con monitoreo activo el turno lleva los dos botones y su request_id."""

    cliente, _ = make_client(engine, monitoring=True)

    html = cliente.get("/chat").text

    assert 'data-monitoring="1"' in html
    assert 'data-marca="1"' in html
    assert 'data-marca="0"' in html
    assert "data-marcas" in html
    js = cliente.get("/static/chat-stream.js").text
    assert '"/chat/feedback"' in js
    assert "crearMarcas" in js
    assert "monitoreoActivo" in js


def test_una_marca_no_fuerza_una_traza() -> None:
    """T028: marcar un turno no muestreado no crea una raíz nueva (FR-012)."""

    from carvajal.infrastructure.monitoring import build_tracer

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

    with tracer.turn(
        request_id="req-sin-muestra",
        thread_hash="0123456789abcdef",
        router="rules",
        question="hola",
    ) as turno:
        turno.add_metadata(route="rag")

    # La marca vive en la base, no en la traza: el muestreo no se fuerza.
    assert construcciones == []


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
