"""Adaptador de Jev con ``httpx.MockTransport`` (spec 046, T007; contrato §2 a §4).

Sin red: cada prueba arma un transporte falso que registra la petición y responde lo que el
caso necesita. Cubre el cuerpo exacto de Choice y Noul, la validación de las respuestas, los
estados de error, el plazo total, el tope diario, el circuito, la clave fuera de logs y
``repr`` y el libro de llamadas sin texto.
"""

import contextlib
import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from carvajal.adapters.judge import build_judge, jev_health
from carvajal.adapters.judge.jev import JevClient, JevJudge
from carvajal.application.ports import (
    GATE_CLASSES,
    ClaimsQuery,
    JudgeUnavailable,
    RouteQuery,
)
from carvajal.infrastructure.settings import Settings
from carvajal.prompts import (
    JEV_CLAIMS_CRITERIA,
    JEV_CLAIMS_INSTRUCTIONS,
    JEV_ROUTE_CRITERIA,
    JEV_ROUTE_INSTRUCTIONS,
)

KEY = "clave-de-prueba-no-real-123"
PROBS = {"herramienta_estructurada": 0.09, "rag": 0.83, "mixta": 0.05, "respuesta_directa": 0.03}


class Recorder:
    """Transporte falso: registra cada petición y responde con ``respond``."""

    def __init__(self, respond: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self.respond = respond

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.respond(request)

    @property
    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]


def _choice(**overrides: Any) -> dict[str, Any]:
    answer = {"type": "choice", "choice": "rag", "confidence": 0.83, "probabilities": PROBS}
    answer.update(overrides)
    return {
        "model": "jev-1.14.0",
        "answers": {"ruta": answer},
        "usage": {"input_tokens": 412, "output_tokens": 9},
    }


def _noul(p: Any = 0.22) -> dict[str, Any]:
    return {
        "model": "jev-1.14.0",
        "answers": {"respaldo": {"type": "noul", "noul": p}},
        "usage": {"input_tokens": 2300, "output_tokens": 22},
    }


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _judge(
    respond: Callable[[httpx.Request], httpx.Response],
    *,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = lambda: datetime(2026, 11, 24, 15, tzinfo=UTC),
    timeout_s: float = 0.8,
    cap: int = 1000,
    ledger: Path | None = None,
    key: str = KEY,
) -> tuple[JevJudge, Recorder]:
    recorder = Recorder(respond)
    client = JevClient(
        api_key=key,
        timeout_s=timeout_s,
        max_calls_per_day=cap,
        ledger=ledger,
        transport=httpx.MockTransport(recorder.handler),
        clock=clock,
        now=now,
    )
    return JevJudge(client, claims_threshold=0.5), recorder


