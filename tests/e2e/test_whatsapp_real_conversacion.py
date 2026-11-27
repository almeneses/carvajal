"""WhatsApp real de punta a punta: texto, foto y nota de voz desde WhatsApp Web (docs/ensayo.md).

El número de WhatsApp Web del Firefox del owner está **fuera** de ``allowFrom`` (así lo exige
``test_whatsapp_real.py``). Esta prueba lo agrega **temporalmente**, conversa con el bot como
un cliente y lo quita al terminar, pase lo que pase. Es la única prueba del ensayo que escribe
en la configuración del gateway, así que solo corre con ``ENSAYO_WA_CONVERSACION=1``
(``make ensayo-whatsapp-real``) y nunca dentro de ``make ensayo-completo``.

Pasos (el agente no es determinista: se exigen marcas mínimas, nunca precios):

0. «nuevo» → conversación limpia (sin el modelo).
1. «Cotízame 500 vasos para bebida fría en Bogotá» → opciones («Opciones» o «Recomendada»).
2. «dame esa» → «Agregué».
3. La foto del ajiaco → una recomendación, nunca «No pude abrir esa foto» (WhatsApp entrega
   las fotos en WebP; antes de la PR #291 se rechazaban).
4. Una nota de voz **grabada** (micrófono virtual de PipeWire) → alguna respuesta. Sin
   PipeWire el paso se salta con un aviso.
5. «cancela todo» → «cancelé».

Guarda la conversación (sin números) en ``ENSAYO_WA_DIR`` (por defecto
``data/ensayo-whatsapp/<run_id>/conversacion.md``). Al quitar el número de ``allowFrom`` no
reescribe una copia vieja: lee la lista vigente y quita solo ese número, porque otras personas
pueden cambiar el gateway mientras tanto (pasó el 2026-11-27).
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.e2e.clientes import Config, Ssh
from tests.e2e.test_whatsapp_real import (  # noqa: F401 (fixtures)
    _JS_MI_NUMERO,
    _estado_listo,
    _gateway,
    digitos,
    firefox,
    lista_json,
    pestana,
    redactar,
    url_envio,
)
from tests.e2e.webdriver import Sesion
from tests.e2e.whatsapp_web import (
    Burbuja,
    adjuntar_foto,
    burbujas,
    copia_legible,
    esperar_respuestas,
    nota_de_voz,
    pipewire_disponible,
    sin_numero,
)

pytestmark = [
    pytest.mark.gasto,
    # Antes de pedir Firefox o SSH: dentro de ``ensayo-completo`` ni se engancha al navegador.
    pytest.mark.skipif(
        os.environ.get("ENSAYO_WA_CONVERSACION") != "1",
        reason="sin ENSAYO_WA_CONVERSACION=1: escribe en allowFrom (make ensayo-whatsapp-real)",
    ),
]

CLAVE = "channels.whatsapp.allowFrom"
FIXTURES = Path(__file__).parent / "fixtures"
# Carpeta legible por el Firefox de snap (bajo el home, ignorada por git).
MEDIOS = Path("data/ensayo-whatsapp/medios")
NO_ABRE_FOTO = "No pude abrir esa foto"
# Marca de que esta prueba agregó el número de WhatsApp Web a allowFrom: guarda su huella SHA-256,
# nunca el número. Si el proceso muere antes de quitarlo (Ctrl-C, SSH caído), la siguiente
# corrida lo quita al empezar, solo si la huella coincide y el número sigue en la lista.
PENDIENTE = Path("data/ensayo-whatsapp/.allowfrom-pendiente")


def _carpeta(config: Config) -> Path:
    base = os.environ.get("ENSAYO_WA_DIR") or f"data/ensayo-whatsapp/{config.run_id}"
    carpeta = Path(base)
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


@pytest.fixture
def numero_permitido(
    config: Config,
    ssh_openclaw: Ssh,
    pestana: Sesion,  # noqa: F811
) -> Iterator[Sesion]:
    """El número de WhatsApp Web en ``allowFrom`` durante la prueba; al salir, se quita.

    Quitarlo nunca pasa en silencio: se reintenta y, si no se logra (SSH caído, salida
    ilegible o la lista quedaría vacía), la prueba **falla** pidiendo quitarlo a mano.
    """

    usuario = config.openclaw_user
    pestana.navegar(url_envio(config.wa_bot, ""))
    estado = pestana.esperar(lambda: _estado_listo(pestana), timeout=90, intervalo=1)
    if estado == "qr":
        pytest.skip("WhatsApp Web no tiene sesión en este Firefox (pide el código QR)")
    assert estado == "listo", f"WhatsApp Web no quedó listo (estado={estado})"
    mio = digitos(str(pestana.ejecutar(_JS_MI_NUMERO) or ""))
    if not mio or mio == digitos(config.wa_bot):
        pytest.skip("WhatsApp Web no tiene un número propio distinto del bot")

    def lista() -> list[str]:
        salida = _gateway(ssh_openclaw, usuario, f"openclaw config get {CLAVE}", saltar=False)
        if "[" not in salida:
            raise RuntimeError("openclaw no devolvió una lista para allowFrom")
        return [str(n) for n in lista_json(salida)]

    def fijar(valor: list[str]) -> None:
        comando = f"openclaw config set {CLAVE} {shlex.quote(json.dumps(valor))} --strict-json"
        _gateway(ssh_openclaw, usuario, comando, saltar=False)

    huella = hashlib.sha256(mio.encode()).hexdigest()
    original = lista()
    if PENDIENTE.exists():
        # La dejó una corrida que murió antes de quitar el número. Solo se actúa si la huella es
        # la de este número (nunca se guarda el número) y si sigue en la lista.
        if PENDIENTE.read_text().strip() == huella and any(digitos(n) == mio for n in original):
            _quitar_numero(lista, fijar, mio)
            original = lista()
        PENDIENTE.unlink(missing_ok=True)
    if any(digitos(n) == mio for n in original):
        yield pestana  # ya estaba permitido (lo puso el owner): no se toca nada
        return
    PENDIENTE.parent.mkdir(parents=True, exist_ok=True)
    PENDIENTE.write_text(huella + "\n")  # huella del número que la prueba agrega
    try:
        fijar([*original, f"+{mio}"])
        yield pestana
    finally:
        _quitar_numero(lista, fijar, mio)
        PENDIENTE.unlink(missing_ok=True)


def _quitar_numero(lista, fijar, mio: str) -> None:
    """Quita ``mio`` de la lista vigente (no de una copia vieja); falla si no lo consigue."""

    motivo = ""
    for _intento in range(3):
        try:
            vigente = lista()
            if all(digitos(n) != mio for n in vigente):
                return
            restante = sin_numero(vigente, mio)
            if not restante:
                motivo = "la lista quedaría vacía (alguien quitó los demás números)"
                break
            fijar(restante)
        except BaseException as error:
            motivo = f"{type(error).__name__}: {redactar(str(error))[:200]}"
    pytest.fail(
        f"el número de prueba sigue en allowFrom ({motivo or 'sin confirmar'}): quítalo a mano "
        "(docs/ensayo.md, «Conversación por WhatsApp real (foto y voz)»)"
    )


def _paso(sesion: Sesion, accion, transcripcion: list[str], etiqueta: str) -> list[Burbuja]:
    antes = {b.id for b in burbujas(sesion)}
    accion()
    respuestas = esperar_respuestas(sesion, antes)
    transcripcion.append(f"**👤 {etiqueta}**\n")
    transcripcion += [redactar(b.texto) + "\n" for b in respuestas] or ["_(sin respuesta)_\n"]
    transcripcion.append("---\n")
    return respuestas


def _texto(sesion: Sesion, mensaje: str) -> None:
    from tests.e2e.test_whatsapp_real import COMPOSITOR, _enviar

    compositor = next((e for css in COMPOSITOR if (e := sesion.buscar(css))), None)
    assert compositor, "no se encontró el compositor de WhatsApp Web"
    sesion.clic(compositor)
    sesion.teclear(mensaje)
    _enviar(sesion, mensaje)


def _wav(ogg: Path, carpeta: Path) -> Path | None:
    """La nota de voz en WAV para ``pw-play`` (con ffmpeg); ``None`` si no se puede."""

    import shutil
    import subprocess

    if not shutil.which("ffmpeg") or not ogg.exists():
        return None
    wav = carpeta / "nota-voz.wav"
    hecho = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(ogg), "-ar", "48000", "-ac", "2", str(wav)],
        check=False,
    )
    return wav if hecho.returncode == 0 else None


def _une(respuestas: list[Burbuja]) -> str:
    return "\n".join(b.texto for b in respuestas)


def test_conversacion_real_con_texto_foto_y_voz(config: Config, numero_permitido: Sesion) -> None:
    # Arrange
    sesion = numero_permitido
    carpeta = _carpeta(config)
    medios = MEDIOS.resolve()
    foto = copia_legible(FIXTURES / "ajiaco.jpg", medios)
    voz = _wav(FIXTURES / "nota-voz.ogg", medios)
    transcripcion = [f"# WhatsApp real · {config.run_id}\n\n"]

    # Act / Assert, paso a paso: un paso que falla deja escrita la transcripción.
    try:
        # Conversación limpia: «nuevo» la reinicia sin el modelo (una corrida cortada deja
        # el carrito con el vaso y «dame esa» respondería «Actualicé»).
        r = _paso(sesion, lambda: _texto(sesion, "nuevo"), transcripcion, "nuevo")
        assert r, "«nuevo» no tuvo respuesta: ¿el bot atiende este número?"

        r = _paso(
            sesion,
            lambda: _texto(
                sesion,
                f"Hola, ensayo {config.run_id}: cotízame 500 vasos para bebida fría en Bogotá",
            ),
            transcripcion,
            "cotízame 500 vasos para bebida fría en Bogotá",
        )
        assert r and ("Opciones" in _une(r) or "Recomendada" in _une(r)), "sin opciones de vasos"

        r = _paso(sesion, lambda: _texto(sesion, "dame esa"), transcripcion, "dame esa")
        assert r and ("Agregué" in _une(r) or "Actualicé" in _une(r)), (
            "«dame esa» no agregó la línea"
        )

        r = _paso(sesion, lambda: adjuntar_foto(sesion, foto), transcripcion, "(foto del ajiaco)")
        assert r, "la foto no tuvo respuesta"
        assert NO_ABRE_FOTO not in _une(r), "el bot no pudo abrir la foto (¿WebP rechazada?)"

        if pipewire_disponible() and voz is not None:
            r = _paso(sesion, lambda: nota_de_voz(sesion, voz), transcripcion, "(nota de voz)")
            assert r, "la nota de voz no tuvo respuesta"
        else:
            warnings.warn("sin PipeWire o sin ffmpeg: no se probó la nota de voz", stacklevel=1)

        r = _paso(sesion, lambda: _texto(sesion, "cancela todo"), transcripcion, "cancela todo")
        assert r and "cancelé" in _une(r), "«cancela todo» no canceló"
    finally:
        (carpeta / "conversacion.md").write_text("".join(transcripcion), encoding="utf-8")
