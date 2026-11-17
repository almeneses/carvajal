"""Pruebas de la interfaz de línea de comandos."""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError

from carvajal import cli
from carvajal.adapters.storage import import_corpus
from carvajal.adapters.storage.import_corpus import GoldImportReport
from carvajal.cli import build_parser, main


class _FakeEngine:
    """Motor falso que registra si se liberó el pool."""

    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


class _FakeSessionFactory:
    """Fábrica de sesiones que no toca la base de datos."""

    def __init__(self, session: object | None = None, error: Exception | None = None) -> None:
        self._session = session
        self._error = error

    @contextmanager
    def begin(self) -> Generator[object]:
        if self._error is not None:
            raise self._error
        yield self._session


def _fake_storage(monkeypatch: pytest.MonkeyPatch, engine: _FakeEngine) -> None:
    """Inyecta un motor y una fábrica de sesiones inertes en la CLI."""

    monkeypatch.setattr(cli, "build_engine", lambda: engine)
    monkeypatch.setattr(cli, "build_session_factory", lambda _engine: _FakeSessionFactory(object()))


def test_parser_lista_subcomandos_validos() -> None:
    """El parser reconoce los subcomandos definidos."""

    parser = build_parser()

    assert parser.parse_args(["list"]) is not None
    assert parser.parse_args(["status"]) is not None
    assert parser.parse_args(["import"]) is not None


def test_parser_import_no_exige_argumentos() -> None:
    """``import`` funciona con la configuración por defecto, sin flags."""

    parser = build_parser()

    args = parser.parse_args(["import"])

    assert args.command == "import"


def test_parser_reconoce_las_tareas_de_llm() -> None:
    """Los subcomandos de Resumen y FAQ existen y aceptan su salida."""

    parser = build_parser()

    resumen = parser.parse_args(["summarize", "--focus", "empaques"])
    faq = parser.parse_args(["faq"])

    assert resumen.command == "summarize"
    assert resumen.focus == "empaques"
    assert faq.command == "faq"
    assert faq.output.name == "faq.md"


def test_parser_reconoce_migrate_gold() -> None:
    """``migrate-gold`` acepta el directorio por defecto y uno explícito."""

    parser = build_parser()

    por_defecto = parser.parse_args(["migrate-gold"])
    explicito = parser.parse_args(["migrate-gold", "--gold", "/tmp/corpus"])

    assert por_defecto.command == "migrate-gold"
    assert por_defecto.gold.name == "gold"
    assert por_defecto.allow_empty is False
    assert explicito.gold == Path("/tmp/corpus")

    con_flag = parser.parse_args(["migrate-gold", "--allow-empty"])
    assert con_flag.allow_empty is True


