"""Monitoreo opcional del agente con LangSmith (spec 038).

El módulo concentra la política del monitoreo para que la app y la CLI la
compartan sin acoplar el chat al servicio:

- ``monitoring_status`` resume la decisión de despliegue para ``/health``.
- ``redact`` aplica la lista blanca de metadatos cuando el contenido no debe
  salir del proceso (FR-006).
- ``build_client`` construye el ``langsmith.Client`` de forma perezosa; sin
  configuración devuelve ``None`` y no se importa el SDK.
- ``TurnTracer``/``NoopTracer`` deciden el muestreo por turno y abren la traza
  raíz. Cualquier fallo del servicio se registra en los logs y nunca se
  propaga al chat (FR-008).
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable, Generator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from threading import Lock
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

import structlog

from carvajal.infrastructure.settings import Settings

log = structlog.get_logger(__name__)

# Claves que sobreviven a la redacción en producción: metadatos del turno,
# decisiones del enrutador, identificadores del corpus, métricas, tokens y el
# tipo del error. Todo lo demás se elimina, sin importar la profundidad.
_DESCARTAR = object()

# Claves que nunca viajan, ni siquiera en desarrollo: identifican al visitante
# (FR-005). El hilo se envía seudonimizado como ``thread_hash``.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "owner_key",
        "thread_id",
        "conversation_id",
        "cookie",
        "cookies",
        "csrf",
        "csrf_token",
        "api_key",
        "authorization",
        "password",
        "secret",
        "session",
        "session_id",
        "ip",
    }
)

SAFE_KEYS: frozenset[str] = frozenset(
    {
        "route",
        "intent",
        "tool",
        "tool_name",
        "tool_period",
        "preferences_count",
        "product_count",
        "slug",
        "chunk_id",
        "layer",
        "source_type",
        "score",
        "scores",
        "model",
        "model_name",
        "provider",
        "token_usage",
        "usage_metadata",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "duration_ms",
        "latency_ms",
        "question_chars",
        "answer_chars",
        "error_type",
        "status",
        "request_id",
        "thread_hash",
        "router",
        "outcome",
        "step",
        # LangChain publica el modelo, el proveedor y el consumo bajo estas
        # claves; conservarlas mantiene FR-003 sin arrastrar el texto.
        "llm_output",
        "ls_provider",
        "ls_model_name",
        "ls_model_type",
        "ls_integration",
    }
)


@dataclass(frozen=True, slots=True)
class MonitoringStatus:
    """Estado efectivo del monitoreo para el diagnóstico de salud (FR-011)."""

    enabled: bool
    sample_rate: float
    content: str
    project: str

    def as_health(self) -> dict[str, Any]:
        """Bloque público de ``/health``: sin llave, sin endpoint (FR-005)."""

        return {"enabled": self.enabled, "sample_rate": self.sample_rate, "content": self.content}


def monitoring_status(settings: Settings) -> MonitoringStatus:
    """Resume si el monitoreo está activo y con qué política.

    Se considera activo solo si está encendido, hay llave y el muestreo es
    mayor que cero; así ``/health`` no anuncia un monitoreo que no envía nada.
    """

    activo = (
        settings.monitoring_enabled
        and bool(settings.langsmith_api_key.strip())
        and settings.monitoring_sample_rate > 0
    )
    return MonitoringStatus(
        enabled=activo,
        sample_rate=settings.monitoring_sample_rate,
        content="full" if settings.monitoring_include_content else "metadata",
        project=settings.langsmith_project,
    )


def thread_hash(thread_id: str) -> str:
    """Identificador seudónimo del hilo: primeros 16 hex del SHA-256.

    El hilo crudo nunca viaja a la traza (FR-005). Para buscar por hilo se
    aplica la misma derivación al identificador guardado en la base.
    """

    return hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:16]


def redact(payload: Any, include_content: bool) -> Any:
    """Filtra ``payload`` con la lista blanca de metadatos (FR-006).

    Con ``include_content`` activo devuelve el diccionario tal cual: solo se usa
    en desarrollo y en las corridas de evaluación.
    """

    if include_content:
        return payload
    limpio = _redact(payload, permitido=False)
    return limpio if isinstance(limpio, dict) else {}


def _redact(value: Any, permitido: bool) -> Any:
    """Recorre el payload conservando solo lo permitido.

    Un escalar sobrevive únicamente bajo una clave de la lista blanca. Los
    diccionarios se filtran por sus propias claves —así un fragmento conserva
    ``slug`` y ``score`` aunque su contenedor no esté permitido— y una lista de
    escalares solo sobrevive si su clave lo está (por ejemplo ``scores``).
    """

    if isinstance(value, Mapping):
        limpio: dict[str, Any] = {}
        for clave, item in value.items():
            if not isinstance(clave, str):
                continue
            hijo = _redact(item, clave in SAFE_KEYS)
            if hijo is _DESCARTAR:
                continue
            limpio[clave] = hijo
        if not limpio and not permitido:
            return _DESCARTAR
        return limpio
    if isinstance(value, (list, tuple, set)):
        elementos = [
            _redact(item, permitido if not isinstance(item, Mapping) else False) for item in value
        ]
        limpios = [item for item in elementos if item is not _DESCARTAR]
        if not limpios and not permitido:
            return _DESCARTAR
        return limpios
    return value if permitido else _DESCARTAR


def redact_run(payload: Any, include_content: bool) -> Any:
    """Anonimizador del cliente: lista blanca y errores sin mensaje.

    El SDK aplica este gancho a entradas, salidas, metadatos **y** al texto de
    ``error`` de cada run, incluidas las hijas que instrumenta LangChain. Los
    errores se reducen siempre a su tipo —ni en desarrollo viaja un mensaje que
    pueda arrastrar datos del visitante o credenciales— (FR-005, FR-006).
    """

    if not isinstance(payload, Mapping):
        return {}
    if set(payload) == {"error"}:
        return {"error": _tipo_de_error(payload["error"])}
    if include_content:
        return dict(payload)
    limpio = redact(payload, include_content=False)
    return limpio if isinstance(limpio, dict) else {}


def _tipo_de_error(error: Any) -> str:
    """Reduce un mensaje de excepción a su tipo.

    Cubre las dos formas habituales: ``ValueError: detalle`` y el ``repr``
    ``ValueError('detalle')`` que produce LangChain. En ambos casos el texto
    del visitante queda fuera.
    """

    texto = str(error).strip()
    if not texto:
        return "Error"
    primera = texto.splitlines()[0].strip()
    for corte in ("(", ":", "\n"):
        primera = primera.split(corte, 1)[0]
    candidato = primera.strip()
    # Solo un nombre de tipo sobrevive: un mensaje suelto («boom», con espacios
    # o en minúsculas) se descarta para no arriesgar FR-005.
    if not candidato or not all(caracter.isalnum() or caracter in "_." for caracter in candidato):
        return "Error"
    if not (candidato[0].isupper() or candidato.endswith(("Error", "Exception", "Warning"))):
        return "Error"
    return candidato


def redact_run_ops(ops: Any, include_content: bool) -> Any:
    """Anonimizador de segundo nivel para los campos que el SDK no filtra.

    El ``anonymizer`` del cliente cubre entradas, salidas, metadatos y errores,
    pero no ``extra`` ni ``serialized``, donde un nodo puede dejar parámetros o
    prompts. En producción se vacían; en desarrollo se respetan.
    """

    if include_content:
        return ops
    limpios = []
    for op in ops:
        if not isinstance(op, dict):
            limpios.append(op)
            continue
        nuevo = dict(op)
        if "extra" in nuevo:
            extra = nuevo.get("extra")
            metadatos = extra.get("metadata") if isinstance(extra, dict) else None
            nuevo["extra"] = {"metadata": redact(metadatos, False)}
        if "serialized" in nuevo:
            nuevo["serialized"] = {}
        limpios.append(nuevo)
    return limpios


def build_client(settings: Settings) -> Any | None:
    """Construye el cliente de LangSmith, o ``None`` si no hay monitoreo.

    La llave se toma del entorno; el endpoint es opcional. El cliente redacta
    entradas, salidas y metadatos de cada run con la lista blanca y reporta sus
    fallos por el callback de errores, que solo escribe en los logs.
    """

    if not settings.monitoring_enabled:
        return None
    if not settings.langsmith_api_key.strip():
        log.warning("monitoring.disabled", reason="missing_api_key")
        return None
    if settings.monitoring_sample_rate <= 0:
        return None

    anonimizador = partial(redact_run, include_content=settings.monitoring_include_content)
    try:
        # Importación perezosa: sin monitoreo no se carga el SDK. Un SDK
        # ausente o roto degrada a «sin monitoreo», nunca al chat (FR-008).
        from langsmith import Client

        return Client(
            api_url=settings.langsmith_endpoint or None,
            api_key=settings.langsmith_api_key.strip(),
            # ``anonymizer`` cubre entradas, salidas, metadatos y el texto de
            # ``error``; ``hide_inputs``/``hide_outputs`` no llegan al error de
            # una run hija, que puede arrastrar el texto de la conversación.
            anonymizer=anonimizador,
            # ``extra``/``serialized`` quedan fuera del anonymizer: se filtran
            # en el búfer antes de enviar (FR-006).
            process_buffered_run_ops=partial(
                redact_run_ops, include_content=settings.monitoring_include_content
            ),
            # El SDK exige un tamaño explícito cuando hay este callback.
            run_ops_buffer_size=100,
            run_ops_buffer_timeout_ms=settings.monitoring_timeout_ms,
            timeout_ms=settings.monitoring_timeout_ms,
            auto_batch_tracing=True,
            tracing_error_callback=_registrar_fallo,
            # Sin ``info`` el SDK consulta ``/info`` al construir el cliente y
            # esa llamada bloquearía el primer fragmento del turno (SC-003).
            # El precio es un aviso de compresión en el primer envío.
            info={},
        )
    except Exception as error:  # SDK ausente, endpoint inválido o firma rota
        _registrar_fallo(error)
        return None


def _registrar_fallo(error: BaseException) -> None:
    """Registra el fallo del monitoreo sin filtrar el mensaje ni la credencial."""

    log.warning("monitoring.failed", stage="tracing", error_type=type(error).__name__)


@dataclass(frozen=True, slots=True)
class ExperimentOutcome:
    """Resultado de intentar registrar una corrida de evaluación (FR-009)."""

    registered: bool
    dataset: str = ""
    experiment: str = ""
    detail: str = ""

    def as_message(self) -> str:
        if self.registered:
            return f"Experimento {self.experiment} registrado sobre {self.dataset}."
        return f"Experimento no registrado: {self.detail}"


def build_eval_client(settings: Settings) -> Any | None:
    """Cliente para registrar experimentos; ``None`` si no hay llave.

    La evaluación es explícita (bandera ``--langsmith``), así que no depende de
    ``MONITORING_ENABLED``: el contenido completo está permitido aquí, aunque
    los errores se siguen reduciendo a su tipo.
    """

    if not settings.langsmith_api_key.strip():
        return None
    try:
        from langsmith import Client

        return Client(
            api_url=settings.langsmith_endpoint or None,
            api_key=settings.langsmith_api_key.strip(),
            anonymizer=partial(redact_run, include_content=True),
            timeout_ms=settings.monitoring_timeout_ms,
            info={},
        )
    except Exception as error:
        _registrar_fallo(error)
        return None


def commit_sha() -> str:
    """Commit del código evaluado, resuelto contra la raíz del repositorio.

    Se ejecuta con ``cwd`` en la raíz del checkout, no en el directorio actual,
    para no registrar el commit de otro proyecto. Devuelve cadena vacía si el
    árbol no es un repositorio (por ejemplo, una instalación empaquetada).
    """

    from subprocess import CalledProcessError, run

    raiz = Path(__file__).resolve().parents[5]
    try:
        return run(
            ["git", "-C", str(raiz), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (CalledProcessError, OSError):
        return ""


def example_id(dataset: str, row_id: str) -> str:
    """Identificador estable del ejemplo: el mismo en cada corrida."""

    return str(uuid5(NAMESPACE_URL, f"carvajal://{dataset}/{row_id}"))


def _ensure_dataset(
    client: Any,
    dataset: str,
    rows: list[dict[str, Any]],
    description: str,
) -> Any:
    """Devuelve el dataset y completa los ejemplos que falten.

    El nombre se deriva del archivo de preguntas, así un conjunto distinto no
    reutiliza un dataset con otras preguntas. Si una corrida anterior falló a
    medias, los ejemplos que falten se crean ahora en lugar de dejar el
    dataset vacío para siempre.
    """

    try:
        referencia = client.read_dataset(dataset_name=dataset)
    except Exception:
        try:
            referencia = client.create_dataset(dataset_name=dataset, description=description)
        except Exception:
            # Otra corrida lo creó entre la lectura y el alta.
            referencia = client.read_dataset(dataset_name=dataset)
    existentes = {str(ejemplo.id) for ejemplo in client.list_examples(dataset_id=referencia.id)}
    faltantes = [
        {
            "id": example_id(dataset, fila["id"]),
            "dataset_id": referencia.id,
            "inputs": fila["inputs"],
            "outputs": fila.get("expected") or {},
        }
        for fila in rows
        if example_id(dataset, fila["id"]) not in existentes
    ]
    if faltantes:
        # El SDK exige ``dataset_id`` en la llamada (``xor_args``): el campo
        # dentro del ejemplo no existe en ``ExampleCreate``.
        client.create_examples(dataset_id=referencia.id, examples=faltantes)
    return referencia


def register_experiment(
    client: Any,
    *,
    dataset: str,
    experiment: str,
    rows: list[dict[str, Any]],
    metrics: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    description: str = "",
) -> ExperimentOutcome:
    """Registra una corrida ya calculada como experimento de LangSmith.

    Cada fila queda enlazada a su ejemplo del dataset (``reference_example_id``),
    que es lo que permite comparar dos corridas pregunta a pregunta, y se crea
    cerrada (``start_time``/``end_time``) para que el servicio calcule la
    latencia. Los agregados van a los metadatos del experimento, no a una run
    ficticia. No se vuelve a llamar al modelo (T019).
    """

    ahora = datetime.now(UTC)
    try:
        referencia = _ensure_dataset(client, dataset, rows, description)
        metadatos = {**(metadata or {}), "metrics": metrics}
        proyecto = client.create_project(
            experiment,
            description=description,
            reference_dataset_id=referencia.id,
            metadata=metadatos,
            upsert=True,
        )
        for fila in rows:
            client.create_run(
                name=fila["id"],
                run_type="chain",
                inputs=fila["inputs"],
                outputs=fila.get("outputs") or {},
                project_name=proyecto.name,
                reference_example_id=example_id(dataset, fila["id"]),
                start_time=ahora,
                # La latencia medida del informe local da la duración real.
                end_time=ahora
                + timedelta(
                    milliseconds=float(
                        (fila.get("outputs") or {}).get("latency_ms")
                        or fila.get("latency_ms")
                        or 0.0
                    )
                ),
                extra={"metadata": metadata or {}},
            )
    except Exception as error:
        log.warning("monitoring.experiment_failed", error_type=type(error).__name__)
        return ExperimentOutcome(
            False, dataset=dataset, experiment=experiment, detail=type(error).__name__
        )
    return ExperimentOutcome(True, dataset=dataset, experiment=experiment)


class Turn(Protocol):
    """Handle de un turno en curso; no hace nada si el turno no se muestra."""

    def add_metadata(self, **values: Any) -> None:
        """Añade metadatos a la raíz (ruta, intención, herramienta, longitudes)."""

    def add_outputs(self, **values: Any) -> None:
        """Añade salidas al cerrar la raíz."""

    def finish(self, *, outcome: str = "ok", error: BaseException | None = None) -> None:
        """Cierra la raíz una sola vez con ``ok``, ``cancelled`` o ``failed``."""


class TurnTracer(Protocol):
    """Abre la traza raíz de un turno si el muestreo lo decide."""

    def turn(
        self,
        *,
        request_id: str,
        thread_hash: str,
        router: str,
        question: str = "",
    ) -> AbstractContextManager[Turn]:
        """Devuelve el *context manager* del turno."""


class NoopTurn:
    """Turno sin traza: la misma interfaz, sin efectos."""

    __slots__ = ()

    def add_metadata(self, **values: Any) -> None:
        return None

    def add_outputs(self, **values: Any) -> None:
        return None

    def finish(self, *, outcome: str = "ok", error: BaseException | None = None) -> None:
        return None


class NoopTracer:
    """Tracer del monitoreo apagado: sin cliente, sin SDK y sin red."""

    @contextmanager
    def turn(
        self,
        *,
        request_id: str,
        thread_hash: str,
        router: str,
        question: str = "",
    ) -> Generator[Turn]:
        yield NoopTurn()


class LangSmithTurn:
    """Raíz ``chat_turn`` de un turno muestreado."""

    def __init__(self, root: Any, *, include_content: bool) -> None:
        self._root = root
        self._include_content = include_content
        self._outputs: dict[str, Any] = {}
        self._closed = False

    @contextmanager
    def child(
        self, name: str, *, run_type: str = "chain", metadata: Mapping[str, Any] | None = None
    ) -> Generator[Turn]:
        """Crea un hijo directo de la raíz, sin depender del contexto."""

        if self._closed:
            yield NoopTurn()
            return
        try:
            hijo = self._root.create_child(name=name, run_type=run_type)
            if metadata:
                hijo.add_metadata(dict(metadata))
            hijo.post()
        except Exception as error:  # pragma: no cover - defensa ante SDK
            _registrar_fallo(error)
            yield NoopTurn()
            return
        interno = LangSmithTurn(hijo, include_content=self._include_content, client=self._client)
        try:
            yield interno
        except GeneratorExit:
            # Cancelación del visitante: el paso queda cancelado, no fallido.
            interno.finish(outcome="cancelled")
            raise
        except BaseException as error:
            interno.finish(outcome="failed", error=error)
            raise
        else:
            interno.finish(outcome="ok")

    @contextmanager
    def generation(self) -> Generator[None]:
        """Reentra el contexto de tracing en el hilo que avanza el stream."""

        if self._client is None:
            yield
            return
        contexto: Any = None
        try:
            from langsmith.run_helpers import tracing_context

            contexto = tracing_context(parent=self._root, client=self._client, enabled=True)
            contexto.__enter__()
        except Exception as error:  # pragma: no cover - defensa ante SDK
            _registrar_fallo(error)
            yield
            return
        try:
            yield
        finally:
            # El error del cuerpo no se captura aquí: debe propagarse tal cual
            # (un segundo yield rompería el protocolo de contextmanager).
            try:
                contexto.__exit__(None, None, None)
            except Exception as error:  # pragma: no cover - defensa ante SDK
                _registrar_fallo(error)

    def add_metadata(self, **values: Any) -> None:
        if self._closed:
            return
        try:
            self._root.add_metadata(values)
        except Exception as error:  # pragma: no cover - defensa ante un SDK roto
            _registrar_fallo(error)

    def add_outputs(self, **values: Any) -> None:
        if self._closed:
            return
        self._outputs.update(values)
        # ``answer_chars`` sobrevive a la redacción: es el dato útil cuando el
        # turno se cancela o falla a medias (T010).
        respuesta = self._outputs.get("answer")
        if isinstance(respuesta, str):
            try:
                self._root.add_metadata({"answer_chars": len(respuesta)})
            except Exception as error:  # pragma: no cover - defensa ante SDK
                _registrar_fallo(error)

    def finish(self, *, outcome: str = "ok", error: BaseException | None = None) -> None:
        """Cierra la raíz; idempotente ante ``close`` y ``finally``."""

        if self._closed:
            return
        self._closed = True
        respuesta = self._outputs.get("answer")
        salidas = {
            "outcome": outcome,
            "answer_chars": len(respuesta) if isinstance(respuesta, str) else 0,
            **self._outputs,
        }
        if error is not None:
            salidas["error_type"] = type(error).__name__
        try:
            cierre: dict[str, Any] = {"outcome": outcome}
            # ``answer_chars`` también en un fallo sin tokens: es el único dato
            # de la respuesta que sobrevive en producción (T010).
            respuesta = self._outputs.get("answer")
            cierre["answer_chars"] = len(respuesta) if isinstance(respuesta, str) else 0
            if error is not None:
                cierre["error_type"] = type(error).__name__
            self._root.add_metadata(cierre)
            self._root.end(
                outputs=salidas,
                error=None if error is None else type(error).__name__,
            )
            self._root.patch(exclude_inputs=not self._include_content)
        except Exception as fallo:
            _registrar_fallo(fallo)


class LangSmithTurnTracer:
    """Tracer real: muestrea, construye el cliente una vez y abre la raíz."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: Any = None,
        client_factory: Callable[[Settings], Any] | None = None,
        random_fn: Callable[[], float] | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._client_factory = client_factory or build_client
        self._random = random_fn or random.random
        self._cliente_resuelto = client is not None
        self._lock = Lock()

    def _cliente(self) -> Any | None:
        """Construye el cliente la primera vez y lo reutiliza entre turnos.

        El lock evita que dos peticiones simultáneas construyan un cliente cada
        una en el primer turno muestreado.
        """

        with self._lock:
            if not self._cliente_resuelto:
                self._client = self._client_factory(self._settings)
                self._cliente_resuelto = True
            return self._client

    @contextmanager
    def turn(
        self,
        *,
        request_id: str,
        thread_hash: str,
        router: str,
        question: str = "",
    ) -> Generator[Turn]:
        estado = monitoring_status(self._settings)
        if not estado.enabled or self._random() >= estado.sample_rate:
            yield NoopTurn()
            return

        try:
            cliente = self._cliente()
        except Exception as fallo:  # fábrica rota: el turno sigue sin traza
            _registrar_fallo(fallo)
            yield NoopTurn()
            return
        if cliente is None:
            yield NoopTurn()
            return

        incluir = self._settings.monitoring_include_content
        try:
            from langsmith import RunTree
            from langsmith.run_helpers import tracing_context
        except Exception as error:  # pragma: no cover - SDK ausente o roto
            _registrar_fallo(error)
            yield NoopTurn()
            return

        raiz: Any
        try:
            raiz = RunTree(
                name="chat_turn",
                run_type="chain",
                inputs={"question": question} if incluir else {},
                session_name=self._settings.langsmith_project,
                ls_client=cliente,
            )
            raiz.add_metadata(
                {
                    "request_id": request_id,
                    "thread_hash": thread_hash,
                    "router": router,
                    "question_chars": len(question),
                }
            )
            raiz.post()
        except Exception as fallo:
            _registrar_fallo(fallo)
            yield NoopTurn()
            return

        # El contexto de tracing se abre y cierra aparte: si el SDK falla al
        # entrar o al salir, la traza se degrada pero el error del turno no se
        # confunde con un fallo del monitoreo (FR-008).
        contexto: Any = None
        try:
            contexto = tracing_context(parent=raiz, client=cliente, enabled=True)
            contexto.__enter__()
        except Exception as fallo:
            _registrar_fallo(fallo)
            contexto = None

        turno = LangSmithTurn(raiz, include_content=incluir)
        try:
            yield turno
        except GeneratorExit:
            # Cancelación del visitante: se cierra con lo que alcanzó a generarse.
            turno.finish(outcome="cancelled")
            raise
        except BaseException as fallo:
            turno.finish(outcome="failed", error=fallo)
            raise
        else:
            turno.finish(outcome="ok")
        finally:
            if contexto is not None:
                try:
                    contexto.__exit__(None, None, None)
                except Exception as fallo:
                    _registrar_fallo(fallo)


def build_tracer(
    settings: Settings,
    *,
    client: Any = None,
    client_factory: Callable[[Settings], Any] | None = None,
    random_fn: Callable[[], float] | None = None,
) -> TurnTracer:
    """Devuelve el tracer que corresponde a la configuración.

    Apagado no se construye el tracer real, de modo que ni siquiera se importa
    el SDK. Las pruebas inyectan el cliente y la función de azar.
    """

    if not monitoring_status(settings).enabled:
        return NoopTracer()
    return LangSmithTurnTracer(
        settings,
        client=client,
        client_factory=client_factory,
        random_fn=random_fn,
    )
