"""Fotos por WhatsApp (spec 051, US4, FR-024 y FR-025): adjunto de OpenClaw → puente MCP.

El puente lee el adjunto solo de la carpeta configurada, lo prepara igual que la web, cobra
el cupo solo si el clasificador respondió y entrega la recomendación en formato WhatsApp.
"""

import io
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from apps.api.chat.streaming import ChatEvent
from apps.mcp.bridge import (
    LIMIT_REACHED,
    PHOTO_REJECTED,
    PHOTO_UNAVAILABLE,
    WhatsAppBridge,
    read_inbound_photo,
)
from carvajal.application.ports import JudgeCall, JudgeUnavailable
from carvajal.application.quota import QuotaResult
from carvajal.food.photo import PreparedPhoto
from carvajal.food.profile import FoodProfile

CHAT = "+573001234567"
NAME = "22f1f9db-d6df-4816-912e-db5edca021a2.jpg"
EMPANADAS = FoodProfile(
    0.98, "frituras", 0.91, "panaderia", "solido", 1.0, "caliente", 0.96, 2.0, 0.8
)


def _jpeg(size: tuple[int, int] = (800, 600)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (220, 160, 40)).save(out, "JPEG")
    return out.getvalue()


@dataclass
class FakeClassifier:
    status: str | None = None
    fail: bool = False
    photos: list[PreparedPhoto] = field(default_factory=list)

    def unavailable_status(self) -> str | None:
        return self.status

    def classify(self, photo: PreparedPhoto) -> tuple[FoodProfile, JudgeCall]:
        self.photos.append(photo)
        if self.fail:
            raise JudgeUnavailable("timeout")
        return EMPANADAS, JudgeCall(kind="food", status="ok", latency_ms=180)


@dataclass
class FakePhotoChat:
    """Doble del servicio: clasifica, cobra y responde como ``ChatService.photo_stream``."""

    calls: list[tuple[str, str]] = field(default_factory=list)

    def stream(self, owner_key: str, question: str, **_: Any) -> Iterator[ChatEvent]:
        yield ChatEvent("done", {})

    def new_conversation(self, owner_key: str) -> Any:
        return None

    def photo_stream(
        self,
        owner_key: str,
        note: str,
        *,
        analyze: Callable[[], FoodProfile],
        charge: Callable[[], str | None],
        precheck: Callable[[], str | None] | None = None,
        **_: Any,
    ) -> Iterator[ChatEvent]:
        self.calls.append((owner_key, note))
        denied = precheck() if precheck is not None else None
        if denied:
            yield ChatEvent("error", {"message": denied, "incomplete": False})
            return
        try:
            profile = analyze()
        except Exception:
            yield ChatEvent("error", {"message": PHOTO_UNAVAILABLE, "incomplete": False})
            return
        denied = charge()
        if denied:
            yield ChatEvent("error", {"message": denied, "incomplete": False})
            return
        yield ChatEvent(
            "fallback",
            {
                "text": f"Veo **{profile.kind}**.\n\n1. **Contenedor** (ref. CO-CAZ-16)",
                "sources": [],
            },
        )
        yield ChatEvent("done", {"quote_ids": []})


def _bridge(
    media: Path,
    *,
    classifier: FakeClassifier | None = None,
    allowed: bool = True,
    room: bool = True,
    reader: bool = True,
) -> tuple[WhatsAppBridge, FakePhotoChat, list[str]]:
    chat = FakePhotoChat()
    charges: list[str] = []

    def quota(owner: str) -> QuotaResult:
        charges.append(owner)
        return QuotaResult(allowed=allowed)

    bridge = WhatsAppBridge(
        chat=chat,
        secret=b"secreto",
        quota=quota,
        quota_peek=lambda _owner: QuotaResult(allowed=room),
        photo_reader=partial(read_inbound_photo, directory=str(media)) if reader else None,
        food_classifier=classifier if classifier is not None else FakeClassifier(),
    )
    return bridge, chat, charges


@pytest.fixture()
def media(tmp_path: Path) -> Path:
    folder = tmp_path / "inbound"
    folder.mkdir()
    (folder / NAME).write_bytes(_jpeg())
    return folder


def test_la_foto_de_whatsapp_recibe_la_recomendacion_en_su_formato(media: Path) -> None:
    # Arrange
    bridge, chat, charges = _bridge(media)

    # Act
    text = bridge.consult("para mi local", CHAT, photo=f"media://inbound/{NAME}")

    # Assert
    assert "Veo *frituras*." in text and "(ref. CO-CAZ-16)" in text
    assert chat.calls == [(bridge.owner_key(CHAT), "para mi local")]
    assert charges == [bridge.owner_key(CHAT)]


