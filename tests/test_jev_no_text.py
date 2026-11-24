"""Ningún texto enviado a Jev queda en logs, traza, LangSmith ni Sentry (spec 046, T041).

Con un turno completo (gatekeeper y compuerta con ``FakeJudge``) se capturan los logs de
structlog, el evento ``trace`` y la raíz y los hijos del monitoreo (con contenido completo y
solo metadatos), y se busca cualquier fragmento de lo que viajaría a Jev. Sentry se prueba en
un subproceso con un transporte falso: una excepción con texto en sus variables locales no
lo envía.
"""

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from structlog.testing import capture_logs
from tests.fakes.judge import FakeJudge
from tests.fakes.tool_chat import ScriptStep
from tests.test_chat_service_tools import FakeTracer, graph_service, named

from apps.api.observability import strip_local_variables
from carvajal.adapters.storage import tables
from carvajal.infrastructure.monitoring import redact

REPO = Path(__file__).resolve().parents[1]
QUESTION = "me llamo ana restrepo y quiero saber cuándo se fundó Carvajal, porfa"
ANSWER = "Carvajal fue fundada en Cali en 1904 [1], dato que confirma la historia oficial."
EVIDENCE = "Carvajal fue fundada en Cali en 1904."
FRAGMENTS = ("ana restrepo", "porfa", "historia oficial", EVIDENCE)


@pytest.fixture()
def sqlite_engine() -> Iterator[Any]:
    """SQLite en memoria con el esquema real (como ``tests/test_chat_service_tools.py``)."""

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
        tables.metadata.drop_all(motor)
        motor.dispose()


def _turn(engine: Any) -> tuple[list[Any], list[Any], FakeTracer, FakeJudge]:
    judge = FakeJudge(choice="rag", confidence=0.9, p=0.9)
    tracer = FakeTracer()
    steps = [
        ScriptStep(tool_calls=[{"name": "buscar_corpus", "args": {"consulta": "fundación"}}]),
        ScriptStep(text=ANSWER),
    ]
    service = graph_service(engine, steps, judge=judge, tracer=tracer)
    with capture_logs() as logs:
        events = list(service.stream("dueno-sin-texto", QUESTION))
    return events, logs, tracer, judge


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def test_logs_y_traza_sin_texto_enviado(sqlite_engine: Any) -> None:
    # Act
    events, logs, _, judge = _turn(sqlite_engine)

    # Assert: Jev sí recibió las cargas (para que la búsqueda tenga sentido).
    assert judge.routes and judge.claims
    jev_logs = [
        log for log in logs if str(log["event"]).startswith(("jev.", "gatekeeper.", "claims."))
    ]
    assert {log["event"] for log in jev_logs} >= {"gatekeeper.decided", "claims.judged"}
    dumped_logs = _dump(logs)
    trace = _dump(named(events, "trace").data)
    for fragment in FRAGMENTS:
        assert fragment not in dumped_logs
        assert fragment not in trace
    assert judge.routes[0].question not in dumped_logs


@pytest.mark.parametrize("include_content", [True, False])
def test_langsmith_sin_texto_enviado_con_y_sin_contenido(
    sqlite_engine: Any, include_content: bool
) -> None:
    # Act
    _, _, tracer, _ = _turn(sqlite_engine)

    # Assert: la raíz y los hijos ``jev`` (redactados como lo haría el SDK) no llevan el
    # texto de las cargas; con contenido completo solo la salida ``answer`` lleva la
    # respuesta emitida, que no es una carga de Jev.
    span = tracer.turns[0]
    root = redact(span.metadata, include_content=include_content)
    children = [
        redact(meta, include_content=include_content)
        for name, _, meta, child in span.children
        if name == "jev"
    ]
    assert len(children) == 2
    for fragment in FRAGMENTS:
        assert fragment not in _dump(root)
        assert fragment not in _dump(children)
    for name, _, _, child in span.children:
        if name == "jev":
            assert child.outputs == {}


def test_strip_local_variables_quita_vars_de_todos_los_frames() -> None:
    event = {
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {"module": "carvajal.agent.gatekeeper.claims", "vars": {"a": QUESTION}},
                            {"module": "carvajal.adapters.judge.jev", "vars": {"b": ANSWER}},
                        ]
                    }
                }
            ]
        }
    }

    cleaned = strip_local_variables(event)

    assert QUESTION not in _dump(cleaned) and ANSWER not in _dump(cleaned)


_SENTRY_SCRIPT = r"""
import json
import sentry_sdk
from sentry_sdk.transport import Transport
from apps.api import observability
from carvajal.infrastructure.settings import Settings

events = []

class Capture(Transport):
    def capture_envelope(self, envelope):
        for item in envelope.items:
            events.append(item.get_bytes().decode("utf-8", "replace"))

original = sentry_sdk.init
sentry_sdk.init = lambda **kwargs: original(transport=Capture(), **kwargs)
observability.init_sentry(Settings(_env_file=None, sentry_dsn="https://public@example.invalid/1"))

def {name}():
    texto = "PREGUNTA" + "-ENVIADA-A-JEV"
    raise RuntimeError("fallo")

try:
    {name}()
except RuntimeError as error:
    sentry_sdk.capture_exception(error)
sentry_sdk.flush()
text = "".join(events)
print(json.dumps({{"events": len(events), "leak": "PREGUNTA-ENVIADA-A-JEV" in text}}))
"""


def _script(name: str) -> str:
    return _SENTRY_SCRIPT.replace("{name}", name).replace("{{", "{").replace("}}", "}")


@pytest.mark.parametrize("name", ["build_claims_query", "check"])
def test_sentry_no_envia_variables_locales(name: str) -> None:
    # Act: una excepción con el texto en una variable local, capturada por Sentry real con
    # un transporte falso, en un proceso aislado (Sentry es global).
    result = subprocess.run(
        [sys.executable, "-c", _script(name)],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env={"PYTHONPATH": str(REPO), "PATH": "/usr/bin:/bin"},
    )

    # Assert
    assert result.returncode == 0, result.stderr[-2000:]
    outcome = json.loads(result.stdout.strip().splitlines()[-1])
    assert outcome == {"events": 1, "leak": False}
