"""Frontend propio del M2: proceso aparte que consume la API por HTTP/SSE.

No contiene lógica de RAG, credenciales ni acceso a base de datos. Se sirve en
``http://localhost:8001`` y la API en ``http://localhost:8000``; el origen se
configura con ``CARVAJAL_API_BASE``.
"""

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
API_BASE = os.environ.get("CARVAJAL_API_BASE", "http://localhost:8000").rstrip("/")

app = FastAPI(title="Carvajal · frontend M2", docs_url=None, redoc_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """CSP construida con el mismo API_BASE que consume el JavaScript."""

    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        f"connect-src 'self' {API_BASE}; "
        "img-src 'self' data:; style-src 'self'; script-src 'self'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    """Sirve la aplicación; la entrada oficial del M2 es esta URL."""

    return FileResponse(STATIC_DIR / "index.html")


@app.get("/config.js")
def config() -> PlainTextResponse:
    """Expone solo la URL pública de la API, nunca credenciales."""

    return PlainTextResponse(
        f'window.CARVAJAL_API = "{API_BASE}";\n',
        media_type="application/javascript",
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Vitalidad del proceso de frontend (no comprueba la API)."""

    return {"status": "ok", "api": API_BASE}
