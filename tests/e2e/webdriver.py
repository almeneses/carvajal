"""Cliente W3C WebDriver mínimo sobre httpx para hablar con geckodriver (sin Selenium).

Solo cubre lo que usa el ensayo de WhatsApp real (docs/ensayo.md): abrir una pestaña propia
en un Firefox que ya está abierto (``geckodriver --connect-existing``), navegar, buscar
elementos por CSS, escribir con teclas especiales, ejecutar JavaScript y cerrar esa pestaña.

Semántica de ``--connect-existing``: geckodriver no lanzó Firefox, así que no es dueño del
proceso. Comprobado el 2026-11-25 con geckodriver 0.37.1 y Firefox 159 sobre un perfil
aislado: ni ``DELETE /session/{id}`` ni terminar geckodriver cierran el navegador, y
Marionette acepta una conexión nueva después. Aun así el ensayo **no** borra la sesión
(el comportamiento ha cambiado entre versiones y el Firefox es el del owner): cierra solo
su pestaña, suelta el cliente HTTP y termina geckodriver; Marionette libera la conexión y
las pestañas del usuario quedan intactas. ``Firefox.conectar`` lo hace así.
"""

from __future__ import annotations

import socket
import subprocess
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx

# Clave W3C de una referencia a elemento (WebDriver §12.1).
ELEMENTO = "element-6066-11e4-a52e-4f735466cecf"
# Puntos de código de las teclas especiales (WebDriver §17.4.2).
ENTER = ""

T = TypeVar("T")


class WebDriverError(RuntimeError):
    """Error con el formato W3C ``{"value": {"error", "message"}}``."""

    def __init__(self, error: str, mensaje: str, status: int) -> None:
        super().__init__(f"{error} (HTTP {status}): {mensaje}")
        self.error = error
        self.mensaje = mensaje
        self.status = status


def puerto_libre() -> int:
    with socket.socket() as libre:
        libre.bind(("127.0.0.1", 0))
        return int(libre.getsockname()[1])


def puerto_abierto(puerto: int, host: str = "127.0.0.1", timeout: float = 2) -> bool:
    try:
        with socket.create_connection((host, puerto), timeout=timeout):
            return True
    except OSError:
        return False


