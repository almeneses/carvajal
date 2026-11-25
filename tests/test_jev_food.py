"""Clasificador de fotos con Jev (spec 051): petición con imagen, parseo y disponibilidad.

Sin red: TypeSafe responde desde ``httpx.MockTransport``. La respuesta de ejemplo es la
forma real que devolvió ``jev-1.14.0`` en la prueba previa (ajiaco, 2026-11-24).
"""

import base64
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from carvajal.adapters.judge import (
    JevClient,
    JevFoodClassifier,
    build_food_classifier,
    food_photo_health,
)
from carvajal.application.ports import JudgeUnavailable
from carvajal.food.photo import PreparedPhoto
from carvajal.food.profile import PHOTO_REF, parse_food_answers
from carvajal.infrastructure.settings import Settings

PHOTO = PreparedPhoto(b"\xff\xd8jpeg-de-prueba\xff\xd9", 600, 400)

AJIACO: dict[str, Any] = {
    "model": "jev-1.14.0",
    "answers": {
        "es_comida": {"type": "noul", "noul": 0.95},
        "tipo": {
            "type": "choice",
            "choice": "sopas",
            "confidence": 0.84,
            "probabilities": {"sopas": 0.85, "plato_fuerte": 0.15, "frituras": 0.0},
        },
        "consistencia": {
            "type": "choice",
            "choice": "liquido",
            "confidence": 0.83,
            "probabilities": {"solido": 0.0, "semiliquido": 0.11, "liquido": 0.89},
        },
        "temperatura": {
            "type": "choice",
            "choice": "caliente",
            "confidence": 1.0,
            "probabilities": {"caliente": 1.0, "fria": 0.0, "ambiente": 0.0},
        },
        "porcion": {"type": "score", "score": 1.93, "confidence": 0.85},
    },
    "usage": {"input_tokens": 3047, "output_tokens": 274},
}


def _classifier(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    timeout_s: float = 4.0,
    max_calls: int = 10,
) -> JevFoodClassifier:
    client = JevClient(
        api_key="ts-prueba",
        timeout_s=timeout_s,
        max_calls_per_day=max_calls,
        transport=httpx.MockTransport(handler),
    )
    return JevFoodClassifier(client, timeout_s=timeout_s)


def test_envia_la_foto_como_asset_con_la_cabecera_de_imagenes() -> None:
    # Arrange
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=AJIACO)

    classifier = _classifier(handler, timeout_s=4.0)

    # Act
    profile, call = classifier.classify(PHOTO)

    # Assert
    request = seen[0]
    body = json.loads(request.content)
    assert request.headers["TypeSafe-Danger-Zone-Images"] == "true"
    assert request.headers["Authorization"] == "Bearer ts-prueba"
    assert body["state"] == {"foto": PHOTO_REF}
    asset = body["assets"][PHOTO_REF]
    assert asset["type"] == "image" and asset["format"] == "jpeg"
    assert base64.b64decode(asset["data_base64"]) == PHOTO.data
    assert set(body["questions"]) == {"es_comida", "tipo", "consistencia", "temperatura", "porcion"}
    assert request.extensions["timeout"]["read"] == 4.0
    assert profile.kind == "sopas" and profile.kind_runner_up == "plato_fuerte"
    assert profile.temperature == "caliente" and profile.temperature_confidence == 1.0
    assert call.kind == "food" and call.status == "ok" and call.input_tokens == 3047


def test_la_peticion_no_lleva_texto_ni_identidad_de_la_persona() -> None:
    # Arrange
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json=AJIACO)

    # Act
    _classifier(handler).classify(PHOTO)

    # Assert: solo modelo, estado con la referencia opaca, preguntas fijas y la imagen.
    body = json.loads(seen[0])
    assert set(body) == {"model", "state", "questions", "assets"}


def test_una_respuesta_invalida_es_juez_no_disponible() -> None:
    # Arrange
    broken = {**AJIACO, "answers": {**AJIACO["answers"], "tipo": {"choice": "pizza"}}}
    classifier = _classifier(lambda request: httpx.Response(200, json=broken))

    # Act / Assert
    with pytest.raises(JudgeUnavailable) as error:
        classifier.classify(PHOTO)
    assert error.value.status == "invalid"


def test_sin_creditos_o_con_el_tope_no_llama() -> None:
    # Arrange
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(402, json={"detail": "sin créditos"})

    classifier = _classifier(handler, max_calls=1)

    # Act
    with pytest.raises(JudgeUnavailable):
        classifier.classify(PHOTO)
    status = classifier.unavailable_status()

    # Assert: 402 abre el circuito y el tope ya está en 1.
    assert calls == [1]
    assert status in {"circuit_open", "cap"}
    assert not classifier.available()


def test_parseo_sin_confidence_usa_la_mayor_probabilidad() -> None:
    # Arrange
    payload = json.loads(json.dumps(AJIACO))
    del payload["answers"]["tipo"]["confidence"]

    # Act
    profile, extras = parse_food_answers(payload)

    # Assert
    assert profile.kind_confidence == 0.85
    assert extras["food"]["tipo"] == "sopas"
    assert "foto" not in json.dumps(extras)


def _settings(**values: Any) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_el_clasificador_no_depende_de_jev_enabled() -> None:
    # Act
    on = build_food_classifier(_settings(typesafe_api_key="ts-x", jev_enabled=False))
    off = build_food_classifier(_settings(typesafe_api_key="ts-x", food_photo_enabled=False))
    keyless = build_food_classifier(_settings(typesafe_api_key=""))

    # Assert
    assert isinstance(on, JevFoodClassifier)
    assert on.timeout_s == 4.0 and on.client.max_calls_per_day == 500
    assert off is None and keyless is None


def test_health_de_la_foto_sin_red() -> None:
    # Arrange
    settings = _settings(typesafe_api_key="ts-x")
    classifier = build_food_classifier(settings)

    # Act / Assert
    assert food_photo_health(_settings())["status"] == "off"
    assert food_photo_health(settings)["status"] == "ok"
    assert food_photo_health(settings, classifier) == {
        "status": "ok",
        "calls_today": 0,
        "last_error": None,
    }
