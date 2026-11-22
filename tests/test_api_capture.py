"""Pruebas de la captura web en la pestaña Configuración."""

import threading
from collections.abc import Callable

from fastapi.testclient import TestClient

from apps.api.capture_jobs import CaptureJobs
from apps.api.ingestion import IngestReport
from apps.api.main import create_app
from apps.api.security import CSRF_COOKIE, CSRF_FIELD
from apps.api.web_capture import CaptureOutcome, CaptureRequest
from carvajal.infrastructure.settings import Settings
from carvajal.pipeline import CaptureEvent

Runner = Callable[[CaptureRequest, Callable[[CaptureEvent], None]], CaptureOutcome]

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


def _client(
    runner: Runner,
    *,
    rate_limit: int = 5,
    window_seconds: int = 3600,
) -> TestClient:
    app = create_app()
    app.state.captures = CaptureJobs(
        runner,
        rate_limit=rate_limit,
        window_seconds=window_seconds,
    )
    app.state.ingest = lambda documents: IngestReport(files=len(documents))
    return TestClient(app)


def _token(client: TestClient) -> str:
    client.get("/configuracion")
    return client.cookies[CSRF_COOKIE]


def _job_id(html: str) -> str:
    marker = 'data-captura-job="'
    start = html.index(marker) + len(marker)
    return html[start:].split('"', 1)[0]


def _runner_ok(
    request: CaptureRequest,
    on_event: Callable[[CaptureEvent], None],
) -> CaptureOutcome:
    on_event(CaptureEvent(stage="descubrir", detail="1 semilla"))
    return OUTCOME


def test_la_pagina_muestra_el_formulario_de_captura() -> None:
    # Arrange
    client = _client(_runner_ok)

    # Act
    response = client.get("/configuracion")

    # Assert
    assert response.status_code == 200
    assert 'name="urls"' in response.text
    assert 'name="scope"' in response.text
    assert 'name="limit"' in response.text
    assert 'name="respect_robots"' in response.text
    assert f'max="{Settings().capture_max_urls}"' in response.text
    assert "Trae páginas nuevas al corpus" in response.text


def test_la_captura_es_publica_y_emite_progreso_por_sse() -> None:
    # Arrange
    client = _client(_runner_ok)
    token = _token(client)

    # Act
    response = client.post(
        "/configuracion/captura",
        data={
            CSRF_FIELD: token,
            "urls": "http://8.8.8.8/",
            "scope": "manual",
            "limit": "5",
            "respect_robots": "on",
        },
        headers={"HX-Request": "true"},
    )

    # Assert
    assert response.status_code == 200
    job_id = _job_id(response.text)
    stream = client.get(f"/configuracion/captura/{job_id}/eventos")
    assert "event: stage" in stream.text
    assert "event: done" in stream.text
    assert "home" in stream.text
    estado = client.get(f"/configuracion/captura/{job_id}")
    assert estado.status_code == 200
    assert estado.json()["status"] == "done"
    assert estado.json()["outcome"]["written"] == 1


def test_sin_javascript_la_captura_devuelve_la_pagina_completa() -> None:
    # Arrange
    client = _client(_runner_ok)
    token = _token(client)

    # Act
    response = client.post(
        "/configuracion/captura",
        data={
            CSRF_FIELD: token,
            "urls": "http://8.8.8.8/",
            "scope": "manual",
            "limit": "5",
            "respect_robots": "on",
        },
    )

    # Assert
    assert response.status_code == 200
    assert "Configuración" in response.text
    job_id = _job_id(response.text)
    _stream = client.get(f"/configuracion/captura/{job_id}/eventos").text


def test_la_captura_exige_csrf() -> None:
    # Arrange
    client = _client(_runner_ok)

    # Act
    response = client.post(
        "/configuracion/captura",
        data={CSRF_FIELD: "ajeno", "urls": "http://8.8.8.8/"},
        headers={"HX-Request": "true"},
    )

    # Assert
    assert response.status_code == 403
    assert "La sesión del formulario expiró" in response.text


