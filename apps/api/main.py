"""Composition root del shell web: monta estáticos y registra los routers.

Las rutas viven en ``apps/api/routers`` y las piezas compartidas en
``apps/api/deps.py``. Aquí solo se compone la aplicación, para que cada pista
de trabajo añada su router con una línea y no compita por este archivo.

Los handlers reales se construyen al primer uso, nunca al importar; ``embedder``
y ``llm`` son singletons y cada petición abre su propia sesión.
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from apps.api.auth.router import router as auth_router
from apps.api.deps import (
    APP_DIR,
    DB_UNAVAILABLE,
    SERVICE_UNAVAILABLE,
    Handlers,
    HealthStatus,
)
from apps.api.observability import RequestContextMiddleware, configure_observability
from apps.api.routers import chat, health
from apps.api.security import SecurityMiddleware
from apps.api.session import OwnerSessionMiddleware

__all__ = [
    "DB_UNAVAILABLE",
    "SERVICE_UNAVAILABLE",
    "Handlers",
    "HealthStatus",
    "create_app",
]


def create_app(handlers: Handlers | None = None) -> FastAPI:
    """Crea la aplicación; sin ``handlers`` usa los reales, diferidos al primer uso."""

    configure_observability()
    # Sin Swagger público: la interfaz de la app es el formulario HTML y el
    # esquema no aporta a un visitante anónimo.
    app = FastAPI(title="Corpus Carvajal", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.handlers = handlers
    app.add_middleware(OwnerSessionMiddleware)
    app.add_middleware(SecurityMiddleware)
    app.add_middleware(RequestContextMiddleware)
    app.mount("/static", StaticFiles(directory=APP_DIR / "static", check_dir=False), name="static")
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(auth_router)
    return app


app = create_app()
