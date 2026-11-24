"""Conjuntos congelados de la spec 046 (T004, T037) y comandos de medición (T047).

Formato y composición de ``data/eval/gatekeeper_046.json`` y ``data/eval/claims_046.json``;
cada ``source`` existe en su conjunto de origen. Sin red ni LLM.
"""

import json
from collections import Counter
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from carvajal.adapters.judge.jev import JevClient, JevJudge
from carvajal.agent.gatekeeper.evaluation import (
    ClaimsItem,
    GateItem,
    QuotaJudge,
    ReplayJudge,
    eval_claims,
    eval_gatekeeper,
    load_claims_items,
    load_gate_items,
    render_claims_report,
    render_gatekeeper_report,
    run_smoke,
)
from carvajal.agent.gatekeeper.model import map_route
from carvajal.agent.router import decide_route
from carvajal.agent.toolagent.graph import shortcut_intent
from carvajal.application.ports import (
    GATE_CLASSES,
    ClaimsQuery,
    ClaimsVerdict,
    GateClass,
    JudgeCall,
    JudgeUnavailable,
    RouteQuery,
    RouteVerdict,
)
from carvajal.infrastructure.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
GATEKEEPER = ROOT / "data" / "eval" / "gatekeeper_046.json"
ROUTING = ROOT / "data" / "eval" / "routing_questions.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_gatekeeper_046_formato_y_composicion() -> None:
    # Arrange
    data = _load(GATEKEEPER)
    items = data["items"]

    # Assert
    assert data["frozen_at"]
    assert data["origin"]
    assert len(items) == 20
    assert len({item["id"] for item in items}) == 20
    assert Counter(item["expected"] for item in items) == {
        "herramienta_estructurada": 7,
        "rag": 6,
        "mixta": 2,
        "respuesta_directa": 5,
    }
    for item in items:
        assert set(item) >= {"id", "question", "context", "expected", "source", "rules_route"}
        assert item["expected"] in GATE_CLASSES
        assert item["question"].strip()


def test_gatekeeper_046_origenes() -> None:
    # Arrange
    items = _load(GATEKEEPER)["items"]
    routing = {item["id"]: item for item in _load(ROUTING)["items"]}
    sourced = [item for item in items if item["source"]]
    written = [item for item in items if not item["source"]]

    # Assert: 15 de routing_questions.json con su ruta de reglas; 5 redactadas con origen.
    assert len(sourced) == 15
    assert len(written) == 5
    for item in sourced:
        name, _, rid = item["source"].partition("#")
        assert name == "routing_questions.json"
        origin = routing[rid]
        assert origin["question"] == item["question"]
        assert item["rules_route"] == f"{origin['route']}/{origin['intent']}"
        expected = map_route(origin["route"], origin["intent"])
        if rid == "c-54":
            assert expected == "mixta"
            assert item["expected"] == "herramienta_estructurada"
        else:
            assert item["expected"] == expected
    mixed = {item["source"] for item in sourced if item["expected"] == "mixta"}
    assert mixed == {"routing_questions.json#c-52", "routing_questions.json#c-53"}
    for item in written:
        assert item["origin"]
        assert item["expected"] == "respuesta_directa"


def test_el_saludo_coloquial_no_lo_detectan_las_reglas() -> None:
    # Arrange
    items = _load(GATEKEEPER)["items"]
    question = "quiubo pues, todo bien?"

    # Assert
    assert question in {item["question"] for item in items}
    assert shortcut_intent(question) == ("", "")
    assert decide_route(question).intent != "greeting"


CLAIMS = ROOT / "data" / "eval" / "claims_046.json"


def test_claims_046_formato_y_composicion() -> None:
    # Arrange
    data = _load(CLAIMS)
    items = data["items"]

    # Assert
    assert data["frozen_at"]
    assert data["origin"]
    assert len(items) == 16
    assert len({item["id"] for item in items}) == 16
    assert Counter(item["variant"] for item in items) == {"original": 8, "perturbada": 8}
    assert Counter(item["label"] for item in items) == {"supported": 6, "unsupported": 10}
    originals = {item["v3"]: item["label"] for item in items if item["variant"] == "original"}
    assert set(originals) == {
        "v3-001",
        "v3-002",
        "v3-005",
        "v3-008",
        "v3-010",
        "v3-018",
        "v3-027",
        "v3-034",
    }
    assert {v3 for v3, label in originals.items() if label == "unsupported"} == {
        "v3-001",
        "v3-005",
    }
    assert originals["v3-027"] == "supported"
    for item in items:
        assert set(item) >= {"id", "v3", "variant", "question", "answer", "evidence", "label"}
        assert item["note"]
        assert all(set(e) == {"consulta", "texto"} for e in item["evidence"])
        if item["variant"] == "perturbada":
            assert item["label"] == "unsupported"


