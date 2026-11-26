"""Arranque: ``python -m apps.mcp`` (en Docker, el servicio ``mcp`` de compose.prod.yml).

Escucha en ``MCP_HOST:MCP_PORT`` (``127.0.0.1:8765`` por defecto). Dentro del contenedor
se usa ``0.0.0.0`` y compose publica el puerto solo en el loopback del host.
"""

import uvicorn

from apps.api.observability import configure_observability
from apps.api.runtime import warm_up_embeddings
from apps.mcp.server import build_app, build_server, default_bridge
from carvajal.infrastructure.settings import Settings


def main() -> None:
    settings = Settings()
    configure_observability(settings)
    app = build_app(build_server(default_bridge), settings.mcp_token)
    if settings.embed_warm_up:
        # WhatsApp corre el agente en este proceso: su primera búsqueda tampoco espera a
        # que Ollama cargue el modelo. En segundo plano y sin fallar si Ollama no está.
        warm_up_embeddings()
    uvicorn.run(app, host=settings.mcp_host, port=settings.mcp_port, access_log=False)


if __name__ == "__main__":
    main()
