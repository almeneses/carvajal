"""Jobs de captura web con progreso por SSE.

La captura es síncrona y con rate limit (un segundo por host), así que no puede
correr dentro de la petición del formulario: el endpoint crea el job, responde
con su identificador y el navegador consume el progreso por SSE. El registro
vive en memoria porque el despliegue usa un solo worker de uvicorn; si algún día
se escala, hará falta un lock distribuido.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

import structlog

from apps.api.sse import encode
from apps.api.web_capture import CaptureOutcome, CaptureRequest, ingest_urls
from carvajal.pipeline import CaptureEvent

__all__ = [
    "CaptureBusy",
    "CaptureJob",
    "CaptureJobs",
    "CaptureRateLimited",
    "default_capture_runner",
]

logger = structlog.get_logger(__name__)

EventHook = Callable[[CaptureEvent], None]
Runner = Callable[[CaptureRequest, EventHook], CaptureOutcome]

_JOB_TTL_SECONDS = 60 * 60


class CaptureBusy(RuntimeError):
    """Ya hay una captura en curso; escribir dos a la vez corrompería Gold."""


class CaptureRateLimited(RuntimeError):
    """El visitante superó el límite de capturas de su ventana."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = max(1, retry_after)
        super().__init__(
            "Has alcanzado el límite de capturas. "
            f"Inténtalo de nuevo en {self.retry_after} segundos."
        )


@dataclass
class CaptureJob:
    """Estado de una captura y la cola de eventos que consume el SSE."""

    id: str
    request: CaptureRequest
    status: str = "pending"
    outcome: CaptureOutcome | None = None
    error: str = ""
    created_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    finished: threading.Event = field(default_factory=threading.Event, repr=False)
    events: queue.Queue[tuple[str, dict[str, Any]] | None] = field(default_factory=queue.Queue)


def default_capture_runner(request: CaptureRequest, on_event: EventHook) -> CaptureOutcome:
    """Runner real: captura, importa a PostgreSQL e indexa en Qdrant."""

    return ingest_urls(request, on_event=on_event)


def _final_event(job: CaptureJob) -> str:
    """Evento final para una reconexión que llega después del cierre."""

    if job.outcome is not None:
        return encode("done", asdict(job.outcome))
    return encode("error", {"message": "La captura no se pudo completar."})


