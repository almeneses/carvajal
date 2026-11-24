"""Compuerta de evidencia con las herramientas de la spec 045 (revisión adversarial).

Turnos completos con ``ScriptedToolChat`` y ``ToolAgentRunner``, sin red ni LLM reales:

- una búsqueda documental vacía seguida de una herramienta que siempre da ``ok`` con fuente
  (``fecha_actual``, ``indicadores_disponibles``, ``empresas_del_grupo`` o
  ``ciudades_con_cobertura`` sin argumentos, y las de la 044) no libera un texto inventado:
  el servidor garantiza la abstención (FR-004);
- un nombre ajeno que contiene la palabra de una unidad («Empaques Flexa») no respalda una
  afirmación de pertenencia;
- la ciudad habitual de la memoria (``sugerencia_ciudad``) no respalda un dato de Carvajal
  en ``no_query`` (FR-017): solo sale en la pregunta neutra de confirmación.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from functools import cache
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from tests.fakes.tool_chat import ScriptedToolChat, ScriptStep

from apps.api.chat.agent import ToolAgentFinal, ToolAgentRunner, ToolAgentToken
from carvajal.agent.memory.store import remember
from carvajal.agent.toolagent.graph import build_tool_agent
from carvajal.agent.toolagent.tools import ToolDeps
from carvajal.application.ports import ScoredChunk
from carvajal.catalog.loader import load_catalog
from carvajal.commercial.loader import load_commercial_book
from carvajal.domain.entities import Chunk
from carvajal.infrastructure.settings import Settings
from carvajal.institutional import load_institutional
from carvajal.prompts import NO_CONTEXT_ANSWER

OWNER = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]
INVENTED = "Carvajal tiene una oficina en Tokio desde 1990, con 40 empleados y 3 plantas."


class FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.1] * 8


class EmptyIndex:
    """Índice sin resultados: toda búsqueda documental sale vacía."""

    def upsert(self, chunks: Any, embeddings: Any) -> None:
        raise AssertionError("no indexa")

    def search(self, embedding: Any, *, limit: int = 8, **kwargs: Any) -> Sequence[ScoredChunk]:
        return []

    def delete_by_document(self, document_id: str) -> None:
        raise AssertionError("no borra")


class OneHitIndex(EmptyIndex):
    """Índice con un fragmento del núcleo que sí responde."""

    def search(self, embedding: Any, *, limit: int = 8, **kwargs: Any) -> Sequence[ScoredChunk]:
        chunk = Chunk(
            id="historia:3",
            document_id="historia",
            text="Carvajal fue fundada en Cali en 1904.",
            position=0,
            layer="core",
            section="historia",
        )
        return [ScoredChunk(chunk=chunk, score=0.9)]


@cache
def _catalog() -> Any:
    return load_catalog()


@cache
def _book() -> Any:
    return load_commercial_book()


def _deps(index: Any = None) -> ToolDeps:
    return ToolDeps(
        vector_store=index or EmptyIndex(),
        embedder=FakeEmbedder(),
        settings=SETTINGS,
        catalog=_catalog(),
        book=_book(),
        now=datetime(2026, 11, 24, 15, 0, tzinfo=UTC),
        institutional=load_institutional(),
    )


def _call(name: str, **args: Any) -> dict[str, Any]:
    return {"name": name, "args": args}


def _stream(
    steps: Sequence[ScriptStep], question: str, *, memory: Any = None, index: Any = None
) -> tuple[str, ToolAgentFinal]:
    chat = ScriptedToolChat(steps=list(steps))
    graph = build_tool_agent(_deps(index), chat, checkpointer=InMemorySaver(), store=memory)
    events = list(ToolAgentRunner(graph, SETTINGS).stream(OWNER, "hilo-1", question, []))
    text = "".join(event.text for event in events if isinstance(event, ToolAgentToken))
    final = events[-1]
    assert isinstance(final, ToolAgentFinal)
    return text, final


# --- Búsqueda documental vacía + herramienta con ``ok`` -----------------------------------


@pytest.mark.parametrize(
    "extra",
    [
        _call("fecha_actual"),
        _call("indicadores_disponibles"),
        _call("empresas_del_grupo"),
        _call("ciudades_con_cobertura"),
        _call("buscar_producto", familia="vaso", temperatura="caliente", uso="bebida_caliente"),
        _call("indicador_financiero", nombre="ebitda", periodo="2025-09"),
    ],
    ids=lambda extra: extra["name"],
)
def test_corpus_vacio_mas_otra_herramienta_no_libera_un_texto_inventado(
    extra: dict[str, Any],
) -> None:
    # Arrange
    steps = [
        ScriptStep(tool_calls=[_call("buscar_corpus", consulta="oficina de Carvajal en Tokio")]),
        ScriptStep(tool_calls=[extra]),
        ScriptStep(text=INVENTED),
    ]

    # Act
    text, final = _stream(steps, "¿Carvajal tiene oficina en Tokio?")

    # Assert
    assert "Tokio" not in text and "1990" not in text, (final.gate, text)
    assert NO_CONTEXT_ANSWER in text
    assert final.abstained


def test_corpus_vacio_mas_otra_herramienta_deja_pasar_lo_que_esta_respalda() -> None:
    # Arrange: el texto solo usa datos de empresas_del_grupo.
    steps = [
        ScriptStep(tool_calls=[_call("buscar_corpus", consulta="empresas de Carvajal")]),
        ScriptStep(tool_calls=[_call("empresas_del_grupo")]),
        ScriptStep(text="Entre sus unidades están Carvajal Empaques [2] y Carvajal Educación [3]."),
    ]

    # Act
    text, final = _stream(steps, "¿Qué empresas tiene Carvajal?")

    # Assert
    assert "Carvajal Empaques" in text
    assert not final.abstained


def test_con_evidencia_del_corpus_el_texto_se_transmite_como_antes() -> None:
    # Arrange: la búsqueda documental sí trae evidencia; fecha_actual no cambia el camino.
    answer = "Carvajal fue fundada en Cali en 1904 [1]."
    steps = [
        ScriptStep(tool_calls=[_call("buscar_corpus", consulta="fundación de Carvajal")]),
        ScriptStep(tool_calls=[_call("fecha_actual")]),
        ScriptStep(text=answer),
    ]

    # Act
    text, final = _stream(steps, "¿Cuándo se fundó Carvajal?", index=OneHitIndex())

    # Assert
    assert text == answer
    assert final.gate == "evidence"
    assert not final.abstained


# --- empresas_del_grupo con un nombre ajeno ------------------------------------------------


def test_un_nombre_ajeno_no_libera_una_afirmacion_de_pertenencia() -> None:
    # Arrange
    claim = "Sí: Empaques Flexa es una empresa del grupo Carvajal, como dice su página [1]."
    steps = [
        ScriptStep(tool_calls=[_call("empresas_del_grupo", nombre="Empaques Flexa")]),
        ScriptStep(text=claim),
    ]

    # Act
    text, final = _stream(steps, "¿Empaques Flexa es del grupo Carvajal?")

    # Assert
    assert "Flexa" not in text, (final.gate, text)
    assert final.gate != "evidence"


# --- La ciudad habitual no respalda datos de Carvajal (FR-017) -----------------------------

COFFEE = {"use": "bebida_caliente", "temperature": "caliente", "family": "vaso", "quantity": 500}


def test_la_ciudad_habitual_no_respalda_un_dato_de_carvajal() -> None:
    # Arrange: Yumbo no está en el directorio; solo la memoria la conoce.
    memory = InMemoryStore()
    remember(memory, OWNER, "ciudad_habitual", "Yumbo")
    claim = "Carvajal tiene su planta principal en Yumbo. ¿Te lo cotizo en Yumbo?"
    steps = [
        ScriptStep(tool_calls=[_call("cotizar", **COFFEE)]),
        ScriptStep(text=claim),
    ]

    # Act
    text, final = _stream(steps, "Cotiza 500 vasos para café", memory=memory)

    # Assert: sale la petición neutra con la sugerencia, no la afirmación del modelo.
    assert final.gate == "no_query"
    assert "planta" not in text, text
    assert "ciudad" in text.casefold()
    assert "Yumbo" in text
    assert text.rstrip().endswith("?")
