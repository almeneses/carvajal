"""``keep_alive``, caché LRU de consultas y calentamiento de los embeddings (2026-11-26).

``buscar_corpus`` tardaba 6 s de p50 en producción frente a 1,3 s en local: Ollama descarga
el modelo tras 5 minutos sin peticiones y el tráfico espaciado lo recargaba. Sin red.
"""

import threading
from collections.abc import Callable, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient

from carvajal.adapters.embeddings import CachedEmbedder, ollama, warm_up
from carvajal.adapters.embeddings.cache import WARM_UP_TEXT
from carvajal.infrastructure.settings import Settings


class CountingEmbedder:
    """Embedder falso que cuenta sus llamadas y puede fallar."""

    model = "nomic-embed-text"

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail

    def embed(self, text: str) -> Sequence[float]:
        self.calls.append(text)
        if self.fail:
            raise ConnectionError("Ollama caído")
        return [float(len(text)), 1.0]


def test_ollama_embedder_pide_keep_alive_de_24_horas_por_defecto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    captured: list[dict[str, object]] = []

    def build(**kwargs: object) -> object:
        captured.append(kwargs)
        return object()

    factory: Callable[..., object] = build
    monkeypatch.setattr(ollama, "OllamaEmbeddings", factory)

    # Act
    ollama.OllamaEmbedder()
    ollama.OllamaEmbedder(keep_alive=-1)

    # Assert
    assert [kwargs["keep_alive"] for kwargs in captured] == [86_400, -1]
    assert Settings(_env_file=None).ollama_embed_keep_alive_s == 86_400


def test_la_cache_devuelve_el_vector_guardado_sin_llamar_de_nuevo() -> None:
    # Arrange
    inner = CountingEmbedder()
    cached = CachedEmbedder(inner, maxsize=4)

    # Act
    first = cached.embed("historia de Carvajal")
    second = cached.embed("historia de Carvajal")
    second.append(99.0)  # una copia: no altera el guardado
    third = cached.embed("historia de Carvajal")

    # Assert
    assert inner.calls == ["historia de Carvajal"]
    assert first == third == [20.0, 1.0]
    assert (cached.hits, cached.misses) == (2, 1)
    assert cached.model == "nomic-embed-text"


def test_la_cache_esta_acotada_y_descarta_la_menos_usada() -> None:
    # Arrange
    inner = CountingEmbedder()
    cached = CachedEmbedder(inner, maxsize=2)
    cached.embed("a")
    cached.embed("bb")
    cached.embed("a")  # «a» pasa a ser la más reciente

    # Act
    cached.embed("ccc")  # descarta «bb»
    cached.embed("a")
    cached.embed("bb")

    # Assert
    assert len(cached) == 2
    assert inner.calls == ["a", "bb", "ccc", "bb"]


def test_un_fallo_no_se_guarda_en_la_cache() -> None:
    # Arrange
    inner = CountingEmbedder(fail=True)
    cached = CachedEmbedder(inner, maxsize=2)

    # Act / Assert
    for _ in range(2):
        with pytest.raises(ConnectionError):
            cached.embed("hola")
    assert inner.calls == ["hola", "hola"]
    assert len(cached) == 0


def test_con_tamano_cero_la_cache_no_guarda_nada() -> None:
    inner = CountingEmbedder()
    cached = CachedEmbedder(inner, maxsize=0)

    cached.embed("x")
    cached.embed("x")

    assert inner.calls == ["x", "x"]


def test_el_calentamiento_embebe_un_texto_corto_en_segundo_plano() -> None:
    # Arrange
    inner = CountingEmbedder()

    # Act
    thread = warm_up(lambda: inner)
    thread.join(timeout=5)

    # Assert
    assert thread.daemon is True
    assert inner.calls == [WARM_UP_TEXT]


def test_el_calentamiento_no_falla_si_ollama_no_responde() -> None:
    # Arrange: ni construir el embedder ni embeber deben tumbar el arranque.
    def broken() -> CountingEmbedder:
        raise RuntimeError("sin Ollama")

    waits: list[float] = []

    # Act
    threads = [
        warm_up(broken, retry_delays=(1, 2), sleep=waits.append),
        warm_up(lambda: CountingEmbedder(fail=True), retry_delays=(), sleep=waits.append),
    ]
    for thread in threads:
        thread.join(timeout=5)

    # Assert
    assert all(not thread.is_alive() for thread in threads)
    assert waits == [1, 2]


