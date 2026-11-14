"""Pruebas de la captura web: semillas, límites, slugs y cierre del pipeline."""

from pathlib import Path
from unittest.mock import Mock

from apps.api.web_capture import CaptureRequest, ingest_urls, resolve_seeds
from carvajal.infrastructure.settings import Settings


class FakeClient:
    """Fetcher de prueba: devuelve HTML sin tocar la red."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def can_fetch(self, url: str) -> bool:
        return True

    def fetch(self, url: str) -> str:
        self.urls.append(url)
        return (
            f"<html><title>{url}</title><body>"
            f"<h1>{url}</h1><p>Contenido de prueba para {url}.</p>"
            "</body></html>"
        )


def _settings(
    tmp_path: Path,
    *,
    seeds: tuple[str, ...] = (),
    external_seeds: tuple[str, ...] = (),
    capture_allowed_domains: tuple[str, ...] = (),
) -> Settings:
    return Settings(
        gold_dir=tmp_path / "gold",
        raw_dir=tmp_path / "raw",
        processed_dir=tmp_path / "processed",
        seeds=seeds,
        external_seeds=external_seeds,
        capture_allowed_domains=capture_allowed_domains,
    )


def test_resolve_seeds_pone_las_urls_explicitas_primero_y_aplica_el_limite(
    tmp_path: Path,
) -> None:
    # Arrange
    settings = _settings(
        tmp_path,
        seeds=("https://example.com/oficial",),
        external_seeds=("https://example.org/tercero",),
    )
    request = CaptureRequest(
        urls=("https://example.net/propia",),
        scope="todo",
        limit=2,
    )

    # Act
    seeds, errors = resolve_seeds(request, settings)

    # Assert
    assert seeds == ["https://example.net/propia", "https://example.com/oficial"]
    assert errors == []


def test_resolve_seeds_rechaza_mas_urls_explicitas_que_el_limite(tmp_path: Path) -> None:
    # Arrange
    settings = _settings(tmp_path)
    request = CaptureRequest(
        urls=("https://example.net/a", "https://example.net/b"),
        scope="manual",
        limit=1,
    )

    # Act
    seeds, errors = resolve_seeds(request, settings)

    # Assert
    assert seeds == []
    assert errors and "máximo de esta ejecución" in errors[0]


def test_resolve_seeds_rechaza_un_alcance_desconocido(tmp_path: Path) -> None:
    # Act
    seeds, errors = resolve_seeds(
        CaptureRequest(scope="inventado"),
        _settings(tmp_path),
    )

    # Assert
    assert seeds == []
    assert errors and "Alcance desconocido" in errors[0]


def test_ingest_urls_captura_importa_e_indexa_solo_los_slugs_nuevos(tmp_path: Path) -> None:
    # Arrange
    settings = _settings(tmp_path)
    client = FakeClient()
    importar = Mock(return_value=(1, 2))
    indexar = Mock(return_value=2)
    request = CaptureRequest(
        urls=("https://example.com/a",),
        scope="manual",
        limit=5,
    )

    # Act
    outcome = ingest_urls(
        request,
        settings=settings,
        client=client,
        importar=importar,
        indexar=indexar,
    )

    # Assert
    assert outcome.requested == 1
    assert outcome.written == 1
    assert outcome.captured == ("a",)
    assert outcome.documents == 1
    assert outcome.chunks == 2
    assert outcome.indexed == 2
    assert outcome.errors == ()
    assert (settings.gold_dir / "a.md").is_file()
    assert importar.call_args.args == (settings.gold_dir, {"a"})
    assert indexar.call_args.args == (settings.gold_dir, {"a"})
    assert client.urls == ["https://example.com/a"]


def test_ingest_urls_omite_la_indexacion_si_falla_postgres(tmp_path: Path) -> None:
    # Arrange
    settings = _settings(tmp_path)
    importar = Mock(side_effect=RuntimeError("sin conexión"))
    indexar = Mock(return_value=0)

    # Act
    outcome = ingest_urls(
        CaptureRequest(urls=("https://example.com/a",), scope="manual", limit=5),
        settings=settings,
        client=FakeClient(),
        importar=importar,
        indexar=indexar,
    )

    # Assert
    assert outcome.written == 1
    assert outcome.documents == 0
    assert outcome.indexed == 0
    assert any("PostgreSQL" in error for error in outcome.errors)
    indexar.assert_not_called()


def test_ingest_urls_reporta_el_fallo_de_qdrant_sin_perder_el_gold(tmp_path: Path) -> None:
    # Arrange
    settings = _settings(tmp_path)
    indexar = Mock(side_effect=RuntimeError("sin qdrant"))

    # Act
    outcome = ingest_urls(
        CaptureRequest(urls=("https://example.com/a",), scope="manual", limit=5),
        settings=settings,
        client=FakeClient(),
        importar=Mock(return_value=(1, 1)),
        indexar=indexar,
    )

    # Assert
    assert outcome.written == 1
    assert outcome.documents == 1
    assert outcome.indexed == 0
    assert any("Qdrant" in error for error in outcome.errors)
    assert (settings.gold_dir / "a.md").is_file()


def test_ingest_urls_rechaza_un_slug_que_ya_usa_otra_url(tmp_path: Path) -> None:
    # Arrange
    settings = _settings(tmp_path)
    settings.gold_dir.mkdir(parents=True)
    (settings.gold_dir / "a.md").write_text(
        "---\nurl: https://otro.example/a\ntitle: Otro\n---\n\nTexto.\n",
        encoding="utf-8",
    )
    client = FakeClient()

    # Act
    outcome = ingest_urls(
        CaptureRequest(urls=("https://example.com/a",), scope="manual", limit=5),
        settings=settings,
        client=client,
        importar=Mock(return_value=(0, 0)),
        indexar=Mock(return_value=0),
    )

    # Assert
    assert outcome.written == 0
    assert any("slug" in error for error in outcome.errors)
    assert client.urls == []


def test_ingest_urls_rechaza_urls_privadas_antes_de_tocar_la_red(tmp_path: Path) -> None:
    # Arrange
    settings = _settings(tmp_path)

    # Act
    outcome = ingest_urls(
        CaptureRequest(urls=("http://127.0.0.1/",), scope="manual", limit=5),
        settings=settings,
    )

    # Assert
    assert outcome.written == 0
    assert any("no pública" in error for error in outcome.errors)
    assert not list(settings.gold_dir.glob("*.md"))


def test_ingest_urls_respeta_la_allowlist_de_dominios(tmp_path: Path) -> None:
    # Arrange
    settings = _settings(tmp_path, capture_allowed_domains=("example.com",))

    # Act
    outcome = ingest_urls(
        CaptureRequest(urls=("https://example.org/a",), scope="manual", limit=5),
        settings=settings,
    )

    # Assert
    assert outcome.written == 0
    assert any("no está permitido" in error for error in outcome.errors)


def test_ingest_urls_captura_las_semillas_del_alcance(tmp_path: Path) -> None:
    # Arrange
    settings = _settings(
        tmp_path,
        seeds=("https://example.com/oficial",),
        external_seeds=("https://example.org/tercero",),
    )

    # Act
    outcome = ingest_urls(
        CaptureRequest(scope="todo", limit=2),
        settings=settings,
        client=FakeClient(),
        importar=Mock(return_value=(2, 4)),
        indexar=Mock(return_value=4),
    )

    # Assert
    assert outcome.requested == 2
    assert outcome.written == 2
    assert outcome.captured == ("oficial", "tercero")
