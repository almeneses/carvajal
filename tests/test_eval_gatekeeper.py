"""Conjuntos congelados de la spec 046 (T004, T037) y comandos de medición (T047).

Formato y composición de ``data/eval/gatekeeper_046.json`` y ``data/eval/claims_046.json``;
cada ``source`` existe en su conjunto de origen. Sin red ni LLM.
"""

import json
from collections import Counter
from pathlib import Path

from carvajal.agent.gatekeeper.model import map_route
from carvajal.agent.router import decide_route
from carvajal.agent.toolagent.graph import shortcut_intent
from carvajal.application.ports import GATE_CLASSES

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
