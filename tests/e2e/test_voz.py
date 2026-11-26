"""Ensayo de voz: dictado y lectura en la web, y la nota de voz de WhatsApp por MCP.

- **Web** (spec 047): un turno con ``voz=1`` trae ``voz_resumen`` en ``done``;
  ``/api/voz/hablar`` lo sintetiza en MP3 a partir del id del mensaje; ese audio, pasado a
  Ogg/Opus con ``ffmpeg`` como lo grabaría ``MediaRecorder``, vuelve por
  ``/api/voz/transcribir``, y su texto sirve como el siguiente turno dictado. Así se prueban los
  dos proveedores reales sin micrófono.
- **WhatsApp**: OpenClaw transcribe la nota y llama a ``consultar_carvajal`` con
  ``voz=true``. La respuesta empieza por el resumen para el TTS, sin enlaces, y deja el texto
  escrito o los enlaces tras su cabecera.

Gastan STT y TTS de OpenAI y turnos del agente: llevan ``gasto``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

import pytest
from tests.e2e.clientes import Mcp, Web

pytestmark = pytest.mark.gasto

PEDIDO_VOZ = "Cotízame 1.000 vasos para bebida fría en Bogotá"
TEXT_HEADER = "--- TEXTO (enviar como mensaje escrito, no leer en voz alta) ---"
LINKS_HEADER = "--- ENLACES (enviar como texto, no leer en voz alta) ---"
URL = re.compile(r"https?://\S+")


@pytest.fixture
def voz_servidor(salud: dict[str, Any]) -> None:
    voz = salud.get("voice") or {}
    if not voz.get("available"):
        pytest.skip(f"voz del servidor apagada: {voz}")


def _ogg(mp3: bytes) -> bytes:
    if shutil.which("ffmpeg") is None:
        pytest.skip("sin ffmpeg para convertir el MP3 a Ogg/Opus")
    proceso = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-c:a", "libopus", "-f", "ogg", "pipe:1"],
        input=mp3,
        capture_output=True,
        check=True,
        timeout=60,
    )
    return proceso.stdout


def _palabras(texto: str) -> set[str]:
    return {p for p in re.findall(r"[a-záéíóúñ0-9]+", texto.casefold()) if len(p) > 3}


def test_web_dictado_y_lectura_de_ida_y_vuelta(web: Web, voz_servidor: None) -> None:
    # Arrange: un turno dictado con su resumen hablado.
    hilo = web.nuevo_hilo()
    primero = web.turno(PEDIDO_VOZ, hilo, voz=True)
    assert primero.status == 200 and primero.done, primero.resumen()
    resumen = str(primero.done.get("voz_resumen") or "")
    mensaje = str(primero.done.get("message_id") or "")
    assert resumen, f"sin voz_resumen en done\n{primero.resumen()}"
    assert not URL.search(resumen), f"el resumen hablado lee un enlace: {resumen!r}"
    assert mensaje, primero.resumen()

    # Act: TTS del mensaje y STT de ese audio.
    hablado = web.cliente.post(
        "/api/voz/hablar", data={"message_id": mensaje, "csrf_token": web.csrf}
    )
    assert hablado.status_code == 200, hablado.text[:300]
    assert hablado.headers["content-type"].startswith("audio/")
    assert "no-store" in hablado.headers.get("cache-control", "")
    audio = _ogg(hablado.content)
    transcrito = web.cliente.post(
        "/api/voz/transcribir",
        files={"audio": ("nota.ogg", audio, "audio/ogg")},
        headers={"X-CSRF-Token": web.csrf},
    )

    # Assert: el texto transcrito se parece a lo que se leyó y sirve como turno dictado.
    assert transcrito.status_code == 200, transcrito.text[:300]
    texto = str(transcrito.json()["texto"])
    comunes = _palabras(texto) & _palabras(resumen)
    assert len(comunes) >= 3, f"transcripción {texto!r} lejos del resumen {resumen!r}"
    segundo = web.turno(texto, hilo, voz=True)
    assert segundo.status == 200 and segundo.done and not segundo.error, segundo.resumen()
    assert segundo.done.get("voz_resumen"), segundo.resumen()


def test_web_hablar_no_sintetiza_mensajes_ajenos(web: Web, voz_servidor: None) -> None:
    # Arrange
    turno = web.turno(PEDIDO_VOZ, web.nuevo_hilo(), voz=True)
    mensaje = str(turno.done.get("message_id") or "")
    assert mensaje, turno.resumen()
    otro = Web(web.base_url, Web.visitante_para(f"ajeno:{mensaje}"))

    # Act
    try:
        respuesta = otro.cliente.post(
            "/api/voz/hablar", data={"message_id": mensaje, "csrf_token": otro.csrf}
        )
    finally:
        otro.cerrar()

    # Assert: no es un TTS público.
    assert respuesta.status_code == 404, respuesta.text[:200]


def test_whatsapp_nota_de_voz_responde_con_resumen_sin_enlaces(mcp: Mcp, conversacion: str) -> None:
    # Act
    respuesta = mcp.consultar(PEDIDO_VOZ, conversacion, nueva=True, voz=True)

    # Assert: primero el resumen para TTS, sin URL; lo demás tras su cabecera.
    assert respuesta, "respuesta vacía"
    hablado = respuesta
    for cabecera in (TEXT_HEADER, LINKS_HEADER):
        hablado = hablado.split(cabecera, 1)[0]
    hablado = hablado.strip()
    assert hablado, f"sin resumen hablado:\n{respuesta[:800]}"
    assert not URL.search(hablado), f"el resumen hablado trae enlaces:\n{hablado}"
    # VOICE_TTS_MAX_CHARS (1200 por defecto) recorta lo que se sintetiza.
    assert len(hablado) <= 1200, f"resumen hablado demasiado largo ({len(hablado)}):\n{hablado}"
