"""Jobs de captura web con estado compartido en PostgreSQL y progreso por SSE.

La captura es síncrona y con rate limit (un segundo por host), así que no puede
correr dentro de la petición del formulario: el endpoint crea el job, responde
con su identificador y el navegador consume el progreso por SSE.

Con tres workers de uvicorn sobre el mismo contenedor, el registro en memoria no
sirve: la petición que crea el job y la que consulta su progreso caen en procesos
distintos. Este módulo mueve a PostgreSQL el estado del job, sus eventos de
progreso, la exclusión global de la captura y el cupo por IP, y deja un backend
en memoria con la misma interfaz para las pruebas unitarias.

La exclusión es una fila única (``capture_control``) tomada con
``SELECT ... FOR UPDATE``; el worker que la posee escribe el estado condicionado
a su ``lease_token``. Un latido persistido permite detectar al worker que dejó de
dar señales de vida: el job se marca ``interrupted``, se ejecuta la
sincronización idempotente de Gold antes de aceptar la siguiente captura y nunca
se reanuda la captura por URL. La IP no se guarda: el cupo se indexa por un
resumen HMAC estable.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import socket
import threading
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

import structlog
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    MetaData,
    SmallInteger,
    Table,
    Text,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, sessionmaker

from apps.api.sse import encode
from apps.api.web_capture import CaptureOutcome, CaptureRequest, ingest_urls
from carvajal.pipeline import CaptureEvent

__all__ = [
    "CaptureBusy",
    "CaptureJob",
    "CaptureJobs",
    "CaptureRateLimited",
    "MemoryCaptureBackend",
    "PostgresCaptureBackend",
    "default_capture_runner",
]

logger = structlog.get_logger(__name__)

EventHook = Callable[[CaptureEvent], None]
Runner = Callable[[CaptureRequest, EventHook], CaptureOutcome]

_JOB_TTL_SECONDS = 60 * 60
_MAX_JOBS = 20
_LEASE_SECONDS = 120.0
_HEARTBEAT_SECONDS = 15.0
_POLL_SECONDS = 0.5
_PRUNE_BATCH = 200
_FINAL_EVENTS = frozenset({"done", "error"})
_TERMINAL = frozenset({"done", "error", "interrupted"})
_ERROR_MESSAGE = "La captura no se pudo completar."
_INTERRUPTED_MESSAGE = "La captura se interrumpió antes de terminar. Puedes lanzar una nueva."
_BUSY_MESSAGE = "Ya hay una captura en curso. Espera a que termine."
_OWNER_PEPPER = b"carvajal-captura-v1"
_RECOVERY_LEASE = b"carvajal-recovery-v1"


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
    """Estado de una captura, reconstruible desde la base en cualquier worker."""

    id: str
    request: CaptureRequest
    status: str = "pending"
    outcome: CaptureOutcome | None = None
    error: str = ""
    created_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None


@dataclass(frozen=True)
class StartResult:
    """Job aceptado y, si lo hubo, el job interrumpido que se recuperó antes."""

    job: CaptureJob
    recovered: CaptureJob | None = None


@dataclass
class _ActiveCapture:
    """Captura local de este proceso y su latido."""

    job: CaptureJob
    lease_token: str
    stop: threading.Event = field(default_factory=threading.Event)


def default_capture_runner(request: CaptureRequest, on_event: EventHook) -> CaptureOutcome:
    """Runner real: captura, importa a PostgreSQL e indexa en Qdrant."""

    return ingest_urls(request, on_event=on_event)


def _worker_id() -> str:
    """Identidad del proceso que atiende la captura, para diagnóstico."""

    return f"{socket.gethostname()}:{os.getpid()}"


def owner_digest(owner_key: str, secret: str = "") -> str:
    """Resumen estable de la IP para el cupo; nunca se guarda la IP en claro.

    Con ``SESSION_SECRET`` se usa HMAC-SHA256 (el valor no permite recuperar ni
    enumerar IPs con la tabla). En desarrollo, sin secreto, se usa SHA-256 con un
    prefijo fijo: sigue siendo estable entre workers, que es lo que exige el cupo
    compartido, y evita guardar el dato crudo.
    """

    data = owner_key.encode("utf-8")
    if secret:
        return hmac.new(secret.encode("utf-8"), data, hashlib.sha256).hexdigest()
    return hashlib.sha256(_OWNER_PEPPER + b":" + data).hexdigest()


def _fresh(heartbeat_at: datetime | None, lease_seconds: float, now: datetime) -> bool:
    """Indica si un latido está dentro del umbral de vida."""

    if heartbeat_at is None:
        return False
    return now - heartbeat_at < timedelta(seconds=lease_seconds)


def _final_event(job: CaptureJob) -> str:
    """Evento final para una reconexión que llega después del cierre."""

    if job.status == "interrupted":
        return encode("error", {"message": _INTERRUPTED_MESSAGE})
    if job.outcome is not None:
        return encode("done", asdict(job.outcome))
    return encode("error", {"message": _ERROR_MESSAGE})


def _encode_event(seq: int | None, name: str, data: Mapping[str, Any]) -> str:
    """Evento SSE con identificador estándar para que EventSource reanude."""

    evento = encode(name, data)
    if seq is None:
        return evento
    return f"id: {seq}\n{evento}"


def _outcome_from_json(raw: Mapping[str, Any] | None) -> CaptureOutcome | None:
    """Reconstruye el resumen estable a partir del JSON persistido."""

    if not raw:
        return None
    return CaptureOutcome(
        requested=int(raw.get("requested", 0)),
        discovered=int(raw.get("discovered", 0)),
        fetched=int(raw.get("fetched", 0)),
        written=int(raw.get("written", 0)),
        discarded=int(raw.get("discarded", 0)),
        chars=int(raw.get("chars", 0)),
        documents=int(raw.get("documents", 0)),
        chunks=int(raw.get("chunks", 0)),
        indexed=int(raw.get("indexed", 0)),
        duration_seconds=float(raw.get("duration_seconds", 0.0)),
        errors=tuple(raw.get("errors") or ()),
        robots_denied=tuple(raw.get("robots_denied") or ()),
        duplicates=tuple(raw.get("duplicates") or ()),
        captured=tuple(raw.get("captured") or ()),
    )


def _request_to_json(request: CaptureRequest) -> dict[str, Any]:
    """Serializa la petición normalizada del formulario."""

    return {
        "urls": list(request.urls),
        "scope": request.scope,
        "respect_robots": request.respect_robots,
        "limit": request.limit,
    }


def _request_from_json(raw: Mapping[str, Any]) -> CaptureRequest:
    """Reconstruye la petición normalizada desde el JSON persistido."""

    return CaptureRequest(
        urls=tuple(raw.get("urls") or ()),
        scope=str(raw.get("scope", "manual")),
        respect_robots=bool(raw.get("respect_robots", True)),
        limit=int(raw.get("limit", 1)),
    )


class CaptureBackend(Protocol):
    """Persistencia y exclusión de la captura; en memoria o en PostgreSQL."""

    def acquire(
        self,
        request: CaptureRequest,
        owner_hash: str,
        *,
        job_id: str,
        lease_token: str,
        worker: str,
    ) -> StartResult:
        """Reserva la captura global y crea el job, o lanza un error de captura."""

        ...

    def mark_running(self, job_id: str, lease_token: str) -> None:
        """Marca el job como en ejecución."""

        ...

    def heartbeat(self, job_id: str, lease_token: str) -> bool:
        """Renueva el latido; ``False`` si el worker perdió el testigo."""

        ...

    def record_event(
        self,
        job_id: str,
        lease_token: str,
        name: str,
        data: Mapping[str, Any],
    ) -> bool:
        """Persiste un evento de progreso; ``False`` si perdió el testigo."""

        ...

    def complete(
        self,
        job_id: str,
        lease_token: str,
        *,
        status: str,
        outcome: CaptureOutcome | None,
        error: str,
    ) -> bool:
        """Cierra el job con su evento final y libera la captura global."""

        ...

    def get(self, job_id: str) -> CaptureJob | None:
        """Devuelve el job o ``None`` si no existe o expiró."""

        ...

    def events(self, job_id: str, after: int = 0) -> list[tuple[int, str, dict[str, Any]]]:
        """Eventos del job con secuencia mayor que ``after``, en orden."""

        ...

    def recover_interrupted(self) -> list[CaptureJob]:
        """Marca los jobs interrumpidos y los devuelve."""

        ...

    def shutdown(self) -> None:
        """Libera recursos del backend al apagar el proceso."""

        ...


class MemoryCaptureBackend:
    """Registro en memoria con la misma interfaz; solo para pruebas y desarrollo.

    Conserva el comportamiento que tenía el registro original: un job activo por
    proceso, cupo en memoria y poda por TTL y por número de jobs.
    """

    def __init__(
        self,
        *,
        max_jobs: int = _MAX_JOBS,
        rate_limit: int = 5,
        window_seconds: int = 3600,
        job_ttl_seconds: float | None = None,
    ) -> None:
        self._jobs: dict[str, CaptureJob] = {}
        self._events: dict[str, list[tuple[int, str, dict[str, Any]]]] = {}
        self._leases: dict[str, str] = {}
        self._lock = threading.Lock()
        self._active: str | None = None
        self._max_jobs = max_jobs
        self._rate_limit = max(0, rate_limit)
        self._window_seconds = max(1, window_seconds)
        self._job_ttl = job_ttl_seconds
        self._recent: dict[str, list[float]] = {}

    def acquire(
        self,
        request: CaptureRequest,
        owner_hash: str,
        *,
        job_id: str,
        lease_token: str,
        worker: str,
    ) -> StartResult:
        """Crea el job si no hay otro activo y queda cupo para el dueño."""

        with self._lock:
            self._prune_locked()
            if self._active is not None:
                active = self._jobs.get(self._active)
                if active is not None and active.status in {"pending", "running"}:
                    raise CaptureBusy(_BUSY_MESSAGE)
            self._check_rate_locked(owner_hash)
            job = CaptureJob(id=job_id, request=request)
            self._jobs[job.id] = job
            self._events[job.id] = []
            self._leases[job.id] = lease_token
            self._active = job.id
        return StartResult(job)

    def mark_running(self, job_id: str, lease_token: str) -> None:
        """Marca el job como en ejecución."""

        with self._lock:
            job = self._jobs.get(job_id)
            if (
                job is not None
                and self._leases.get(job_id) == lease_token
                and job.status == "pending"
            ):
                job.status = "running"

    def heartbeat(self, job_id: str, lease_token: str) -> bool:
        """En memoria el latido solo confirma que el testigo sigue vigente."""

        with self._lock:
            job = self._jobs.get(job_id)
            return (
                job is not None
                and job.status == "running"
                and self._leases.get(job_id) == lease_token
            )

    def record_event(
        self,
        job_id: str,
        lease_token: str,
        name: str,
        data: Mapping[str, Any],
    ) -> bool:
        """Guarda un evento de progreso del job activo."""

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or self._leases.get(job_id) != lease_token:
                return False
            if job.status in _TERMINAL:
                return False
            history = self._events.setdefault(job_id, [])
            history.append((len(history) + 1, name, dict(data)))
            return True

    def complete(
        self,
        job_id: str,
        lease_token: str,
        *,
        status: str,
        outcome: CaptureOutcome | None,
        error: str,
    ) -> bool:
        """Cierra el job y libera la captura global."""

        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or self._leases.get(job_id) != lease_token:
                return False
            if job.status in _TERMINAL:
                return False
            job.status = status
            job.outcome = outcome
            job.error = error
            job.finished_at = time.monotonic()
            history = self._events.setdefault(job_id, [])
            name, data = self._final_payload(job)
            history.append((len(history) + 1, name, data))
            self._leases.pop(job_id, None)
            if self._active == job_id:
                self._active = None
            return True

    def get(self, job_id: str) -> CaptureJob | None:
        """Devuelve el job o ``None`` si no existe o expiró."""

        with self._lock:
            return self._jobs.get(job_id)

    def events(self, job_id: str, after: int = 0) -> list[tuple[int, str, dict[str, Any]]]:
        """Eventos con secuencia mayor que ``after``."""

        with self._lock:
            return [item for item in self._events.get(job_id, []) if item[0] > after]

    def recover_interrupted(self) -> list[CaptureJob]:
        """Marca como interrumpidos los jobs que quedaron activos en memoria."""

        marcados: list[CaptureJob] = []
        with self._lock:
            for job in self._jobs.values():
                if job.status in {"pending", "running"}:
                    job.status = "interrupted"
                    job.error = _INTERRUPTED_MESSAGE
                    job.finished_at = time.monotonic()
                    history = self._events.setdefault(job.id, [])
                    history.append((len(history) + 1, "error", {"message": _INTERRUPTED_MESSAGE}))
                    marcados.append(job)
            self._active = None
        return marcados

    def shutdown(self) -> None:
        """No hay recursos externos que liberar en memoria."""

    def _final_payload(self, job: CaptureJob) -> tuple[str, dict[str, Any]]:
        """Nombre y datos del evento final, iguales en todos los backends."""

        if job.status == "done" and job.outcome is not None:
            return "done", asdict(job.outcome)
        if job.status == "interrupted":
            return "error", {"message": _INTERRUPTED_MESSAGE}
        return "error", {"message": _ERROR_MESSAGE}

    def _check_rate_locked(self, owner_hash: str) -> None:
        """Anota la captura o lanza ``CaptureRateLimited`` si no queda cupo."""

        if self._rate_limit <= 0:
            return
        now = time.monotonic()
        recent = [
            stamp
            for stamp in self._recent.get(owner_hash, [])
            if now - stamp < self._window_seconds
        ]
        if len(recent) >= self._rate_limit:
            retry_after = max(1, round(self._window_seconds - (now - recent[0])))
            raise CaptureRateLimited(retry_after)
        recent.append(now)
        self._recent[owner_hash] = recent

    def _prune_locked(self) -> None:
        """Descarta jobs y marcas de cupo viejas."""

        ttl = self._job_ttl if self._job_ttl is not None else _JOB_TTL_SECONDS
        now = time.monotonic()
        for job_id, job in list(self._jobs.items()):
            if job.finished_at is not None and now - job.finished_at > ttl:
                self._jobs.pop(job_id, None)
                self._events.pop(job_id, None)
                self._leases.pop(job_id, None)
        for owner_hash, stamps in list(self._recent.items()):
            recent = [stamp for stamp in stamps if now - stamp < self._window_seconds]
            if recent:
                self._recent[owner_hash] = recent
            else:
                del self._recent[owner_hash]
        por_encima = len(self._jobs) - self._max_jobs
        if por_encima <= 0:
            return
        terminados = sorted(
            (job for job in self._jobs.values() if job.status in _TERMINAL),
            key=lambda job: job.created_at,
        )
        for job in terminados[:por_encima]:
            self._jobs.pop(job.id, None)
            self._events.pop(job.id, None)
            self._leases.pop(job.id, None)


_metadata = MetaData()

capture_jobs_table = Table(
    "capture_jobs",
    _metadata,
    Column("id", Text, primary_key=True),
    Column("status", Text, nullable=False),
    Column("request", JSONB, nullable=False),
    Column("outcome", JSONB, nullable=True),
    Column("error", Text, nullable=False),
    Column("worker", Text, nullable=False),
    Column("lease_token", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    CheckConstraint(
        "status IN ('pending', 'running', 'done', 'error', 'interrupted')",
        name="ck_capture_jobs_status",
    ),
)

capture_events_table = Table(
    "capture_events",
    _metadata,
    Column("seq", BigInteger, primary_key=True, autoincrement=True),
    Column("job_id", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("data", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_capture_events_job_seq", "job_id", "seq"),
)

capture_control_table = Table(
    "capture_control",
    _metadata,
    Column("id", SmallInteger, primary_key=True),
    Column("job_id", Text, nullable=True),
    Column("worker", Text, nullable=False),
    Column("heartbeat_at", DateTime(timezone=True), nullable=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("id = 1", name="ck_capture_control_single_row"),
)

capture_rate_limit_table = Table(
    "capture_rate_limit",
    _metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("owner_hash", Text, nullable=False),
    Column("job_id", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index("ix_capture_rate_limit_owner_created", "owner_hash", "created_at"),
)


def _job_from_mapping(row: Mapping[str, Any]) -> CaptureJob:
    """Reconstruye el job a partir de una fila de ``capture_jobs``."""

    created = row["created_at"]
    finished = row["finished_at"]
    return CaptureJob(
        id=str(row["id"]),
        request=_request_from_json(row["request"]),
        status=str(row["status"]),
        outcome=_outcome_from_json(row["outcome"]),
        error=str(row["error"] or ""),
        created_at=created.timestamp() if created is not None else time.time(),
        finished_at=finished.timestamp() if finished is not None else None,
    )


def _recover_from_gold() -> None:
    """Punto seguro: sincroniza la capa core completa (sin poda) con PostgreSQL y Qdrant.

    ``sync_corpus`` documenta ``only=None`` como convergencia completa; se pasa
    así a propósito porque un documento pudo escribirse en Gold sin que llegara
    su evento de progreso.
    """

    from apps.api.ingestion import sync_gold
    from carvajal.infrastructure.settings import Settings

    settings = Settings()
    report = sync_gold(settings.gold_dir, cast(set[str], None))
    logger.info(
        "capture.recovery_synced",
        new=report.new,
        updated=report.updated,
        indexed=report.indexed,
        errors=len(report.errors),
    )


class PostgresCaptureBackend:
    """Estado de la captura en el PostgreSQL existente, compartido por los workers.

    La exclusión global y el arranque de una captura usan dos transacciones
    cortas con un marcador de recuperación en medio: la recuperación desde Gold
    se completa antes de aceptar la captura siguiente.
    """

    def __init__(
        self,
        factory: Callable[[], sessionmaker[Session]],
        *,
        rate_limit: int = 5,
        window_seconds: int = 3600,
        lease_seconds: float = _LEASE_SECONDS,
        max_jobs: int = _MAX_JOBS,
        job_ttl_seconds: float | None = None,
        recover: Callable[[], None] | None = None,
        worker: str | None = None,
    ) -> None:
        # La fábrica se resuelve en cada uso: importar el módulo o construir la
        # app no abre el motor (``/live`` no depende de la base).
        self._factory_source = factory
        self._rate_limit = max(0, rate_limit)
        self._window_seconds = max(1, window_seconds)
        self._lease_seconds = max(0.01, lease_seconds)
        self._max_jobs = max(1, max_jobs)
        self._job_ttl = job_ttl_seconds
        self._recover = recover or _recover_from_gold
        self._worker = worker or _worker_id()

    # -- arranque ---------------------------------------------------------

    def acquire(
        self,
        request: CaptureRequest,
        owner_hash: str,
        *,
        job_id: str,
        lease_token: str,
        worker: str,
    ) -> StartResult:
        """Reserva la captura global, recupera lo interrumpido y crea el job."""

        recovered: CaptureJob | None = None
        marker: tuple[str | None, str] | None = None
        with self._sessionmaker().begin() as session:
            control = self._lock_control(session)
            now = datetime.now(UTC)
            active = self._job_row(session, control["job_id"]) if control["job_id"] else None
            # Un latido fresco significa captura en curso o recuperación de otro
            # worker: nunca se le roba el control ni se arranca otra captura.
            if _fresh(control["heartbeat_at"], self._lease_seconds, now):
                raise CaptureBusy(_BUSY_MESSAGE)
            if active is not None:
                recovered = self._mark_interrupted(session, active.id)
            marker = (active.id if active is not None else None, worker)
            self._hold_control(session, marker_job=marker[0], worker=worker, now=now)

        try:
            if active is not None:
                self._recover_safely()
            limited = self._finish_start(
                request,
                owner_hash,
                job_id=job_id,
                lease_token=lease_token,
                worker=worker,
                marker=marker,
            )
        except BaseException:
            self._release_marker(marker)
            raise
        if limited is not None:
            raise CaptureRateLimited(limited)
        self._prune()
        logger.info("capture.backend_started", job_id=job_id, worker=worker)
        return StartResult(
            CaptureJob(id=job_id, request=request, status="pending"),
            recovered,
        )

    def _finish_start(
        self,
        request: CaptureRequest,
        owner_hash: str,
        *,
        job_id: str,
        lease_token: str,
        worker: str,
        marker: tuple[str | None, str],
    ) -> int | None:
        """Cierra el arranque: cupo, job nuevo y control, o ``Retry-After``."""

        with self._sessionmaker().begin() as session:
            control = self._lock_control(session)
            if (control["job_id"], control["worker"]) != (marker[0], marker[1]):
                raise CaptureBusy(_BUSY_MESSAGE)
            retry_after = self._rate_retry_after(session, owner_hash)
            if retry_after is not None:
                self._clear_control(session)
                return retry_after
            now = datetime.now(UTC)
            session.execute(
                insert(capture_jobs_table).values(
                    id=job_id,
                    status="pending",
                    request=_request_to_json(request),
                    outcome=None,
                    error="",
                    worker=worker,
                    lease_token=lease_token,
                    created_at=now,
                    updated_at=now,
                    started_at=None,
                    finished_at=None,
                )
            )
            if self._rate_limit > 0:
                session.execute(
                    insert(capture_rate_limit_table).values(
                        owner_hash=owner_hash,
                        job_id=job_id,
                        created_at=now,
                    )
                )
            session.execute(
                update(capture_control_table)
                .where(capture_control_table.c.id == 1)
                .values(job_id=job_id, worker=worker, heartbeat_at=now, updated_at=now)
            )
        return None

    # -- ciclo de vida ----------------------------------------------------

    def mark_running(self, job_id: str, lease_token: str) -> None:
        """Marca el job como en ejecución."""

        now = datetime.now(UTC)
        with self._sessionmaker().begin() as session:
            session.execute(
                update(capture_jobs_table)
                .where(
                    *self._lease_guard(job_id, lease_token),
                    capture_jobs_table.c.status == "pending",
                )
                .values(status="running", started_at=now, updated_at=now)
            )

    def heartbeat(self, job_id: str, lease_token: str) -> bool:
        """Renueva el latido del job y de la captura global."""

        now = datetime.now(UTC)
        with self._sessionmaker().begin() as session:
            result = cast(
                Any,
                session.execute(
                    update(capture_jobs_table)
                    .where(
                        *self._lease_guard(job_id, lease_token),
                        capture_jobs_table.c.status == "running",
                    )
                    .values(updated_at=now)
                ),
            )
            if result.rowcount == 0:
                return False
            session.execute(
                update(capture_control_table)
                .where(capture_control_table.c.id == 1, capture_control_table.c.job_id == job_id)
                .values(heartbeat_at=now, updated_at=now)
            )
        return True

    def record_event(
        self,
        job_id: str,
        lease_token: str,
        name: str,
        data: Mapping[str, Any],
    ) -> bool:
        """Persiste un evento de progreso si el worker conserva el testigo."""

        now = datetime.now(UTC)
        with self._sessionmaker().begin() as session:
            result = cast(
                Any,
                session.execute(
                    update(capture_jobs_table)
                    .where(
                        *self._lease_guard(job_id, lease_token),
                        capture_jobs_table.c.status == "running",
                    )
                    .values(updated_at=now)
                ),
            )
            if result.rowcount == 0:
                return False
            session.execute(
                insert(capture_events_table).values(
                    job_id=job_id,
                    name=name,
                    data=dict(data),
                    created_at=now,
                )
            )
        return True

    def complete(
        self,
        job_id: str,
        lease_token: str,
        *,
        status: str,
        outcome: CaptureOutcome | None,
        error: str,
    ) -> bool:
        """Cierra el job, guarda su evento final y libera la captura global."""

        now = datetime.now(UTC)
        name, data = self._final_payload(status, outcome)
        with self._sessionmaker().begin() as session:
            result = cast(
                Any,
                session.execute(
                    update(capture_jobs_table)
                    .where(
                        *self._lease_guard(job_id, lease_token),
                        capture_jobs_table.c.status.in_(("pending", "running")),
                    )
                    .values(
                        status=status,
                        outcome=asdict(outcome) if outcome is not None else None,
                        error=error,
                        lease_token=None,
                        finished_at=now,
                        updated_at=now,
                    )
                ),
            )
            if result.rowcount == 0:
                return False
            session.execute(
                insert(capture_events_table).values(
                    job_id=job_id,
                    name=name,
                    data=data,
                    created_at=now,
                )
            )
            session.execute(
                update(capture_control_table)
                .where(capture_control_table.c.id == 1, capture_control_table.c.job_id == job_id)
                .values(job_id=None, worker="", heartbeat_at=None, updated_at=now)
            )
        return True

    def get(self, job_id: str) -> CaptureJob | None:
        """Devuelve el job o ``None`` si no existe o expiró."""

        with self._sessionmaker()() as session:
            row = self._job_row(session, job_id)
        return row

    def events(self, job_id: str, after: int = 0) -> list[tuple[int, str, dict[str, Any]]]:
        """Eventos del job con secuencia mayor que ``after``, en orden."""

        with self._sessionmaker()() as session:
            rows = session.execute(
                select(
                    capture_events_table.c.seq,
                    capture_events_table.c.name,
                    capture_events_table.c.data,
                )
                .where(
                    capture_events_table.c.job_id == job_id,
                    capture_events_table.c.seq > after,
                )
                .order_by(capture_events_table.c.seq)
            ).all()
        return [(int(seq), str(name), dict(data)) for seq, name, data in rows]

    def recover_interrupted(self) -> list[CaptureJob]:
        """Detecta jobs con latido caducado, los marca y recupera Gold.

        La transición condicional garantiza que un solo worker registra cada
        interrupción; el marcador del control con latido fresco impide que otro
        arranque acepte una captura mientras se ejecuta la sincronización.
        """

        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=self._lease_seconds)
        recovered: list[CaptureJob] = []
        marker: tuple[str | None, str] | None = None
        with self._sessionmaker().begin() as session:
            control = self._lock_control(session)
            active = self._job_row(session, control["job_id"]) if control["job_id"] else None
            fresh = _fresh(control["heartbeat_at"], self._lease_seconds, now)
            needs_recovery = False
            if active is not None:
                if active.status in {"pending", "running"}:
                    if not fresh:
                        marked = self._mark_interrupted(session, active.id)
                        if marked is not None:
                            recovered.append(marked)
                            needs_recovery = True
                elif not fresh:
                    # La captura cerró pero el proceso murió antes de liberar el control:
                    # igual hay que converger Gold antes de aceptar otro arranque.
                    needs_recovery = True

            huerfanos = (
                session.execute(
                    update(capture_jobs_table)
                    .where(
                        capture_jobs_table.c.status.in_(("pending", "running")),
                        capture_jobs_table.c.updated_at < cutoff,
                    )
                    .values(
                        status="interrupted",
                        error=_INTERRUPTED_MESSAGE,
                        lease_token=None,
                        finished_at=now,
                        updated_at=now,
                    )
                    .returning(*capture_jobs_table.c)
                )
                .mappings()
                .all()
            )
            recovered.extend(_job_from_mapping(row) for row in huerfanos)
            needs_recovery = needs_recovery or bool(huerfanos)

            if not needs_recovery:
                return []
            marker = (control["job_id"], self._worker)
            self._hold_control(session, marker_job=marker[0], worker=self._worker, now=now)

        try:
            self._recover_safely()
            logger.info(
                "capture.recovered",
                job_ids=[job.id for job in recovered],
                worker=self._worker,
            )
        finally:
            self._release_marker(marker)
        return recovered

    def shutdown(self) -> None:
        """El motor lo cierra ``runtime``; aquí no hay recursos propios."""

    # -- utilidades internas ---------------------------------------------

    def _sessionmaker(self) -> sessionmaker[Session]:
        """Resuelve la fábrica de sesiones de forma diferida."""

        return self._factory_source()

    @staticmethod
    def _final_payload(
        status: str,
        outcome: CaptureOutcome | None,
    ) -> tuple[str, dict[str, Any]]:
        """Nombre y datos del evento final, iguales en todos los backends."""

        if status == "done" and outcome is not None:
            return "done", asdict(outcome)
        if status == "interrupted":
            return "error", {"message": _INTERRUPTED_MESSAGE}
        return "error", {"message": _ERROR_MESSAGE}

    @staticmethod
    def _lease_guard(job_id: str, lease_token: str) -> tuple[Any, Any]:
        """Condiciones para que solo el dueño del testigo escriba el job."""

        return (
            capture_jobs_table.c.id == job_id,
            capture_jobs_table.c.lease_token == lease_token,
        )

    def _lock_control(self, session: Session) -> Mapping[str, Any]:
        """Toma la fila única de control con bloqueo de fila."""

        return (
            session.execute(
                select(
                    capture_control_table.c.job_id,
                    capture_control_table.c.worker,
                    capture_control_table.c.heartbeat_at,
                )
                .where(capture_control_table.c.id == 1)
                .with_for_update()
            )
            .mappings()
            .one()
        )

    def _hold_control(
        self, session: Session, *, marker_job: str | None, worker: str, now: datetime
    ) -> None:
        """Deja el control como marcador de recuperación con latido fresco."""

        session.execute(
            update(capture_control_table)
            .where(capture_control_table.c.id == 1)
            .values(job_id=marker_job, worker=worker, heartbeat_at=now, updated_at=now)
        )

    def _clear_control(self, session: Session) -> None:
        """Libera la captura global."""

        session.execute(
            update(capture_control_table)
            .where(capture_control_table.c.id == 1)
            .values(job_id=None, worker="", heartbeat_at=None, updated_at=datetime.now(UTC))
        )

    def _release_marker(self, marker: tuple[str | None, str] | None) -> None:
        """Libera el marcador solo si sigue siendo de este worker."""

        if marker is None:
            return
        marker_job, worker = marker
        try:
            with self._sessionmaker().begin() as session:
                session.execute(
                    update(capture_control_table)
                    .where(
                        capture_control_table.c.id == 1,
                        capture_control_table.c.worker == worker,
                        capture_control_table.c.job_id.is_not_distinct_from(marker_job),
                    )
                    .values(job_id=None, worker="", heartbeat_at=None, updated_at=datetime.now(UTC))
                )
        except Exception:  # el apagado no debe fallar por no poder limpiar el marcador
            logger.warning("capture.marker_release_failed", worker=worker)

    def _mark_interrupted(self, session: Session, job_id: str) -> CaptureJob | None:
        """Transición condicional a interrumpido; solo un worker la gana."""

        now = datetime.now(UTC)
        row = (
            session.execute(
                update(capture_jobs_table)
                .where(
                    capture_jobs_table.c.id == job_id,
                    capture_jobs_table.c.status.in_(("pending", "running")),
                )
                .values(
                    status="interrupted",
                    error=_INTERRUPTED_MESSAGE,
                    lease_token=None,
                    finished_at=now,
                    updated_at=now,
                )
                .returning(*capture_jobs_table.c)
            )
            .mappings()
            .first()
        )
        return _job_from_mapping(row) if row is not None else None

    def _job_row(self, session: Session, job_id: str | None) -> CaptureJob | None:
        """Lee un job por identificador."""

        if not job_id:
            return None
        row = (
            session.execute(select(*capture_jobs_table.c).where(capture_jobs_table.c.id == job_id))
            .mappings()
            .first()
        )
        return _job_from_mapping(row) if row is not None else None

    def _rate_retry_after(self, session: Session, owner_hash: str) -> int | None:
        """Devuelve ``Retry-After`` si el dueño agotó su ventana, o ``None``."""

        if self._rate_limit <= 0:
            return None
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=self._window_seconds)
        count, oldest = session.execute(
            select(
                func.count(),
                func.min(capture_rate_limit_table.c.created_at),
            ).where(
                capture_rate_limit_table.c.owner_hash == owner_hash,
                capture_rate_limit_table.c.created_at > cutoff,
            )
        ).one()
        if int(count) < self._rate_limit:
            return None
        if oldest is None:
            return max(1, round(self._window_seconds))
        restante = (oldest + timedelta(seconds=self._window_seconds) - now).total_seconds()
        return max(1, round(restante))

    def _prune(self) -> None:
        """Limpieza oportunista y acotada de jobs, eventos y cupos."""

        ttl = self._job_ttl if self._job_ttl is not None else _JOB_TTL_SECONDS
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=ttl)
        rate_cutoff = now - timedelta(seconds=self._window_seconds)
        try:
            with self._sessionmaker().begin() as session:
                session.execute(
                    delete(capture_rate_limit_table).where(
                        capture_rate_limit_table.c.created_at < rate_cutoff
                    )
                )
                viejos = (
                    select(capture_jobs_table.c.id)
                    .where(
                        capture_jobs_table.c.status.in_(tuple(_TERMINAL)),
                        func.coalesce(
                            capture_jobs_table.c.finished_at,
                            capture_jobs_table.c.updated_at,
                        )
                        < cutoff,
                    )
                    .limit(_PRUNE_BATCH)
                )
                session.execute(
                    delete(capture_jobs_table).where(capture_jobs_table.c.id.in_(viejos))
                )
                sobrantes = (
                    select(capture_jobs_table.c.id)
                    .where(capture_jobs_table.c.status.in_(tuple(_TERMINAL)))
                    .order_by(capture_jobs_table.c.created_at.desc())
                    .offset(self._max_jobs)
                )
                session.execute(
                    delete(capture_jobs_table).where(capture_jobs_table.c.id.in_(sobrantes))
                )
        except Exception:  # la limpieza es oportunista; nunca tumba un arranque
            logger.warning("capture.prune_failed", worker=self._worker)

    def _recover_safely(self) -> None:
        """Recuperación best-effort: el arranque no se cae si falla la sync."""

        try:
            self._recover()
        except Exception as error:  # la siguiente captura o el arranque lo reintentan
            logger.warning("capture.recovery_failed", error=str(error), worker=self._worker)


class CaptureJobs:
    """Orquesta el runner de captura sobre un backend compartido o en memoria.

    Sin ``store`` usa el backend en memoria, que reproduce el comportamiento de
    un solo worker y mantiene las pruebas unitarias sin PostgreSQL.
    """

    def __init__(
        self,
        runner: Runner | None = None,
        *,
        max_jobs: int = _MAX_JOBS,
        rate_limit: int = 5,
        window_seconds: int = 3600,
        store: CaptureBackend | None = None,
        lease_seconds: float = _LEASE_SECONDS,
        heartbeat_seconds: float = _HEARTBEAT_SECONDS,
        rate_limit_secret: str = "",
        job_ttl_seconds: float | None = None,
    ) -> None:
        self._runner = runner or default_capture_runner
        self._lease_seconds = max(0.01, lease_seconds)
        self._heartbeat_seconds = max(0.01, heartbeat_seconds)
        self._secret = rate_limit_secret
        self._store: CaptureBackend = store or MemoryCaptureBackend(
            max_jobs=max_jobs,
            rate_limit=rate_limit,
            window_seconds=window_seconds,
            job_ttl_seconds=job_ttl_seconds,
        )
        self._active: dict[str, _ActiveCapture] = {}
        self._lock = threading.Lock()

    def start(self, request: CaptureRequest, *, owner_key: str = "") -> CaptureJob:
        """Crea el job y lo ejecuta en un hilo; falla si no hay exclusión o cupo.

        ``owner_key`` es la IP del visitante para el cupo. Se convierte en un
        resumen antes de tocar la base: la IP nunca se guarda ni se registra.
        """

        owner_hash = owner_digest(owner_key, self._secret)
        job_id = uuid.uuid4().hex
        lease_token = uuid.uuid4().hex
        result = self._store.acquire(
            request,
            owner_hash,
            job_id=job_id,
            lease_token=lease_token,
            worker=_worker_id(),
        )
        job = result.job
        if result.recovered is not None:
            logger.info("capture.recovered_job", job_id=result.recovered.id)
        logger.info(
            "capture.started",
            job_id=job.id,
            scope=request.scope,
            limit=request.limit,
            urls=len(request.urls),
        )
        capture = _ActiveCapture(job=job, lease_token=lease_token)
        with self._lock:
            self._active[job.id] = capture
        try:
            thread = threading.Thread(
                target=self._run,
                args=(capture,),
                name=f"captura-{job.id[:8]}",
                daemon=True,
            )
            thread.start()
        except Exception as error:  # el hilo no arrancó: el job no queda colgado
            self._finish_with_error(capture, error)
        return job

    def get(self, job_id: str) -> CaptureJob | None:
        """Devuelve el job o ``None`` si ya se limpió."""

        return self._store.get(job_id)

    def recover_interrupted(self) -> list[CaptureJob]:
        """Marca y recupera las capturas que quedaron interrumpidas."""

        recovered = self._store.recover_interrupted()
        for job in recovered:
            logger.info("capture.interrupted", job_id=job.id, worker=_worker_id())
        return recovered

    def shutdown(self) -> None:
        """Marca interrumpidas las capturas locales y para sus latidos."""

        with self._lock:
            activas = list(self._active.values())
        for capture in activas:
            capture.stop.set()
            try:
                self._store.complete(
                    capture.job.id,
                    capture.lease_token,
                    status="interrupted",
                    outcome=None,
                    error=_INTERRUPTED_MESSAGE,
                )
            except Exception:  # el apagado no debe fallar por la base
                logger.warning("capture.shutdown_failed", job_id=capture.job.id)
        self._store.shutdown()

    def stream(self, job_id: str, *, after: int = 0) -> Iterator[str]:
        """Emite los eventos del job hasta su cierre.

        Reproduce el progreso persistido (incluido el final) desde cualquier
        worker; ``after`` llega del ``Last-Event-ID`` del navegador.
        """

        job = self._store.get(job_id)
        if job is None:
            raise KeyError(job_id)
        seq = max(0, after)
        saw_final = False
        while True:
            for item_seq, name, data in self._store.events(job_id, seq):
                seq = item_seq
                if name in _FINAL_EVENTS:
                    saw_final = True
                yield _encode_event(item_seq, name, data)
            job = self._store.get(job_id)
            if job is None:
                return
            if job.status in _TERMINAL:
                if not saw_final:
                    yield _final_event(job)
                return
            time.sleep(_POLL_SECONDS)

    async def stream_async(
        self,
        job_id: str,
        is_disconnected: Callable[[], Awaitable[bool]] | None = None,
        *,
        after: int = 0,
    ) -> AsyncIterator[str]:
        """Emite el progreso sin retener el hilo si el cliente se desconecta.

        ``is_disconnected`` lo aporta la petición de Starlette; se consulta
        entre eventos para abortar el generador cuando el navegador cierra.
        El job de captura sigue corriendo en su hilo y termina de persistir.
        """

        job = self._store.get(job_id)
        if job is None:
            raise KeyError(job_id)
        seq = max(0, after)
        saw_final = False
        while True:
            if is_disconnected is not None and await is_disconnected():
                return
            eventos = await asyncio.to_thread(self._store.events, job_id, seq)
            for item_seq, name, data in eventos:
                seq = item_seq
                if name in _FINAL_EVENTS:
                    saw_final = True
                yield _encode_event(item_seq, name, data)
            job = await asyncio.to_thread(self._store.get, job_id)
            if job is None:
                return
            if job.status in _TERMINAL:
                if not saw_final:
                    yield _final_event(job)
                return
            await asyncio.sleep(_POLL_SECONDS)

    def _run(self, capture: _ActiveCapture) -> None:
        """Ejecuta el runner, publica cada etapa y cierra el job."""

        job = capture.job
        self._store.mark_running(job.id, capture.lease_token)
        latido = threading.Thread(
            target=self._heartbeat_loop,
            args=(capture,),
            name=f"captura-latido-{job.id[:8]}",
            daemon=True,
        )
        latido.start()

        def on_event(event: CaptureEvent) -> None:
            self._store.record_event(
                job.id,
                capture.lease_token,
                "stage",
                {"stage": event.stage, "url": event.url, "detail": event.detail},
            )

        try:
            outcome = self._runner(job.request, on_event)
        except Exception as error:
            self._finish_with_error(capture, error)
        else:
            recorded = self._store.complete(
                job.id,
                capture.lease_token,
                status="done",
                outcome=outcome,
                error="",
            )
            if not recorded:
                logger.warning("capture.lease_lost", job_id=job.id)
            logger.info(
                "capture.finished",
                job_id=job.id,
                written=outcome.written,
                indexed=outcome.indexed,
                errors=len(outcome.errors),
            )
        finally:
            capture.stop.set()
            latido.join(timeout=2)
            with self._lock:
                self._active.pop(job.id, None)

    def _heartbeat_loop(self, capture: _ActiveCapture) -> None:
        """Renueva el latido mientras el runner vive y el testigo siga vigente."""

        while not capture.stop.wait(self._heartbeat_seconds):
            try:
                if not self._store.heartbeat(capture.job.id, capture.lease_token):
                    logger.warning("capture.lease_lost", job_id=capture.job.id)
                    return
            except Exception:  # una base lenta no debe matar el latido
                logger.warning("capture.heartbeat_failed", job_id=capture.job.id)

    def _finish_with_error(self, capture: _ActiveCapture, error: Exception) -> None:
        """Marca el job como fallido sin exponer detalles internos al navegador."""

        logger.warning("capture.failed", job_id=capture.job.id, error=str(error))
        self._store.complete(
            capture.job.id,
            capture.lease_token,
            status="error",
            outcome=None,
            error=str(error),
        )
        capture.stop.set()
