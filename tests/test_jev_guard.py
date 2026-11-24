"""La fixture *autouse* de ``conftest.py`` aísla las pruebas de Jev (spec 046, T003; R13).

Aunque el ``.env`` local traiga la clave y ``JEV_ENABLED=true``, dentro de una prueba Jev
está apagado y cualquier conexión de httpx a ``api.typesafe.ai`` falla.
"""

import os
from pathlib import Path

import httpx
import pytest

from carvajal.adapters.judge import build_judge
from carvajal.infrastructure.settings import Settings


def test_la_fixture_apaga_jev_aunque_el_env_traiga_la_clave(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: un ``.env`` con la clave y Jev encendido, como el del owner.
    (tmp_path / ".env").write_text(
        "JEV_ENABLED=true\nTYPESAFE_API_KEY=clave-del-env\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    # Act
    settings = Settings()

    # Assert: las variables de entorno de la fixture ganan sobre el ``.env``.
    assert os.environ["JEV_ENABLED"] == "false"
    assert settings.jev_enabled is False
    assert settings.typesafe_api_key.get_secret_value() == ""
    assert settings.jev_active is False
    assert build_judge(settings) is None


def test_la_fixture_bloquea_conexiones_a_typesafe() -> None:
    # ``AssertionError``: pytest importa ``conftest`` con otro nombre de módulo.
    with httpx.Client() as client, pytest.raises(AssertionError, match="bloqueada"):
        client.post("https://api.typesafe.ai/v1/systemone", json={})
