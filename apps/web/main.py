"""Frontend propio del M2: proceso aparte que consume la API por HTTP/SSE.

No contiene lógica de RAG, credenciales ni acceso a base de datos. Se sirve en
``http://localhost:8001`` y la API en ``http://localhost:8000``; el origen se
configura con ``CARVAJAL_API_BASE``.

El shell es una SPA mínima: todas las rutas de pantalla devuelven el mismo
``index.html`` y el enrutador de ``static/app.js`` decide qué vista mostrar.
"""

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
INDEX_FILE = STATIC_DIR / "index.html"
_API_BASE_ENV = os.environ.get("CARVAJAL_API_BASE", "").strip().rstrip("/")
API_BASE = _API_BASE_ENV or "http://localhost:8000"
# Orígenes literales: el Host de la petición nunca se interpola en la CSP.
_LOCAL_API_BASES = {
    "localhost": "http://localhost:8000",
    "127.0.0.1": "http://127.0.0.1:8000",
}


def api_base(request: Request | None = None) -> str:
    """URL de la API para config.js y la CSP.

    Sin ``CARVAJAL_API_BASE`` se usa el mismo host local del navegador para que
    la cookie de identidad sea de primera parte; en otros hosts se conserva el
    valor por defecto y el despliegue debe fijar la variable.
    """

    if _API_BASE_ENV:
        return _API_BASE_ENV
    host = request.url.hostname if request is not None else ""
    return _LOCAL_API_BASES.get(host, API_BASE)


app = FastAPI(title="Carvajal · frontend M2", docs_url=None, redoc_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """CSP construida con el mismo API base que consume el JavaScript."""

    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        f"connect-src 'self' {api_base(request)}; "
        "img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "base-uri 'self'; frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith("/static/"):
        # El HTML no se cachea; los assets se revalidan para que una versión
        # nueva de la SPA no combine con JS/CSS viejos en caché.
        response.headers["Cache-Control"] = "no-cache"
    return response


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _pagina() -> FileResponse:
    """Devuelve el shell SPA sin caché: cada ruta comparte el mismo documento."""

    return FileResponse(INDEX_FILE, headers={"Cache-Control": "no-store"})


@app.get("/")
def index() -> FileResponse:
    """Sirve la aplicación; la entrada oficial del M2 es esta URL."""

    return _pagina()


@app.get("/faq")
def faq() -> FileResponse:
    """Vista de preguntas frecuentes generadas a partir del corpus."""

    return _pagina()


@app.get("/corpus")
def corpus() -> FileResponse:
    """Vista del explorador del corpus dentro del shell SPA."""

    return _pagina()


@app.get("/metricas")
def metricas() -> FileResponse:
    """Vista del estado del índice y de la evaluación de retrieval."""

    return _pagina()


@app.get("/documents")
def documents() -> FileResponse:
    """Vista de documento sin selección (estado vacío) dentro del shell SPA."""

    return _pagina()


@app.get("/documents/{document_id}")
def document(document_id: str) -> FileResponse:
    """Ficha de un documento; el identificador lo resuelve el cliente por JSON."""

    return _pagina()


@app.get("/config.js")
def config(request: Request) -> PlainTextResponse:
    """Expone solo la URL pública de la API, nunca credenciales."""

    return PlainTextResponse(
        f'window.CARVAJAL_API = "{api_base(request)}";\n',
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/healthz")
def healthz(request: Request) -> dict[str, str]:
    """Vitalidad del proceso de frontend (no comprueba la API)."""

    return {"status": "ok", "api": api_base(request)}