@dataclass
class Sesion:
    """Una sesión W3C WebDriver: ``POST /session`` y los comandos que cuelgan de ella."""

    url_driver: str
    capacidades: dict[str, Any] = field(default_factory=dict)
    timeout: float = 60
    cliente: httpx.Client = field(init=False)
    id: str = field(init=False, default="")

    def __post_init__(self) -> None:
        self.cliente = httpx.Client(
            base_url=self.url_driver, timeout=httpx.Timeout(self.timeout, connect=5)
        )
        respuesta = self._pedir("POST", "/session", {"capabilities": {"alwaysMatch": {}}})
        self.id = str(respuesta["sessionId"])

    # --- transporte ---------------------------------------------------------------------

    def _pedir(self, metodo: str, ruta: str, cuerpo: dict[str, Any] | None = None) -> Any:
        if metodo == "POST" and cuerpo is None:
            cuerpo = {}
        respuesta = self.cliente.request(metodo, ruta, json=cuerpo)
        try:
            valor = respuesta.json().get("value")
        except ValueError as error:
            raise WebDriverError(
                "invalid response", respuesta.text[:200], respuesta.status_code
            ) from error
        if respuesta.status_code >= 400 or (isinstance(valor, dict) and "error" in valor):
            datos = valor if isinstance(valor, dict) else {}
            raise WebDriverError(
                str(datos.get("error", "unknown error")),
                str(datos.get("message", ""))[:300],
                respuesta.status_code,
            )
        return valor

    def comando(self, metodo: str, ruta: str, cuerpo: dict[str, Any] | None = None) -> Any:
        return self._pedir(metodo, f"/session/{self.id}{ruta}", cuerpo)

    # --- ventanas -----------------------------------------------------------------------

    def ventanas(self) -> list[str]:
        return [str(h) for h in self.comando("GET", "/window/handles")]

    def ventana_actual(self) -> str:
        return str(self.comando("GET", "/window"))

    def nueva_ventana(self, tipo: str = "tab") -> str:
        """Abre una pestaña (o ventana) nueva y la deja como contexto actual."""

        valor = self.comando("POST", "/window/new", {"type": tipo})
        manejador = str(valor["handle"])
        self.cambiar_a(manejador)
        return manejador

    def cambiar_a(self, manejador: str) -> None:
        self.comando("POST", "/window", {"handle": manejador})

    def cerrar_ventana(self) -> list[str]:
        """Cierra solo la ventana actual; devuelve las que quedan abiertas."""

        return [str(h) for h in self.comando("DELETE", "/window")]

    # --- navegación y elementos ---------------------------------------------------------

    def navegar(self, url: str) -> None:
        self.comando("POST", "/url", {"url": url})

    def url_actual(self) -> str:
        return str(self.comando("GET", "/url"))

    def buscar(self, css: str) -> str | None:
        """Id del primer elemento que casa con ``css``, o ``None`` si no hay ninguno."""

        encontrados = self.buscar_todos(css)
        return encontrados[0] if encontrados else None

    def buscar_todos(self, css: str) -> list[str]:
        valor = self.comando("POST", "/elements", {"using": "css selector", "value": css})
        return [str(elemento[ELEMENTO]) for elemento in valor]

    def clic(self, elemento: str) -> None:
        self.comando("POST", f"/element/{elemento}/click")

    def escribir(self, elemento: str, texto: str) -> None:
        """Teclea ``texto`` en el elemento; admite teclas especiales como ``ENTER``."""

        self.comando("POST", f"/element/{elemento}/value", {"text": texto})

    def ejecutar(self, script: str, *argumentos: Any) -> Any:
        """``execute/sync``: el cuerpo de una función JS que recibe ``arguments``."""

        return self.comando("POST", "/execute/sync", {"script": script, "args": list(argumentos)})

    def referencia(self, elemento: str) -> dict[str, str]:
        """Referencia W3C para pasar un elemento como argumento de ``ejecutar``."""

        return {ELEMENTO: elemento}

    def esperar(
        self,
        condicion: Callable[[], T | None],
        *,
        timeout: float,
        intervalo: float = 0.5,
    ) -> T | None:
        """Repite ``condicion`` hasta que devuelva algo verdadero o venza ``timeout``."""

        limite = time.monotonic() + timeout
        while True:
            try:
                resultado = condicion()
            except WebDriverError as error:
                if error.error not in {"stale element reference", "no such element"}:
                    raise
                resultado = None
            if resultado:
                return resultado
            if time.monotonic() >= limite:
                return None
            time.sleep(intervalo)

    def soltar(self) -> None:
        """Cierra el cliente HTTP sin ``DELETE /session`` (ver docstring del módulo)."""

        self.cliente.close()


@dataclass
class Firefox:
    """geckodriver conectado a un Firefox ya abierto con ``--marionette``."""

    marionette: int = 2828
    binario: str = "geckodriver"

    @contextmanager
    def conectar(self, *, timeout: float = 20) -> Generator[Sesion]:
        """Arranca geckodriver, abre la sesión y, al salir, suelta el navegador sin cerrarlo."""

        puerto = puerto_libre()
        proceso = subprocess.Popen(
            [
                self.binario,
                "--connect-existing",
                "--marionette-port",
                str(self.marionette),
                "--port",
                str(puerto),
                "--log",
                "error",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        sesion: Sesion | None = None
        try:
            url = f"http://127.0.0.1:{puerto}"
            limite = time.monotonic() + timeout
            while not puerto_abierto(puerto, timeout=0.5):
                if proceso.poll() is not None or time.monotonic() >= limite:
                    raise RuntimeError("geckodriver no abrió su puerto")
                time.sleep(0.2)
            sesion = Sesion(url)
            yield sesion
        finally:
            if sesion is not None:
                sesion.soltar()
            proceso.terminate()
            try:
                proceso.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proceso.kill()