def test_una_url_privada_devuelve_422_sin_lanzar_el_job() -> None:
    # Arrange
    client = _client(_runner_ok)
    token = _token(client)

    # Act
    response = client.post(
        "/configuracion/captura",
        data={
            CSRF_FIELD: token,
            "urls": "http://127.0.0.1/",
            "scope": "manual",
            "limit": "5",
        },
        headers={"HX-Request": "true"},
    )

    # Assert
    assert response.status_code == 422
    assert "no pública" in response.text


def test_el_alcance_manual_exige_al_menos_una_url() -> None:
    # Arrange
    client = _client(_runner_ok)
    token = _token(client)

    # Act
    response = client.post(
        "/configuracion/captura",
        data={CSRF_FIELD: token, "urls": "", "scope": "manual", "limit": "5"},
        headers={"HX-Request": "true"},
    )

    # Assert
    assert response.status_code == 422
    assert "Escribe al menos una URL" in response.text


def test_no_permite_mas_urls_que_el_maximo_de_la_ejecucion() -> None:
    # Arrange
    client = _client(_runner_ok)
    token = _token(client)

    # Act
    response = client.post(
        "/configuracion/captura",
        data={
            CSRF_FIELD: token,
            "urls": "http://8.8.8.8/\nhttp://1.1.1.1/",
            "scope": "manual",
            "limit": "1",
        },
        headers={"HX-Request": "true"},
    )

    # Assert
    assert response.status_code == 422
    assert "máximo de esta ejecución" in response.text


def test_un_segundo_envio_mientras_corre_devuelve_409() -> None:
    # Arrange
    puerta = threading.Event()

    def runner(
        request: CaptureRequest,
        on_event: Callable[[CaptureEvent], None],
    ) -> CaptureOutcome:
        puerta.wait(timeout=5)
        return OUTCOME

    client = _client(runner)
    token = _token(client)
    payload = {
        CSRF_FIELD: token,
        "urls": "http://8.8.8.8/",
        "scope": "manual",
        "limit": "5",
        "respect_robots": "on",
    }

    # Act
    primero = client.post(
        "/configuracion/captura",
        data=payload,
        headers={"HX-Request": "true"},
    )
    segundo = client.post(
        "/configuracion/captura",
        data=payload,
        headers={"HX-Request": "true"},
    )
    puerta.set()
    job_id = _job_id(primero.text)
    _stream = client.get(f"/configuracion/captura/{job_id}/eventos").text

    # Assert
    assert primero.status_code == 200
    assert segundo.status_code == 409
    assert "Ya hay una captura en curso" in segundo.text


def test_un_job_inexistente_devuelve_404() -> None:
    # Arrange
    client = _client(_runner_ok)

    # Act / Assert
    assert client.get("/configuracion/captura/no-existe").status_code == 404
    assert client.get("/configuracion/captura/no-existe/eventos").status_code == 404


def test_el_limite_por_ip_devuelve_429_con_retry_after() -> None:
    # Arrange
    client = _client(_runner_ok, rate_limit=1, window_seconds=60)
    token = _token(client)
    payload = {
        CSRF_FIELD: token,
        "urls": "http://8.8.8.8/",
        "scope": "manual",
        "limit": "5",
        "respect_robots": "on",
    }

    # Act
    primero = client.post(
        "/configuracion/captura",
        data=payload,
        headers={"HX-Request": "true"},
    )
    job_id = _job_id(primero.text)
    _stream = client.get(f"/configuracion/captura/{job_id}/eventos").text
    segundo = client.post(
        "/configuracion/captura",
        data=payload,
        headers={"HX-Request": "true"},
    )

    # Assert
    assert primero.status_code == 200
    assert segundo.status_code == 429
    assert "límite de capturas" in segundo.text
    assert int(segundo.headers["Retry-After"]) >= 1
