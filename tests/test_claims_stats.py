"""Contadores de la compuerta de afirmaciones y su bloque ``claims`` (medición 2026-11-26)."""

import threading
from datetime import UTC, datetime
from typing import Any

import pytest

from carvajal.agent.gatekeeper.stats import HISTOGRAM_BINS, ClaimsStats, claims_health
from carvajal.infrastructure.settings import Settings

FIXED = datetime(2026, 11, 26, 15, 30, tzinfo=UTC)


def _stats() -> ClaimsStats:
    return ClaimsStats(clock=lambda: FIXED)


def test_sin_turnos_las_tasas_son_nulas() -> None:
    # Arrange
    stats = _stats()

    # Act
    snapshot = stats.snapshot()

    # Assert
    assert snapshot == {
        "since": "2026-11-26T15:30:00Z",
        "turns": 0,
        "judged": 0,
        "supported": 0,
        "unsupported": 0,
        "substituted": 0,
        "substitution_rate": None,
        "unsupported_rate": None,
        "unavailable": {},
        "skipped": {},
        "off": 0,
        "p_histogram": [0] * HISTOGRAM_BINS,
    }


def test_cuenta_juzgadas_sustituidas_y_motivos() -> None:
    # Arrange
    stats = _stats()

    # Act
    stats.record("supported", p=0.93)
    stats.record("supported", p=0.61)
    stats.record("supported", p=1.0)
    stats.record("unsupported", p=0.27, substituted=True)
    stats.record("unavailable", reason="timeout")
    stats.record("unavailable", reason="timeout")
    stats.record("skipped", reason="atajo")
    stats.record("skipped")
    stats.record("off")

    # Assert
    snapshot = stats.snapshot()
    assert snapshot["turns"] == 9
    assert (snapshot["judged"], snapshot["supported"], snapshot["unsupported"]) == (4, 3, 1)
    assert snapshot["substituted"] == 1
    assert snapshot["substitution_rate"] == 0.25
    assert snapshot["unsupported_rate"] == 0.25
    assert snapshot["unavailable"] == {"timeout": 2}
    assert snapshot["skipped"] == {"atajo": 1, "sin_motivo": 1}
    assert snapshot["off"] == 1
    # p = 1,0 cae en la última cubeta, no fuera del histograma.
    assert snapshot["p_histogram"] == [0, 0, 1, 0, 0, 0, 1, 0, 0, 2]


def test_en_sombra_la_no_respaldada_no_cuenta_como_sustituida() -> None:
    # Arrange
    stats = _stats()

    # Act: sombra juzga y registra sin sustituir.
    stats.record("unsupported", p=0.2, substituted=False)
    stats.record("supported", p=0.8)

    # Assert
    snapshot = stats.snapshot()
    assert snapshot["substituted"] == 0
    assert snapshot["substitution_rate"] == 0.0
    assert snapshot["unsupported_rate"] == 0.5


def test_record_verdict_lee_el_dict_del_data_model() -> None:
    # Arrange
    stats = _stats()

    # Act
    stats.record_verdict({"status": "skipped", "p": None, "reason": "solo_preguntas"})
    stats.record_verdict({"status": "unsupported", "p": 0.31, "substituted": True})

    # Assert
    snapshot = stats.snapshot()
    assert snapshot["skipped"] == {"solo_preguntas": 1}
    assert snapshot["substituted"] == 1
    assert snapshot["p_histogram"][3] == 1


def test_un_estado_desconocido_se_ignora() -> None:
    # Arrange
    stats = _stats()

    # Act
    stats.record("raro", p=0.4)

    # Assert
    assert stats.snapshot()["turns"] == 0


def test_reset_vuelve_a_cero() -> None:
    # Arrange
    stats = _stats()
    stats.record("unsupported", p=0.1, substituted=True)

    # Act
    stats.reset()

    # Assert
    assert stats.snapshot()["turns"] == 0
    assert stats.snapshot()["substituted"] == 0


def test_los_contadores_son_seguros_entre_hilos() -> None:
    # Arrange
    stats = _stats()
    per_thread = 500

    def work() -> None:
        for _ in range(per_thread):
            stats.record("unsupported", p=0.3, substituted=True)
            stats.record("supported", p=0.9)

    threads = [threading.Thread(target=work) for _ in range(8)]

    # Act
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Assert
    snapshot = stats.snapshot()
    assert snapshot["judged"] == 8 * per_thread * 2
    assert snapshot["substituted"] == 8 * per_thread
    assert snapshot["substitution_rate"] == 0.5
    assert sum(snapshot["p_histogram"]) == snapshot["judged"]


@pytest.mark.parametrize(
    ("env", "active", "expected"),
    [
        pytest.param({"deepseek_api_key": "x"}, None, "off", id="jev-apagado"),
        pytest.param(
            {"jev_enabled": True, "typesafe_api_key": "x", "deepseek_api_key": "x"},
            None,
            "enforce",
            id="jev-encendido",
        ),
        pytest.param(
            {
                "jev_enabled": True,
                "typesafe_api_key": "x",
                "deepseek_api_key": "x",
                "jev_claims_mode": "shadow",
            },
            None,
            "shadow",
            id="sombra",
        ),
        pytest.param(
            {
                "jev_enabled": True,
                "typesafe_api_key": "x",
                "deepseek_api_key": "x",
                "agent_mode": "routes",
            },
            None,
            "off",
            id="modo-routes",
        ),
        # El bloque ``agent`` dirá ``routes``: la compuerta no puede publicar ``enforce``.
        pytest.param(
            {
                "jev_enabled": True,
                "typesafe_api_key": "x",
                "deepseek_api_key": "x",
                "llm_provider": "ollama",
            },
            None,
            "off",
            id="tools-sin-deepseek",
        ),
        pytest.param(
            {"jev_enabled": True, "typesafe_api_key": "x", "deepseek_api_key": ""},
            None,
            "off",
            id="tools-sin-clave-deepseek",
        ),
        pytest.param(
            {"jev_enabled": True, "typesafe_api_key": "x"}, False, "off", id="runtime-sin-juez"
        ),
    ],
)
def test_claims_health_publica_modo_umbral_y_contadores(
    env: dict[str, Any], active: bool | None, expected: str
) -> None:
    # Arrange
    settings = Settings(**env)
    stats = _stats()
    stats.record("unsupported", p=0.2, substituted=True)

    # Act
    block = claims_health(settings, stats, active=active)

    # Assert
    assert block["mode"] == expected
    assert block["threshold"] == 0.3
    assert block["substituted"] == 1
    assert block["substitution_rate"] == 1.0
    assert "x" not in {str(value) for value in block.values()}
