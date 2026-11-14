"""Pruebas de la interfaz de línea de comandos."""

import json
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import ClassVar

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
    assert parser.parse_args(["qdrant-stats"]) is not None


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


class _FakeQdrantStats:
    """Doble del adaptador Qdrant que solo expone ``info`` y ``close``."""

    creados: ClassVar[list["_FakeQdrantStats"]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.closed = False
        _FakeQdrantStats.creados.append(self)

    def info(self) -> dict[str, object]:
        return {
            "status": "green",
            "optimizer_status": "ok",
            "collection": "carvajal_chunks",
            "points": 125,
            "indexed_vectors": 0,
            "segments": 6,
            "dimensions": 768,
            "distance": "Cosine",
        }

    def close(self) -> None:
        self.closed = True


class _FakeSettings:
    qdrant_url = "http://127.0.0.1:6333"
    qdrant_api_key = ""
    qdrant_collection = "carvajal_chunks"
    qdrant_dimensions = 768
    qdrant_distance = "Cosine"


def test_qdrant_stats_resume_la_coleccion(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``qdrant-stats`` pinta el estado y cierra el cliente."""

    _FakeQdrantStats.creados.clear()
    monkeypatch.setattr("carvajal.adapters.vector.qdrant_store.QdrantVectorStore", _FakeQdrantStats)
    monkeypatch.setattr("carvajal.infrastructure.settings.Settings", _FakeSettings)

    codigo = main(["qdrant-stats"])

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "Colección: carvajal_chunks" in salida
    assert "Puntos: 125" in salida
    assert "Distancia: Cosine" in salida
    assert _FakeQdrantStats.creados[0].closed is True


def test_qdrant_stats_json_es_parseable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--json`` imprime el estado completo, incluida la URL configurada."""

    monkeypatch.setattr("carvajal.adapters.vector.qdrant_store.QdrantVectorStore", _FakeQdrantStats)
    monkeypatch.setattr("carvajal.infrastructure.settings.Settings", _FakeSettings)

    codigo = main(["qdrant-stats", "--json"])

    datos = json.loads(capsys.readouterr().out)
    assert codigo == 0
    assert datos["url"] == "http://127.0.0.1:6333"
    assert datos["points"] == 125


def test_qdrant_stats_devuelve_1_si_el_indice_no_responde(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Un Qdrant caído no se disfraza de éxito: código 1 y detalle explícito."""

    class _Caido(_FakeQdrantStats):
        def info(self) -> dict[str, object]:
            return {
                "status": "unavailable",
                "collection": "carvajal_chunks",
                "points": 0,
                "detail": "ConnectionError",
            }

    monkeypatch.setattr("carvajal.adapters.vector.qdrant_store.QdrantVectorStore", _Caido)
    monkeypatch.setattr("carvajal.infrastructure.settings.Settings", _FakeSettings)

    codigo = main(["qdrant-stats"])

    assert codigo == 1
    assert "Detalle: ConnectionError" in capsys.readouterr().out
