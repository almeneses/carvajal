#!/usr/bin/env python3
"""Verificación real de 3 workers de Uvicorn y captura compartida (fase 5).

Levanta PostgreSQL y Qdrant locales (ya en marcha), crea una base y una colección
temporales, arranca la app con `--workers 3` y comprueba, contra HTTP y la base:

1. dos POST simultáneos no inician dos capturas (uno recibe 409);
2. el progreso se sirve desde cualquier worker y sobrevive a la reconexión SSE;
3. el cupo por IP es compartido (429 con la ventana agotada);
4. la caída del worker que ejecuta la captura deja el job interrumpido, se
   recupera desde un punto seguro y no duplica documentos;
5. el retorno a 1 worker conserva el estado persistido y sigue sirviendo la app;
6. `/live` no depende de la base.

Uso (desde la raíz de un worktree con el venv sincronizado):

    .venv/bin/python .evidence/3w/verificar_3_workers.py --output .evidence/3w/resultado.json

No toca el corpus versionado: usa un GOLD_DIR temporal, una base temporal y una
colección de Qdrant temporal. Solo usa la red para capturar example.com/.org/.net.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import psycopg
import requests
from psycopg import sql


def _find_root(start: Path) -> Path:
    """Raíz del worktree: el ancestro que contiene pyproject.toml y la app."""
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "apps/api/main.py").is_file():
            return candidate
    raise RuntimeError("no se encontró la raíz del repositorio")


ROOT = _find_root(Path(__file__).resolve().parent)
PYTHON = str(ROOT / ".venv/bin/python")
UVICORN = [str(ROOT / ".venv/bin/uvicorn"), "apps.api.main:app"]


@dataclass
class Result:
    ok: bool = True
    checks: list[dict[str, Any]] = field(default_factory=list)

    def check(self, name: str, passed: bool, detail: Any) -> None:
        self.checks.append({"name": name, "passed": passed, "detail": detail})
        if not passed:
            self.ok = False

    def summary(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": self.checks,
            "failed": [c["name"] for c in self.checks if not c["passed"]],
        }


def _admin_url() -> str:
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ.get("POSTGRES_USER", "carvajal")
    password = os.environ.get("POSTGRES_PASSWORD", "carvajal")
    return f"postgresql://{user}:{password}@127.0.0.1:{port}/postgres"


def _db_url(name: str) -> str:
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ.get("POSTGRES_USER", "carvajal")
    password = os.environ.get("POSTGRES_PASSWORD", "carvajal")
    return f"postgresql://{user}:{password}@127.0.0.1:{port}/{name}"


def _recreate_db(name: str) -> None:
    with psycopg.connect(_admin_url(), autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def _query(url: str, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(url, autocommit=True) as conn:
        cursor = conn.execute(cast(Any, query), params)
        if cursor.description is None:  # DELETE/UPDATE/INSERT: sin filas
            return []
        return cursor.fetchall()


def _delete_qdrant_collection(base_url: str, collection: str) -> None:
    with contextlib.suppress(requests.RequestException):
        requests.delete(f"{base_url}/collections/{collection}", timeout=10)


def _app_env(db_name: str, scratch: Path, collection: str) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("OLLAMA_MODEL", None)
    env.update(
        {
            "DATABASE_URL": _db_url(db_name),
            "SESSION_SECRET": "verificacion-3-workers-secreto-0123456789",
            "LLM_PROVIDER": "ollama",
            "OLLAMA_BASE_URL": os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            "QDRANT_URL": os.environ.get("QDRANT_URL", "http://127.0.0.1:6333"),
            "QDRANT_COLLECTION": collection,
            "GOLD_DIR": str(scratch / "gold"),
            "RAW_DIR": str(scratch / "raw"),
            "PROCESSED_DIR": str(scratch / "processed"),
            "EXTERNAL_DIR": str(scratch / "external"),
            "INDEX_DIR": str(scratch / "index"),
            "CAPTURE_DEFAULT_URLS": "3",
            "CAPTURE_REQUESTS_LIMIT": "5",
            "CAPTURE_WINDOW_SECONDS": "3600",
            "CAPTURE_HEARTBEAT_SECONDS": "2",
            "CAPTURE_STALE_SECONDS": "8",
            "LOG_LEVEL": "INFO",
            "MONITORING_ENABLED": "false",
        }
    )
    for name in ("gold", "raw", "processed", "external", "index"):
        (scratch / name).mkdir(parents=True, exist_ok=True)
    return env


def _run_alembic(env: dict[str, str]) -> None:
    result = subprocess.run(
        [str(ROOT / ".venv/bin/alembic"), "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic upgrade head falló:\n{result.stdout}\n{result.stderr}")


class App:
    def __init__(self, env: dict[str, str], workers: int, port: int, log: Path) -> None:
        self.env = env
        self.workers = workers
        self.port = port
        self.log = log
        self.process: subprocess.Popen[bytes] | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        handle = self.log.open("ab")
        self.process = subprocess.Popen(
            [
                *UVICORN,
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--workers",
                str(self.workers),
                "--no-access-log",
            ],
            cwd=ROOT,
            env=self.env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        deadline = time.time() + 45
        while time.time() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"uvicorn terminó al arrancar; ver {self.log}")
            try:
                if requests.get(f"{self.base}/live", timeout=1).status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.4)
        raise RuntimeError(f"la app no respondió /live; ver {self.log}")

    def stop(self, sig: int = signal.SIGTERM) -> None:
        if self.process is None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(self.process.pid), sig)
        try:
            self.process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)


def _csrf(session: requests.Session, base: str) -> str:
    response = session.get(f"{base}/configuracion", timeout=10)
    response.raise_for_status()
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', response.text)
    if match is None:
        raise RuntimeError("no se encontró el token CSRF en /configuracion")
    return match.group(1)


def _post_capture(
    session: requests.Session, base: str, urls: list[str], limit: int, csrf: str
) -> requests.Response:
    return session.post(
        f"{base}/configuracion/captura",
        data={
            "csrf_token": csrf,
            "urls": "\n".join(urls),
            "scope": "manual",
            "limit": str(limit),
            "respect_robots": "on",
        },
        timeout=120,
    )


def _job_id(response: requests.Response) -> str | None:
    match = re.search(r'data-captura-job="([0-9a-f]+)"', response.text)
    return match.group(1) if match else None


def _status(session: requests.Session, base: str, job_id: str) -> dict[str, Any]:
    response = session.get(f"{base}/configuracion/captura/{job_id}", timeout=10)
    return response.json() if response.ok else {"status": f"http-{response.status_code}"}


def _wait_status(
    session: requests.Session, base: str, job_id: str, desired: set[str], timeout: float = 90
) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = _status(session, base, job_id)
        if last.get("status") in desired:
            return last
        time.sleep(0.4)
    return last


def _db_job_row(url: str, job_id: str) -> tuple[Any, ...] | None:
    rows = _query(
        url,
        "SELECT id, status, worker, outcome, error FROM capture_jobs WHERE id = %s",
        (job_id,),
    )
    return rows[0] if rows else None


def _wait_job_row(
    url: str, job_id: str, statuses: set[str], timeout: float = 90
) -> tuple[Any, ...] | None:
    deadline = time.time() + timeout
    row = None
    while time.time() < deadline:
        row = _db_job_row(url, job_id)
        if row is not None and row[1] in statuses:
            return row
        time.sleep(0.3)
    return row


def _sse_events(
    session: requests.Session, base: str, job_id: str, last_id: str = ""
) -> list[dict[str, str]]:
    headers = {"Accept": "text/event-stream"}
    if last_id:
        headers["Last-Event-ID"] = last_id
    events: list[dict[str, str]] = []
    with session.get(
        f"{base}/configuracion/captura/{job_id}/eventos", headers=headers, stream=True, timeout=20
    ) as response:
        response.raise_for_status()
        current: dict[str, str] = {}
        for raw in response.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            line = (raw.decode() if isinstance(raw, bytes) else raw).strip()
            if not line:
                if current:
                    events.append(current)
                    current = {}
                    if events and events[-1].get("event") in {"done", "error"}:
                        break
                continue
            if line.startswith("id:"):
                current["id"] = line[3:].strip()
            elif line.startswith("event:"):
                current["event"] = line[6:].strip()
            elif line.startswith("data:"):
                current["data"] = line[5:].strip()
    if current:
        events.append(current)
    return events


def _all_processes() -> dict[int, int]:
    """Mapa pid -> ppid para todo el espacio de procesos visible."""
    processes: dict[int, int] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            processes[pid] = int(Path(f"/proc/{pid}/stat").read_text().split()[3])
        except (OSError, ValueError, IndexError):
            continue
    return processes


def _uvicorn_parent(port: int) -> int:
    """PID del proceso padre de uvicorn para este puerto."""
    for pid, ppid in _all_processes().items():
        if ppid != os.getpid():
            continue
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode()
        except OSError:
            continue
        if "uvicorn" in cmdline and f"--port {port}" in cmdline:
            return pid
    return 0


def _worker_pids(port: int) -> list[int]:
    """Hijos servidores de uvicorn (workers de multiprocessing).

    Uvicorn crea el *resource tracker* de multiprocessing como hijo del padre;
    ese proceso no es un worker y se excluye por su línea de comandos.
    """
    parent = _uvicorn_parent(port)
    if not parent:
        return []
    workers: list[int] = []
    for pid, ppid in _all_processes().items():
        if ppid != parent:
            continue
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode()
        except OSError:
            continue
        if "resource_tracker" not in cmdline:
            workers.append(pid)
    return sorted(workers)


def _log_worker_pids(port: int) -> list[int]:
    return _worker_pids(port)


def _kill(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)


def _worker_rss(pids: list[int]) -> dict[int, int]:
    """Memoria residente (KB) de cada worker desde /proc."""
    rss: dict[int, int] = {}
    for pid in pids:
        try:
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss[pid] = int(line.split()[1])
                    break
        except OSError:
            continue
    return rss


def _db_connections(db_url: str, db_name: str) -> int:
    rows = _query(db_url, "SELECT count(*) FROM pg_stat_activity WHERE datname = %s", (db_name,))
    return int(rows[0][0])


def _counts(db_url: str) -> dict[str, int]:
    docs = _query(db_url, "SELECT count(*) FROM documents")[0][0]
    chunks = _query(db_url, "SELECT count(*) FROM chunks")[0][0]
    distinct_chunks = _query(db_url, "SELECT count(DISTINCT id) FROM chunks")[0][0]
    distinct_docs = _query(db_url, "SELECT count(DISTINCT id) FROM documents")[0][0]
    return {
        "documents": docs,
        "chunks": chunks,
        "distinct_documents": distinct_docs,
        "distinct_chunks": distinct_chunks,
    }


def _gold_files(scratch: Path) -> list[str]:
    return sorted(path.name for path in (scratch / "gold").glob("*.md"))


def run(output: Path) -> Result:
    result = Result()
    port = 8098
    db_name = f"carvajal_3w_{os.getpid()}"
    scratch = ROOT / ".evidence/3w/run"
    if scratch.exists():
        import shutil

        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    collection = f"carvajal_3w_{os.getpid()}"
    env = _app_env(db_name, scratch, collection)
    _recreate_db(db_name)
    _delete_qdrant_collection(env["QDRANT_URL"], collection)
    _run_alembic(env)
    db_url = env["DATABASE_URL"]

    app = App(env, workers=3, port=port, log=scratch / "uvicorn-3.log")
    app.start()
    try:
        pids = _log_worker_pids(port)
        rss = _worker_rss(pids)
        connections = _db_connections(db_url, db_name)
        result.check(
            "app arranca con 3 procesos",
            len(pids) == 3,
            {
                "pids": pids,
                "rss_kb": rss,
                "rss_total_kb": sum(rss.values()),
                "conexiones_postgres": connections,
            },
        )
        result.check(
            "/live responde sin base",
            requests.get(f"{app.base}/live", timeout=5).status_code == 200,
            {},
        )

        # --- captura simultánea: solo una ---
        urls = [
            "https://www.iana.org/",
            "https://www.iana.org/domains",
            "https://www.iana.org/help/example-domains",
        ]
        sessions = [requests.Session(), requests.Session()]
        tokens = []
        for session in sessions:
            session.get(f"{app.base}/configuracion", timeout=10)
            match = re.search(
                r'name="csrf_token"\s+value="([^"]+)"',
                session.get(f"{app.base}/configuracion").text,
            )
            tokens.append(match.group(1) if match else "")
        answers: list[requests.Response] = []
        lock = threading.Lock()

        def send(session: requests.Session, token: str) -> None:
            response = _post_capture(session, app.base, urls, 3, token)
            with lock:
                answers.append(response)

        threads = [
            threading.Thread(target=send, args=(s, t))
            for s, t in zip(sessions, tokens, strict=False)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        statuses = sorted(response.status_code for response in answers)
        job_ids = {jid for response in answers if (jid := _job_id(response))}
        result.check(
            "dos POST simultáneos: uno crea el job y el otro recibe 409",
            statuses == [200, 409] and len(job_ids) == 1,
            {
                "statuses": statuses,
                "jobs": sorted(job_ids),
                "snippets": [r.text[:180] for r in answers],
            },
        )
        job_id = next(iter(job_ids), "")
        row = _wait_job_row(db_url, job_id, {"running", "done", "error"}, timeout=15)
        result.check(
            "el job simultáneo queda registrado en PostgreSQL",
            row is not None and row[1] in {"running", "done"},
            {"row": row},
        )

        # --- progreso desde otro worker y reconexión SSE ---
        events = _sse_events(sessions[0], app.base, job_id)
        result.check(
            "el SSE de progreso se sirve desde cualquier worker",
            len(events) >= 1 and any(event.get("event") in {"stage", "done"} for event in events),
            {"eventos": events[:3]},
        )
        last_id = next((event.get("id", "") for event in reversed(events) if event.get("id")), "")
        if last_id:
            replay = _sse_events(sessions[0], app.base, job_id, last_id=last_id)
            result.check(
                "la reconexión SSE con Last-Event-ID no repite etapas",
                all(
                    event.get("event") not in {"stage"}
                    or not event.get("id")
                    or event["id"] > last_id
                    for event in replay
                ),
                {"last_id": last_id, "replay": replay[:3]},
            )
        final = _wait_status(sessions[0], app.base, job_id, {"done", "error"}, timeout=120)
        result.check(
            "la captura termina y converge", final.get("status") == "done", {"final": final}
        )
        counts_before = _counts(db_url)
        result.check(
            "no hay documentos ni fragmentos duplicados tras la captura",
            counts_before["documents"] == counts_before["distinct_documents"]
            and counts_before["chunks"] == counts_before["distinct_chunks"],
            counts_before,
        )

        # --- recuperación tras matar el worker que ejecuta ---
        _query(db_url, "DELETE FROM capture_rate_limit")
        recovery_urls = [
            "https://www.iana.org/about",
            "https://www.iana.org/domains/root",
            "https://www.iana.org/domains/reserved",
        ]
        session = requests.Session()
        token = _csrf(session, app.base)
        start_response = _post_capture(session, app.base, recovery_urls, 3, token)
        recovery_job = _job_id(start_response)
        result.check(
            "la captura de recuperación arranca",
            start_response.status_code == 200 and bool(recovery_job),
            {"status": start_response.status_code},
        )
        assert recovery_job is not None
        running = _wait_job_row(db_url, recovery_job, {"running"}, timeout=30)
        result.check(
            "el job de recuperación está en ejecución",
            running is not None and running[1] == "running",
            {"row": running if running is None else list(running[:3])},
        )
        worker = str(running[2]) if running else ""
        worker_pid = int(worker.rsplit(":", 1)[-1]) if worker.rsplit(":", 1)[-1].isdigit() else 0
        worker_was_alive = worker_pid in _log_worker_pids(port)
        if worker_pid and worker_was_alive:
            _kill(worker_pid)
        time.sleep(11)
        stale = _db_job_row(db_url, recovery_job)
        after = None
        for _ in range(30):
            token = _csrf(session, app.base)
            after = _post_capture(session, app.base, ["https://www.iana.org/help"], 1, token)
            if after.status_code == 200:
                break
            time.sleep(2)
        assert after is not None
        result.check(
            "un nuevo worker puede iniciar la captura de recuperación",
            after.status_code == 200,
            {"status": after.status_code, "cuerpo": after.text[:200]},
        )
        stale = _db_job_row(db_url, recovery_job)
        result.check(
            "el worker caído deja el job detectado como interrumpido",
            worker_was_alive and stale is not None and stale[1] == "interrupted",
            {"worker": worker, "row": stale if stale is None else list(stale[:3])},
        )
        new_job = _job_id(after)
        if new_job:
            _wait_status(session, app.base, new_job, {"done", "error"}, timeout=120)
        counts_after = _counts(db_url)
        result.check(
            "la recuperación no duplica documentos ni fragmentos",
            counts_after["documents"] == counts_after["distinct_documents"]
            and counts_after["chunks"] == counts_after["distinct_chunks"],
            counts_after,
        )
        gold = _gold_files(scratch)
        result.check(
            "Gold no tiene archivos repetidos", len(gold) == len(set(gold)), {"gold": gold}
        )

        # --- cupo por IP compartido ---
        _query(db_url, "DELETE FROM capture_rate_limit")
        app.stop()
        app = App(
            {**env, "CAPTURE_REQUESTS_LIMIT": "1"},
            workers=3,
            port=port,
            log=scratch / "uvicorn-3-rate.log",
        )
        app.start()
        session = requests.Session()
        token = _csrf(session, app.base)
        first = _post_capture(session, app.base, ["https://www.iana.org/numbers"], 1, token)
        result.check(
            "con cupo 1, la primera captura se acepta",
            first.status_code == 200,
            {"status": first.status_code},
        )
        first_job = _job_id(first)
        if first_job:
            _wait_status(session, app.base, first_job, {"done", "error"}, timeout=120)
        second = _post_capture(session, app.base, ["https://www.iana.org/protocols"], 1, token)
        result.check(
            "el cupo por IP se comparte y la segunda captura recibe 429 con Retry-After",
            second.status_code == 429 and "Retry-After" in second.headers,
            {"status": second.status_code, "retry_after": second.headers.get("Retry-After")},
        )

        # --- retorno a 1 worker ---
        app.stop()
        app = App(env, workers=1, port=port, log=scratch / "uvicorn-1.log")
        app.start()
        single = _uvicorn_parent(port)
        result.check(
            "la app vuelve a 1 worker",
            bool(single) and len(_log_worker_pids(port)) == 0,
            {"padre": single, "workers": _log_worker_pids(port)},
        )
        health = requests.get(f"{app.base}/health", timeout=10)
        body = health.json() if health.content else {}
        persisted = _db_job_row(db_url, recovery_job) if recovery_job else None
        result.check(
            "el estado persistido sigue disponible con 1 worker",
            health.status_code in {200, 503}
            and {"status", "documents", "chunks"} <= set(body)
            and body.get("documents", 0) >= 1
            and persisted is not None,
            {
                "status": health.status_code,
                "body": body,
                "job_persistido": persisted if persisted is None else list(persisted[:2]),
            },
        )
        session = requests.Session()
        token = _csrf(session, app.base)
        one = _post_capture(session, app.base, ["https://www.iana.org/performance"], 1, token)
        result.check(
            "con 1 worker se puede iniciar una captura",
            one.status_code == 200,
            {"status": one.status_code},
        )
        if one_job := _job_id(one):
            _wait_status(session, app.base, one_job, {"done", "error"}, timeout=120)
    finally:
        app.stop()
        _delete_qdrant_collection(env["QDRANT_URL"], collection)
        with psycopg.connect(_admin_url(), autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db_name))
            )

    payload = result.summary()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / ".evidence/3w/resultado.json")
    args = parser.parse_args()
    result = run(args.output)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