def _ok(payload: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(200, json=payload)


# --- Cuerpos (contrato §2 y §3) ---------------------------------------------------------------


def test_choice_envia_el_cuerpo_del_contrato_sin_contexto() -> None:
    # Arrange
    judge, recorder = _judge(_ok(_choice()))

    # Act
    verdict = judge.classify(RouteQuery(question="¿Quién fundó Carvajal?"))

    # Assert
    body = recorder.bodies[0]
    assert recorder.requests[0].url == "https://api.typesafe.ai/v1/systemone"
    assert recorder.requests[0].headers["Authorization"] == f"Bearer {KEY}"
    assert body["model"] == "jev-1.14.0"
    assert body["state"] == {"pregunta": "¿Quién fundó Carvajal?"}
    question = body["questions"]["ruta"]
    assert question["type"] == "choice"
    assert question["instructions"] == JEV_ROUTE_INSTRUCTIONS
    assert list(question["criteria"]) == list(GATE_CLASSES)
    assert question["criteria"] == JEV_ROUTE_CRITERIA
    assert set(body["questions"]) == {"ruta"}
    assert verdict.choice == "rag"
    assert verdict.confidence == 0.83
    assert verdict.probabilities == PROBS
    assert verdict.clarity is None
    assert verdict.call.status == "ok"
    assert verdict.call.model == "jev-1.14.0"
    assert (verdict.call.input_tokens, verdict.call.output_tokens) == (412, 9)


def test_choice_con_contexto_y_score() -> None:
    # Arrange
    payload = _choice()
    payload["answers"]["claridad"] = {"type": "score", "score": 3, "confidence": 0.71}
    judge, recorder = _judge(_ok(payload))

    # Act
    verdict = judge.classify(
        RouteQuery(question="¿y en Cali?", context="vasos 9 oz"), with_score=True
    )

    # Assert
    body = recorder.bodies[0]
    assert body["state"] == {"pregunta": "¿y en Cali?", "contexto": "vasos 9 oz"}
    assert body["questions"]["claridad"]["type"] == "score"
    assert len(body["questions"]["claridad"]["criteria"]) == 5
    assert verdict.clarity == 3
    assert verdict.clarity_confidence == 0.71


def test_noul_envia_respuesta_y_evidencia_sin_pregunta() -> None:
    # Arrange
    judge, recorder = _judge(_ok(_noul(0.22)))
    query = ClaimsQuery(
        answer="La de 16 oz es la más usada [1].",
        evidence=(("dicho_por_la_persona", "ciudad: Cali"), ("buscar_producto", '{"n": 1}')),
    )

    # Act
    verdict = judge.check(query)

    # Assert
    body = recorder.bodies[0]
    assert body["state"] == {
        "evidencia": [
            {"consulta": "dicho_por_la_persona", "texto": "ciudad: Cali"},
            {"consulta": "buscar_producto", "texto": '{"n": 1}'},
        ],
        "respuesta": "La de 16 oz es la más usada [1].",
    }
    assert "pregunta" not in body["state"]
    question = body["questions"]["respaldo"]
    assert question == {
        "type": "noul",
        "instructions": JEV_CLAIMS_INSTRUCTIONS,
        "criteria": JEV_CLAIMS_CRITERIA,
    }
    assert verdict.p_supported == 0.22
    assert verdict.call.kind == "claims"


# --- Validación ------------------------------------------------------------------------------


def test_sin_confidence_usa_la_mayor_probabilidad() -> None:
    # Arrange
    payload = _choice()
    del payload["answers"]["ruta"]["confidence"]
    judge, _ = _judge(_ok(payload))

    # Act
    verdict = judge.classify(RouteQuery(question="hola"))

    # Assert
    assert verdict.confidence == 0.83


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(_choice(choice="otra"), id="opcion-desconocida"),
        pytest.param(_choice(probabilities={"rag": 1.0}), id="probabilidades-incompletas"),
        pytest.param(_choice(confidence=1.4), id="confianza-fuera-de-rango"),
        pytest.param(_choice(type="noul"), id="tipo-equivocado"),
        pytest.param({"answers": {}}, id="sin-respuesta"),
        pytest.param({**_choice(), "usage": {"input_tokens": "x"}}, id="usage-no-entero"),
    ],
)
def test_choice_invalida(payload: dict[str, Any]) -> None:
    # Arrange
    judge, _ = _judge(_ok(payload))

    # Act / Assert
    with pytest.raises(JudgeUnavailable) as raised:
        judge.classify(RouteQuery(question="hola"))
    assert raised.value.status == "invalid"
    assert raised.value.call is not None and raised.value.call.status == "invalid"


@pytest.mark.parametrize("p", [1.5, -0.1, "0.5", None])
def test_noul_fuera_de_rango_es_invalida(p: Any) -> None:
    judge, _ = _judge(_ok(_noul(p)))

    with pytest.raises(JudgeUnavailable) as raised:
        judge.check(ClaimsQuery(answer="x", evidence=()))
    assert raised.value.status == "invalid"


