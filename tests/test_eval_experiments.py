"""Pruebas de los experimentos de evaluación en LangSmith (spec 038, US3).

Sin ``--langsmith`` no se importa el SDK ni se toca la red (FR-009, FR-010);
con la bandera se usa un cliente falso y un fallo del servicio deja intacto el
informe local y el código de salida.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from carvajal.agent.routers import build_router
from carvajal.agent.routers.evaluation import RouterReport, evaluate_router
from carvajal.infrastructure.monitoring import ExperimentOutcome, register_experiment

ITEMS = [
    {"id": "q1", "question": "¿Qué es Carvajal?", "route": "rag", "intent": "documental"},
    {
        "id": "q2",
        "question": "¿Vasos de 12 oz para café?",
        "route": "commercial",
        "intent": "commercial_custom",
        "expected": {"capacity_oz": 12.0},
    },
]


class DatasetFalso:
    id = "dataset-1"


class ClienteFalso:
    """Doble del cliente de LangSmith que registra cada llamada."""

    def __init__(self, *, existe: bool = False, falla: bool = False) -> None:
        self.existe = existe
        self.falla = falla
        self.datasets: list[dict[str, Any]] = []
        self.examples: list[dict[str, Any]] = []
        self.projects: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []

    def _quizas_falla(self) -> None:
        if self.falla:
            raise RuntimeError("servicio de evaluación caído")

    def has_dataset(self, dataset_name: str) -> bool:
        return self.existe

    def read_dataset(self, dataset_name: str) -> DatasetFalso:
        self._quizas_falla()
        return DatasetFalso()

    def create_dataset(self, dataset_name: str, description: str = "") -> DatasetFalso:
        self._quizas_falla()
        self.datasets.append({"name": dataset_name, "description": description})
        return DatasetFalso()

    def create_examples(self, *, inputs: list[Any], outputs: list[Any], dataset_id: str) -> None:
        self._quizas_falla()
        self.examples.append({"inputs": inputs, "outputs": outputs, "dataset_id": dataset_id})

    def create_project(
        self, name: str, description: str = "", reference_dataset_id: str = ""
    ) -> Any:
        self._quizas_falla()
        self.projects.append({"name": name, "dataset": reference_dataset_id})

        class Proyecto:
            def __init__(self, nombre: str) -> None:
                self.name = nombre

        return Proyecto(name)

    def create_run(self, **kwargs: Any) -> None:
        self._quizas_falla()
        self.runs.append(kwargs)


def filas(report: RouterReport) -> list[dict[str, Any]]:
    return [
        {
            "id": fila["id"],
            "inputs": {"question": fila["question"]},
            "outputs": {"route": fila["route"], "intent": fila["intent"]},
        }
        for fila in report.results
    ]


def test_el_informe_local_expone_una_fila_por_pregunta() -> None:
    """T017: cada pregunta deja su ruta, intención, validez y latencia."""

    report = evaluate_router(build_router("rules"), ITEMS)

    assert len(report.results) == len(ITEMS)
    assert {fila["id"] for fila in report.results} == {"q1", "q2"}
    primera = report.results[0]
    assert primera["route"] == "rag"
    assert primera["expected_route"] == "rag"
    assert primera["invalid"] is False
    assert primera["latency_ms"] >= 0


def test_el_informe_local_no_cambia_de_formato() -> None:
    """La tabla del informe sigue sin columnas nuevas (FR-009)."""

    from carvajal.agent.routers.evaluation import render_report

    tabla = render_report([evaluate_router(build_router("rules"), ITEMS)])

    assert "| Enrutador | Preguntas | Ruta |" in tabla
    assert "latency_ms" not in tabla


def test_registrar_crea_dataset_y_una_fila_por_pregunta() -> None:
    """T019: dataset idempotente y un run por pregunta con sus métricas."""

    cliente = ClienteFalso()
    report = evaluate_router(build_router("rules"), ITEMS)

    resultado = register_experiment(
        cliente,
        dataset="carvajal-routing",
        experiment="routing-rules-2026-11-19",
        rows=filas(report),
        metrics={"route_accuracy": 1.0, "invalid_outputs": 0},
        metadata={"commit": "abc123", "date": "2026-11-19"},
    )

    assert resultado.registered is True
    assert cliente.datasets[0]["name"] == "carvajal-routing"
    assert len(cliente.examples[0]["inputs"]) == len(ITEMS)
    assert cliente.projects[0]["dataset"] == "dataset-1"
    nombres = [run["name"] for run in cliente.runs]
    assert nombres[: len(ITEMS)] == ["q1", "q2"]
    assert nombres[-1] == "metricas"
    assert cliente.runs[-1]["outputs"] == {"route_accuracy": 1.0, "invalid_outputs": 0}
    assert cliente.runs[0]["extra"]["metadata"]["commit"] == "abc123"


def test_registrar_reutiliza_el_dataset_existente() -> None:
    """Volver a correr la evaluación no duplica el dataset (T019)."""

    cliente = ClienteFalso(existe=True)
    report = evaluate_router(build_router("rules"), ITEMS)

    register_experiment(
        cliente,
        dataset="carvajal-routing",
        experiment="routing-rules-2",
        rows=filas(report),
        metrics={},
    )

    assert cliente.datasets == []
    assert cliente.examples == []
    assert cliente.runs


def test_un_fallo_del_servicio_no_propaga() -> None:
    """T021: el experimento falla, pero la corrida local sigue (FR-009)."""

    cliente = ClienteFalso(falla=True)
    report = evaluate_router(build_router("rules"), ITEMS)

    resultado = register_experiment(
        cliente,
        dataset="carvajal-routing",
        experiment="routing-rules-3",
        rows=filas(report),
        metrics={},
    )

    assert isinstance(resultado, ExperimentOutcome)
    assert resultado.registered is False
    assert "servicio de evaluación caído" in resultado.detail
    assert "no registrado" in resultado.as_message()
    assert len(report.results) == len(ITEMS)


def _preguntas(tmp_path: Path) -> Path:
    ruta = tmp_path / "preguntas.json"
    ruta.write_text(json.dumps({"items": ITEMS}), encoding="utf-8")
    return ruta


def test_sin_bandera_no_toca_langsmith(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """T021: ``eval-router`` sin ``--langsmith`` no registra ni abre red."""

    import socket

    from carvajal import cli

    def prohibido(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("sin bandera no debe haber red")

    monkeypatch.setattr(socket.socket, "connect", prohibido)
    monkeypatch.delitem(sys.modules, "carvajal.infrastructure.monitoring", raising=False)
    salida = tmp_path / "tabla.md"

    codigo = cli.main(
        ["eval-router", "--questions", str(_preguntas(tmp_path)), "--output", str(salida)]
    )

    assert codigo == 0
    assert "Enrutador" in capsys.readouterr().out
    assert salida.exists()
    # El módulo del monitoreo ni siquiera se carga sin la bandera.
    assert "carvajal.infrastructure.monitoring" not in sys.modules


def test_con_bandera_y_sin_llave_avisa_y_no_falla(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sin credenciales el experimento no se registra, pero el informe se genera."""

    from carvajal import cli

    monkeypatch.setenv("LANGSMITH_API_KEY", "")

    codigo = cli.main(["eval-router", "--questions", str(_preguntas(tmp_path)), "--langsmith"])

    salida = capsys.readouterr().out
    assert codigo == 0
    assert "Enrutador" in salida
    assert "falta LANGSMITH_API_KEY" in salida