def test_claims_046_sin_datos_personales() -> None:
    # Arrange
    from carvajal.agent.memory.sensitive import find_sensitive

    items = _load(CLAIMS)["items"]

    # Assert: la pregunta no trae datos de la persona; la evidencia no lleva la pregunta y
    # solo conserva datos públicos del catálogo y del directorio (teléfonos de sedes y SKU
    # EAN-13, que el filtro de la 045 confunde con tarjetas), nunca correos, documentos,
    # cuentas ni claves.
    for item in items:
        assert find_sensitive(item["question"]) == []
        for extract in item["evidence"]:
            assert item["question"] not in extract["texto"]
            kinds = set(find_sensitive(extract["texto"])) - {"telefono", "tarjeta"}
            assert kinds == set(), (item["id"], kinds)


def test_claims_046_evidencia_igual_a_la_de_produccion() -> None:
    # La evidencia congelada es la que arma ``build_claims_query`` (no se edita a mano).
    from carvajal.agent.gatekeeper.claims import PERSON_EXTRACT, said_by_person

    for item in _load(CLAIMS)["items"]:
        said = said_by_person(item["question"])
        names = [e["consulta"] for e in item["evidence"]]
        assert (PERSON_EXTRACT in names) == bool(said)


# --- Comandos de medición (T047): ``eval-gatekeeper`` y ``eval-claims`` con dobles ---------


def _probabilities(choice: str, confidence: float) -> dict[str, float]:
    rest = round((1 - confidence) / 3, 4)
    return {name: confidence if name == choice else rest for name in GATE_CLASSES}


class ScriptedRouteJudge:
    """Juez de Choice guionizado por pregunta: (clase, confianza, claridad)."""

    def __init__(self, script: dict[str, tuple[str, float, int | None]]) -> None:
        self.script = script
        self.queries: list[tuple[RouteQuery, bool]] = []

    def available(self) -> bool:
        return True

    def classify(self, query: RouteQuery, *, with_score: bool = False) -> RouteVerdict:
        self.queries.append((query, with_score))
        choice, confidence, clarity = self.script[query.question]
        return RouteVerdict(
            choice=cast(GateClass, choice),
            confidence=confidence,
            probabilities=cast(Any, _probabilities(choice, confidence)),
            call=JudgeCall(kind="router", status="ok", latency_ms=200, model="jev-1.14.0"),
            clarity=clarity,
            clarity_confidence=0.7 if clarity is not None else None,
        )


class ScriptedClaimsJudge:
    def __init__(self, script: dict[str, float]) -> None:
        self.script = script
        self.queries: list[ClaimsQuery] = []

    def available(self) -> bool:
        return True

    def check(self, query: ClaimsQuery) -> ClaimsVerdict:
        self.queries.append(query)
        return ClaimsVerdict(
            p_supported=self.script[query.answer],
            call=JudgeCall(kind="claims", status="ok", latency_ms=150, model="jev-1.14.0"),
        )


GATE_ITEMS = [
    GateItem(id="g1", question="cotízame 500 vasos", expected="herramienta_estructurada"),
    GateItem(id="g2", question="¿Qué es LiquidezYa?", expected="rag"),
    GateItem(id="g3", question="¿Quién fundó Carvajal?", expected="rag"),
    GateItem(id="g4", question="¿eres una persona o un bot?", expected="respuesta_directa"),
    GateItem(
        id="g5",
        question="¿Cuántos empleados tiene Carvajal?",
        expected=("herramienta_estructurada"),
    ),
]
JEV_SCRIPT: dict[str, tuple[str, float, int | None]] = {
    "cotízame 500 vasos": ("herramienta_estructurada", 0.92, 4),
    "¿Qué es LiquidezYa?": ("rag", 0.65, 3),
    "¿Quién fundó Carvajal?": ("rag", 0.88, 4),
    "¿eres una persona o un bot?": ("respuesta_directa", 0.85, 2),
    # Error con confianza media: una pregunta con datos enviada a la respuesta directa.
    "¿Cuántos empleados tiene Carvajal?": ("respuesta_directa", 0.55, 1),
}


def _llm_router(question: str, turns: Any = None) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(route="rag", intent="documental")