def test_migrate_gold_devuelve_1_con_url_mal_formada(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Una ``DATABASE_URL`` inválida termina con mensaje y código 1, sin traceback."""

    monkeypatch.setenv("DATABASE_URL", "no-es-una-url")

    code = main(["migrate-gold", "--gold", str(tmp_path)])

    assert code == 1
    assert "Configuración de base de datos inválida" in capsys.readouterr().out


def test_migrate_gold_devuelve_1_con_puerto_invalido(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Un puerto no numérico en la URL es configuración inválida, no un traceback."""

    monkeypatch.setenv("DATABASE_URL", "postgresql://usuario:clave@host:no-es-puerto/db")

    code = main(["migrate-gold", "--gold", str(tmp_path)])

    salida = capsys.readouterr().out
    assert code == 1
    assert "Configuración de base de datos inválida" in salida
    assert "Traceback" not in salida


def test_migrate_gold_no_propaga_operational_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Un fallo de conexión se traduce en mensaje y código 1, y libera el motor."""

    engine = _FakeEngine()
    fallo = OperationalError("SELECT 1", {}, Exception("base caída"))
    monkeypatch.setattr(cli, "build_engine", lambda: engine)
    monkeypatch.setattr(
        cli, "build_session_factory", lambda _engine: _FakeSessionFactory(error=fallo)
    )

    code = main(["migrate-gold", "--gold", str(tmp_path)])

    assert code == 1
    assert "No se pudo conectar a la base de datos" in capsys.readouterr().out
    assert engine.disposed is True


def test_migrate_gold_no_propaga_interface_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """``InterfaceError`` también se trata como fallo de conexión y libera el motor."""

    engine = _FakeEngine()
    fallo = InterfaceError("SELECT 1", {}, Exception("conexión interrumpida"))
    monkeypatch.setattr(cli, "build_engine", lambda: engine)
    monkeypatch.setattr(
        cli, "build_session_factory", lambda _engine: _FakeSessionFactory(error=fallo)
    )

    code = main(["migrate-gold", "--gold", str(tmp_path)])

    assert code == 1
    assert "No se pudo conectar a la base de datos" in capsys.readouterr().out
    assert engine.disposed is True


def test_migrate_gold_redacta_la_contrasena_de_la_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """El mensaje de conexión oculta la contraseña y conserva usuario y host."""

    engine = _FakeEngine()
    original = Exception("fallo en postgresql://usuario:s3cr3t@db:5432/carvajal")
    fallo = OperationalError("SELECT 1", {}, original)
    monkeypatch.setattr(cli, "build_engine", lambda: engine)
    monkeypatch.setattr(
        cli, "build_session_factory", lambda _engine: _FakeSessionFactory(error=fallo)
    )

    code = main(["migrate-gold", "--gold", str(tmp_path)])

    salida = capsys.readouterr().out
    assert code == 1
    assert "s3cr3t" not in salida
    assert "usuario" in salida
    assert "db:5432" in salida


def test_migrate_gold_deja_pasar_los_errores_inesperados(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Un fallo de escritura no se disfraza de error de conexión: se propaga."""

    engine = _FakeEngine()
    fallo = IntegrityError("INSERT", {}, Exception("constraint"))
    monkeypatch.setattr(cli, "build_engine", lambda: engine)
    monkeypatch.setattr(
        cli, "build_session_factory", lambda _engine: _FakeSessionFactory(error=fallo)
    )

    with pytest.raises(IntegrityError):
        main(["migrate-gold", "--gold", str(tmp_path)])
    assert engine.disposed is True


def test_migrate_gold_no_propaga_un_gold_corrupto(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Un Markdown con slug inválido devuelve 1 con mensaje claro y sin traceback."""

    engine = _FakeEngine()
    _fake_storage(monkeypatch, engine)
    (tmp_path / "malo.md").write_text("---\nslug: a/b\n---\n\nCuerpo", encoding="utf-8")

    code = main(["migrate-gold", "--gold", str(tmp_path)])

    salida = capsys.readouterr().out
    assert code == 1
    assert "Corpus Gold no importable" in salida
    assert "Traceback" not in salida
    assert engine.disposed is True


def test_migrate_gold_rechaza_un_directorio_inexistente(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Un ``--gold`` inexistente se reporta como error, no como excepción."""

    _fake_storage(monkeypatch, _FakeEngine())

    code = main(["migrate-gold", "--gold", str(tmp_path / "no-existe")])

    salida = capsys.readouterr().out
    assert code == 1
    assert "no existe" in salida
    assert "Traceback" not in salida


def test_migrate_gold_rechaza_un_directorio_vacio_sin_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Un Gold vacío exige ``--allow-empty`` para podar toda la capa."""

    _fake_storage(monkeypatch, _FakeEngine())

    code = main(["migrate-gold", "--gold", str(tmp_path)])

    salida = capsys.readouterr().out
    assert code == 1
    assert "no hay documentos Gold" in salida
    assert "Traceback" not in salida


def test_migrate_gold_reenvia_allow_empty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """``--allow-empty`` llega a ``migrate_gold`` y se reporta la poda."""

    _fake_storage(monkeypatch, _FakeEngine())
    recibido: dict[str, bool] = {}

    def fake_migrate(session: object, *, gold_dir: Path, allow_empty: bool) -> GoldImportReport:
        recibido["allow_empty"] = allow_empty
        return GoldImportReport(documents=0, chunks=0, removed=2)

    monkeypatch.setattr(import_corpus, "migrate_gold", fake_migrate)

    code = main(["migrate-gold", "--gold", str(tmp_path), "--allow-empty"])

    assert code == 0
    assert recibido["allow_empty"] is True
    assert "Documentos podados: 2" in capsys.readouterr().out


def test_parser_reconoce_sync_con_sus_banderas() -> None:
    """``sync`` acepta capa, poda y verificación."""

    parser = build_parser()

    args = parser.parse_args(["sync", "--layer", "all", "--prune", "--verify"])

    assert args.command == "sync"
    assert args.layer == "all"
    assert args.prune is True
    assert args.verify is True


def test_sync_verify_reporta_diferencias_y_falla(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Con una capa real resuelta, una diferencia hace fallar la verificación."""

    from carvajal.application.sync import CorpusLayer, SyncReport

    monkeypatch.setattr(
        cli,
        "_sync_corpus",
        lambda *a, **k: SyncReport(verified=True, differences=("historia: falta en Qdrant.",)),
    )
    monkeypatch.setattr(
        "carvajal.application.sync.corpus_layers",
        lambda *a, **k: (CorpusLayer("core", (tmp_path,)),),
    )

    code = cli.main(["sync", "--verify"])

    assert code == 1
    assert "falta en Qdrant" in capsys.readouterr().out


def test_capture_sincroniza_los_slugs_capturados(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``capture`` termina en la sincronización salvo con ``--solo-gold``."""

    from carvajal.application.sync import SyncReport
    from carvajal.domain.entities import CapturedPage
    from carvajal.pipeline import CaptureReport

    page = CapturedPage(url="https://example.com/", html="<p>x</p>", markdown="# X")
    report = CaptureReport(pages=[page], slugs={page.url: "home"})
    llamadas: list[set[str]] = []

    monkeypatch.setattr(cli, "_build_client", lambda args: object())
    monkeypatch.setattr("carvajal.pipeline.capture", lambda *a, **k: report)
    monkeypatch.setattr(
        cli,
        "_sync_corpus",
        lambda settings, layers, **kwargs: llamadas.append(kwargs["only"]) or SyncReport(indexed=1),
    )

    code = cli.main(["capture", "--seed", "https://example.com/"])

    assert code == 0
    assert llamadas == [{"home"}]


def test_capture_solo_gold_no_toca_servicios(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--solo-gold`` no construye la sincronización."""

    from carvajal.domain.entities import CapturedPage
    from carvajal.pipeline import CaptureReport

    page = CapturedPage(url="https://example.com/", html="<p>x</p>", markdown="# X")
    report = CaptureReport(pages=[page], slugs={page.url: "home"})

    monkeypatch.setattr(cli, "_build_client", lambda args: object())
    monkeypatch.setattr("carvajal.pipeline.capture", lambda *a, **k: report)
    monkeypatch.setattr(
        cli,
        "_sync_corpus",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debe sincronizar")),
    )

    code = cli.main(["capture", "--seed", "https://example.com/", "--solo-gold"])

    assert code == 0


def test_parser_sync_acepta_adopt() -> None:
    """``sync --adopt`` completa la identidad del registro legado."""

    args = build_parser().parse_args(["sync", "--adopt"])

    assert args.adopt is True


def test_sync_con_adopt_completa_el_registro_sin_reindexar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """La adopción escribe source_key y raw_hash y luego sincroniza."""

    import json
    from types import SimpleNamespace

    from carvajal.application.sync import SyncReport
    from carvajal.gold import read_index

    # Arrange: un índice legado y la configuración apuntando a él.
    data = tmp_path / "data"
    gold = data / "gold"
    gold.mkdir(parents=True)
    (gold / "index.jsonl").write_text(
        json.dumps({"slug": "historia", "url": "https://www.carvajal.com/historia/", "hash": "h1"})
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "carvajal.infrastructure.settings.Settings",
        lambda: SimpleNamespace(
            gold_dir=gold,
            external_dir=data / "external",
            raw_dir=data / "raw",
        ),
    )
    monkeypatch.setattr(cli, "_sync_corpus", lambda *a, **k: SyncReport())

    # Act
    code = cli.main(["sync", "--adopt"])

    # Assert
    assert code == 0
    entrada = read_index(gold / "index.jsonl")["historia"]
    assert entrada["source_key"] == "url:https://carvajal.com/historia"
    assert "Adopción: 1 adoptadas" in capsys.readouterr().out


def test_parser_index_qdrant_acepta_la_capa() -> None:
    """``index-qdrant`` elige la capa y por defecto indexa solo el núcleo."""

    parser = build_parser()

    assert parser.parse_args(["index-qdrant"]).layer == "core"
    assert parser.parse_args(["index-qdrant", "--layer", "extended"]).layer == "extended"
    assert parser.parse_args(["index-qdrant", "--layer", "all"]).layer == "all"


def test_index_qdrant_all_indexa_nucleo_y_fuentes_extendidas_en_una_corrida(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Con ``--layer all`` se pasan al indexador las áreas con su capa."""

    from types import SimpleNamespace

    from carvajal.application.sync import CorpusLayer
    from carvajal.indexing.qdrant import IndexReport

    core = tmp_path / "gold"
    extended = tmp_path / "external" / "donante" / "gold"
    core.mkdir()
    extended.mkdir(parents=True)
    received: dict[str, object] = {}

    def fake_index_gold(**kwargs: object) -> IndexReport:
        received.update(kwargs)
        return IndexReport(
            collection="corpus",
            documents=2,
            chunks=4,
            indexed=4,
            deleted=0,
            points=4,
            dimensions=768,
            elapsed_ms=1,
        )

    class FakeStore:
        collection = "corpus"
        dimensions = 768

        def __init__(self, **kwargs: object) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        "carvajal.infrastructure.settings.Settings",
        lambda: SimpleNamespace(
            external_dir=tmp_path / "external",
            qdrant_url="http://localhost:6333",
            qdrant_api_key=None,
            qdrant_collection="corpus",
            qdrant_dimensions=768,
            qdrant_distance="cosine",
            ollama_base_url="http://localhost:11434",
        ),
    )
    monkeypatch.setattr(
        "carvajal.application.sync.corpus_layers",
        lambda *a, **k: (
            CorpusLayer("core", (core,)),
            CorpusLayer("extended", (extended,)),
        ),
    )
    monkeypatch.setattr("carvajal.adapters.vector.qdrant_store.QdrantVectorStore", FakeStore)
    monkeypatch.setattr("carvajal.adapters.embeddings.OllamaEmbedder", lambda **k: object())
    monkeypatch.setattr("carvajal.indexing.qdrant.index_gold", fake_index_gold)

    code = cli.main(["index-qdrant", "--layer", "all", "--gold", str(core)])

    assert code == 0
    assert received["gold_dirs"] == [(core, "core"), (extended, "extended")]
    assert "Puntos indexados: 4" in capsys.readouterr().out
