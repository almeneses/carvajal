"""Clientes del ensayo general (docs/ensayo.md): web, WhatsApp (MCP y webhook) y SSH.

Hablan con una app ya desplegada (local en Docker o producción) exactamente como lo hacen
el navegador (``/chat/stream``, ``/chat/foto``) y OpenClaw (MCP ``consultar_carvajal``).
No importan nada de la app: así el ensayo prueba lo desplegado y no el checkout.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import shlex
import socket
import subprocess
import time
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

# Avisos de OpenSSH que no son salida del comando remoto.
_RUIDO_SSH = re.compile(r"post-quantum|store now|openssh\.com/pq|may need to be upgraded|Warning:")
VISITANTE = "carvajal_visitante"


class Secreto(str):
    """Cadena que nunca se muestra: pytest imprime los argumentos y valores al fallar."""

    def __new__(cls, valor: object = "") -> Secreto:
        # ``str(otro_secreto)`` daría «<oculto>»: se copia el valor real.
        crudo = str.__str__(valor) if isinstance(valor, str) else str(valor)
        return super().__new__(cls, crudo)

    def __repr__(self) -> str:
        return "<oculto>" if self else "''"

    __str__ = __repr__

    @property
    def valor(self) -> str:
        return str.__str__(self)


def _bool(nombre: str, defecto: bool = False) -> bool:
    valor = os.environ.get(nombre, "").strip().lower()
    return defecto if not valor else valor in {"1", "true", "si", "sí", "yes"}


@dataclass(frozen=True)
class Config:
    """Configuración del ensayo, leída del entorno (``.env.ensayo``, ver docs/ensayo.md)."""

    base_url: str
    target: str  # "local" o "prod"
    run_id: str
    app_ssh: str
    app_ssh_port: str
    app_dir: str
    openclaw_ssh: str
    openclaw_user: str
    mcp_url: str
    mcp_token: Secreto = field(repr=False)
    webhook_secret: Secreto = field(repr=False)
    wa_fuera: str
    cotizar: str  # "todas", "una" o "ninguna"
    media_dir: str
    firefox_marionette: str
    wa_bot: str

    @classmethod
    def desde_entorno(cls) -> Config:
        base = os.environ.get("ENSAYO_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        host = urlparse(base).hostname or ""
        local = host in {"127.0.0.1", "localhost", "::1"}
        target = os.environ.get("ENSAYO_TARGET", "local" if local else "prod")
        return cls(
            base_url=base,
            target=target,
            run_id=os.environ.get("ENSAYO_RUN_ID") or time.strftime("%Y%m%d%H%M%S", time.gmtime()),
            app_ssh=os.environ.get("ENSAYO_APP_SSH", ""),
            app_ssh_port=os.environ.get("ENSAYO_APP_SSH_PORT", "22"),
            app_dir=os.environ.get("ENSAYO_APP_DIR", "/opt/carvajal"),
            openclaw_ssh=os.environ.get("ENSAYO_OPENCLAW_SSH", ""),
            openclaw_user=os.environ.get("ENSAYO_OPENCLAW_USER", "marko"),
            mcp_url=os.environ.get("ENSAYO_MCP_URL", "http://127.0.0.1:8765/mcp"),
            mcp_token=Secreto(
                os.environ.get("ENSAYO_MCP_TOKEN", "dev-mcp-solo-local" if local else "")
            ),
            webhook_secret=Secreto(os.environ.get("ENSAYO_WEBHOOK_SECRET", "")),
            wa_fuera=os.environ.get("ENSAYO_WA_FUERA", "+15550100999"),
            cotizar=os.environ.get("ENSAYO_COTIZAR", "todas" if local else "una"),
            media_dir=os.environ.get("ENSAYO_MEDIA_DIR", "data/openclaw-media"),
            firefox_marionette=os.environ.get("ENSAYO_FIREFOX_MARIONETTE", ""),
            wa_bot=os.environ.get("ENSAYO_WA_BOT", ""),
        )

    @property
    def prod(self) -> bool:
        return self.target == "prod"


# --- SSH -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Ssh:
    """Comandos remotos por SSH, sin pedir contraseña (BatchMode) y con tiempo límite."""

    destino: str
    puerto: str = "22"

    def base(self) -> list[str]:
        return [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=15",
            "-p",
            self.puerto,
            self.destino,
        ]

    def run(self, comando: str, *, timeout: float = 120) -> subprocess.CompletedProcess[str]:
        proceso = subprocess.run(
            [*self.base(), comando],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        stderr = "\n".join(
            linea for linea in proceso.stderr.splitlines() if not _RUIDO_SSH.search(linea)
        )
        return subprocess.CompletedProcess(proceso.args, proceso.returncode, proceso.stdout, stderr)

    def como(
        self, usuario: str, comando: str, *, timeout: float = 120
    ) -> subprocess.CompletedProcess[str]:
        """Corre ``comando`` como ``usuario`` (``sudo -n``), con su sesión de systemd --user."""

        interno = f"export XDG_RUNTIME_DIR=/run/user/$(id -u); {comando}"
        return self.run(f"sudo -n -u {usuario} -H bash -lc {shlex.quote(interno)}", timeout=timeout)

    def secreto(self, archivo: str, clave: str) -> Secreto:
        """Valor de ``clave`` en un .env remoto; nunca se imprime ni se guarda."""

        proceso = self.run(f"grep -E '^{clave}=' {shlex.quote(archivo)} | tail -n1")
        valor = proceso.stdout.strip().split("=", 1)[1] if "=" in proceso.stdout else ""
        return Secreto(valor.strip().strip("'\""))

    @contextmanager
    def tunel(self, remoto: int) -> Generator[int]:
        """Reenvía un puerto libre del loopback local a ``127.0.0.1:remoto`` del servidor."""

        with socket.socket() as libre:
            libre.bind(("127.0.0.1", 0))
            local = libre.getsockname()[1]
        proceso = subprocess.Popen(
            [
                *self.base()[:-1],
                "-N",
                "-o",
                "ExitOnForwardFailure=yes",
                "-L",
                f"127.0.0.1:{local}:127.0.0.1:{remoto}",
                self.destino,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            limite = time.monotonic() + 20
            while time.monotonic() < limite:
                if proceso.poll() is not None:
                    raise RuntimeError("el túnel SSH terminó antes de abrir el puerto")
                with socket.socket() as prueba:
                    if prueba.connect_ex(("127.0.0.1", local)) == 0:
                        break
                time.sleep(0.3)
            else:
                raise RuntimeError("el túnel SSH no abrió el puerto en 20 s")
            yield local
        finally:
            proceso.terminate()
            proceso.wait(timeout=10)


# --- Web --------------------------------------------------------------------------------


def parse_sse(texto: str) -> list[tuple[str, dict[str, Any]]]:
    """Eventos ``(nombre, datos)`` de una respuesta ``text/event-stream``."""

    eventos: list[tuple[str, dict[str, Any]]] = []
    for bloque in texto.replace("\r\n", "\n").split("\n\n"):
        nombre, datos = "message", []
        for linea in bloque.splitlines():
            if linea.startswith("event:"):
                nombre = linea.removeprefix("event:").strip()
            elif linea.startswith("data:"):
                datos.append(linea.removeprefix("data:").lstrip())
        if datos:
            eventos.append((nombre, json.loads("\n".join(datos))))
    return eventos


@dataclass
class Turno:
    """Lo que vio el navegador en un turno: texto, tarjetas, traza y cierre."""

    eventos: list[tuple[str, dict[str, Any]]]
    status: int = 200

    def _primero(self, nombre: str) -> dict[str, Any]:
        return next((datos for evento, datos in self.eventos if evento == nombre), {})

    @property
    def texto(self) -> str:
        partes: list[str] = []
        for evento, datos in self.eventos:
            if evento == "fallback":
                partes = [str(datos.get("text", ""))]
            elif evento == "token":
                partes.append(str(datos.get("text", "")))
        return "".join(partes)

    @property
    def done(self) -> dict[str, Any]:
        return self._primero("done")

    @property
    def error(self) -> dict[str, Any]:
        return self._primero("error")

    @property
    def traza(self) -> dict[str, Any]:
        return self._primero("trace")

    @property
    def tarjetas(self) -> str:
        return str(self._primero("products").get("html", ""))

    @property
    def nombres_tarjetas(self) -> list[str]:
        return re.findall(r'data-campo="producto-nombre">([^<]+)<', self.tarjetas)

    @property
    def flujo(self) -> dict[str, Any]:
        return self.done.get("flow") or {}

    @property
    def awaiting(self) -> str | None:
        return self.flujo.get("awaiting")

    def resumen(self) -> str:
        """Transcripción corta para los mensajes de fallo."""

        return (
            f"status={self.status} route={self.done.get('route')} awaiting={self.awaiting} "
            f"error={self.error.get('message')!r}\n--- texto ---\n{self.texto[:1500]}"
        )


@dataclass
class Web:
    """Un visitante del chat web: su cookie fija, su CSRF y sus hilos."""

    base_url: str
    visitante: str
    timeout: float = 120
    cliente: httpx.Client = field(init=False)
    csrf: str = field(init=False, default="")

    def __post_init__(self) -> None:
        self.cliente = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout, connect=10),
            cookies={VISITANTE: self.visitante},
            follow_redirects=False,
        )
        respuesta = self.cliente.get("/api/csrf")
        respuesta.raise_for_status()
        self.csrf = respuesta.json()["token"]

    @staticmethod
    def visitante_para(semilla: str) -> str:
        return hashlib.sha256(semilla.encode()).hexdigest()

    def nuevo_hilo(self) -> str:
        respuesta = self.cliente.post("/api/chat/new", data={"csrf_token": self.csrf})
        respuesta.raise_for_status()
        return str(respuesta.json()["thread_id"])

    def turno(self, pregunta: str, hilo: str = "", *, voz: bool = False) -> Turno:
        datos = {
            "question": pregunta,
            "csrf_token": self.csrf,
            "client_turn_id": uuid.uuid4().hex,
        }
        if hilo:
            datos["thread_id"] = hilo
        if voz:
            datos["voz"] = "1"
        respuesta = self.cliente.post("/chat/stream", data=datos)
        return Turno(parse_sse(respuesta.text), respuesta.status_code)

    def foto(self, contenido: bytes, *, texto: str = "", hilo: str = "") -> Turno:
        datos = {"texto": texto, "client_turn_id": uuid.uuid4().hex}
        if hilo:
            datos["thread_id"] = hilo
        respuesta = self.cliente.post(
            "/chat/foto",
            data=datos,
            files={"foto": ("plato.jpg", contenido, "image/jpeg")},
            headers={"X-CSRF-Token": self.csrf},
        )
        return Turno(parse_sse(respuesta.text), respuesta.status_code)

    def cerrar(self) -> None:
        self.cliente.close()


# --- WhatsApp: MCP (lo que llama OpenClaw) y webhook firmado ----------------------------


@dataclass(frozen=True)
class Mcp:
    """Cliente MCP de ``consultar_carvajal`` con el mismo transporte que usa OpenClaw."""

    url: str
    token: str = field(repr=False)
    timeout: float = 150

    async def _llamar(self, metodo: str, argumentos: dict[str, Any] | None) -> Any:
        # El SDK de MCP trae su propio httpx (``httpx2``): el timeout debe ser de ese.
        import httpx2
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client

        from mcp import Client

        http = create_mcp_http_client(
            headers={"Authorization": f"Bearer {str.__str__(self.token)}"},
            timeout=httpx2.Timeout(self.timeout, connect=10),
        )
        async with Client(
            streamable_http_client(self.url, http_client=http),
            mode="legacy",
            read_timeout_seconds=self.timeout,
        ) as cliente:
            if metodo == "list_tools":
                return await cliente.list_tools()
            return await cliente.call_tool("consultar_carvajal", argumentos or {})

    def herramientas(self) -> list[Any]:
        return list(asyncio.run(self._llamar("list_tools", None)).tools)

    def consultar(
        self,
        mensaje: str,
        conversacion: str,
        *,
        nueva: bool = False,
        voz: bool = False,
        foto: str = "",
    ) -> str:
        argumentos: dict[str, Any] = {"mensaje": mensaje, "conversacion": conversacion}
        if nueva:
            argumentos["nueva"] = True
        if voz:
            argumentos["voz"] = True
        if foto:
            argumentos["foto"] = foto
        resultado = asyncio.run(self._llamar("call_tool", argumentos))
        return "\n".join(str(getattr(bloque, "text", "")) for bloque in resultado.content).strip()


def firmar_webhook(secreto: str, cuerpo: bytes, momento: int | None = None) -> dict[str, str]:
    """Cabeceras ``X-Carvajal-*`` del webhook (docs/webhooks.md)."""

    sello = str(int(time.time()) if momento is None else momento)
    firma = hmac.new(
        str.__str__(secreto).encode(), f"{sello}.".encode() + cuerpo, hashlib.sha256
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Carvajal-Timestamp": sello,
        "X-Carvajal-Signature": f"sha256={firma}",
    }