def test_eval_gatekeeper_exactitud_por_juez_rejilla_y_confianza_frente_a_score() -> None:
    # Arrange
    jev = ScriptedRouteJudge(JEV_SCRIPT)

    # Act
    reports = eval_gatekeeper(
        GATE_ITEMS,
        judges=["rules", "llm", "jev"],
        jev=jev,
        llm_router=_llm_router,
        settings=Settings(),
    )

    # Assert
    by_judge = {report.judge: report for report in reports}
    assert set(by_judge) == {"rules", "llm", "jev"}
    assert by_judge["llm"].hits == 2  # siempre «rag»
    assert by_judge["jev"].hits == 4
    assert by_judge["jev"].total == 5
    # Score solo en la medición: cada petición lleva Score.
    assert all(with_score for _, with_score in jev.queries)
    grid = {row["threshold"]: row for row in by_judge["jev"].grid()}
    assert grid[0.5]["applied"] == 5 and grid[0.5]["applied_hits"] == 4
    assert grid[0.6]["applied"] == 4 and grid[0.6]["applied_hits"] == 4
    assert grid[0.6]["coverage"] == 0.8
    assert grid[0.8]["applied"] == 3
    assert grid[0.5]["data_to_direct"] == 1
    assert grid[0.6]["data_to_direct"] == 0
    choice = by_judge["jev"].recommend()
    assert (choice["min_confidence"], choice["direct_min_confidence"]) == (0.6, 0.6)
    versus = by_judge["jev"].confidence_vs_score()
    assert versus["errors"] == 1
    assert versus["confidence_correct"] > versus["confidence_wrong"]
    assert versus["clarity_correct"] > versus["clarity_wrong"]
    # Decisión de producción con los umbrales vigentes (0,6 y 0,8), sin llamadas nuevas.
    decisions = {r.item.id: r.decision or {} for r in by_judge["jev"].results}
    assert decisions["g5"]["cautious"] is True
    assert decisions["g1"]["scope"] == "structured"
    assert len(jev.queries) == 5


def test_eval_gatekeeper_informe_markdown_con_las_tres_tablas() -> None:
    reports = eval_gatekeeper(
        GATE_ITEMS, judges=["rules", "jev"], jev=ScriptedRouteJudge(JEV_SCRIPT), settings=Settings()
    )

    text = render_gatekeeper_report(reports, questions=Path("x.json"), settings=Settings())

    assert "| rules |" in text and "| jev |" in text
    assert "Rejilla de umbrales" in text
    assert "Confianza frente a Score" in text
    # El informe no repite la pregunta en la tabla de la rejilla ni en el libro.
    assert "0,6" in text or "0.6" in text


def test_eval_gatekeeper_se_detiene_en_max_jev_calls() -> None:
    jev = ScriptedRouteJudge(JEV_SCRIPT)

    reports = eval_gatekeeper(
        GATE_ITEMS, judges=["jev"], jev=jev, settings=Settings(), max_jev_calls=2
    )

    assert len(jev.queries) == 2
    statuses = [result.status for result in reports[0].results]
    assert statuses == ["ok", "ok", "cuota", "cuota", "cuota"]
    assert reports[0].total == 2  # los ítems sin llamada se declaran y no cuentan


def _jev_transport(requests: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "jev-1.14.0"}]})
        body = json.loads(request.content)
        if "respaldo" in body["questions"]:
            answers = {"respaldo": {"type": "noul", "noul": 0.31}}
        else:
            answers = {
                "ruta": {
                    "type": "choice",
                    "choice": "rag",
                    "confidence": 0.83,
                    "probabilities": _probabilities("rag", 0.83),
                },
                "claridad": {"type": "score", "score": 3, "confidence": 0.71},
            }
        return httpx.Response(
            200,
            json={
                "model": "jev-1.14.0",
                "answers": answers,
                "usage": {"input_tokens": 400, "output_tokens": 9},
            },
        )

    return httpx.MockTransport(handler)


def _real_judge(tmp_path: Path, requests: list[httpx.Request]) -> tuple[JevJudge, Path]:
    ledger = tmp_path / "jev-calls.jsonl"
    client = JevClient(api_key="clave-falsa", ledger=ledger, transport=_jev_transport(requests))
    return JevJudge(client), ledger