def test_el_calentamiento_reintenta_mientras_ollama_arranca() -> None:
    """Con ``service_started`` la app arranca antes que Ollama: el primer intento falla."""

    # Arrange: Ollama responde a partir del tercer intento.
    inner = CountingEmbedder(fail=True)
    waits: list[float] = []

    def wait(seconds: float) -> None:
        waits.append(seconds)
        if len(waits) == 2:
            inner.fail = False

    # Act
    thread = warm_up(lambda: inner, retry_delays=(5, 10, 15), sleep=wait)
    thread.join(timeout=5)

    # Assert: tres intentos, dos esperas y ninguna espera de más.
    assert inner.calls == [WARM_UP_TEXT] * 3
    assert waits == [5, 10]


def test_la_app_calienta_los_embeddings_al_arrancar(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    import apps.api.main as main

    started = threading.Event()
    monkeypatch.setattr(main, "warm_up_embeddings", started.set)
    monkeypatch.setenv("EMBED_WARM_UP", "true")
    app = main.create_app()

    # Act
    with TestClient(app) as client:
        client.get("/live")

    # Assert
    assert started.is_set()


def test_sin_la_bandera_la_app_no_calienta(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.api.main as main

    calls: list[Any] = []
    monkeypatch.setattr(main, "warm_up_embeddings", lambda: calls.append(1))
    app = main.create_app()  # EMBED_WARM_UP=false en conftest

    with TestClient(app) as client:
        client.get("/live")

    assert calls == []


def test_la_app_y_el_agente_comparten_el_embedder_de_consultas() -> None:
    from apps.api import runtime

    settings = Settings(_env_file=None)

    assert runtime.query_embedder_for(settings) is runtime.query_embedder_for(settings)
    assert runtime.query_embedder_for(settings).inner is not runtime.embedder()


class SlowEmbedder(CountingEmbedder):
    """Embedder falso que se bloquea hasta que la prueba lo suelta."""

    def __init__(self, *, fail: bool = False) -> None:
        super().__init__(fail=fail)
        self.entered = threading.Event()
        self.release = threading.Event()
        self._calls_lock = threading.Lock()

    def embed(self, text: str) -> Sequence[float]:
        with self._calls_lock:
            first = not self.calls
            self.calls.append(text)
        if first:
            self.entered.set()
            self.release.wait(5)
            if self.fail:
                raise ConnectionError("Ollama caído")
        return [float(len(text)), 1.0]


def _embed_in_threads(
    cached: CachedEmbedder, text: str, count: int
) -> tuple[list[threading.Thread], list[Any]]:
    results: list[Any] = [None] * count

    def run(index: int) -> None:
        try:
            results[index] = cached.embed(text)
        except ConnectionError as error:
            results[index] = error

    threads = [threading.Thread(target=run, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    return threads, results


def test_la_misma_consulta_en_varios_hilos_llega_una_vez_a_ollama() -> None:
    # Arrange: el primer hilo queda dentro de Ollama mientras llegan los demás.
    inner = SlowEmbedder()
    cached = CachedEmbedder(inner, maxsize=4)
    first = threading.Thread(target=cached.embed, args=("dónde comprar",))
    first.start()
    assert inner.entered.wait(5)
    threads, results = _embed_in_threads(cached, "dónde comprar", 3)

    # Act
    other = cached.embed("otra consulta")  # otra consulta no espera al cálculo en curso
    inner.release.set()
    for thread in [first, *threads]:
        thread.join(5)

    # Assert
    assert other == [13.0, 1.0]
    assert inner.calls.count("dónde comprar") == 1
    assert results == [[13.0, 1.0]] * 3
    assert cached.misses == 2


def test_si_el_calculo_en_curso_falla_quien_esperaba_lo_intenta_de_nuevo() -> None:
    # Arrange
    inner = SlowEmbedder(fail=True)
    cached = CachedEmbedder(inner, maxsize=4)
    errors: list[BaseException] = []

    def first_call() -> None:
        try:
            cached.embed("hola")
        except ConnectionError as error:
            errors.append(error)

    first = threading.Thread(target=first_call)
    first.start()
    assert inner.entered.wait(5)
    threads, results = _embed_in_threads(cached, "hola", 1)

    # Act
    inner.release.set()
    for thread in [first, *threads]:
        thread.join(5)

    # Assert: el fallo no se cachea ni se contagia; el segundo hilo obtiene su vector.
    assert len(errors) == 1
    assert results == [[4.0, 1.0]]
    assert inner.calls == ["hola", "hola"]
    assert len(cached) == 1
