"""Validación de URLs y cliente HTTP seguro para la captura web.

La captura la dispara cualquier visitante de la pestaña Configuración, así que
el servidor no puede pedir una URL arbitraria: sería un SSRF. Este módulo
rechaza esquemas inusuales, credenciales embebidas, puertos fuera de 80/443 e
IPs privadas, loopback, link-local o reservadas, y revalida cada salto de una
redirección. La resolución DNS y la petición real las hace ``requests`` por
separado; queda un riesgo residual de *DNS rebinding* que se documenta y se
mitiga con el límite de bytes y el timeout.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

from carvajal.config import BASE_URL, REQUEST_DELAY_SECONDS, RESPECT_ROBOTS, TIMEOUT_SECONDS
from carvajal.fetching import FetchError, RobotsDenied, WebClient, normalize_robots

__all__ = ["SafeWebClient", "UrlRejected", "validate_capture_url"]

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_PORTS = {80, 443}
MAX_URL_CHARS = 2048
_MAX_ROBOTS_BYTES = 512 * 1024
_CHUNK_SIZE = 64 * 1024


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


def validate_capture_url(
    raw: str,
    *,
    allowed_domains: Iterable[str] = (),
    allow_private: bool = False,
    max_length: int = MAX_URL_CHARS,
) -> str:
    """Devuelve la URL normalizada o lanza ``UrlRejected``.

    ``allow_private`` existe solo para desarrollo y pruebas: permite loopback,
    redes privadas y puertos no estándar. En producción debe quedar apagado.
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

    if not allow_private:
        default_port = port or (443 if parsed.scheme.lower() == "https" else 80)
        _reject_private(_resolve(normalized_host, default_port))
    return url


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
        self._allowed_domains = _normalize_domains(allowed_domains)
        self._allow_private = allow_private
        self._max_bytes = max_bytes
        self._max_redirects = max_redirects

    def can_fetch(self, url: str) -> bool:
        """Valida la URL antes de descargar su ``robots.txt``."""

        try:
            validate_capture_url(
                url,
                allowed_domains=self._allowed_domains,
                allow_private=self._allow_private,
            )
        except UrlRejected as error:
            raise RobotsDenied(str(error)) from error
        return super().can_fetch(url)

    def fetch(self, url: str) -> str:
        """Descarga la página si ``robots.txt`` lo permite y con límite de bytes."""

        if not super().can_fetch(url):
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
            validate_capture_url(
                current,
                allowed_domains=self._allowed_domains,
                allow_private=self._allow_private,
            )
            self._throttle(current)
            try:
                response = self.session.get(
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

        raise FetchError(f"{url}: demasiadas redirecciones.")


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