def test_eval_gatekeeper_y_claims_escriben_el_libro_sin_texto(tmp_path: Path) -> None:
    # Arrange
    requests: list[httpx.Request] = []
    judge, ledger = _real_judge(tmp_path, requests)
    claims = [
        ClaimsItem(
            id="cl-1",
            v3="v3-001",
            variant="original",
            question="¿Qué vaso me sirve?",
            answer="El vaso de 9 oz es de papel [1].",
            evidence=(("buscar_producto", "vaso de 9 oz, papel"),),
            label="supported",
        )
    ]

    # Act
    smoke = run_smoke(judge)
    eval_gatekeeper(GATE_ITEMS[:2], judges=["jev"], jev=judge, settings=Settings())
    eval_claims(claims, judge, settings=Settings())

    # Assert
    assert smoke["status"] == "ok" and smoke["model_listed"] is True
    lines = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert [(line["run"], line["item"], line["kind"]) for line in lines] == [
        ("smoke", "", "smoke"),
        ("gatekeeper", "g1", "router"),
        ("gatekeeper", "g2", "router"),
        ("claims", "cl-1", "claims"),
    ]
    assert lines[1]["clarity"] == 3 and lines[1]["clarity_confidence"] == 0.71
    assert lines[3]["p_supported"] == 0.31
    raw = ledger.read_text(encoding="utf-8")
    for text in ("cotízame", "LiquidezYa", "9 oz", "papel", "clave-falsa"):
        assert text not in raw
    assert len(requests) == 4


def test_eval_claims_deteccion_y_falsas_alarmas_por_umbral() -> None:
    # Arrange
    def item(id_: str, answer: str, label: str) -> ClaimsItem:
        return ClaimsItem(
            id=id_,
            v3="v3-x",
            variant="original",
            question="",
            answer=answer,
            evidence=(("buscar_producto", "dato"),),
            label=label,
        )

    items = [
        item("a", "buena 1", "supported"),
        item("b", "buena 2", "supported"),
        item("c", "mala 1", "unsupported"),
        item("d", "mala 2", "unsupported"),
        item("e", "mala 3", "unsupported"),
    ]
    judge = ScriptedClaimsJudge(
        {"buena 1": 0.9, "buena 2": 0.45, "mala 1": 0.1, "mala 2": 0.35, "mala 3": 0.55}
    )

    # Act
    report = eval_claims(items, judge, settings=Settings())
    text = render_claims_report(report, questions=Path("claims.json"))

    # Assert
    rows = {row["threshold"]: row for row in report.grid()}
    assert (rows[0.5]["detected"], rows[0.5]["unsupported"]) == (2, 3)
    assert (rows[0.5]["false_alarms"], rows[0.5]["supported"]) == (1, 2)
    assert (rows[0.3]["detected"], rows[0.3]["false_alarms"]) == (1, 0)
    assert (rows[0.6]["detected"], rows[0.6]["false_alarms"]) == (3, 1)
    assert "Detección" in text and "Falsas alarmas" in text
    assert "mala 1" not in text  # las respuestas no van al informe


def test_eval_claims_se_detiene_en_max_jev_calls() -> None:
    items = load_claims_items(ROOT / "data" / "eval" / "claims_046.json")
    judge = ScriptedClaimsJudge(_DefaultP())

    report = eval_claims(items, judge, settings=Settings(), max_jev_calls=3)

    assert len(judge.queries) == 3
    assert [r.status for r in report.results].count("cuota") == 13


class _DefaultP(dict):
    def __getitem__(self, key: str) -> float:
        return 0.8


def test_la_evidencia_congelada_viaja_tal_cual_y_la_pregunta_no() -> None:
    # Arrange
    items = load_claims_items(ROOT / "data" / "eval" / "claims_046.json")
    judge = ScriptedClaimsJudge(_DefaultP())

    # Act
    eval_claims(items[:2], judge, settings=Settings())

    # Assert
    for item, query in zip(items[:2], judge.queries, strict=True):
        assert query.evidence == item.evidence
        assert item.question not in json.dumps(
            [query.answer, list(query.evidence)], ensure_ascii=False
        )


def test_carga_los_dos_formatos_de_preguntas_del_gatekeeper() -> None:
    gate = load_gate_items(GATEKEEPER)
    routing = load_gate_items(ROUTING)

    assert len(gate.items) == 20 and gate.frozen_at
    assert len(routing.items) == 130
    by_id = {item.id: item for item in routing.items}
    assert by_id["r-q01"].expected == "rag"
    assert by_id["n-17"].turns  # el historial del ítem se conserva para las reglas


def test_las_reglas_sobre_las_130_no_llaman_a_jev() -> None:
    routing = load_gate_items(ROUTING)

    reports = eval_gatekeeper(routing.items, judges=["rules"], settings=Settings())

    assert reports[0].total == 130
    assert reports[0].hits >= 120


