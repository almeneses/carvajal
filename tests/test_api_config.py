"""Pruebas de la pestaña de configuración y la carga en segundo plano."""

import re
import threading
from collections.abc import Callable
from typing import Any, cast

from fastapi.testclient import TestClient

from apps.api.ingestion import IngestReport, UploadedDocument
from apps.api.main import create_app
from apps.api.security import CSRF_COOKIE, CSRF_FIELD
from apps.api.upload_jobs import UploadJobs

Runner = Callable[[list[UploadedDocument], Callable[[str, str, int, int], None]], IngestReport]


def _report(files: int) -> IngestReport:
    return IngestReport(
        files=files,
        bronze=files,
        silver=files,
        gold=files,
        documents=12,
        chunks=34,
        indexed=56,
    )


def _runner(
    documents: list[UploadedDocument],
    on_event: Callable[[str, str, int, int], None],
) -> IngestReport:
    on_event("Gold", documents[0].filename if documents else "", len(documents), len(documents))
    return _report(len(documents))


def make_client(runner: Runner = _runner) -> TestClient:
    """Cliente de la app con la carga real desactivada por defecto."""

    app = create_app()
    app.state.uploads = UploadJobs(runner)
    return TestClient(app)


def _token(client: TestClient) -> str:
    client.get("/configuracion")
    return client.cookies[CSRF_COOKIE]


def _job_id(html: str) -> str:
    match = re.search(r'data-carga-job-id="([a-f0-9]+)"', html)
    assert match is not None
    return match.group(1)


def _wait(client: TestClient, job_id: str) -> None:
    uploads = cast(Any, client.app).state.uploads
    job = uploads.get(job_id)
    assert job is not None
    assert job.finished.wait(timeout=5)


def test_configuracion_muestra_formulario_spinner_y_lista_de_cargas() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.get("/configuracion")

    # Assert
    assert response.status_code == 200
    assert 'enctype="multipart/form-data"' in response.text
    assert 'name="archivos"' in response.text
    assert 'name="carpeta"' in response.text
    assert "Bronze" in response.text and "Qdrant" in response.text
    assert "Configuración" in response.text
    assert 'href="/configuracion"' in response.text
    assert response.text.count('aria-current="page"') == 1
    assert 'name="token_admin"' not in response.text
    assert "Cargas recientes" in response.text
    assert 'id="carga-jobs"' in response.text
    assert "data-carga-form" in response.text
    assert "data-carga-spinner" in response.text
    assert 'hx-post="/configuracion"' in response.text


def test_subir_documento_crea_un_job_y_publica_el_resultado() -> None:
    # Arrange
    recibidos: list[UploadedDocument] = []

    def runner(
        documents: list[UploadedDocument],
        on_event: Callable[[str, str, int, int], None],
    ) -> IngestReport:
        recibidos.extend(documents)
        on_event("Gold", documents[0].filename, 1, 1)
        return _report(len(documents))

    client = make_client(runner)
    token = _token(client)

    # Act
    response = client.post(
        "/configuracion",
        data={CSRF_FIELD: token},
        files={"archivos": ("nota.md", b"# Nota\n\nTexto de prueba.", "text/markdown")},
        headers={"HX-Request": "true"},
    )

    # Assert
    assert response.status_code == 200
    assert recibidos and recibidos[0].filename == "nota.md"
    job_id = _job_id(response.text)
    _wait(client, job_id)
    resultado = client.get(
        f"/configuracion/carga/{job_id}",
        headers={"HX-Request": "true"},
    )
    assert "Corpus actualizado" in resultado.text
    assert "56" in resultado.text


def test_el_job_en_curso_se_muestra_y_el_boton_queda_ocupado() -> None:
    # Arrange
    puerta = threading.Event()

    def runner(
        documents: list[UploadedDocument],
        on_event: Callable[[str, str, int, int], None],
    ) -> IngestReport:
        on_event("Gold", "nota.md", 1, 1)
        puerta.wait(timeout=5)
        return _report(len(documents))

    client = make_client(runner)
    token = _token(client)

    # Act
    response = client.post(
        "/configuracion",
        data={CSRF_FIELD: token},
        files={"archivos": ("nota.md", b"# Nota\n\nTexto de prueba.", "text/markdown")},
        headers={"HX-Request": "true"},
    )
    job_id = _job_id(response.text)

    # Assert
    assert response.status_code == 200
    assert 'data-carga-estado="running"' in response.text
    assert "Procesando" in response.text
    puerta.set()
    _wait(client, job_id)


def test_subir_sin_archivos_devuelve_422() -> None:
    # Arrange
    client = make_client()
    token = _token(client)

    # Act
    response = client.post("/configuracion", data={CSRF_FIELD: token})

    # Assert
    assert response.status_code == 422
    assert "Selecciona al menos un documento" in response.text


def test_subir_sin_csrf_devuelve_403() -> None:
    # Arrange
    client = make_client()

    # Act
    response = client.post(
        "/configuracion",
        data={CSRF_FIELD: "ajeno"},
        files={"archivos": ("nota.md", b"# Nota", "text/markdown")},
    )

    # Assert
    assert response.status_code == 403
    assert "La sesión del formulario expiró" in response.text


def test_subir_desde_una_ip_remota_no_exige_token() -> None:
    # Arrange
    app = create_app()
    app.state.uploads = UploadJobs(_runner)
    client = TestClient(app, client=("203.0.113.10", 12345))
    token = _token(client)

    # Act
    response = client.post(
        "/configuracion",
        data={CSRF_FIELD: token},
        files={"archivos": ("nota.md", b"# Nota\n\nTexto.", "text/markdown")},
        headers={"HX-Request": "true"},
    )
    job_id = _job_id(response.text)
    _wait(client, job_id)
    resultado = client.get(
        f"/configuracion/carga/{job_id}",
        headers={"HX-Request": "true"},
    )

    # Assert
    assert response.status_code == 200
    assert "Corpus actualizado" in resultado.text


def test_la_lista_lateral_acumula_los_jobs() -> None:
    # Arrange
    client = make_client()
    token = _token(client)
    ids: list[str] = []
    for nombre in ("uno.md", "dos.md"):
        response = client.post(
            "/configuracion",
            data={CSRF_FIELD: token},
            files={"archivos": (nombre, b"# Nota\n\nTexto.", "text/markdown")},
            headers={"HX-Request": "true"},
        )
        job_id = _job_id(response.text)
        _wait(client, job_id)
        ids.append(job_id)

    # Act
    page = client.get("/configuracion")

    # Assert
    assert all(job_id in page.text for job_id in ids)
    assert page.text.count("Carga terminada") >= 2


def test_el_endpoint_json_expone_el_estado_del_job() -> None:
    # Arrange
    client = make_client()
    token = _token(client)
    response = client.post(
        "/configuracion",
        data={CSRF_FIELD: token},
        files={"archivos": ("nota.md", b"# Nota\n\nTexto.", "text/markdown")},
        headers={"HX-Request": "true"},
    )
    job_id = _job_id(response.text)
    _wait(client, job_id)

    # Act
    estado = client.get(f"/configuracion/carga/{job_id}/estado")

    # Assert
    assert estado.status_code == 200
    assert estado.json()["status"] == "done"
    assert estado.json()["report"]["indexed"] == 56