def test_acepta_solo_el_nombre_del_archivo(media: Path) -> None:
    # Act
    text = _bridge(media)[0].consult("", CHAT, photo=NAME)

    # Assert
    assert "Veo *frituras*." in text


@pytest.mark.parametrize(
    "reference",
    [
        "../secreto.jpg",
        "media://inbound/../../etc/passwd",
        "/etc/hostname",
        "sub/carpeta.jpg",
        "documento.pdf",
        "no-existe.jpg",
    ],
)
def test_rechaza_referencias_fuera_de_la_carpeta_sin_cobrar(media: Path, reference: str) -> None:
    # Arrange
    classifier = FakeClassifier()
    bridge, chat, charges = _bridge(media, classifier=classifier)

    # Act
    text = bridge.consult("", CHAT, photo=reference)

    # Assert
    assert text == PHOTO_REJECTED
    assert classifier.photos == [] and charges == [] and chat.calls == []


def test_no_sigue_enlaces_simbolicos(media: Path, tmp_path: Path) -> None:
    # Arrange
    outside = tmp_path / "fuera.jpg"
    outside.write_bytes(_jpeg())
    os.symlink(outside, media / "enlace.jpg")

    # Act / Assert
    with pytest.raises(ValueError):
        read_inbound_photo("enlace.jpg", str(media))


def test_un_adjunto_que_no_es_imagen_se_rechaza(media: Path) -> None:
    # Arrange
    (media / "falsa.jpg").write_bytes(b"no soy una imagen")
    bridge, _chat, charges = _bridge(media)

    # Act
    text = bridge.consult("", CHAT, photo="falsa.jpg")

    # Assert
    assert PHOTO_REJECTED in text and charges == []


@pytest.mark.parametrize(
    "options",
    [{"reader": False}, {"classifier": FakeClassifier(status="circuit_open")}],
)
def test_sin_carpeta_o_sin_clasificador_no_hay_fotos(media: Path, options: dict[str, Any]) -> None:
    # Arrange
    bridge, chat, charges = _bridge(media, **options)

    # Act
    text = bridge.consult("", CHAT, photo=NAME)

    # Assert
    assert text == PHOTO_UNAVAILABLE and chat.calls == [] and charges == []


def test_si_el_clasificador_falla_no_cobra(media: Path) -> None:
    # Arrange
    bridge, _chat, charges = _bridge(media, classifier=FakeClassifier(fail=True))

    # Act
    text = bridge.consult("", CHAT, photo=NAME)

    # Assert
    assert text == PHOTO_UNAVAILABLE and charges == []


def test_sin_cupo_responde_el_limite(media: Path) -> None:
    # Act
    text = _bridge(media, allowed=False)[0].consult("", CHAT, photo=NAME)

    # Assert
    assert text == LIMIT_REACHED


def test_sin_cupo_la_foto_no_llega_al_clasificador(media: Path) -> None:
    # Arrange
    classifier = FakeClassifier()
    bridge, _chat, charges = _bridge(media, classifier=classifier, allowed=False, room=False)

    # Act
    text = bridge.consult("", CHAT, photo=NAME)

    # Assert
    assert text == LIMIT_REACHED
    assert classifier.photos == [] and charges == []


def test_si_no_se_puede_mirar_el_cupo_decide_el_cobro(media: Path) -> None:
    # Arrange
    bridge, _chat, charges = _bridge(media)

    def broken(_owner: str) -> QuotaResult:
        raise RuntimeError("base caída")

    bridge = replace(bridge, quota_peek=broken)

    # Act
    text = bridge.consult("", CHAT, photo=NAME)

    # Assert
    assert "Veo *frituras*." in text and charges == [bridge.owner_key(CHAT)]


def test_con_voz_la_foto_normaliza_el_texto_y_responde_en_formato_de_voz(media: Path) -> None:
    # Arrange
    bridge, chat, _charges = _bridge(media)

    # Act
    spoken = bridge.consult("es para 1,800 personas", CHAT, photo=NAME, voice=True)

    # Assert: el texto que acompaña la foto se trata como cualquier transcripción.
    assert chat.calls == [(bridge.owner_key(CHAT), "es para 1.800 personas")]
    assert spoken == bridge.photo("", CHAT, NAME).voice_message()
    assert "**" not in spoken and "*frituras*" not in spoken
