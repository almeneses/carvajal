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
from carvajal.infrastructure.monitoring import (
    ExperimentOutcome,
    example_id,
    register_experiment,
)

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

    def __init__(
        self,
        *,
        existe: bool = False,
        ejemplos: int = 0,
        falla: str | None = None,
    ) -> None:
        self.existe = existe
        self.falla = falla
        self.datasets: list[dict[str, Any]] = []
        self.examples: list[list[dict[str, Any]]] = []
        self.projects: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []
        self._ids: set[str] = {f"previo-{i}" for i in range(ejemplos)}

    def sembrar(self, *identificadores: str) -> None:
        """Simula ejemplos ya creados en el dataset."""

        self._ids.update(identificadores)

    def _quizas_falla(self, paso: str) -> None:
        if self.falla == paso:
            raise RuntimeError(f"fallo en {paso}")

    def read_dataset(self, dataset_name: str) -> DatasetFalso:
        self._quizas_falla("read_dataset")
        if not self.existe:
            raise LookupError("el dataset no existe")
        return DatasetFalso()

    def create_dataset(self, dataset_name: str, description: str = "") -> DatasetFalso:
        self._quizas_falla("create_dataset")
        self.datasets.append({"name": dataset_name, "description": description})
        return DatasetFalso()

    def list_examples(self, dataset_id: str) -> list[Any]:
        self._quizas_falla("list_examples")

        class Ejemplo:
            def __init__(self, identificador: str) -> None:
                self.id = identificador

        return [Ejemplo(identificador) for identificador in self._ids]

    def create_examples(
        self, dataset_id: str | None = None, examples: list[dict[str, Any]] | None = None
    ) -> None:
        # El SDK real exige ``dataset_id`` (xor_args); si falta, la prueba falla.
        assert dataset_id, "create_examples necesita dataset_id"
        self._quizas_falla("create_examples")
        assert examples is not None
        self.examples.append(examples)
        self._ids.update(str(ejemplo["id"]) for ejemplo in examples)

    def create_project(
        self,
        name: str,
        *,
        description: str = "",
        reference_dataset_id: str = "",
        metadata: dict[str, Any] | None = None,
        upsert: bool = False,
    ) -> Any:
        self._quizas_falla("create_project")
        self.projects.append(
            {
                "name": name,
                "dataset": reference_dataset_id,
                "metadata": metadata or {},
                "upsert": upsert,
            }
        )

        class Proyecto:
            def __init__(self, nombre: str) -> None:
                self.name = nombre

        return Proyecto(name)

    def create_run(self, **kwargs: Any) -> None:
        self._quizas_falla("create_run")
        self.runs.append(kwargs)


