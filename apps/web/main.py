"""Frontend propio del M2: proceso aparte que consume la API por HTTP/SSE.

No contiene lógica de RAG, credenciales ni acceso a base de datos. Se sirve en
``http://localhost:8001`` y la API en ``http://localhost:8000``; el origen se
configura con ``CARVAJAL_API_BASE``.
"""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
API_BASE = os.environ.get("CARVAJAL_API_BASE", "http://localhost:8000").rstrip("/")

app = FastAPI(title="Carvajal · frontend M2", docs_url=None, redoc_url=None)
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
