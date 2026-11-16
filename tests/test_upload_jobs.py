"""Pruebas del job de carga de archivos y su registro."""

import threading
import time
from collections.abc import Callable

import pytest

from apps.api.ingestion import IngestReport, UploadedDocument
from apps.api.upload_jobs import UploadBusy, UploadJobs

REPORT = IngestReport(
    files=1,
    bronze=1,
    silver=1,
    gold=1,
    documents=1,
    chunks=2,
    indexed=2,
)


def _document() -> UploadedDocument:
    return UploadedDocument(filename="nota.md", data=b"# Nota\n\nTexto.")


def test_el_job_procesa_y_publica_el_reporte() -> None:
    # Arrange
    def runner(
        documents: list[UploadedDocument],
        on_event: Callable[[str, str, int, int], None],
    ) -> IngestReport:
        on_event("Gold", "nota.md", 1, 1)
        return REPORT

    jobs = UploadJobs(runner)

    # Act
    job = jobs.start([_document()])

    # Assert
    assert job.finished.wait(timeout=5)
    snapshot = job.snapshot()
    assert snapshot["status"] == "done"
    assert snapshot["ok"] is True
    assert snapshot["stage"] == "Listo"
    assert snapshot["documents"] == 1
    assert job.report == REPORT
    assert job.documents == []


def test_un_segundo_job_mientras_corre_devuelve_upload_busy() -> None:
    # Arrange
    puerta = threading.Event()

    def runner(
        documents: list[UploadedDocument],
        on_event: Callable[[str, str, int, int], None],
    ) -> IngestReport:
        puerta.wait(timeout=5)
        return REPORT

    jobs = UploadJobs(runner)
    job = jobs.start([_document()])

    # Act / Assert
    with pytest.raises(UploadBusy):
        jobs.start([_document()])

    puerta.set()
    assert job.finished.wait(timeout=5)


def test_el_fallo_del_runner_deja_un_error_generico() -> None:
    # Arrange
    def runner(
        documents: list[UploadedDocument],
        on_event: Callable[[str, str, int, int], None],
    ) -> IngestReport:
        raise RuntimeError("detalle interno")

    jobs = UploadJobs(runner)

    # Act
    job = jobs.start([_document()])

    # Assert
    assert job.finished.wait(timeout=5)
    snapshot = job.snapshot()
    assert snapshot["status"] == "error"
    assert "detalle interno" not in snapshot["error"]
    assert "logs" in snapshot["error"]


def test_las_notas_de_la_lectura_marcan_incidencias() -> None:
    # Arrange
    def runner(
        documents: list[UploadedDocument],
        on_event: Callable[[str, str, int, int], None],
    ) -> IngestReport:
        return REPORT

    jobs = UploadJobs(runner)

    # Act
    job = jobs.start([_document()], notes=("otro.pdf: formato no admitido",))

    # Assert
    assert job.finished.wait(timeout=5)
    snapshot = job.snapshot()
    assert snapshot["ok"] is False
    assert snapshot["status_label"] == "Con incidencias"
    assert snapshot["notes"] == ["otro.pdf: formato no admitido"]


def test_recent_lista_los_jobs_mas_nuevos_primero() -> None:
    # Arrange
    def runner(
        documents: list[UploadedDocument],
        on_event: Callable[[str, str, int, int], None],
    ) -> IngestReport:
        return REPORT

    jobs = UploadJobs(runner)
    primero = jobs.start([_document()])
    assert primero.finished.wait(timeout=5)
    segundo = jobs.start([_document()])
    assert segundo.finished.wait(timeout=5)

    # Act
    recientes = jobs.recent()

    # Assert
    assert [job["id"] for job in recientes] == [segundo.id, primero.id]


def test_get_de_un_job_inexistente_es_none() -> None:
    # Arrange
    def runner(
        documents: list[UploadedDocument],
        on_event: Callable[[str, str, int, int], None],
    ) -> IngestReport:
        return REPORT

    jobs = UploadJobs(runner)

    # Act / Assert
    assert jobs.get("no-existe") is None


def test_un_runner_colgado_marca_interrumpido_sin_abrir_otra_ingesta() -> None:
    # Arrange
    puerta = threading.Event()

    def runner(
        documents: list[UploadedDocument],
        on_event: Callable[[str, str, int, int], None],
    ) -> IngestReport:
        puerta.wait(timeout=5)
        return REPORT

    jobs = UploadJobs(runner, max_running_seconds=0.05)
    primero = jobs.start([_document()])

    # Act: vence el lease; el intento de arrancar otra ingesta lo marca interrumpido
    time.sleep(0.15)
    with pytest.raises(UploadBusy):
        jobs.start([_document()])
    estado = primero.snapshot()

    # Assert: la interfaz ve el job terminado y no se arranca otra ingesta encima
    assert estado["status"] == "error"
    assert "interrumpió" in estado["error"]
    assert primero.finished.is_set()

    # Al morir el hilo, el turno se libera y la carga se puede reintentar
    puerta.set()
    limite = time.monotonic() + 5
    while True:
        try:
            segundo = jobs.start([_document()])
            break
        except UploadBusy:
            if time.monotonic() > limite:
                raise
            time.sleep(0.02)
    assert segundo.finished.wait(timeout=5)


def test_el_runner_recibe_una_lista_propia_de_documentos() -> None:
    # Arrange
    recibidos: list[list[UploadedDocument]] = []

    def runner(
        documents: list[UploadedDocument],
        on_event: Callable[[str, str, int, int], None],
    ) -> IngestReport:
        recibidos.append(documents)
        return REPORT

    jobs = UploadJobs(runner)

    # Act
    job = jobs.start([_document()])

    # Assert
    assert job.finished.wait(timeout=5)
    assert len(recibidos) == 1
    assert [document.filename for document in recibidos[0]] == ["nota.md"]
    assert job.documents == []