def test_score_fuera_de_rango_es_invalida() -> None:
    payload = _choice()
    payload["answers"]["claridad"] = {"type": "score", "score": 7, "confidence": 0.5}
    judge, _ = _judge(_ok(payload))

    with pytest.raises(JudgeUnavailable):
        judge.classify(RouteQuery(question="hola"), with_score=True)


# --- Errores HTTP y red ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (400, "error"),
        (401, "auth"),
        (402, "auth"),
        (403, "auth"),
        (422, "error"),
        (429, "error"),
        (500, "error"),
        (529, "error"),
    ],
)
def test_errores_http(code: int, status: str) -> None:
    # Arrange
    judge, _ = _judge(lambda request: httpx.Response(code, json={"error": "x"}))

    # Act / Assert
    with pytest.raises(JudgeUnavailable) as raised:
        judge.classify(RouteQuery(question="hola"))
    assert raised.value.status == status
    assert KEY not in str(raised.value)
    assert KEY not in repr(raised.value)


def test_error_de_red() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red", request=request)

    judge, _ = _judge(fail)

    with pytest.raises(JudgeUnavailable) as raised:
        judge.check(ClaimsQuery(answer="x", evidence=()))
    assert raised.value.status == "error"
    assert judge.client.last_error == "red"


def test_demora_mayor_que_el_plazo_es_timeout_dentro_del_plazo() -> None:
    # Arrange
    def slow(request: httpx.Request) -> httpx.Response:
        time.sleep(1.0)
        return httpx.Response(200, json=_choice())

    judge, _ = _judge(slow, timeout_s=0.2)

    # Act
    started = time.monotonic()
    with pytest.raises(JudgeUnavailable) as raised:
        judge.classify(RouteQuery(question="hola"))
    elapsed = time.monotonic() - started

    # Assert
    assert raised.value.status == "timeout"
    assert elapsed <= 0.2 + 0.1


def test_la_concurrencia_no_consume_el_plazo_ni_abre_el_circuito() -> None:
    # Arrange: 8 turnos a la vez con Jev respondiendo dentro del plazo (0,3 s de 0,8 s).
    def slow(request: httpx.Request) -> httpx.Response:
        time.sleep(0.3)
        return httpx.Response(200, json=_choice())

    judge, recorder = _judge(slow, timeout_s=0.8)
    barrier = threading.Barrier(8)
    results: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        try:
            judge.classify(RouteQuery(question="¿qué vasos tienen?"))
            status = "ok"
        except JudgeUnavailable as error:
            status = error.status
        with lock:
            results.append(status)

    threads = [threading.Thread(target=worker) for _ in range(8)]

    # Act
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Assert: ninguna espera en cola vence el plazo.
    assert results == ["ok"] * 8
    assert len(recorder.requests) == 8
    assert judge.client.calls_today == 8
    assert judge.client.last_error is None
    assert judge.available() is True


def test_una_excepcion_fuera_de_httpx_es_error_de_red() -> None:
    # Arrange: clave con un espacio de ancho cero (no se puede codificar en la cabecera).
    judge, recorder = _judge(_ok(_choice()), key="abc\u200bdef")

    # Act
    with capture_logs() as logs, pytest.raises(JudgeUnavailable) as raised:
        judge.classify(RouteQuery(question="hola"))

    # Assert: se convierte en ``JudgeUnavailable`` y cuenta para el circuito.
    assert raised.value.status == "error"
    assert judge.client.last_error == "red"
    assert judge.client.failures == 1
    assert recorder.requests == []
    failed = [log for log in logs if log["event"] == "jev.request_failed"]
    assert failed == [
        {"event": "jev.request_failed", "error_type": "UnicodeEncodeError", "log_level": "warning"}
    ]


def test_un_cliente_cerrado_es_error_de_red() -> None:
    judge, _ = _judge(_ok(_choice()))
    judge.close()

    with pytest.raises(JudgeUnavailable) as raised:
        judge.check(ClaimsQuery(answer="x", evidence=()))

    assert raised.value.status == "error"
    assert judge.client.last_error == "red"


