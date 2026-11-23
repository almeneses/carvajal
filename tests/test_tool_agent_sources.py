"""Pruebas del registro de fuentes del agente con herramientas (spec 044, R6)."""

import copy
import json
from dataclasses import asdict, replace
from typing import Any

from carvajal.agent.toolagent.sources import SourceRegistry
from carvajal.application.ask import Source


def _source(chunk_id: str = "c1", **overrides: Any) -> Source:
    base = Source(
        chunk_id=chunk_id,
        slug=f"doc-{chunk_id}",
        section="Historia",
        layer="core",
        score=0.9,
        excerpt=f"Fragmento {chunk_id}",
        url=f"https://www.carvajal.com/{chunk_id}",
        title=f"Título {chunk_id}",
    )
    return replace(base, **overrides)


def test_register_assigns_consecutive_numbers_from_one() -> None:
    # Arrange
    batch = [_source("a"), _source("b"), _source("c")]

    # Act
    result = SourceRegistry.register([], batch)

    # Assert
    assert result.numbers == [1, 2, 3]
    assert [entry["n"] for entry in result.sources] == [1, 2, 3]
    assert [entry["chunk_id"] for entry in result.sources] == ["a", "b", "c"]


def test_register_continues_numbering_across_tools() -> None:
    # Arrange
    first = SourceRegistry.register([], [_source("a"), _source("b")])

    # Act
    second = SourceRegistry.register(first.sources, [_source("c")])

    # Assert
    assert second.numbers == [3]
    assert [entry["n"] for entry in second.sources] == [1, 2, 3]


def test_register_deduplicates_by_chunk_id_and_reuses_number() -> None:
    # Arrange
    first = SourceRegistry.register([], [_source("a"), _source("b")])
    repeated = _source("b", score=0.1, excerpt="otro recorte")

    # Act
    second = SourceRegistry.register(first.sources, [repeated, _source("c")])

    # Assert
    assert second.numbers == [2, 3]
    assert len(second.sources) == 3
    assert second.sources[1]["score"] == 0.9


def test_register_deduplicates_within_the_same_batch() -> None:
    # Arrange
    batch = [_source("a"), _source("a"), _source("b")]

    # Act
    result = SourceRegistry.register([], batch)

    # Assert
    assert result.numbers == [1, 1, 2]
    assert len(result.sources) == 2


def test_register_deduplicates_by_url_and_title_without_chunk_id() -> None:
    # Arrange
    sede = _source("", url="https://www.carvajal.com/sedes", title="Sede Cali")
    same_sede = _source("", url="https://www.carvajal.com/sedes", title="Sede Cali", score=0.2)
    other_sede = _source("", url="https://www.carvajal.com/sedes", title="Sede Bogotá")

    # Act
    result = SourceRegistry.register([], [sede, same_sede, other_sede])

    # Assert
    assert result.numbers == [1, 1, 2]
    assert [entry["title"] for entry in result.sources] == ["Sede Cali", "Sede Bogotá"]


def test_register_does_not_merge_chunk_with_url_only_source() -> None:
    # Arrange
    chunk = _source("a", url="https://www.carvajal.com/x", title="X")
    loose = _source("", url="https://www.carvajal.com/x", title="X")

    # Act
    result = SourceRegistry.register([], [chunk, loose])

    # Assert
    assert result.numbers == [1, 2]


def test_register_accepts_serialized_dicts() -> None:
    # Arrange
    payload = asdict(_source("a"))

    # Act
    result = SourceRegistry.register([], [payload, _source("a")])

    # Assert
    assert result.numbers == [1, 1]
    assert result.sources[0] == {**payload, "n": 1}


def test_register_does_not_mutate_input() -> None:
    # Arrange
    existing = SourceRegistry.register([], [_source("a")]).sources
    snapshot = copy.deepcopy(existing)

    # Act
    result = SourceRegistry.register(existing, [_source("b")])
    result.sources[0]["title"] = "cambiado"

    # Assert
    assert existing == snapshot
    assert result.sources is not existing


def test_register_is_deterministic_and_keeps_stable_order() -> None:
    # Arrange
    batches = [[_source("b"), _source("a")], [_source("c"), _source("b")]]

    # Act
    runs = []
    for _ in range(2):
        state: list[dict] = []
        for batch in batches:
            state = SourceRegistry.register(state, batch).sources
        runs.append(state)

    # Assert
    assert runs[0] == runs[1]
    assert [entry["chunk_id"] for entry in runs[0]] == ["b", "a", "c"]


def test_register_with_empty_batch_returns_copy_without_numbers() -> None:
    # Arrange
    existing = SourceRegistry.register([], [_source("a")]).sources

    # Act
    result = SourceRegistry.register(existing, [])

    # Assert
    assert result.numbers == []
    assert result.sources == existing


def test_sources_are_json_serializable() -> None:
    # Arrange
    result = SourceRegistry.register([], [_source("a"), _source("", title="Sede")])

    # Act
    dumped = json.dumps(result.sources, ensure_ascii=False)

    # Assert
    assert json.loads(dumped) == result.sources


def test_to_sources_round_trips_in_number_order() -> None:
    # Arrange
    originals = [_source("a"), _source("b", source_type="tercero", origin="prensa")]
    state = SourceRegistry.register([], originals).sources
    shuffled = list(reversed(state))

    # Act
    sources = SourceRegistry.to_sources(shuffled)

    # Assert
    assert sources == tuple(originals)
    assert all(isinstance(source, Source) for source in sources)


def test_to_sources_tolerates_missing_and_extra_fields() -> None:
    # Arrange
    state = [{"n": 1, "chunk_id": "a", "score": "0.5", "extra": "ignorado"}]

    # Act
    (source,) = SourceRegistry.to_sources(state)

    # Assert
    assert source.chunk_id == "a"
    assert source.score == 0.5
    assert source.slug == ""
    assert source.source_type == "oficial"