def test_replay_judge_reproduce_los_veredictos_del_libro_sin_red(tmp_path: Path) -> None:
    # Arrange: el libro de una corrida con Jev (con una línea fallida y otra de compuerta).
    ledger = tmp_path / "jev-calls.jsonl"
    lines = [
        {
            "run": "gatekeeper",
            "item": "g1",
            "kind": "router",
            "status": "ok",
            "latency_ms": 250,
            "model": "jev-1.14.0",
            "input_tokens": 400,
            "output_tokens": 9,
            "choice": "respuesta_directa",
            "confidence": 0.71,
            "probabilities": _probabilities("respuesta_directa", 0.71),
        },
        {
            "run": "gatekeeper",
            "item": "g2",
            "kind": "router",
            "status": "timeout",
            "latency_ms": 800,
            "model": "",
            "input_tokens": 0,
            "output_tokens": 0,
        },
        {
            "run": "claims",
            "item": "cl-1",
            "kind": "claims",
            "status": "ok",
            "latency_ms": 100,
            "model": "jev-1.14.0",
            "input_tokens": 1,
            "output_tokens": 1,
            "p_supported": 0.2,
        },
    ]
    ledger.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    # Act
    replay = ReplayJudge.from_ledger(ledger)
    replay.item = "g1"
    verdict = replay.classify(RouteQuery(question="cualquier texto"))
    replay.item = "g2"

    # Assert
    assert verdict.choice == "respuesta_directa" and verdict.confidence == 0.71
    assert verdict.call.latency_ms == 250
    with pytest.raises(JudgeUnavailable):
        replay.classify(RouteQuery(question="otra"))
    with pytest.raises(JudgeUnavailable):
        replay.check(ClaimsQuery(answer="x", evidence=()))
    assert replay.items() == {"g1"}


def test_quota_judge_corta_cada_cuota_por_separado() -> None:
    from tests.fakes.judge import FakeJudge

    inner = FakeJudge()
    judge = QuotaJudge(inner, router_calls=1, claims_calls=2)

    judge.classify(RouteQuery(question="a"))
    with pytest.raises(JudgeUnavailable) as router_error:
        judge.classify(RouteQuery(question="b"))
    judge.check(ClaimsQuery(answer="x", evidence=()))
    judge.check(ClaimsQuery(answer="y", evidence=()))
    with pytest.raises(JudgeUnavailable) as claims_error:
        judge.check(ClaimsQuery(answer="z", evidence=()))

    assert len(inner.routes) == 1 and len(inner.claims) == 2
    assert router_error.value.reason == claims_error.value.reason == "cuota"
    assert judge.available() is True
    assert (judge.router_skipped, judge.claims_skipped) == (1, 1)


def test_la_cuota_del_gatekeeper_se_declara_en_la_decision() -> None:
    from tests.fakes.judge import FakeJudge

    from carvajal.agent.gatekeeper.decide import decide_gate

    judge = QuotaJudge(FakeJudge(), router_calls=0, claims_calls=None)

    decision = decide_gate("¿Qué es LiquidezYa?", judge=judge, settings=Settings())

    assert decision.source == "llm"
    assert decision.reason == "sin_jev:cuota"


# --- CLI ------------------------------------------------------------------------------------


def test_parser_de_eval_gatekeeper_y_eval_claims() -> None:
    from carvajal.cli import build_parser

    parser = build_parser()
    gate = parser.parse_args(
        [
            *("eval-gatekeeper", "--smoke", "--judge", "rules", "--judge", "jev"),
            *("--ledger", "l.jsonl", "--max-jev-calls", "21"),
        ]
    )
    replay = parser.parse_args(
        ["eval-gatekeeper", "--replay-ledger", "l.jsonl", "--agent", "--only", "cautious,direct"]
    )
    claims = parser.parse_args(["eval-claims", "--ledger", "l.jsonl", "--max-jev-calls", "16"])

    assert gate.judge == ["rules", "jev"] and gate.smoke and gate.max_jev_calls == 21
    assert gate.questions == Path("data") / "eval" / "gatekeeper_046.json"
    assert replay.agent and replay.only == "cautious,direct"
    assert claims.questions == Path("data") / "eval" / "claims_046.json"