def test_con_bandera_registra_un_experimento(tmp_path: Path, monkeypatch: Any) -> None:
    """T019: con cliente falso, la corrida queda registrada pregunta a pregunta."""

    from carvajal import cli

    cliente = ClienteFalso()
    monkeypatch.setattr("carvajal.infrastructure.monitoring.build_eval_client", lambda _s: cliente)

    codigo = cli.main(["eval-router", "--questions", str(_preguntas(tmp_path)), "--langsmith"])

    assert codigo == 0
    assert [run["name"] for run in cliente.runs][:2] == ["q1", "q2"]
    metadata = cliente.runs[0]["extra"]["metadata"]
    assert metadata["router"] == "rules"
    assert metadata["commit"]
    assert "T" in metadata["date"]


def test_un_fallo_al_registrar_no_cambia_el_codigo_de_salida(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    """T021: el servicio caído deja el informe local y el exit code en cero."""

    from carvajal import cli

    monkeypatch.setattr(
        "carvajal.infrastructure.monitoring.build_eval_client",
        lambda _s: ClienteFalso(falla=True),
    )

    codigo = cli.main(["eval-router", "--questions", str(_preguntas(tmp_path)), "--langsmith"])

    assert codigo == 0
    salida = capsys.readouterr().out
    assert "Enrutador" in salida
    assert "Experimento no registrado" in salida


@pytest.mark.parametrize("bandera", ["--langsmith"])
def test_eval_retrieval_acepta_la_bandera(bandera: str) -> None:
    """T020: el script de retrieval expone el mismo contrato."""

    proceso = subprocess.run(
        [sys.executable, "scripts/eval_retrieval.py", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proceso.returncode == 0
    assert bandera in proceso.stdout