def filas(report: RouterReport) -> list[dict[str, Any]]:
    return [
        {
            "id": fila["id"],
            "inputs": {"question": fila["question"]},
            "expected": {"route": fila["expected_route"], "intent": fila["expected_intent"]},
            "outputs": {"route": fila["route"], "intent": fila["intent"]},
            "latency_ms": fila["latency_ms"],
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
    """T019: dataset con la verdad de referencia y un run por pregunta."""

    cliente = ClienteFalso()
    report = evaluate_router(build_router("rules"), ITEMS)

    resultado = register_experiment(
        cliente,
        dataset="carvajal-routing_questions",
        experiment="routing-rules-2026-11-19",
        rows=filas(report),
        metrics={"route_accuracy": 1.0, "invalid_outputs": 0},
        metadata={"commit": "abc123", "date": "2026-11-19"},
    )

    assert resultado.registered is True
    assert cliente.datasets[0]["name"] == "carvajal-routing_questions"
    ejemplo = cliente.examples[0][0]
    assert ejemplo["outputs"] == {"route": "rag", "intent": "documental"}
    assert ejemplo["id"] == example_id("carvajal-routing_questions", "q1")
    # Una run por pregunta, sin filas ficticias, y cada una enlazada a su ejemplo.
    assert [run["name"] for run in cliente.runs] == ["q1", "q2"]
    for run in cliente.runs:
        assert run["reference_example_id"] == example_id("carvajal-routing_questions", run["name"])
        assert run["start_time"] and run["end_time"]
        assert run["end_time"] > run["start_time"]
    assert cliente.projects[0]["dataset"] == "dataset-1"
    assert cliente.projects[0]["metadata"]["metrics"] == {
        "route_accuracy": 1.0,
        "invalid_outputs": 0,
    }
    assert cliente.projects[0]["upsert"] is True
    assert cliente.runs[0]["extra"]["metadata"]["commit"] == "abc123"


def test_registrar_reutiliza_el_dataset_existente() -> None:
    """Volver a correr la evaluación no duplica el dataset (T019)."""

    dataset = "carvajal-routing_questions"
    cliente = ClienteFalso(existe=True)
    cliente.sembrar(example_id(dataset, "q1"), example_id(dataset, "q2"))
    report = evaluate_router(build_router("rules"), ITEMS)

    register_experiment(
        cliente,
        dataset=dataset,
        experiment="routing-rules-2",
        rows=filas(report),
        metrics={},
    )

    assert cliente.datasets == []
    assert cliente.examples == []
    assert cliente.runs


def test_un_dataset_a_medias_se_completa() -> None:
    """Si una corrida anterior falló, los ejemplos que faltan se crean (T019)."""

    cliente = ClienteFalso(existe=True)
    cliente.sembrar(example_id("carvajal-routing_questions", "q1"))
    report = evaluate_router(build_router("rules"), ITEMS)

    resultado = register_experiment(
        cliente,
        dataset="carvajal-routing_questions",
        experiment="routing-rules-3",
        rows=filas(report),
        metrics={},
    )

    assert resultado.registered is True
    # Solo se crea el ejemplo que faltaba; el que ya existía no se duplica.
    assert [ejemplo["id"] for ejemplo in cliente.examples[0]] == [
        example_id("carvajal-routing_questions", "q2")
    ]


@pytest.mark.parametrize(
    "paso",
    ["create_dataset", "create_examples", "create_project", "create_run"],
)
def test_un_fallo_del_servicio_no_propaga(paso: str) -> None:
    """T021: el experimento falla, pero la corrida local sigue (FR-009)."""

    cliente = ClienteFalso(falla=paso)
    report = evaluate_router(build_router("rules"), ITEMS)

    resultado = register_experiment(
        cliente,
        dataset="carvajal-routing_questions",
        experiment="routing-rules-4",
        rows=filas(report),
        metrics={},
    )

    assert isinstance(resultado, ExperimentOutcome)
    assert resultado.registered is False
    # El detalle no repite el mensaje del servicio, solo su tipo.
    assert resultado.detail in {"RuntimeError", "LookupError"}
    assert "no registrado" in resultado.as_message()
    assert len(report.results) == len(ITEMS)


def test_el_commit_se_resuelve_contra_el_repositorio() -> None:
    """T019: el commit sale de este repo, no del directorio actual."""

    from carvajal.infrastructure.monitoring import commit_sha

    sha = commit_sha()

    assert len(sha) == 12
    assert all(caracter in "0123456789abcdef" for caracter in sha)


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

    capturado = capsys.readouterr()
    assert codigo == 0
    assert "Enrutador" in capturado.out
    assert "falta LANGSMITH_API_KEY" in capturado.err


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
    assert metadata["commit"] and metadata["commit"] != "desconocido"
    assert "T" in metadata["date"]
    # El dataset se deriva del archivo de preguntas, no de un nombre fijo.
    assert cliente.datasets[0]["name"] == f"carvajal-{_preguntas(tmp_path).stem}"


def test_un_fallo_al_registrar_no_cambia_el_codigo_de_salida(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    """T021: el servicio caído deja el informe local y el exit code en cero."""

    from carvajal import cli

    monkeypatch.setattr(
        "carvajal.infrastructure.monitoring.build_eval_client",
        lambda _s: ClienteFalso(falla="create_dataset"),
    )

    codigo = cli.main(["eval-router", "--questions", str(_preguntas(tmp_path)), "--langsmith"])

    assert codigo == 0
    capturado = capsys.readouterr()
    assert "Enrutador" in capturado.out
    assert "Experimento no registrado" in capturado.err


def test_eval_retrieval_acepta_la_bandera() -> None:
    """T020: el script de retrieval expone el mismo contrato."""

    proceso = subprocess.run(
        [sys.executable, "scripts/eval_retrieval.py", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proceso.returncode == 0
    assert "--langsmith" in proceso.stdout


def _eval_retrieval() -> Any:
    import importlib.util

    ruta = Path("scripts/eval_retrieval.py").resolve()
    especificacion = importlib.util.spec_from_file_location("eval_retrieval", ruta)
    assert especificacion is not None and especificacion.loader is not None
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


def test_las_filas_de_retrieval_separan_verdad_y_prediccion() -> None:
    """T020: cada consulta lleva su verdad de referencia y su resultado."""

    modulo = _eval_retrieval()
    resumen = {
        "k": 10,
        "queries": 1,
        "labeled_queries": 1,
        "recall_at_k": 0.5,
        "ndcg_at_k": 0.4,
        "rows": [
            {
                "id": "q01",
                "query": "¿Cuándo se fundó Carvajal?",
                "relevant": ["historia"],
                "retrieved": ["historia", "otro"],
                "scores": [0.9, 0.5],
                "recall_at_k": 1.0,
                "ndcg_at_k": 1.0,
            }
        ],
    }

    fila = modulo.experiment_rows(resumen)[0]

    assert fila["expected"] == {"relevant": ["historia"], "expect_abstain": False}
    assert fila["outputs"]["recall_at_k"] == 1.0
    assert modulo.experiment_metrics(resumen) == {
        "k": 10,
        "recall_at_k": 0.5,
        "ndcg_at_k": 0.4,
        "queries": 1,
        "labeled_queries": 1,
    }
