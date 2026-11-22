"""Validación de URLs y cliente HTTP seguro para la captura web.

La captura la dispara cualquier visitante de la pestaña Configuración, así que
el servidor no puede pedir una URL arbitraria: sería un SSRF. Este módulo
rechaza esquemas inusuales, credenciales embebidas, puertos fuera de 80/443 e
IPs privadas, loopback, link-local o reservadas, y revalida cada salto de una
redirección. Además, fija la IP ya validada en la conexión: si ``requests``
volviera a resolver el nombre, un cambio de DNS entre la comprobación y la
petición (*DNS rebinding*) podría llevar la conexión a una IP privada.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from contextvars import ContextVar
from typing import Any, cast
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.util.connection import create_connection

from carvajal.config import (
    BASE_URL,
    REQUEST_DELAY_SECONDS,
    RESPECT_ROBOTS,
    TIMEOUT_SECONDS,
    USER_AGENT,
)
from carvajal.fetching import FetchError, RobotsDenied, WebClient, normalize_robots

__all__ = [
    "SafeWebClient",
    "UrlRejected",
    "resolve_capture_target",
    "validate_capture_url",
]

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_PORTS = {80, 443}
MAX_URL_CHARS = 2048
_MAX_ROBOTS_BYTES = 512 * 1024
_CHUNK_SIZE = 64 * 1024
_PINNED_IP: ContextVar[str] = ContextVar("carvajal_pinned_ip")


class UrlRejected(FetchError, ValueError):
    """URL rechazada por la política de captura (SSRF, esquema o dominio)."""


def _normalize_domains(domains: Iterable[str]) -> tuple[str, ...]:
    """Normaliza la allowlist: minúsculas, sin esquema, ruta ni puntos iniciales."""

    normalized: list[str] = []
    for domain in domains:
        value = (domain or "").strip().lower()
        if not value:
            continue
        if "://" in value:
            value = value.split("://", 1)[1]
        value = value.split("/", 1)[0].lstrip(".")
        if value:
            normalized.append(value)
    return tuple(normalized)


def _domain_allowed(host: str, allowed: tuple[str, ...]) -> bool:
    """Acepta el dominio exacto o cualquiera de sus subdominios."""

    return any(host == domain or host.endswith(f".{domain}") for domain in allowed)


def _resolve(host: str, port: int) -> list[str]:
    """Resuelve el host y devuelve todas sus direcciones."""

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as error:
        raise UrlRejected(f"No se pudo resolver el host {host!r}.") from error
    return [str(info[4][0]) for info in infos]


def _reject_private(addresses: Iterable[str]) -> None:
    """Rechaza cualquier dirección que no sea pública."""

    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError as error:
            raise UrlRejected(f"Dirección IP inválida: {raw!r}.") from error
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise UrlRejected(f"La URL apunta a una dirección no pública ({address}).")


def resolve_capture_target(
    raw: str,
    *,
    allowed_domains: Iterable[str] = (),
    allow_private: bool = False,
    max_length: int = MAX_URL_CHARS,
) -> tuple[str, str]:
    """Valida la URL y devuelve ``(url, ip)`` con la IP que se debe usar.

    La IP se resuelve una sola vez y el cliente HTTP la fija en la conexión.
    ``allow_private`` existe solo para desarrollo y pruebas: permite loopback,
    redes privadas y puertos no estándar, pero sigue resolviendo el host.
    """

    url = (raw or "").strip()
    if not url:
        raise UrlRejected("La URL está vacía.")
    if len(url) > max_length:
        raise UrlRejected(f"La URL supera {max_length} caracteres.")

    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise UrlRejected(f"URL mal formada: {raw!r}.") from error

    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise UrlRejected("Solo se admiten URLs http o https.")
    if not host:
        raise UrlRejected(f"La URL no tiene host: {raw!r}.")
    if parsed.username or parsed.password:
        raise UrlRejected("La URL no puede incluir usuario ni contraseña.")
    if port is not None and port not in _ALLOWED_PORTS and not allow_private:
        raise UrlRejected("La captura solo admite los puertos 80 y 443.")

    normalized_host = host.rstrip(".").lower()
    allowed = _normalize_domains(allowed_domains)
    if allowed and not _domain_allowed(normalized_host, allowed):
        raise UrlRejected(f"El dominio {normalized_host!r} no está permitido.")

    default_port = port or (443 if parsed.scheme.lower() == "https" else 80)
    addresses = _resolve(normalized_host, default_port)
    if not allow_private:
        _reject_private(addresses)
    if not addresses:
        raise UrlRejected(f"No se pudo resolver el host {normalized_host!r}.")
    # Preferir IPv4: el contenedor puede no tener salida IPv6.
    addresses.sort(key=lambda address: ":" in address)
    return url, addresses[0]


def validate_capture_url(
    raw: str,
    *,
    allowed_domains: Iterable[str] = (),
    allow_private: bool = False,
    max_length: int = MAX_URL_CHARS,
) -> str:
    """Devuelve la URL normalizada o lanza ``UrlRejected``."""

    return resolve_capture_target(
        raw,
        allowed_domains=allowed_domains,
        allow_private=allow_private,
        max_length=max_length,
    )[0]


class _PinnedConnectionMixin:
    """Conecta a ``pinned_ip`` conservando el host original para Host/SNI."""

    pinned_ip: str
    port: int
    timeout: Any
    source_address: Any
    socket_options: Any

    def _new_conn(self) -> socket.socket:
        return create_connection(
            (self.pinned_ip, self.port),
            self.timeout,
            self.source_address,
            self.socket_options,
        )


class _PinnedHTTPConnection(_PinnedConnectionMixin, HTTPConnection):
    """Conexión HTTP que usa la IP validada en lugar de resolver el host."""

    def __init__(self, *args: Any, pinned_ip: str, **kwargs: Any) -> None:
        self.pinned_ip = pinned_ip
        super().__init__(*args, **kwargs)


class _PinnedHTTPSConnection(_PinnedConnectionMixin, HTTPSConnection):
    """Conexión HTTPS con SNI y certificado del host, pero IP fijada."""

    def __init__(self, *args: Any, pinned_ip: str, **kwargs: Any) -> None:
        self.pinned_ip = pinned_ip
        super().__init__(*args, **kwargs)


class _PinnedHTTPConnectionPool(HTTPConnectionPool):
    """Pool HTTP que propaga la IP fijada a cada conexión."""

    ConnectionCls = cast(Any, _PinnedHTTPConnection)

    def __init__(self, host: str, port: int | None = None, **kwargs: Any) -> None:
        self._pinned_ip = _PINNED_IP.get()
        super().__init__(host, port, **kwargs)

    def _new_conn(self) -> Any:
        return _PinnedHTTPConnection(
            host=self.host,
            port=self.port,
            timeout=self.timeout,
            pinned_ip=self._pinned_ip,
            **self.conn_kw,
        )


class _PinnedHTTPSConnectionPool(HTTPSConnectionPool):
    """Pool HTTPS que propaga la IP fijada a cada conexión."""

    ConnectionCls = cast(Any, _PinnedHTTPSConnection)

    def __init__(self, host: str, port: int | None = None, **kwargs: Any) -> None:
        self._pinned_ip = _PINNED_IP.get()
        super().__init__(host, port, **kwargs)

    def _new_conn(self) -> Any:
        return _PinnedHTTPSConnection(
            host=self.host,
            port=self.port,
            timeout=self.timeout,
            pinned_ip=self._pinned_ip,
            **self.conn_kw,
        )


class _PinnedAdapter(HTTPAdapter):
    """Adaptador de ``requests`` que fija la IP validada en sus pools."""

    def __init__(self, pinned_ip: str) -> None:
        self._pinned_ip = pinned_ip
        super().__init__(max_retries=0)

    def init_poolmanager(
        self,
        connections: int,
        maxsize: int,
        block: bool = False,
        **pool_kwargs: Any,
    ) -> None:
        self.poolmanager = urllib3.PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            **pool_kwargs,
        )
        self.poolmanager.pool_classes_by_scheme = cast(
            Any,
            {
                "http": _PinnedHTTPConnectionPool,
                "https": _PinnedHTTPSConnectionPool,
            },
        )

    def send(
        self,
        request: Any,
        stream: bool = False,
        timeout: Any = None,
        verify: bool | str = True,
        cert: Any = None,
        proxies: Any = None,
    ) -> requests.Response:
        """Fija la IP en el contexto mientras ``requests`` crea el pool."""

        token = _PINNED_IP.set(self._pinned_ip)
        try:
            return super().send(
                request,
                stream=stream,
                timeout=timeout,
                verify=verify,
                cert=cert,
                proxies=proxies,
            )
        finally:
            _PINNED_IP.reset(token)


class SafeWebClient(WebClient):
    """``WebClient`` que valida la URL antes de cada petición y redirección.

    ``capture`` llama primero a ``can_fetch`` para consultar ``robots.txt``; por
    eso la validación también vive ahí: sin ella, el ``robots.txt`` de un host
    privado sería la primera petición y el SSRF ocurriría antes de ``fetch``.
    """

    def __init__(
        self,
        *,
        allowed_domains: Iterable[str] = (),
        allow_private: bool = False,
        max_bytes: int = 5_000_000,
        max_redirects: int = 5,
        base_url: str = BASE_URL,
        timeout: int = TIMEOUT_SECONDS,
        delay: float = REQUEST_DELAY_SECONDS,
        respect_robots: bool = RESPECT_ROBOTS,
    ) -> None:
        super().__init__(
            base_url=base_url,
            timeout=timeout,
            delay=delay,
            respect_robots=respect_robots,
        )
        # Un proxy del entorno podría saltarse la IP fijada.
        self.session.trust_env = False
        self._allowed_domains = _normalize_domains(allowed_domains)
        self._allow_private = allow_private
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects

    def can_fetch(self, url: str) -> bool:
        """Valida la URL y consulta ``robots.txt`` con la carga segura.

        No se delega en ``WebClient.can_fetch`` para que la consulta de
        ``robots.txt`` pase siempre por ``_load_robots``: ese método usa
        ``_get_raw`` con timeout, tope de bytes y revalidación de redirecciones.
        """

        try:
            validate_capture_url(
                url,
                allowed_domains=self._allowed_domains,
                allow_private=self._allow_private,
            )
        except UrlRejected as error:
            raise RobotsDenied(str(error)) from error
        if not self.respect_robots:
            return True
        return self._robots_for(url).can_fetch(USER_AGENT, url)

    def _robots_for(self, url: str) -> RobotFileParser:
        """Devuelve el parser del host, cargándolo una sola vez."""

        host = urlparse(url).netloc
        parser = self._robots.get(host)
        if parser is None:
            parser = self._load_robots(url)
            self._robots[host] = parser
        return parser

    def fetch(self, url: str) -> str:
        """Descarga la página si ``robots.txt`` lo permite y con límite de bytes."""

        validate_capture_url(
            url,
            allowed_domains=self._allowed_domains,
            allow_private=self._allow_private,
        )
        if self.respect_robots and not self._robots_for(url).can_fetch(USER_AGENT, url):
            raise RobotsDenied(f"robots.txt excluye {url}")
        return self._get_raw(url, max_bytes=self._max_bytes)

    def _load_robots(self, url: str) -> RobotFileParser:
        """Carga ``robots.txt`` con la misma validación y límite que las páginas.

        ``WebClient._load_robots`` sigue redirecciones y no limita el tamaño:
        un host público podría redirigir el ``robots.txt`` a una IP privada o
        enviar un archivo enorme. Aquí se usa ``_get_raw``, que revalida cada
        salto y corta la lectura.
        """

        parser = RobotFileParser()
        robots_url = urljoin(url, "/robots.txt")
        try:
            text = self._get_raw(robots_url, max_bytes=_MAX_ROBOTS_BYTES)
        except (FetchError, requests.RequestException):
            parser.parse([])
        else:
            parser.parse(normalize_robots(text))
        return parser

    def _get_raw(self, url: str, *, max_bytes: int) -> str:
        """Descarga con redirecciones manuales, validación por salto y tope de bytes."""

        current = url
        for _ in range(self._max_redirects + 1):
            current, ip = resolve_capture_target(
                current,
                allowed_domains=self._allowed_domains,
                allow_private=self._allow_private,
            )
            self._throttle(current)
            session = self._pinned_session(ip)
            try:
                try:
                    response = session.get(
                        current,
                        timeout=self.timeout,
                        allow_redirects=False,
                        stream=True,
                    )
                except requests.RequestException as error:
                    raise FetchError(f"{current}: {error}") from error

                with response:
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        if not location:
                            raise FetchError(f"{current}: redirección sin destino.")
                        current = urljoin(current, location)
                        continue
                    try:
                        response.raise_for_status()
                    except requests.RequestException as error:
                        raise FetchError(f"{current}: {error}") from error
                    return _read_limited(response, max_bytes)
            finally:
                session.close()

        raise FetchError(f"{url}: demasiadas redirecciones.")

    def _pinned_session(self, ip: str) -> requests.Session:
        """Sesión de un solo salto que conecta a ``ip`` y conserva el Host."""

        session = requests.Session()
        session.trust_env = False
        session.headers.update(self.session.headers)
        session.cookies.update(self.session.cookies)
        adapter = _PinnedAdapter(ip)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session


def _read_limited(response: requests.Response, limit: int) -> str:
    """Lee el cuerpo por bloques y aborta si supera ``limit`` bytes."""

    declared = response.headers.get("content-length", "").strip()
    if declared:
        try:
            if int(declared) > limit:
                raise FetchError(f"{response.url}: la respuesta supera {limit} bytes.")
        except ValueError:
            pass

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
        total += len(chunk)
        if total > limit:
            raise FetchError(f"{response.url}: la respuesta supera {limit} bytes.")
        chunks.append(chunk)

    encoding = response.encoding or "utf-8"
    return b"".join(chunks).decode(encoding, errors="replace")
