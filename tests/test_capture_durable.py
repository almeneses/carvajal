"""Integración del estado duradero de la captura con PostgreSQL real.

Cada prueba usa una base efímera (testcontainers) con las migraciones aplicadas
y simula dos workers como dos motores SQLAlchemy distintos sobre la misma base.
Cubre exclusión simultánea, cupo por IP compartido, progreso servido por otra
instancia y reconexión con ``Last-Event-ID``, caída de worker y recuperación
idempotente sin duplicar documentos, y el contrato de ``/live`` y ``/health``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, inspect, select, text
from sqlalchemy.orm import Session

from apps.api.capture_jobs import (
    CaptureBusy,
    CaptureJob,
    CaptureJobs,
    CaptureRateLimited,
    PostgresCaptureBackend,
    capture_control_table,
    capture_jobs_table,
    capture_rate_limit_table,
)
from apps.api.main import Handlers, HealthStatus, create_app
from apps.api.security import CSRF_COOKIE, CSRF_FIELD
from apps.api.web_capture import CaptureOutcome, CaptureRequest
from carvajal.adapters.storage import tables
from carvajal.adapters.storage.database import build_session_factory, normalize_database_url
from carvajal.adapters.storage.sync_postgres import PostgresSync
from carvajal.application.sync import (
    CorpusLayer,
    gold_index_hashes,
    sync_corpus,
)
from carvajal.pipeline import CaptureEvent

pytestmark = pytest.mark.integration

_ROOT = Path(__file__).resolve().parents[1]

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


def _alembic_config(migration_url: str) -> Config:
    """Config de Alembic apuntando a las migraciones y a la base efímera."""

    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))
    return config


class FakeIndex:
    """Lado Qdrant mínimo de ``sync_corpus`` para la recuperación."""

    def __init__(self) -> None:
        self.points: dict[str, str] = {}

    def hashes(self, layer: str) -> dict[str, str]:
        return dict(self.points)

    def document_ids(self, layer: str) -> set[str]:
        return set(self.points)

    def index_gold(self, layer: CorpusLayer, *, only: set[str] | None, prune: bool) -> Any:
        hashes = gold_index_hashes(layer)
        selected = set(hashes) if only is None else set(only) & set(hashes)
        for slug in selected:
            self.points[slug] = hashes[slug]
        return (len(selected), 0)

    def count(self, layer: str) -> int:
        return len(self.points)


class RecoveryHarness:
    """Recuperación inyectada: ``sync`` completo (``only=None``).

    El lado PostgreSQL es el adaptador real sobre la base efímera; solo Qdrant se
    sustituye por un doble, de modo que los conteos de documentos y fragmentos de
    la evidencia son los de PostgreSQL.
    """

    def __init__(self, gold_dir: Path, factory: Any) -> None:
        self.gold_dir = gold_dir
        self.database = PostgresSync(factory)
        self.index = FakeIndex()
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1
        sync_corpus(
            [CorpusLayer("core", (self.gold_dir,))],
            database=self.database,
            index=self.index,
            only=None,
            prune=False,
            lock_root=self.gold_dir.parent,
        )


@pytest.fixture()
def schema_url(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[str]:
    """Aplica la migración vigente sobre una base vacía de nombre único."""

    monkeypatch.setenv("DATABASE_URL", migration_database_url)
    command.upgrade(_alembic_config(migration_database_url), "head")
    yield migration_database_url


@pytest.fixture()
def engines(schema_url: str) -> Iterator[tuple[Engine, Engine]]:
    """Dos motores distintos sobre la misma base: dos workers simulados."""

    url = normalize_database_url(schema_url)
    primero = create_engine(url, pool_pre_ping=True)
    segundo = create_engine(url, pool_pre_ping=True)
    try:
        yield primero, segundo
    finally:
        primero.dispose()
        segundo.dispose()


def _jobs(
    engine: Engine,
    *,
    runner: Callable[[CaptureRequest, Callable[[CaptureEvent], None]], CaptureOutcome]
    | None = None,
    rate_limit: int = 5,
    window_seconds: int = 3600,
    lease_seconds: float = 120.0,
    heartbeat_seconds: float = 0.1,
    recover: Callable[[], None] | None = None,
) -> CaptureJobs:
    """Captura cableada al PostgreSQL con una recuperación inyectada."""

    backend = PostgresCaptureBackend(
        lambda: build_session_factory(engine),
        rate_limit=rate_limit,
        window_seconds=window_seconds,
        lease_seconds=lease_seconds,
        recover=recover or (lambda: None),
    )
    return CaptureJobs(
        runner or _runner_ok,
        store=backend,
        heartbeat_seconds=heartbeat_seconds,
        rate_limit_secret="secreto-de-prueba",
    )


def _runner_ok(
    request: CaptureRequest,
    on_event: Callable[[CaptureEvent], None],
) -> CaptureOutcome:
    on_event(CaptureEvent(stage="descubrir", detail="1 semilla"))
    return OUTCOME


def _esperar(condicion: Callable[[], bool], *, timeout: float = 10.0) -> None:
    """Espera activa acotada: el estado durable cruza hilos y motores."""

    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        if condicion():
            return
        time.sleep(0.02)
    raise AssertionError("la condición no se cumplió a tiempo")


def _esperar_terminal(jobs: CaptureJobs, job_id: str) -> None:
    """Espera a que el job salga de pending/running."""

    def listo() -> bool:
        job = jobs.get(job_id)
        return job is not None and job.status in {"done", "error", "interrupted"}

    _esperar(listo)


def _conteos(engine: Engine) -> tuple[int, int]:
    """Documentos y fragmentos del corpus en PostgreSQL."""

    with Session(engine) as session:
        documentos = session.scalar(select(func.count()).select_from(tables.documents))
        fragmentos = session.scalar(select(func.count()).select_from(tables.chunks))
    return int(documentos or 0), int(fragmentos or 0)


def _viejo(engine: Engine, job_id: str) -> None:
    """Simula que el worker dueño dejó de latir hace una hora."""

    with Session(engine) as session:
        session.execute(
            text("UPDATE capture_control SET heartbeat_at = now() - interval '1 hour' WHERE id = 1")
        )
        session.execute(
            text("UPDATE capture_jobs SET updated_at = now() - interval '1 hour' WHERE id = :id"),
            {"id": job_id},
        )
        session.commit()


def _gold(tmp_path: Path) -> Path:
    """Gold de prueba con la misma identidad de fuente que la captura."""

    gold = tmp_path / "gold"
    gold.mkdir()
    (gold / "a.md").write_text(
        "---\n"
        "url: https://example.com/a\n"
        "title: Página A\n"
        "section: General\n"
        "domain: example.com\n"
        "source_type: oficial\n"
        "captured_at: 2026-11-20T00:00:00+00:00\n"
        "hash: abc123\n"
        "chars: 5\n"
        "---\n\n"
        "Texto.\n",
        encoding="utf-8",
    )
    return gold


def test_dos_solicitudes_simultaneas_solo_inician_una_captura(
    engines: tuple[Engine, Engine],
) -> None:
    """Dos workers, dos envíos a la vez: exactamente uno arranca."""

    # Arrange
    primero, segundo = engines
    puerta = threading.Event()

    def runner(
        request: CaptureRequest,
        on_event: Callable[[CaptureEvent], None],
    ) -> CaptureOutcome:
        puerta.wait(timeout=10)
        return OUTCOME

    jobs_a = _jobs(primero, runner=runner)
    jobs_b = _jobs(segundo, runner=runner)
    barrera = threading.Barrier(2)
    resultados: list[tuple[str, str | None]] = []
    cerrojo = threading.Lock()

    def enviar(jobs: CaptureJobs) -> None:
        barrera.wait(timeout=10)
        try:
            job = jobs.start(CaptureRequest(limit=1), owner_key="198.51.100.10")
        except CaptureBusy:
            with cerrojo:
                resultados.append(("busy", None))
        else:
            with cerrojo:
                resultados.append(("ok", job.id))

    # Act
    hilos = [
        threading.Thread(target=enviar, args=(jobs_a,)),
        threading.Thread(target=enviar, args=(jobs_b,)),
    ]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=10)

    # Assert: con el runner aún bloqueado, hay un solo job y un solo ganador.
    assert sorted(resultado[0] for resultado in resultados) == ["busy", "ok"]
    aceptado = next(resultado[1] for resultado in resultados if resultado[0] == "ok")
    assert aceptado is not None
    with Session(primero) as session:
        activos = session.scalar(
            select(func.count())
            .select_from(capture_jobs_table)
            .where(capture_jobs_table.c.status.in_(("pending", "running")))
        )
        total = session.scalar(select(func.count()).select_from(capture_jobs_table))
    assert activos == 1
    assert total == 1

    puerta.set()
    _esperar_terminal(jobs_a, aceptado)
    assert jobs_b.get(aceptado) is not None


def test_el_cupo_por_ip_es_compartido_y_no_guarda_la_ip(
    engines: tuple[Engine, Engine],
) -> None:
    """El cupo cuenta una vez para todo el despliegue y se indexa por resumen."""

    # Arrange
    primero, segundo = engines
    jobs_a = _jobs(primero, rate_limit=1, window_seconds=60)
    jobs_b = _jobs(segundo, rate_limit=1, window_seconds=60)
    ip = "203.0.113.77"
    marca = jobs_a.start(CaptureRequest(limit=1), owner_key=ip)
    _esperar_terminal(jobs_a, marca.id)

    # Act / Assert: otra instancia rechaza al mismo dueño…
    with pytest.raises(CaptureRateLimited) as error:
        jobs_b.start(CaptureRequest(limit=1), owner_key=ip)
    assert error.value.retry_after >= 1
    # …y deja pasar a otro dueño.
    otro = jobs_b.start(CaptureRequest(limit=1), owner_key="203.0.113.78")
    _esperar_terminal(jobs_b, otro.id)

    with Session(primero) as session:
        hashes = session.scalars(select(capture_rate_limit_table.c.owner_hash)).all()
    assert ip not in hashes
    assert all(len(valor) == 64 for valor in hashes)


def test_el_progreso_se_sirve_desde_otra_instancia_y_respeta_last_event_id(
    engines: tuple[Engine, Engine],
) -> None:
    """El SSE se reproduce desde la base, en orden, y reanuda sin duplicar."""

    # Arrange
    primero, segundo = engines
    puerta = threading.Event()

    def runner(
        request: CaptureRequest,
        on_event: Callable[[CaptureEvent], None],
    ) -> CaptureOutcome:
        on_event(CaptureEvent(stage="descubrir", detail="1 semilla"))
        on_event(CaptureEvent(stage="descargar", url="https://example.com/a", detail="10 bytes"))
        puerta.wait(timeout=10)
        return OUTCOME

    jobs_a = _jobs(primero, runner=runner)
    jobs_b = _jobs(segundo)
    job = jobs_a.start(CaptureRequest(limit=1), owner_key="198.51.100.20")
    eventos: list[str] = []

    def consumir() -> None:
        eventos.extend(jobs_b.stream(job.id))

    # Act: el worker B sirve el progreso que produjo A.
    hilo = threading.Thread(target=consumir)
    hilo.start()
    _esperar(lambda: any("event: stage" in evento for evento in eventos))
    estado = jobs_b.get(job.id)
    assert estado is not None and estado.status == "running"
    puerta.set()
    hilo.join(timeout=10)

    # Assert: progreso completo y ordenado, con identificadores de evento.
    nombres = [
        linea.removeprefix("event: ")
        for evento in eventos
        for linea in evento.splitlines()
        if linea.startswith("event: ")
    ]
    ids = [
        int(linea.removeprefix("id: "))
        for evento in eventos
        for linea in evento.splitlines()
        if linea.startswith("id: ")
    ]
    assert nombres == ["stage", "stage", "done"]
    assert ids == sorted(ids) == [1, 2, 3]

    # Reanudar desde la secuencia 1 entrega solo lo que falta…
    reanudados = list(jobs_b.stream(job.id, after=1))
    reanudados_nombres = [
        linea.removeprefix("event: ")
        for evento in reanudados
        for linea in evento.splitlines()
        if linea.startswith("event: ")
    ]
    assert reanudados_nombres == ["stage", "done"]
    # …y una reconexión posterior al final reemite el resultado, como hoy.
    final = list(jobs_a.stream(job.id, after=3))
    assert len(final) == 1
    assert "event: done" in final[0]


def test_un_job_interrumpido_se_detecta_y_recupera_sin_duplicar(
    engines: tuple[Engine, Engine],
    tmp_path: Path,
) -> None:
    """Latido caducado: interrumpido, recuperado desde Gold y sin duplicados."""

    primero, segundo = engines
    # Arrange: Gold ya escrito por la captura que va a "morir".
    gold = _gold(tmp_path)
    recuperacion = RecoveryHarness(gold, build_session_factory(primero))
    assert _conteos(primero) == (0, 0)
    puerta = threading.Event()
    empezado = threading.Event()
    terminado = threading.Event()

    def runner_lento(
        request: CaptureRequest,
        on_event: Callable[[CaptureEvent], None],
    ) -> CaptureOutcome:
        on_event(
            CaptureEvent(stage="enriquecer", url="https://example.com/a", detail="1 documento")
        )
        empezado.set()
        puerta.wait(timeout=10)
        terminado.set()
        return OUTCOME

    def runner_ok_con_gold(
        request: CaptureRequest,
        on_event: Callable[[CaptureEvent], None],
    ) -> CaptureOutcome:
        (gold / "a.md").write_text((gold / "a.md").read_text(encoding="utf-8"), encoding="utf-8")
        return OUTCOME

    # Sin latido del worker "muerto": su runner está bloqueado y no renueva.
    jobs_a = _jobs(primero, runner=runner_lento, heartbeat_seconds=30.0)
    job = jobs_a.start(
        CaptureRequest(urls=("https://example.com/a",), scope="manual", limit=5),
        owner_key="198.51.100.30",
    )
    assert empezado.wait(timeout=10)
    _viejo(primero, job.id)

    # Act: el worker A deja de latir y el worker B intenta una captura nueva.
    jobs_b = _jobs(segundo, runner=runner_ok_con_gold, recover=recuperacion)
    nuevo = jobs_b.start(
        CaptureRequest(urls=("https://example.com/a",), scope="manual", limit=5),
        owner_key="198.51.100.31",
    )

    # Assert: la recuperación corrió antes de aceptar la captura nueva y Gold
    # quedó convergido en PostgreSQL y en el índice (conteos reales de la base).
    assert recuperacion.calls == 1
    assert _conteos(primero)[0] == 1
    assert _conteos(primero)[1] >= 1
    assert recuperacion.index.count("core") == 1
    interrumpido = jobs_b.get(job.id)
    assert interrumpido is not None
    assert interrumpido.status == "interrupted"
    stream = list(jobs_b.stream(job.id))
    assert any("event: error" in evento for evento in stream)
    _esperar_terminal(jobs_b, nuevo.id)

    # Act: pasada de recuperación sin nada nuevo y el worker viejo termina tarde.
    assert jobs_b.recover_interrupted() == []
    puerta.set()
    assert terminado.wait(timeout=10)
    _esperar(lambda: jobs_b.get(job.id) is not None)

    # Assert: un solo documento por fuente en Gold, PostgreSQL real y Qdrant.
    assert len(list(gold.glob("*.md"))) == 1
    documentos, fragmentos = _conteos(primero)
    assert documentos == 1
    assert fragmentos >= 1
    assert recuperacion.index.count("core") == 1
    assert recuperacion.calls == 1
    final = jobs_b.get(job.id)
    assert final is not None and final.status == "interrupted"


def test_la_transicion_a_interrumpido_ocurre_una_sola_vez(
    engines: tuple[Engine, Engine],
) -> None:
    """Dos workers recuperan a la vez: solo uno marca y sincroniza."""

    # Arrange: job con latido caducado y dos instancias recuperando.
    primero, segundo = engines
    tercero = create_engine(segundo.url, pool_pre_ping=True)
    puerta = threading.Event()

    def runner(
        request: CaptureRequest,
        on_event: Callable[[CaptureEvent], None],
    ) -> CaptureOutcome:
        puerta.wait(timeout=10)
        return OUTCOME

    jobs_a = _jobs(primero, runner=runner, heartbeat_seconds=30.0)
    job = jobs_a.start(CaptureRequest(limit=1), owner_key="198.51.100.70")
    _viejo(primero, job.id)
    llamadas: list[int] = []
    contador = threading.Lock()

    def recuperacion() -> None:
        with contador:
            llamadas.append(1)

    jobs_b = _jobs(segundo, recover=recuperacion)
    jobs_c = _jobs(tercero, recover=recuperacion)
    barrera = threading.Barrier(2)
    marcados: list[list[CaptureJob]] = []

    def recuperar(jobs: CaptureJobs) -> None:
        barrera.wait(timeout=10)
        marcados.append(jobs.recover_interrupted())

    try:
        hilos = [
            threading.Thread(target=recuperar, args=(jobs_b,)),
            threading.Thread(target=recuperar, args=(jobs_c,)),
        ]
        for hilo in hilos:
            hilo.start()
        for hilo in hilos:
            hilo.join(timeout=15)
        puerta.set()

        # Assert: una sola transición y una sola sincronización.
        assert sum(len(lista) for lista in marcados) == 1
        assert len(llamadas) == 1
        estado = jobs_b.get(job.id)
        assert estado is not None and estado.status == "interrupted"
        assert jobs_b.recover_interrupted() == []
    finally:
        tercero.dispose()


def test_el_apagado_marca_la_captura_local_como_interrumpida(
    engines: tuple[Engine, Engine],
) -> None:
    """Un apagado ordenado libera el control y deja el trabajo recuperable."""

    # Arrange
    primero, _segundo = engines
    puerta = threading.Event()

    def runner(
        request: CaptureRequest,
        on_event: Callable[[CaptureEvent], None],
    ) -> CaptureOutcome:
        puerta.wait(timeout=10)
        return OUTCOME

    jobs = _jobs(primero, runner=runner)
    job = jobs.start(CaptureRequest(limit=1), owner_key="198.51.100.40")
    _esperar(lambda: (jobs.get(job.id) or _sin_job()).status == "running")

    # Act
    jobs.shutdown()
    puerta.set()

    # Assert
    estado = jobs.get(job.id)
    assert estado is not None
    assert estado.status == "interrupted"
    with Session(primero) as session:
        control = session.execute(select(*capture_control_table.c)).mappings().one()
    assert control["job_id"] is None


class _SinJob:
    """Estado de apoyo mientras el job aún no existe."""

    status = "desconocido"


def _sin_job() -> _SinJob:
    return _SinJob()


def test_el_cupo_y_el_progreso_se_comparten_entre_dos_apps(
    engines: tuple[Engine, Engine],
) -> None:
    """Dos apps FastAPI sobre la misma base comparten POST, estado y SSE."""

    # Arrange
    primero, segundo = engines
    app_a = _app(_jobs(primero, rate_limit=1, window_seconds=60))
    app_b = _app(_jobs(segundo, rate_limit=1, window_seconds=60))
    cliente_a = TestClient(app_a)
    cliente_b = TestClient(app_b)
    datos = {
        "urls": "http://8.8.8.8/",
        "scope": "manual",
        "limit": "5",
        "respect_robots": "on",
    }
    datos_a = {CSRF_FIELD: _token(cliente_a), **datos}
    datos_b = {CSRF_FIELD: _token(cliente_b), **datos}

    # Act: A lanza la captura; B la rechaza por cupo una vez completada.
    respuesta = cliente_a.post(
        "/configuracion/captura", data=datos_a, headers={"HX-Request": "true"}
    )
    job_id = _job_id(respuesta.text)
    _esperar_terminal(app_a.state.captures, job_id)
    segundo_post = cliente_b.post(
        "/configuracion/captura", data=datos_b, headers={"HX-Request": "true"}
    )

    # Assert
    assert respuesta.status_code == 200
    assert segundo_post.status_code == 429
    assert int(segundo_post.headers["Retry-After"]) >= 1
    estado = cliente_b.get(f"/configuracion/captura/{job_id}")
    assert estado.status_code == 200
    assert estado.json()["status"] == "done"
    stream = cliente_b.get(f"/configuracion/captura/{job_id}/eventos")
    assert stream.status_code == 200
    assert "event: done" in stream.text


def test_live_no_toca_la_base_y_health_conserva_el_contrato() -> None:
    """``/live`` responde con un backend de captura roto; ``/health`` no cambia."""

    # Arrange
    app = create_app(Handlers(health=lambda: HealthStatus(documents=7, chunks=9)))
    app.state.captures = _BackendRoto()  # type: ignore[assignment]
    cliente = TestClient(app)

    # Act
    live = cliente.get("/live")
    health = cliente.get("/health")

    # Assert
    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert health.status_code == 200
    datos = health.json()
    assert set(datos) >= {"status", "documents", "chunks", "index", "monitoring"}
    assert datos["documents"] == 7 and datos["chunks"] == 9


class _BackendRoto:
    """Cualquier uso del registro de captura debe fallar: /live no lo toca."""

    def __getattr__(self, nombre: str) -> Any:
        raise AssertionError(f"/live no debe usar el registro de captura ({nombre})")


def _app(jobs: CaptureJobs) -> Any:
    """App real con el registro de captura inyectado."""

    app = create_app(Handlers(health=lambda: HealthStatus(documents=1, chunks=1)))
    app.state.captures = jobs
    return app


def _token(client: TestClient) -> str:
    client.get("/configuracion")
    return client.cookies[CSRF_COOKIE]


def _job_id(html: str) -> str:
    marcador = 'data-captura-job="'
    inicio = html.index(marcador) + len(marcador)
    return html[inicio:].split('"', 1)[0]


def test_la_migracion_de_captura_es_reversible(
    migration_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sube, baja un paso y vuelve a subir; conserva las tablas del corpus."""

    # Arrange
    monkeypatch.setenv("DATABASE_URL", migration_database_url)
    config = _alembic_config(migration_database_url)
    motor = create_engine(migration_database_url)
    try:
        command.upgrade(config, "head")
        tablas = set(inspect(motor).get_table_names())
        assert {
            "capture_jobs",
            "capture_events",
            "capture_control",
            "capture_rate_limit",
        } <= tablas

        # Act
        command.downgrade(config, "-1")
        tras_bajar = set(inspect(motor).get_table_names())
        assert "capture_jobs" not in tras_bajar
        assert "documents" in tras_bajar

        # Assert: vuelve a subir sin residuos.
        command.upgrade(config, "head")
        de_nuevo = set(inspect(motor).get_table_names())
        assert "capture_control" in de_nuevo
        with Session(motor) as session:
            fila = session.execute(select(*capture_control_table.c)).mappings().one()
        assert fila["job_id"] is None
    finally:
        motor.dispose()


