"""Monitoreo del modo ``tools`` (spec 044, T044, FR-014).

Un hijo por herramienta con nombre, orden, estado y latencia; ``mode``, ``path`` y tokens
en la raíz. Los argumentos y los resultados solo viajan con el contenido completo
(``MONITORING_INCLUDE_CONTENT``); en producción la redacción por lista blanca los quita.
"""

from contextlib import nullcontext
from typing import Any, cast

from carvajal.infrastructure.monitoring import (
    SAFE_KEYS,
    record_tool_calls,
    redact,
    redact_run,
    token_metadata,
)


class SpanFalso:
    """Raíz que registra hijos, metadatos y salidas, como ``LangSmithTurn``."""

    def __init__(self) -> None:
        self.children: list[tuple[str, str, dict[str, Any], SpanFalso]] = []
        self.metadata: dict[str, Any] = {}
        self.outputs: dict[str, Any] = {}

    def child(self, name: str, *, run_type: str = "chain", metadata: Any = None) -> Any:
        hijo = SpanFalso()
        self.children.append((name, run_type, dict(metadata or {}), hijo))
        return nullcontext(hijo)

    def add_metadata(self, **values: Any) -> None:
        self.metadata.update(values)

    def add_outputs(self, **values: Any) -> None:
        self.outputs.update(values)

    def generation(self) -> Any:
        return nullcontext()

    def finish(self, *, outcome: str = "ok", error: BaseException | None = None) -> None:
        return None


class SpanRoto(SpanFalso):
    def child(self, name: str, *, run_type: str = "chain", metadata: Any = None) -> Any:
        raise RuntimeError("SDK roto")


RESULTADOS = (
    {
        "tool": "buscar_sedes",
        "args": {"ciudad": "Cali", "owner_key": "dueno-secreto"},
        "status": "ok",
        "source_ids": [2, 3],
        "elapsed_ms": 41,
    },
    {"tool": "sin_consulta", "args": {}, "status": "ok", "source_ids": [], "elapsed_ms": 0},
)


def test_un_hijo_por_herramienta_en_orden_sin_contenido() -> None:
    # Arrange
    raiz = SpanFalso()

    # Act
    record_tool_calls(raiz, RESULTADOS, include_content=False)

    # Assert
    assert [(nombre, tipo, meta) for nombre, tipo, meta, _ in raiz.children] == [
        (
            "buscar_sedes",
            "tool",
            {
                "tool": "buscar_sedes",
                "step": 1,
                "status": "ok",
                "latency_ms": 41,
                "source_count": 2,
            },
        ),
        (
            "sin_consulta",
            "tool",
            {"tool": "sin_consulta", "step": 2, "status": "ok", "latency_ms": 0, "source_count": 0},
        ),
    ]
    assert all(hijo.outputs == {} for *_, hijo in raiz.children)


def test_con_contenido_los_argumentos_viajan_sin_identificadores_del_visitante() -> None:
    # Arrange
    raiz = SpanFalso()

    # Act
    record_tool_calls(raiz, RESULTADOS, include_content=True)

    # Assert
    hijo = raiz.children[0][3]
    assert hijo.outputs == {"args": {"ciudad": "Cali"}, "status": "ok", "source_ids": [2, 3]}


def test_un_sdk_roto_no_rompe_el_turno() -> None:
    # Act / Assert: no lanza (FR-008).
    record_tool_calls(SpanRoto(), RESULTADOS, include_content=False)


def test_los_metadatos_del_hijo_y_de_la_raiz_sobreviven_a_la_redaccion() -> None:
    # Arrange
    hijo = {"tool": "cotizar", "step": 2, "status": "ok", "latency_ms": 5, "source_count": 1}
    raiz = {
        "mode": "tools",
        "path": "fallback",
        "gate": "evidence",
        "tools": ["buscar_corpus", "cotizar"],
        "tool_calls": 2,
        "no_query": True,
        "fallback_reason": "timeout",
        "quote_count": 2,
        "input_tokens": 10,
    }

    # Act / Assert
    assert redact(hijo, include_content=False) == hijo
    assert redact(raiz, include_content=False) == raiz
    assert {"mode", "path", "tools", "source_count", "fallback_reason", "no_query"} <= SAFE_KEYS


def test_sin_contenido_los_argumentos_y_resultados_no_salen_del_proceso() -> None:
    # Arrange: aunque un llamador los añadiera, el anonimizador del cliente los quita.
    salidas = {"args": {"ciudad": "Bogotá", "cantidad": 2000}, "source_ids": [1]}

    # Act
    limpio = redact_run(salidas, include_content=False)

    # Assert
    assert limpio == {}


def test_token_metadata_toma_solo_los_conteos_enteros() -> None:
    # Arrange
    uso = {
        "input_tokens": 120,
        "output_tokens": 30,
        "total_tokens": 150,
        "input_token_details": {"cache_read": 4},
        "flag": True,
    }

    # Act / Assert
    assert token_metadata(uso) == {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150}
    assert token_metadata({}) == {}


# --- Memoria (spec 045, T042) ----------------------------------------------------------------


