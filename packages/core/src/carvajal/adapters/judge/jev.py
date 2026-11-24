"""Adaptador de Jev (TypeSafe System One): gatekeeper (Choice) y compuerta (Noul).

Spec 046, research R9 y contrato §2 a §4. Una sola petición ``POST {base}/systemone`` por
pregunta, con ``Authorization: Bearer <clave>``:

- :class:`JevClient` hace la petición con ``httpx.Client`` (conexiones reutilizadas) bajo un
  **plazo total** (``JEV_TIMEOUT_S``): cada petición corre en su propio hilo, que sale de
  inmediato (sin cola compartida, así que la concurrencia no consume el plazo ni envía
  peticiones de turnos que ya se rindieron), y se espera con un evento; los plazos por fase
  de httpx valen lo mismo para que el hilo también termine. Cualquier excepción de la
  petición (no solo de httpx: una clave con caracteres no ASCII, un cliente cerrado) cuenta
  como fallo de red. Sin reintentos. Lleva el tope diario por proceso (se reinicia a
  medianoche de Bogotá), el circuito (3 fallos seguidos → 60 s; 401/402/403 → 15 min) y el
  libro de llamadas (``JEV_LEDGER``: una línea JSON por llamada, también las fallidas, sin
  texto).
- :class:`JevJudge` arma las preguntas con los textos de ``prompts.py``, valida las
  respuestas e implementa los puertos ``RouteJudge`` y ``ClaimsJudge``.

Privacidad (FR-021): ni el cuerpo enviado ni el recibido se registran; los logs y el libro
llevan solo :class:`~carvajal.application.ports.JudgeCall` y la decisión o el veredicto. Las
excepciones se registran sin ``exc_info`` y la clave nunca aparece en mensajes ni ``repr``.
"""

import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final, TypeVar
from zoneinfo import ZoneInfo

import httpx
import structlog

from carvajal.application.ports import (
    GATE_CLASSES,
    ClaimsQuery,
    ClaimsVerdict,
    JevStatus,
    JudgeCall,
    JudgeKind,
    JudgeUnavailable,
    RouteQuery,
    RouteVerdict,
)
from carvajal.prompts import (
    JEV_CLAIMS_CRITERIA,
    JEV_CLAIMS_INSTRUCTIONS,
    JEV_ROUTE_CRITERIA,
    JEV_ROUTE_INSTRUCTIONS,
    JEV_SCORE_CLARITY,
)

__all__ = [
    "AUTH_OPEN_S",
    "FAILURES_TO_OPEN",
    "FAILURE_OPEN_S",
    "JevClient",
    "JevJudge",
    "claims_body",
    "route_body",
]

logger = structlog.get_logger(__name__)

T = TypeVar("T")

FAILURES_TO_OPEN: Final = 3
FAILURE_OPEN_S: Final = 60.0
AUTH_OPEN_S: Final = 15 * 60.0
_AUTH_STATUSES: Final = frozenset({401, 402, 403})
_BOGOTA: Final = ZoneInfo("America/Bogota")
ROUTE_QUESTION: Final = "ruta"
SCORE_QUESTION: Final = "claridad"
CLAIMS_QUESTION: Final = "respaldo"


class _Invalid(ValueError):
    """Respuesta 200 con una forma que no cumple el contrato."""


@dataclass(frozen=True)
class _Outcome:
    """Resultado HTTP crudo de una petición, sin el cuerpo enviado."""

    status_code: int | None
    payload: Any
    category: str = ""


