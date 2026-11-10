"""Pruebas de la interfaz de línea de comandos."""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from carvajal import cli
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
    assert explicito.gold == Path("/tmp/corpus")


def test_migrate_gold_devuelve_1_con_url_mal_formada(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Una ``DATABASE_URL`` inválida termina con mensaje y código 1, sin traceback."""

    monkeypatch.setenv("DATABASE_URL", "no-es-una-url")

    code = main(["migrate-gold", "--gold", str(tmp_path)])

    assert code == 1
    assert "No se pudo conectar a la base de datos" in capsys.readouterr().out


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
