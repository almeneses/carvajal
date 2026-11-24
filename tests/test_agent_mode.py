"""Política del modo del agente (spec 044): qué modo atenderá los turnos según ``Settings``.

``tools`` es el valor por defecto, pero solo arranca con ``LLM_PROVIDER=deepseek`` y
DeepSeek completo. Con otro proveedor los turnos siguen en ``routes`` aunque haya clave.
"""

from typing import Literal

import pytest

from carvajal.infrastructure.agent_mode import agent_mode_status, chat_llm_blocker
from carvajal.infrastructure.settings import Settings

DEEPSEEK_URL = "https://api.deepseek.com/v1"


def _settings(
    *,
    agent_mode: Literal["routes", "tools"] = "tools",
    llm_provider: str = "ollama",
    deepseek_api_key: str = "",
    deepseek_model: str = "deepseek-chat",
    deepseek_base_url: str = DEEPSEEK_URL,
) -> Settings:
    """``Settings`` sin el ``.env`` del desarrollador; el resto, valores por defecto."""

    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        agent_mode=agent_mode,
        llm_provider=llm_provider,
        deepseek_api_key=deepseek_api_key,
        deepseek_model=deepseek_model,
        deepseek_base_url=deepseek_base_url,
    )


def test_por_defecto_con_deepseek_completo_se_espera_tools() -> None:
    # Arrange
    settings = _settings(llm_provider="deepseek", deepseek_api_key="sk-prueba")

    # Act
    status = agent_mode_status(settings)

    # Assert
    assert (status.configured, status.expected, status.reason) == ("tools", "tools", None)


def test_por_defecto_sin_clave_se_espera_routes_por_la_clave() -> None:
    # Arrange: valores por defecto de ``Settings`` (``LLM_PROVIDER=deepseek``, sin clave).
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    # Act
    status = agent_mode_status(settings)

    # Assert
    assert (status.configured, status.expected, status.reason) == (
        "tools",
        "routes",
        "missing_deepseek_api_key",
    )


@pytest.mark.parametrize(
    ("proveedor", "clave", "motivo"),
    [
        ("deepseek", "", "missing_deepseek_api_key"),
        (" DeepSeek ", "  ", "missing_deepseek_api_key"),
        ("deepseek", "sk-prueba", None),
        ("ollama", "", None),
        ("claude", "", None),
    ],
)
def test_chat_llm_blocker_solo_aplica_a_deepseek(
    proveedor: str, clave: str, motivo: str | None
) -> None:
    # Arrange
    settings = _settings(llm_provider=proveedor, deepseek_api_key=clave)

    # Act
    resultado = chat_llm_blocker(settings)

    # Assert
    assert resultado == motivo


@pytest.mark.parametrize(
    ("proveedor", "clave", "modelo", "url", "motivo"),
    [
        ("ollama", "sk-prueba", "deepseek-chat", DEEPSEEK_URL, "llm_provider"),
        ("claude", "sk-prueba", "deepseek-chat", DEEPSEEK_URL, "llm_provider"),
        ("deepseek", "", "deepseek-chat", DEEPSEEK_URL, "missing_deepseek_api_key"),
        ("deepseek", "  ", "deepseek-chat", DEEPSEEK_URL, "missing_deepseek_api_key"),
        ("deepseek", "sk-prueba", "", DEEPSEEK_URL, "missing_deepseek_model"),
        ("deepseek", "sk-prueba", "deepseek-chat", "", "missing_deepseek_base_url"),
    ],
)
def test_motivo_por_el_que_tools_no_arranca(
    proveedor: str, clave: str, modelo: str, url: str, motivo: str
) -> None:
    # Arrange
    settings = _settings(
        llm_provider=proveedor, deepseek_api_key=clave, deepseek_model=modelo, deepseek_base_url=url
    )

    # Act
    status = agent_mode_status(settings)

    # Assert
    assert status.expected == "routes"
    assert status.reason == motivo


def test_routes_explicito_no_tiene_motivo_aunque_deepseek_este_completo() -> None:
    # Arrange
    settings = _settings(agent_mode="routes", llm_provider="deepseek", deepseek_api_key="sk-x")

    # Act
    salud = agent_mode_status(settings).as_health()

    # Assert
    assert salud == {
        "mode_configured": "routes",
        "mode_expected": "routes",
        "tools_unavailable_reason": None,
    }


def test_el_bloque_de_salud_no_expone_la_clave_ni_el_modelo() -> None:
    # Arrange
    settings = _settings(llm_provider="deepseek", deepseek_api_key="sk-secreta-123")

    # Act
    salud = agent_mode_status(settings).as_health()

    # Assert
    assert "sk-secreta-123" not in repr(salud)
    assert settings.deepseek_model not in repr(salud)