class JevClient:
    """Cliente HTTP de Jev con plazo total, tope diario, circuito y libro de llamadas.

    ``transport`` permite inyectar ``httpx.MockTransport`` en pruebas; ``clock`` (segundos
    monotónicos) y ``now`` (fecha y hora con zona) permiten fijar el circuito y el día.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.typesafe.ai/v1",
        model: str = "jev-1.14.0",
        timeout_s: float = 0.8,
        max_calls_per_day: int = 1000,
        enabled: bool = True,
        ledger: str | Path | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = float(timeout_s)
        self.max_calls_per_day = int(max_calls_per_day)
        self.enabled = enabled
        self.ledger = Path(ledger) if ledger else None
        self._clock = clock
        self._now = now
        self._lock = threading.Lock()
        self._http = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(self.timeout_s),
            headers={"Content-Type": "application/json"},
        )
        # Estado del proceso (data-model *Estado del cliente*).
        self.calls_today = 0
        self._day: date | None = None
        self.failures = 0
        self.open_until: float | None = None
        self.last_error: str | None = None
        self.last_model: str = ""
        self._cap_logged = False
        self._auth_logged = False
        # Etiquetas del libro: las fija el comando de evaluación (``run``/``item``).
        self.run = "dev"
        self.item = ""

    def __repr__(self) -> str:  # la clave nunca sale en un ``repr``
        return f"JevClient(base_url={self.base_url!r}, model={self.model!r})"

    @property
    def configured(self) -> bool:
        return bool(self._key)

    # -- Disponibilidad (sin red) --

    def unavailable_status(self) -> JevStatus | None:
        """Estado por el que no se llamaría ahora; ``None`` si se puede llamar."""

        if not (self.configured and self.enabled):
            return "off"
        with self._lock:
            if self.open_until is not None and self._clock() < self.open_until:
                return "circuit_open"
            self._roll_day()
            if self.calls_today >= self.max_calls_per_day:
                return "cap"
        return None

    def available(self) -> bool:
        return self.unavailable_status() is None

    def _roll_day(self) -> None:
        today = self._now().astimezone(_BOGOTA).date()
        if self._day != today:
            self._day = today
            self.calls_today = 0
            self._cap_logged = False

    # -- Llamada --

    def call(
        self,
        kind: JudgeKind,
        state: Mapping[str, Any],
        questions: Mapping[str, Any],
        parse: Callable[[Mapping[str, Any]], tuple[T, dict[str, Any]]],
    ) -> tuple[T, JudgeCall]:
        """Una petición al juez; devuelve lo que ``parse`` extrae y los metadatos.

        ``parse`` recibe la respuesta JSON y devuelve el valor y los campos del libro
        (decisión o veredicto, sin texto); lanza ``ValueError`` si la forma es inválida.
        Cualquier fallo se convierte en :class:`JudgeUnavailable`.
        """

        blocked = self.unavailable_status()
        if blocked is not None:
            if blocked == "cap" and not self._cap_logged:
                self._cap_logged = True
                logger.warning("jev.cap_reached", calls_today=self.calls_today)
            raise JudgeUnavailable(blocked)
        with self._lock:
            self.calls_today += 1
        body = {"model": self.model, "state": dict(state), "questions": dict(questions)}
        started = self._clock()
        outcome = self._send(body)
        latency_ms = max(0, round((self._clock() - started) * 1000))
        status, category = self._classify(outcome)
        model = ""
        usage = (0, 0)
        value: Any = None
        extras: dict[str, Any] = {}
        if status == "ok":
            try:
                payload = outcome.payload
                if not isinstance(payload, Mapping):
                    raise _Invalid("respuesta sin objeto")
                model = str(payload.get("model") or "")
                usage = _usage(payload.get("usage"))
                value, extras = parse(payload)
            except (ValueError, TypeError, KeyError) as error:
                status, category = "invalid", "invalid"
                logger.warning("jev.invalid_response", kind=kind, error_type=type(error).__name__)
        call = JudgeCall(
            kind=kind,
            status=status,
            latency_ms=latency_ms,
            model=model,
            input_tokens=usage[0],
            output_tokens=usage[1],
        )
        self._after(status, category, model)
        self._annotate(call, extras)
        logger.info("jev.call", **call.to_value())
        if status != "ok":
            raise JudgeUnavailable(status, call)
        return value, call

    def _send(self, body: Mapping[str, Any]) -> _Outcome:
        """La petición en un hilo propio, esperada como mucho ``timeout_s``.

        Un hilo por llamada y no un ejecutor compartido: con una cola, el tiempo de espera
        contaría contra el plazo y la petición se enviaría después de que el turno se
        rindiera (gastando cupo y abriendo el circuito por la concurrencia, no por Jev).
        """

        box: list[_Outcome] = []
        done = threading.Event()

        def run() -> None:
            try:
                box.append(self._post(body))
            finally:
                done.set()

        threading.Thread(target=run, name="jev", daemon=True).start()
        if not done.wait(self.timeout_s) or not box:
            return _Outcome(None, None, "timeout" if not done.is_set() else "red")
        return box[0]

    def _post(self, body: Mapping[str, Any]) -> _Outcome:
        try:
            response = self._http.post(
                f"{self.base_url}/systemone",
                content=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers={"Authorization": f"Bearer {self._key}"},
            )
        except httpx.TimeoutException:
            return _Outcome(None, None, "timeout")
        except httpx.HTTPError:
            return _Outcome(None, None, "red")
        except Exception as error:  # nunca escapa del adaptador (FR-006)
            # Clave con caracteres no ASCII, cliente cerrado…: solo el tipo, nunca el texto.
            logger.warning("jev.request_failed", error_type=type(error).__name__)
            return _Outcome(None, None, "red")
        if response.status_code != 200:
            return _Outcome(response.status_code, None)
        try:
            return _Outcome(200, response.json())
        except ValueError:
            return _Outcome(200, None, "invalid")

    @staticmethod
    def _classify(outcome: _Outcome) -> tuple[JevStatus, str]:
        code = outcome.status_code
        if outcome.category == "timeout":
            return "timeout", "timeout"
        if code is None:
            return "error", outcome.category or "red"
        if code in _AUTH_STATUSES:
            return "auth", "auth"
        if code != 200:
            return "error", "http_5xx" if code >= 500 else f"http_{code}"
        if outcome.category == "invalid":
            return "invalid", "invalid"
        return "ok", ""

    def _after(self, status: JevStatus, category: str, model: str) -> None:
        """Circuito y último error tras una llamada (FR-023)."""

        with self._lock:
            if status == "ok":
                self.failures = 0
                self.last_model = model or self.last_model
                return
            self.last_error = category or status
            if status == "auth":
                self.open_until = self._clock() + AUTH_OPEN_S
                self.failures = 0
                if not self._auth_logged:
                    self._auth_logged = True
                    logger.warning("jev.auth_or_credits", open_s=AUTH_OPEN_S)
                return
            self.failures += 1
            if category not in ("timeout", "invalid", "red", "http_5xx", "http_429", "http_529"):
                logger.warning("jev.request_rejected", category=category)
            if self.failures >= FAILURES_TO_OPEN:
                self.open_until = self._clock() + FAILURE_OPEN_S
                self.failures = 0
                logger.warning("jev.circuit_open", open_s=FAILURE_OPEN_S, last_error=category)

    def _annotate(self, call: JudgeCall, extras: Mapping[str, Any]) -> None:
        """Añade una línea sin texto al libro, si está configurado."""

        if self.ledger is None:
            return
        self._write(
            {
                "ts": self._now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "run": self.run,
                "item": self.item,
                **call.to_value(),
                **dict(extras),
            }
        )

    def smoke(self, *, timeout_s: float = 5.0) -> dict[str, Any]:
        """Humo de la medición (research R11): ``GET {base}/models`` con la clave.

        Una llamada, anotada en el libro como ``run="smoke"`` y ``kind="smoke"``; cuenta para
        el tope del día. Devuelve el estado, la latencia y si el modelo fijado aparece en la
        lista. Sin reintentos; nunca lanza.
        """

        blocked = self.unavailable_status()
        if blocked is not None:
            return {"status": blocked, "latency_ms": 0, "model_listed": False}
        with self._lock:
            self.calls_today += 1
        started = self._clock()
        try:
            response = self._http.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self._key}"},
                timeout=timeout_s,
            )
            outcome = _Outcome(response.status_code, _json_or_none(response))
        except httpx.TimeoutException:
            outcome = _Outcome(None, None, "timeout")
        except httpx.HTTPError:
            outcome = _Outcome(None, None, "red")
        except Exception as error:  # el humo nunca lanza
            logger.warning("jev.request_failed", error_type=type(error).__name__)
            outcome = _Outcome(None, None, "red")
        latency_ms = max(0, round((self._clock() - started) * 1000))
        status, category = self._classify(outcome)
        listed = status == "ok" and self.model in _strings(outcome.payload)
        self._after(status, category, self.model if listed else "")
        line = {
            "ts": self._now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "run": "smoke",
            "item": "",
            "kind": "smoke",
            "status": status,
            "latency_ms": latency_ms,
            "model": self.model if listed else "",
            "input_tokens": 0,
            "output_tokens": 0,
        }
        self._write(line)
        logger.info("jev.smoke", status=status, latency_ms=latency_ms, model_listed=listed)
        return {"status": status, "latency_ms": latency_ms, "model_listed": listed}

    def _write(self, line: Mapping[str, Any]) -> None:
        if self.ledger is None:
            return
        try:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.ledger.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(dict(line), ensure_ascii=False) + "\n")
        except OSError as error:
            logger.warning("jev.ledger_failed", error_type=type(error).__name__)

    def close(self) -> None:
        self._http.close()


def _json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _strings(value: Any) -> set[str]:
    """Todas las cadenas de una respuesta JSON (para buscar el modelo en ``/models``)."""

    if isinstance(value, str):
        return {value}
    if isinstance(value, Mapping):
        return {text for item in value.values() for text in _strings(item)}
    if isinstance(value, list):
        return {text for item in value for text in _strings(item)}
    return set()


def _usage(value: Any) -> tuple[int, int]:
    """Tokens de entrada y salida; enteros o la respuesta es inválida."""

    if value is None:
        return 0, 0
    if not isinstance(value, Mapping):
        raise _Invalid("usage")
    tokens = []
    for key in ("input_tokens", "output_tokens"):
        item = value.get(key, 0)
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            raise _Invalid("usage")
        tokens.append(item)
    return tokens[0], tokens[1]


def _probability(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _Invalid("probabilidad")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise _Invalid("probabilidad fuera de rango")
    return number


def _clarity(value: Any) -> float:
    """Nivel de Score: la API devuelve el valor esperado (real) en [0, niveles - 1] (D1)."""

    top = len(JEV_SCORE_CLARITY["criteria"]) - 1
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _Invalid("score")
    number = float(value)
    if not 0.0 <= number <= top:  # también descarta NaN
        raise _Invalid("score fuera de rango")
    return round(number, 4)


def _answer(payload: Mapping[str, Any], name: str, kind: str) -> Mapping[str, Any]:
    answers = payload.get("answers")
    if not isinstance(answers, Mapping):
        raise _Invalid("answers")
    answer = answers.get(name)
    if not isinstance(answer, Mapping) or answer.get("type") != kind:
        raise _Invalid(name)
    return answer


# -- Cuerpos de las preguntas (contrato §2 y §3) --


def route_body(query: RouteQuery, *, with_score: bool = False) -> tuple[dict, dict]:
    """``state`` y ``questions`` de la pregunta Choice (y Score en la medición)."""

    state: dict[str, Any] = {"pregunta": query.question}
    if query.context:
        state["contexto"] = query.context
    questions: dict[str, Any] = {
        ROUTE_QUESTION: {
            "type": "choice",
            "instructions": JEV_ROUTE_INSTRUCTIONS,
            "criteria": {name: JEV_ROUTE_CRITERIA[name] for name in GATE_CLASSES},
        }
    }
    if with_score:
        questions[SCORE_QUESTION] = {
            "type": "score",
            "instructions": JEV_SCORE_CLARITY["instructions"],
            "criteria": list(JEV_SCORE_CLARITY["criteria"]),
        }
    return state, questions


def claims_body(query: ClaimsQuery) -> tuple[dict, dict]:
    """``state`` y ``questions`` de la pregunta Noul (la pregunta de la persona no viaja)."""

    state = {
        "evidencia": [{"consulta": name, "texto": text} for name, text in query.evidence],
        "respuesta": query.answer,
    }
    questions = {
        CLAIMS_QUESTION: {
            "type": "noul",
            "instructions": JEV_CLAIMS_INSTRUCTIONS,
            "criteria": {
                "true": JEV_CLAIMS_CRITERIA["true"],
                "false": JEV_CLAIMS_CRITERIA["false"],
            },
        }
    }
    return state, questions


class JevJudge:
    """Puertos ``RouteJudge`` y ``ClaimsJudge`` sobre :class:`JevClient`."""

    def __init__(self, client: JevClient, *, claims_threshold: float = 0.5) -> None:
        self.client = client
        self.claims_threshold = claims_threshold

    def available(self) -> bool:
        return self.client.available()

    def unavailable_status(self) -> JevStatus | None:
        return self.client.unavailable_status()

    def classify(self, query: RouteQuery, *, with_score: bool = False) -> RouteVerdict:
        state, questions = route_body(query, with_score=with_score)

        def parse(payload: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
            answer = _answer(payload, ROUTE_QUESTION, "choice")
            choice = answer.get("choice")
            if choice not in GATE_CLASSES:
                raise _Invalid("choice")
            raw = answer.get("probabilities")
            if not isinstance(raw, Mapping) or set(raw) != set(GATE_CLASSES):
                raise _Invalid("probabilities")
            probabilities = {name: round(_probability(raw[name]), 4) for name in GATE_CLASSES}
            if answer.get("confidence") is None:
                confidence = max(probabilities.values())
                logger.info("jev.confidence_missing")
            else:
                confidence = _probability(answer["confidence"])
            parsed: dict[str, Any] = {
                "choice": choice,
                "confidence": round(confidence, 4),
                "probabilities": probabilities,
                "clarity": None,
                "clarity_confidence": None,
            }
            if with_score:
                score = _answer(payload, SCORE_QUESTION, "score")
                parsed["clarity"] = _clarity(score.get("score"))
                parsed["clarity_confidence"] = (
                    round(_probability(score["confidence"]), 4)
                    if score.get("confidence") is not None
                    else None
                )
            extras = {
                key: value for key, value in parsed.items() if with_score or "clar" not in key
            }
            return parsed, extras

        parsed, call = self.client.call("router", state, questions, parse)
        return RouteVerdict(
            choice=parsed["choice"],
            confidence=parsed["confidence"],
            probabilities=dict(parsed["probabilities"]),
            call=call,
            clarity=parsed["clarity"],
            clarity_confidence=parsed["clarity_confidence"],
        )

    def check(self, query: ClaimsQuery) -> ClaimsVerdict:
        state, questions = claims_body(query)

        def parse(payload: Mapping[str, Any]) -> tuple[float, dict[str, Any]]:
            answer = _answer(payload, CLAIMS_QUESTION, "noul")
            p = round(_probability(answer.get("noul")), 4)
            return p, {"p_supported": p, "threshold": self.claims_threshold}

        p, call = self.client.call("claims", state, questions, parse)
        return ClaimsVerdict(p_supported=p, call=call)

    def close(self) -> None:
        self.client.close()
