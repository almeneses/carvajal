"""Composition root del shell web: monta estáticos y registra los routers.

Las rutas viven en ``apps/api/routers`` y las piezas compartidas en
``apps/api/deps.py``. Aquí solo se compone la aplicación, para que cada pista
de trabajo añada su router con una línea y no compita por este archivo.

Los handlers reales se construyen al primer uso, nunca al importar; ``embedder``
y ``llm`` son singletons y cada petición abre su propia sesión.
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from apps.api.deps import (
    APP_DIR,
    DB_UNAVAILABLE,
    SERVICE_UNAVAILABLE,
    Handlers,
    HealthStatus,
)
from apps.api.routers import chat, health

__all__ = [
    "DB_UNAVAILABLE",
    "SERVICE_UNAVAILABLE",
    "Handlers",
    "HealthStatus",
    "create_app",
]


def create_app(handlers: Handlers | None = None) -> FastAPI:
    """Crea la aplicación; sin ``handlers`` usa los reales, diferidos al primer uso."""

    app = FastAPI(title="Corpus Carvajal")
    app.state.handlers = handlers
    app.mount("/static", StaticFiles(directory=APP_DIR / "static", check_dir=False), name="static")
    app.include_router(health.router)
    app.include_router(chat.router)
    return app


app = create_app()
