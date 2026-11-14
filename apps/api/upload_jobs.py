"""Jobs de carga de archivos con progreso consultable por la interfaz.

``ingest_uploads`` puede tardar por la importación a PostgreSQL y la
indexación en Qdrant. La petición solo lee los archivos y crea el job; el
trabajo corre en un hilo y la página consulta su estado sin bloquearse.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

from apps.api.ingestion import IngestReport, UploadedDocument, ingest_uploads

__all__ = [
    "UploadBusy",
    "UploadJob",
    "UploadJobs",
    "default_upload_runner",
]

logger = structlog.get_logger(__name__)

EventHook = Callable[[str, str, int, int], None]
Runner = Callable[[list[UploadedDocument], EventHook], IngestReport]

_JOB_TTL_SECONDS = 60 * 60


class UploadBusy(RuntimeError):
    """Ya hay una carga en curso; dos ingestas a la vez saturarían el worker."""


@dataclass
class UploadJob:
    """Estado de una carga y el reporte que produce."""

    id: str
    total: int
    status: str = "pending"
    stage: str = "En cola"
    detail: str = ""
    current: int = 0
    report: IngestReport | None = None
    error: str = ""
    notes: tuple[str, ...] = ()
    created_at: float = field(default_factory=time.monotonic)
    created_wall: float = field(default_factory=time.time)
    finished_at: float | None = None
    finished: threading.Event = field(default_factory=threading.Event, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    documents: list[UploadedDocument] = field(default_factory=list, repr=False)

    def snapshot(self) -> dict[str, Any]:
        """Vista serializable y sin ``documents`` para la plantilla o la API."""

        with self.lock:
            report = self.report
            ok = bool(report is not None and report.ok and not self.notes)
            if self.status == "pending":
                label, css = "En cola", "etiqueta"
            elif self.status == "running":
                label, css = "Procesando", "etiqueta"
            elif self.status == "done":
                label = "Completado" if ok else "Con incidencias"
                css = "etiqueta-oficial" if ok else "etiqueta-externa"
            else:
                label, css = "Error", "etiqueta-externa"
            percent = 0 if self.total <= 0 else min(100, round(self.current / self.total * 100))
            return {
                "id": self.id,
                "status": self.status,
                "stage": self.stage,
                "detail": self.detail,
                "current": self.current,
                "total": self.total,
                "percent": percent,
                "error": self.error,
                "notes": list(self.notes),
                "ok": ok,
                "status_label": label,
                "status_class": css,
                "documents": report.documents if report is not None else 0,
                "chunks": report.chunks if report is not None else 0,
                "indexed": report.indexed if report is not None else 0,
                "gold": report.gold if report is not None else 0,
            }


def default_upload_runner(
    documents: list[UploadedDocument],
    on_event: EventHook,
) -> IngestReport:
    """Runner real: Bronze → Silver → Gold → PostgreSQL → Qdrant."""

    return ingest_uploads(documents, on_event=on_event)


class UploadJobs:
    """Registro en memoria con un solo job activo a la vez."""

    def __init__(self, runner: Runner | None = None, *, max_jobs: int = 20) -> None:
        self._runner = runner or default_upload_runner
        self._jobs: dict[str, UploadJob] = {}
        self._lock = threading.Lock()
        self._active: str | None = None
        self._max_jobs = max_jobs

    def start(
        self,
        documents: list[UploadedDocument],
        *,
        notes: Sequence[str] = (),
    ) -> UploadJob:
        """Crea el job y lo ejecuta en un hilo; falla si ya hay uno activo."""

        with self._lock:
            self._prune()
            if self._active is not None:
                active = self._jobs.get(self._active)
                if active is not None and active.status in {"pending", "running"}:
                    raise UploadBusy("Ya hay una carga en curso. Espera a que termine.")
            job = UploadJob(
                id=uuid.uuid4().hex,
                total=len(documents),
                notes=tuple(notes),
                documents=list(documents),
            )
            self._jobs[job.id] = job
            self._active = job.id

        logger.info(
            "upload.job_started",
            job_id=job.id,
            files=job.total,
            notes=len(job.notes),
        )
        try:
            thread = threading.Thread(
                target=self._run,
                args=(job,),
                name=f"carga-{job.id[:8]}",
                daemon=True,
            )
            thread.start()
        except Exception as error:  # el hilo no arrancó: el job no queda colgado
            self._finish_with_error(job, error)
            job.finished.set()
        return job

    def get(self, job_id: str) -> UploadJob | None:
        """Devuelve el job o ``None`` si ya se limpió."""

        with self._lock:
            return self._jobs.get(job_id)

    def recent(self) -> list[dict[str, Any]]:
        """Lista los jobs más recientes primero, como instantáneas."""

        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)
        return [job.snapshot() for job in jobs]

    def _run(self, job: UploadJob) -> None:
        """Ejecuta la ingesta y publica el progreso en el job."""

        with job.lock:
            job.status = "running"
            job.stage = "Preparando"

        def on_event(stage: str, detail: str, current: int, total: int) -> None:
            with job.lock:
                job.stage = stage or job.stage
                job.detail = detail
                job.current = current
                job.total = total or job.total

        try:
            report = self._runner(job.documents, on_event)
            with job.lock:
                job.report = report
                job.status = "done"
                job.stage = "Listo"
                job.detail = ""
            logger.info(
                "upload.job_finished",
                job_id=job.id,
                documents=report.documents,
                indexed=report.indexed,
                errors=len(report.errors),
            )
        except Exception as error:
            self._finish_with_error(job, error)
        finally:
            with job.lock:
                job.finished_at = time.monotonic()
                job.documents = []
            job.finished.set()
            with self._lock:
                if self._active == job.id:
                    self._active = None

    def _finish_with_error(self, job: UploadJob, error: Exception) -> None:
        """Marca el job como fallido sin exponer el detalle interno al navegador."""

        with job.lock:
            job.status = "error"
            job.stage = "Error"
            job.error = "No se pudo procesar la carga; revisa los logs del servidor."
            job.finished_at = time.monotonic()
            job.documents = []
        logger.warning("upload.job_failed", job_id=job.id, error=str(error))
        job.finished.set()
        with self._lock:
            if self._active == job.id:
                self._active = None

    def _prune(self) -> None:
        """Descarta jobs viejos para que el registro no crezca sin límite."""

        now = time.monotonic()
        for job_id, job in list(self._jobs.items()):
            if job.finished_at is not None and now - job.finished_at > _JOB_TTL_SECONDS:
                del self._jobs[job_id]
        if len(self._jobs) <= self._max_jobs:
            return
        for job_id, job in sorted(self._jobs.items(), key=lambda item: item[1].created_at):
            if job.finished_at is None:
                continue
            del self._jobs[job_id]
            if len(self._jobs) <= self._max_jobs:
                break
