"""Cliente HTTP educado para el sitio del corpus."""

import requests

from carvajal.config import BASE_URL, TIMEOUT_SECONDS, USER_AGENT


class FetchError(RuntimeError):
    """Error al descargar una página del sitio."""


class WebClient:
    """Sesión única con User-Agent identificable y timeout de conexión."""

    def __init__(self, base_url: str = BASE_URL, timeout: int = TIMEOUT_SECONDS) -> None:
        """Inicializa la sesión HTTP con la política de contacto educado."""

        self.base_url = base_url
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def fetch(self, url: str) -> str:
        """Descarga el contenido HTML de una URL pública.

        Args:
            url: URL absoluta o relativa a la base del sitio.

        Returns:
            HTML crudo de la respuesta.

        Raises:
            FetchError: Si la respuesta no es exitosa.
        """

        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.text
