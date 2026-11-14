"""Pruebas del registro de jobs y del stream de progreso de la captura."""

import threading
from collections.abc import Callable

import pytest

from apps.api.capture_jobs import CaptureBusy, CaptureJobs
from apps.api.web_capture import CaptureOutcome, CaptureRequest
from carvajal.pipeline import CaptureEvent

OUTCOME = CaptureOutcome(
    requested=1,
    discovered=1,
    fetched=1,
    written=1,
    documents=1,
    chunks=2,
    indexed=2,
    captured=("home",),
)


def test_start_emite_etapas_y_cierra_con_el_resultado() -> None:
    # Arrange
    def runner(
        request: CaptureRequest,
        on_event: Callable[[CaptureEvent], None],
    ) -> CaptureOutcome:
        assert request.limit == 1
        on_event(CaptureEvent(stage="descubrir", detail="1 semilla"))
        return OUTCOME

    jobs = CaptureJobs(runner)

    # Act
    job = jobs.start(CaptureRequest(limit=1))
    events = list(jobs.stream(job.id))

    # Assert
    assert any("event: stage" in event for event in events)
    assert any("event: done" in event for event in events)
    final = jobs.get(job.id)
    assert final is not None
    assert final.status == "done"
    assert final.outcome == OUTCOME


def test_una_reconexion_tardia_recibe_el_resultado_final() -> None:
    # Arrange
    def runner(
        request: CaptureRequest,
        on_event: Callable[[CaptureEvent], None],
    ) -> CaptureOutcome:
        return OUTCOME

    jobs = CaptureJobs(runner)
    job = jobs.start(CaptureRequest(limit=1))
    list(jobs.stream(job.id))

    # Act
    events = list(jobs.stream(job.id))

    # Assert
    assert len(events) == 1
    assert "event: done" in events[0]


def test_un_segundo_job_mientras_corre_devuelve_capture_busy() -> None:
    # Arrange
    puerta = threading.Event()

    def runner(
        request: CaptureRequest,
        on_event: Callable[[CaptureEvent], None],
    ) -> CaptureOutcome:
        puerta.wait(timeout=5)
        return OUTCOME

    jobs = CaptureJobs(runner)
    job = jobs.start(CaptureRequest(limit=1))

    # Act / Assert
    with pytest.raises(CaptureBusy):
        jobs.start(CaptureRequest(limit=1))

    puerta.set()
    list(jobs.stream(job.id))


def test_un_runner_que_falla_emite_error_sin_exponer_el_detalle() -> None:
    # Arrange
    def runner(
        request: CaptureRequest,
        on_event: Callable[[CaptureEvent], None],
    ) -> CaptureOutcome:
        raise RuntimeError("detalle interno")

    jobs = CaptureJobs(runner)

    # Act
    job = jobs.start(CaptureRequest(limit=1))
    events = list(jobs.stream(job.id))

    # Assert
    assert any("event: error" in event for event in events)
    assert "detalle interno" not in "".join(events)
    final = jobs.get(job.id)
    assert final is not None
    assert final.status == "error"
    assert final.error == "detalle interno"


def test_stream_de_un_job_inexistente_falla() -> None:
    # Arrange
    def runner(
        request: CaptureRequest,
        on_event: Callable[[CaptureEvent], None],
    ) -> CaptureOutcome:
        return OUTCOME

    jobs = CaptureJobs(runner)

    # Act / Assert
    with pytest.raises(KeyError):
        list(jobs.stream("no-existe"))