class CaptureJobs:
    """Registro en memoria con un solo job activo a la vez."""

    def __init__(
        self,
        runner: Runner | None = None,
        *,
        max_jobs: int = 20,
        rate_limit: int = 5,
        window_seconds: int = 3600,
    ) -> None:
        self._runner = runner or default_capture_runner
        self._jobs: dict[str, CaptureJob] = {}
        self._lock = threading.Lock()
        self._active: str | None = None
        self._max_jobs = max_jobs
        self._rate_limit = max(0, rate_limit)
        self._window_seconds = max(1, window_seconds)
        self._recent: dict[str, list[float]] = {}

    def start(self, request: CaptureRequest, *, owner_key: str = "") -> CaptureJob:
        """Crea el job y lo ejecuta en un hilo; falla si ya hay uno activo.

        ``owner_key`` es la IP del visitante para el límite de capturas. Un
        segundo envío con un job en curso recibe ``CaptureBusy``; superar la
        ventana recibe ``CaptureRateLimited``.
        """

        with self._lock:
            self._prune()
            if self._active is not None:
                active = self._jobs.get(self._active)
                if active is not None and active.status in {"pending", "running"}:
                    raise CaptureBusy("Ya hay una captura en curso. Espera a que termine.")
            self._check_rate(owner_key)
            job = CaptureJob(id=uuid.uuid4().hex, request=request)
            self._jobs[job.id] = job
            self._active = job.id

        logger.info(
            "capture.started",
            job_id=job.id,
            scope=request.scope,
            limit=request.limit,
            urls=len(request.urls),
        )
        try:
            thread = threading.Thread(
                target=self._run,
                args=(job,),
                name=f"captura-{job.id[:8]}",
                daemon=True,
            )
            thread.start()
        except Exception as error:  # el hilo no arrancó: el job no queda colgado
            self._finish_with_error(job, error)
            job.events.put(None)
        return job

    def get(self, job_id: str) -> CaptureJob | None:
        """Devuelve el job o ``None`` si ya se limpió."""

        with self._lock:
            return self._jobs.get(job_id)

    async def stream_async(
        self,
        job_id: str,
        is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncIterator[str]:
        """Emite el progreso sin retener el hilo si el cliente se desconecta.

        ``is_disconnected`` lo aporta la petición de Starlette; se consulta
        entre eventos para abortar el generador cuando el navegador cierra.
        El job de captura sigue corriendo en su hilo y termina de persistir.
        """

        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        sent_final = False
        while True:
            if is_disconnected is not None and await is_disconnected():
                return
            if job.finished.is_set() and job.events.empty():
                if not sent_final:
                    yield _final_event(job)
                return
            try:
                item = await asyncio.to_thread(job.events.get, True, 0.5)
            except queue.Empty:
                if job.finished.is_set():
                    if not sent_final:
                        yield _final_event(job)
                    return
                continue
            if item is None:
                continue
            name, data = item
            if name in {"done", "error"}:
                sent_final = True
            yield encode(name, data)

    def stream(self, job_id: str) -> Iterator[str]:
        """Emite los eventos del job hasta su cierre.

        ``finished`` es la señal de sincronización: se activa después de
        escribir ``outcome``/``error`` y antes de poner el centinela. Si el
        navegador reconecta cuando el job ya terminó, se reemite el resultado
        final aunque el centinela ya se haya consumido.
        """

        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        sent_final = False
        while True:
            if job.finished.is_set() and job.events.empty():
                if not sent_final:
                    yield _final_event(job)
                return
            try:
                item = job.events.get(timeout=0.5)
            except queue.Empty:
                if job.finished.is_set():
                    if not sent_final:
                        yield _final_event(job)
                    return
                continue
            if item is None:
                # Centinela: se drena lo que quede y el siguiente ciclo emite
                # el evento final solo si no se emitió ya.
                continue
            name, data = item
            if name in {"done", "error"}:
                sent_final = True
            yield encode(name, data)

    def _run(self, job: CaptureJob) -> None:
        """Ejecuta el runner y publica cada etapa en la cola."""

        job.status = "running"

        def on_event(event: CaptureEvent) -> None:
            job.events.put(
                (
                    "stage",
                    {
                        "stage": event.stage,
                        "url": event.url,
                        "detail": event.detail,
                    },
                )
            )

        try:
            outcome = self._runner(job.request, on_event)
            job.outcome = outcome
            job.status = "done"
            job.events.put(("done", asdict(outcome)))
            logger.info(
                "capture.finished",
                job_id=job.id,
                written=outcome.written,
                indexed=outcome.indexed,
                errors=len(outcome.errors),
            )
        except Exception as error:
            self._finish_with_error(job, error)
        finally:
            job.finished_at = time.monotonic()
            job.finished.set()
            with self._lock:
                if self._active == job.id:
                    self._active = None
            job.events.put(None)

    def _finish_with_error(self, job: CaptureJob, error: Exception) -> None:
        """Marca el job como fallido sin exponer detalles internos al navegador."""

        job.status = "error"
        job.error = str(error)
        logger.warning("capture.failed", job_id=job.id, error=str(error))
        job.events.put(("error", {"message": "La captura no se pudo completar."}))
        job.finished_at = time.monotonic()
        job.finished.set()
        with self._lock:
            if self._active == job.id:
                self._active = None

    def _check_rate(self, owner_key: str) -> None:
        """Anota la captura o lanza ``CaptureRateLimited`` si no queda cupo."""

        if self._rate_limit <= 0:
            return
        now = time.monotonic()
        recent = [
            stamp for stamp in self._recent.get(owner_key, []) if now - stamp < self._window_seconds
        ]
        if len(recent) >= self._rate_limit:
            retry_after = max(1, round(self._window_seconds - (now - recent[0])))
            raise CaptureRateLimited(retry_after)
        recent.append(now)
        self._recent[owner_key] = recent

    def _prune(self) -> None:
        """Descarta jobs y marcas de rate limit viejas."""

        now = time.monotonic()
        for job_id, job in list(self._jobs.items()):
            if job.finished_at is not None and now - job.finished_at > _JOB_TTL_SECONDS:
                del self._jobs[job_id]
        for owner_key, stamps in list(self._recent.items()):
            recent = [stamp for stamp in stamps if now - stamp < self._window_seconds]
            if recent:
                self._recent[owner_key] = recent
            else:
                del self._recent[owner_key]
        if len(self._jobs) <= self._max_jobs:
            return
        for job_id, job in sorted(self._jobs.items(), key=lambda item: item[1].created_at):
            if job.finished_at is None:
                continue
            del self._jobs[job_id]
            if len(self._jobs) <= self._max_jobs:
                break
