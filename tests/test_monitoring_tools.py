"""Monitoreo del modo ``tools`` (spec 044, T044, FR-014).

Un hijo por herramienta con nombre, orden, estado y latencia; ``mode``, ``path`` y tokens
en la raíz. Los argumentos y los resultados solo viajan con el contenido completo
(``MONITORING_INCLUDE_CONTENT``); en producción la redacción por lista blanca los quita.
"""

from contextlib import nullcontext
from typing import Any

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