# --- Tope diario y circuito ------------------------------------------------------------------


def test_tope_diario_y_reinicio_a_medianoche_de_bogota() -> None:
    # Arrange: 23:30 en Bogotá (04:30 UTC del día siguiente).
    moment = {"now": datetime(2026, 11, 25, 4, 30, tzinfo=UTC)}
    judge, recorder = _judge(_ok(_choice()), cap=2, now=lambda: moment["now"])

    # Act
    judge.classify(RouteQuery(question="a"))
    judge.classify(RouteQuery(question="b"))
    with capture_logs() as logs, pytest.raises(JudgeUnavailable) as raised:
        judge.classify(RouteQuery(question="c"))
    blocked = judge.available()
    moment["now"] += timedelta(hours=1)  # 00:30 en Bogotá: otro día

    # Assert
    assert raised.value.status == "cap"
    assert len(recorder.requests) == 2
    assert blocked is False
    assert [log["event"] for log in logs] == ["jev.cap_reached"]
    assert judge.available() is True
    judge.classify(RouteQuery(question="d"))
    assert len(recorder.requests) == 3


def test_tres_fallos_abren_el_circuito_60_segundos() -> None:
    # Arrange
    clock = Clock()
    judge, recorder = _judge(lambda request: httpx.Response(500), clock=clock)

    # Act
    for _ in range(3):
        with pytest.raises(JudgeUnavailable):
            judge.classify(RouteQuery(question="hola"))
    with pytest.raises(JudgeUnavailable) as raised:
        judge.classify(RouteQuery(question="hola"))

    # Assert
    assert raised.value.status == "circuit_open"
    assert len(recorder.requests) == 3
    clock.t += 61
    assert judge.available() is True


def test_un_ok_reinicia_los_fallos() -> None:
    clock = Clock()
    responses = iter([500, 500, 200, 500, 500])

    def respond(request: httpx.Request) -> httpx.Response:
        code = next(responses)
        return httpx.Response(code, json=_choice() if code == 200 else {})

    judge, _ = _judge(respond, clock=clock)

    for _ in range(5):
        with contextlib.suppress(JudgeUnavailable):
            judge.classify(RouteQuery(question="hola"))

    assert judge.available() is True


def test_401_abre_15_minutos_con_un_solo_log() -> None:
    # Arrange
    clock = Clock()
    judge, recorder = _judge(lambda request: httpx.Response(402), clock=clock)

    # Act
    with capture_logs() as logs:
        with pytest.raises(JudgeUnavailable):
            judge.classify(RouteQuery(question="hola"))
        clock.t += 16 * 60
        with pytest.raises(JudgeUnavailable):
            judge.classify(RouteQuery(question="hola"))
        clock.t += 60
        with pytest.raises(JudgeUnavailable) as raised:
            judge.classify(RouteQuery(question="hola"))

    # Assert
    assert raised.value.status == "circuit_open"
    assert len(recorder.requests) == 2
    assert [log["event"] for log in logs].count("jev.auth_or_credits") == 1


def test_sin_clave_no_esta_disponible_ni_hace_peticiones() -> None:
    judge, recorder = _judge(_ok(_choice()), key="")

    assert judge.available() is False
    with pytest.raises(JudgeUnavailable) as raised:
        judge.classify(RouteQuery(question="hola"))
    assert raised.value.status == "off"
    assert recorder.requests == []


# --- Clave, logs y libro ---------------------------------------------------------------------


def test_la_clave_no_aparece_en_repr_ni_en_logs() -> None:
    # Arrange
    judge, _ = _judge(lambda request: httpx.Response(401))

    # Act
    with capture_logs() as logs, pytest.raises(JudgeUnavailable):
        judge.classify(RouteQuery(question="¿Cuánto vale el vaso?"))

    # Assert
    assert KEY not in repr(judge.client)
    assert KEY not in repr(judge)
    dumped = json.dumps(logs, default=str)
    assert KEY not in dumped
    assert "¿Cuánto vale el vaso?" not in dumped