def test_el_stream_no_retiene_conexiones(
    engines: tuple[Engine, Engine],
) -> None:
    """Consultar estado y stream devuelve las conexiones al pool."""

    # Arrange
    primero, segundo = engines
    jobs_a = _jobs(primero)
    jobs_b = _jobs(segundo)
    job = jobs_a.start(CaptureRequest(limit=1), owner_key="198.51.100.50")
    _esperar_terminal(jobs_a, job.id)

    # Act
    assert jobs_b.get(job.id) is not None
    list(jobs_b.stream(job.id))

    # Assert
    assert cast(Any, primero.pool).checkedout() == 0
    assert cast(Any, segundo.pool).checkedout() == 0


def test_la_respuesta_de_estado_no_expone_la_ip_ni_el_worker(
    engines: tuple[Engine, Engine],
) -> None:
    """El JSON de estado conserva las claves y no filtra datos del dueño."""

    # Arrange
    primero, _segundo = engines
    app = _app(_jobs(primero))
    cliente = TestClient(app)
    token = _token(cliente)
    respuesta = cliente.post(
        "/configuracion/captura",
        data={
            CSRF_FIELD: token,
            "urls": "http://8.8.8.8/",
            "scope": "manual",
            "limit": "5",
        },
        headers={"HX-Request": "true"},
    )
    job_id = _job_id(respuesta.text)
    _esperar_terminal(app.state.captures, job_id)

    # Act
    estado = cliente.get(f"/configuracion/captura/{job_id}")

    # Assert
    assert set(estado.json()) == {"id", "status", "error", "outcome"}
    assert "testclient" not in estado.text
    assert "owner" not in estado.text


def test_el_error_de_recuperacion_no_impide_arrancar(
    engines: tuple[Engine, Engine],
) -> None:
    """Si ``sync`` falla, la captura interrumpida se marca y el inicio continúa."""

    # Arrange
    primero, segundo = engines
    puerta = threading.Event()

    def runner(
        request: CaptureRequest,
        on_event: Callable[[CaptureEvent], None],
    ) -> CaptureOutcome:
        puerta.wait(timeout=10)
        return OUTCOME

    def recuperacion_rota() -> None:
        raise RuntimeError("sin Qdrant")

    jobs_a = _jobs(primero, runner=runner, heartbeat_seconds=30.0)
    job = jobs_a.start(CaptureRequest(limit=1), owner_key="198.51.100.60")
    _viejo(primero, job.id)
    jobs_b = _jobs(segundo, recover=recuperacion_rota)

    # Act
    nuevo = jobs_b.start(CaptureRequest(limit=1), owner_key="198.51.100.61")
    puerta.set()
    _esperar_terminal(jobs_b, nuevo.id)

    # Assert
    interrumpido = jobs_b.get(job.id)
    assert interrumpido is not None
    assert interrumpido.status == "interrupted"