def _memory_outcome() -> dict[str, Any]:
    """``ToolOutcome`` real de ``recordar_preferencia``: la redacción ocurre en origen."""

    from langgraph.store.memory import InMemoryStore

    from carvajal.agent.toolagent.tools import ToolContext, ToolDeps, run_tool
    from carvajal.infrastructure.settings import Settings

    deps = ToolDeps(
        vector_store=cast(Any, None),
        embedder=cast(Any, None),
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        memory=InMemoryStore(),
    )
    result = run_tool(
        "recordar_preferencia",
        {"categoria": "nombre", "valor": "Ana Secreta"},
        deps,
        ToolContext(question="Llámame Ana Secreta", owner_key="dueno-1", thread_id="h1"),
    )
    assert result.status == "ok"
    return dict(result.outcome)


def test_la_memoria_viaja_solo_con_su_categoria_con_y_sin_contenido() -> None:
    # Arrange
    outcome = _memory_outcome()
    con, sin = SpanFalso(), SpanFalso()

    # Act
    record_tool_calls(con, [outcome], include_content=True)
    record_tool_calls(sin, [outcome], include_content=False)

    # Assert
    assert outcome["args"] == {"categoria": "nombre"}
    assert con.children[0][3].outputs["args"] == {"categoria": "nombre"}
    assert "Ana" not in str(con.children) and "Ana" not in str(sin.children)
    assert sin.children[0][3].outputs == {}
    assert sin.children[0][2]["tool"] == "recordar_preferencia"


# --- Las 20 herramientas (spec 045, T089; FR-025) ---------------------------------------------


def test_cada_herramienta_del_registro_es_un_hijo_tool_con_estado_y_latencia() -> None:
    # Arrange: un resultado por herramienta, en el orden del registro, salvo sin_consulta
    # (cubierta arriba).
    from carvajal.agent.toolagent.tools import TOOL_NAMES

    nombres = [name for name in TOOL_NAMES if name != "sin_consulta"]
    resultados = [
        {"tool": name, "args": {}, "status": "ok", "source_ids": [1], "elapsed_ms": 10 + i}
        for i, name in enumerate(nombres)
    ]
    raiz = SpanFalso()

    # Act
    record_tool_calls(raiz, resultados, include_content=False)

    # Assert
    assert len(nombres) == 19
    assert [nombre for nombre, *_ in raiz.children] == nombres
    for i, (nombre, tipo, meta, _) in enumerate(raiz.children):
        assert tipo == "tool"
        assert meta == {
            "tool": nombre,
            "step": i + 1,
            "status": "ok",
            "latency_ms": 10 + i,
            "source_count": 1,
        }


# --- Jev (spec 046, T017 y T045) ---------------------------------------------------------------

LLAMADAS_JEV = (
    {
        "kind": "router",
        "status": "ok",
        "latency_ms": 271,
        "model": "jev-1.14.0",
        "input_tokens": 412,
        "output_tokens": 9,
        "choice": "rag",
        "confidence": 0.83,
        "probabilities": {"rag": 0.83, "herramienta_estructurada": 0.17},
    },
    {
        "kind": "claims",
        "status": "ok",
        "latency_ms": 300,
        "model": "jev-1.14.0",
        "input_tokens": 2300,
        "output_tokens": 22,
        "p_supported": 0.22,
        "threshold": 0.5,
    },
)


def test_un_hijo_jev_por_llamada_solo_con_metadatos() -> None:
    # Arrange
    from carvajal.infrastructure.monitoring import record_judge_calls

    raiz = SpanFalso()

    # Act
    record_judge_calls(raiz, LLAMADAS_JEV)

    # Assert
    assert [(nombre, tipo) for nombre, tipo, _, _ in raiz.children] == [
        ("jev", "tool"),
        ("jev", "tool"),
    ]
    assert raiz.children[0][2] == LLAMADAS_JEV[0]
    assert raiz.children[1][2]["p_supported"] == 0.22
    assert all(hijo.outputs == {} for _, _, _, hijo in raiz.children)


def test_los_metadatos_de_jev_sobreviven_a_la_redaccion_sin_texto() -> None:
    # Arrange
    meta = {
        **LLAMADAS_JEV[0],
        "gatekeeper_route": "rag",
        "gatekeeper_scope": "rag",
        "gatekeeper_source": "jev",
        "gatekeeper_llm_class": None,
        "gatekeeper_confidence": 0.83,
        "gatekeeper_cautious": False,
        "gatekeeper_rule": "",
        "claims_status": "supported",
        "claims_p": 0.91,
        "pregunta": "¿Quién fundó Carvajal?",
        "respuesta": "Carvajal se fundó en 1904.",
    }

    # Act
    limpio = redact(meta, include_content=False)

    # Assert
    assert limpio["probabilities"] == {"rag": 0.83, "herramienta_estructurada": 0.17}
    assert limpio["gatekeeper_scope"] == "rag"
    assert limpio["claims_p"] == 0.91
    assert "pregunta" not in limpio
    assert "respuesta" not in limpio
    assert {"gatekeeper_route", "claims_status", "jev_status", "p_supported"} <= SAFE_KEYS


def test_un_sdk_roto_no_rompe_el_registro_de_jev() -> None:
    from carvajal.infrastructure.monitoring import record_judge_calls

    record_judge_calls(SpanRoto(), LLAMADAS_JEV)