def test_settings_no_muestran_la_clave() -> None:
    settings = Settings(_env_file=None, typesafe_api_key=KEY, jev_enabled=True)

    assert KEY not in repr(settings)
    assert KEY not in str(settings.model_dump())


def test_el_libro_anota_cada_llamada_sin_texto(tmp_path: Path) -> None:
    # Arrange
    ledger = tmp_path / "jev-calls.jsonl"
    responses = iter([httpx.Response(200, json=_choice()), httpx.Response(500)])
    judge, _ = _judge(lambda request: next(responses), ledger=ledger)
    judge.client.run, judge.client.item = "gatekeeper", "gk-07"

    # Act
    judge.classify(RouteQuery(question="¿Quién fundó Carvajal?", context="historia"))
    with pytest.raises(JudgeUnavailable):
        judge.classify(RouteQuery(question="¿Quién fundó Carvajal?"))

    # Assert
    lines = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[0]["run"] == "gatekeeper"
    assert lines[0]["item"] == "gk-07"
    assert lines[0]["status"] == "ok"
    assert lines[0]["choice"] == "rag"
    assert lines[0]["probabilities"] == PROBS
    assert lines[1]["status"] == "error"
    text = ledger.read_text()
    assert "Carvajal" not in text
    assert "historia" not in text
    assert KEY not in text


def test_build_judge_respeta_el_encendido_explicito() -> None:
    off = Settings(_env_file=None, typesafe_api_key=KEY)
    on = Settings(_env_file=None, typesafe_api_key=KEY, jev_enabled=True)
    empty = Settings(_env_file=None, jev_enabled=True)

    assert build_judge(off) is None
    assert build_judge(empty) is None
    assert isinstance(build_judge(on), JevJudge)
    assert isinstance(build_judge(off, force=True), JevJudge)


def test_build_judge_rechaza_una_clave_no_ascii_sin_mostrarla() -> None:
    bad = "clave\u200b-pegada"
    settings = Settings(_env_file=None, typesafe_api_key=bad, jev_enabled=True)

    with capture_logs() as logs:
        judge = build_judge(settings)

    assert judge is None
    assert [log["event"] for log in logs] == ["jev.invalid_key"]
    assert bad not in str(logs)
    assert jev_health(settings)["available"] is False


def test_jev_health_sin_runtime_y_con_cliente() -> None:
    # Arrange
    settings = Settings(_env_file=None, typesafe_api_key=KEY, jev_enabled=True)
    judge, _ = _judge(_ok(_choice()))

    # Act
    without = jev_health(settings)
    judge.classify(RouteQuery(question="hola"))
    with_client = jev_health(settings, judge)

    # Assert
    assert without == {
        "configured": True,
        "enabled": True,
        "available": True,
        "model": "jev-1.14.0",
        "router": "on",
        "claims": "enforce",
        "calls_today": 0,
        "last_error": None,
    }
    assert with_client["calls_today"] == 1
    assert with_client["available"] is True


def test_jev_health_en_modo_routes_no_esta_disponible() -> None:
    # Arrange: Jev encendido con clave, pero el modo ``routes`` nunca lo llama (FR-009).
    routes = Settings(_env_file=None, typesafe_api_key=KEY, jev_enabled=True, agent_mode="routes")
    tools = Settings(_env_file=None, typesafe_api_key=KEY, jev_enabled=True)

    # Act
    configured_routes = jev_health(routes)
    served_routes = jev_health(tools, None, mode="routes")
    built_without_judge = jev_health(tools, None, mode="tools")

    # Assert
    for block in (configured_routes, served_routes):
        assert block["available"] is False
        assert block["router"] == "off"
        assert block["claims"] == "off"
        assert block["configured"] is True
    assert built_without_judge["available"] is False
    assert built_without_judge["router"] == "on"