def test_eval_gatekeeper_con_jev_exige_libro(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from carvajal import cli

    monkeypatch.setenv("TYPESAFE_API_KEY", "clave-falsa")

    code = cli.main(["eval-gatekeeper", "--judge", "jev"])

    assert code == 1
    assert "--ledger" in capsys.readouterr().err


def test_eval_claims_exige_libro(capsys: pytest.CaptureFixture[str]) -> None:
    from carvajal import cli

    code = cli.main(["eval-claims"])

    assert code == 1
    assert "--ledger" in capsys.readouterr().err


def test_eval_gatekeeper_solo_reglas_no_construye_juez(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange
    from carvajal import cli
    from carvajal.adapters import judge as judge_module

    def forbidden(*_: Any, **__: Any) -> Any:
        raise AssertionError("Sin --judge jev no se construye el juez.")

    monkeypatch.setattr(judge_module, "build_judge", forbidden)
    output, raw = tmp_path / "gk.md", tmp_path / "gk.json"

    # Act
    code = cli.main(
        ["eval-gatekeeper", "--judge", "rules", "--output", str(output), "--json", str(raw)]
    )

    # Assert
    assert code == 0
    assert "| rules |" in output.read_text(encoding="utf-8")
    payload = json.loads(raw.read_text(encoding="utf-8"))
    assert payload["judges"]["rules"]["total"] == 20


def test_eval_gatekeeper_con_libro_usa_el_juez_y_la_cuota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: el juez real con un servidor falso, construido por el comando.
    from carvajal import cli
    from carvajal.adapters import judge as judge_module

    requests: list[httpx.Request] = []
    original = judge_module.build_judge

    def fake_build(settings: Any, **kwargs: Any) -> Any:
        assert kwargs["force"] is True and kwargs["ledger"] is not None
        return original(settings, transport=_jev_transport(requests), **kwargs)

    monkeypatch.setattr(judge_module, "build_judge", fake_build)
    monkeypatch.setenv("TYPESAFE_API_KEY", "clave-falsa")
    ledger = tmp_path / "jev-calls.jsonl"

    # Act
    code = cli.main(
        [
            *("eval-gatekeeper", "--smoke", "--judge", "jev", "--ledger", str(ledger)),
            *("--max-jev-calls", "4", "--output", str(tmp_path / "gk.md")),
        ]
    )

    # Assert: 1 de humo + 3 del gatekeeper; el resto se declara sin llamada.
    assert code == 0
    assert len(requests) == 4
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 4
    assert "sin llamada por cuota: 17" in (tmp_path / "gk.md").read_text(encoding="utf-8")


def test_eval_gatekeeper_replay_con_agente_solo_cautela_y_directa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: un libro con dos veredictos (uno en cautela y otro aplicado como rag).
    from carvajal import cli
    from carvajal.agent.gatekeeper import evaluation as gk_eval
    from carvajal.agent.toolagent.evaluation import TurnObservation

    ledger = tmp_path / "jev-calls.jsonl"
    lines = [
        {
            "run": "gatekeeper",
            "item": "gk-20",
            "kind": "router",
            "status": "ok",
            "latency_ms": 200,
            "model": "jev-1.14.0",
            "input_tokens": 1,
            "output_tokens": 1,
            "choice": "respuesta_directa",
            "confidence": 0.7,
            "probabilities": _probabilities("respuesta_directa", 0.7),
        },
        {
            "run": "gatekeeper",
            "item": "gk-09",
            "kind": "router",
            "status": "ok",
            "latency_ms": 200,
            "model": "jev-1.14.0",
            "input_tokens": 1,
            "output_tokens": 1,
            "choice": "rag",
            "confidence": 0.95,
            "probabilities": _probabilities("rag", 0.95),
        },
    ]
    ledger.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    asked: list[str] = []

    class Driver:
        mode = "tools"

        def run_turn(self, *, owner_key: str, thread_id: str, question: str, seed_turns: Any):
            asked.append(question)
            return TurnObservation(answer="¡Todo bien! ¿En qué te ayudo?", path="agent")

    monkeypatch.setattr(gk_eval, "build_replay_driver", lambda settings, replay: (Driver(), None))
    output = tmp_path / "replay.md"

    # Act
    code = cli.main(
        [
            *("eval-gatekeeper", "--replay-ledger", str(ledger), "--agent"),
            *("--only", "cautious,direct", "--output", str(output)),
        ]
    )

    # Assert: solo el ítem en cautela corre con el agente; cero llamadas a Jev.
    assert code == 0
    assert asked == ["quiubo pues, todo bien?"]
    text = output.read_text(encoding="utf-8")
    assert "gk-20" in text and "cautela" in text
