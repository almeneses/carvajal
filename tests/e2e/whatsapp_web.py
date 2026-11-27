"""WhatsApp Web en el Firefox del owner: leer burbujas, adjuntar fotos y grabar notas de voz.

Lo usan las pruebas de WhatsApp real (``test_whatsapp_real*.py``). Lo aprendido en el ensayo
del 2026-11-27, que aquí queda resuelto:

- Las filas del chat llevan un ``data-id`` único (ya no empieza por ``true_``/``false_``) y la
  dirección se lee del icono ``tail-in``/``tail-out``; el bot además antepone el nombre del
  agente («[Carva] …»). WhatsApp Web virtualiza la lista: las respuestas nuevas se reconocen
  por ``data-id`` que no estaban antes, no por su posición.
- El Firefox de snap no lee archivos de ``/tmp``: los adjuntos se copian bajo el home.
- «Adjuntar» abre un ``input[type=file]`` de imágenes; la vista previa se envía con el botón
  «Enviar 1 seleccionado».
- Una nota de voz real se graba con el botón «Mensaje de voz» y un micrófono virtual de
  PipeWire (``pw-loopback``) que reproduce un WAV; al terminar vuelve el micrófono de siempre.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from tests.e2e.webdriver import Sesion

ADJUNTAR = (
    "footer button[aria-label='Adjuntar']",
    "footer button[aria-label='Attach']",
    "footer button[title='Adjuntar']",
)
ENVIAR_ADJUNTO = (
    "div[aria-label^='Enviar']",
    "button[aria-label^='Enviar']",
    "div[aria-label^='Send']",
    "span[data-icon='send']",
    "span[data-icon='wds-ic-send-filled']",
)
MICROFONO = (
    "footer button[aria-label='Mensaje de voz']",
    "footer button[aria-label='Voice message']",
)
ENVIAR_VOZ = (
    "footer button[aria-label='Enviar']",
    "footer button[aria-label='Send']",
    "footer span[data-icon='send']",
    "footer span[data-icon='wds-ic-send-filled']",
)
# El bot antepone el nombre del agente de OpenClaw a cada respuesta («[Carva] …»).
_PREFIJO_AGENTE = re.compile(r"^\s*\[[^\]\n]{1,40}\]")

# Filas del chat abierto: id, dirección y texto. ``dir`` sale del icono de la cola de la
# burbuja; las burbujas seguidas de un mismo lado no lo llevan y quedan en «?».
JS_FILAS = """
const raiz = document.querySelector('#main') || document;
return Array.from(raiz.querySelectorAll('[data-id]')).map(fila => {
  let dir = '?';
  if (fila.querySelector("[data-icon='tail-in']") || fila.closest('.message-in')) { dir = 'in'; }
  else if (fila.querySelector("[data-icon='tail-out']") || fila.closest('.message-out')) {
    dir = 'out';
  }
  const cuerpo = fila.querySelector('[data-pre-plain-text]') || fila;
  return {id: fila.getAttribute('data-id') || '', dir: dir,
          texto: (cuerpo.innerText || '').slice(0, 2000)};
});
"""


@dataclass(frozen=True)
class Burbuja:
    id: str
    dir: str
    texto: str

    @property
    def del_bot(self) -> bool:
        """Respuesta del bot: entrante o con el prefijo del agente."""

        return self.dir == "in" or _PREFIJO_AGENTE.match(self.texto) is not None


def burbujas(sesion: Sesion) -> list[Burbuja]:
    filas = sesion.ejecutar(JS_FILAS) or []
    return [
        Burbuja(str(f.get("id", "")), str(f.get("dir", "?")), str(f.get("texto", "")))
        for f in filas
    ]


def nuevas(antes: set[str], ahora: list[Burbuja]) -> list[Burbuja]:
    """Las burbujas cuyo ``data-id`` no estaba antes, en orden (pura, para probarla)."""

    return [b for b in ahora if b.id and b.id not in antes]


def esperar_respuestas(
    sesion: Sesion, antes: set[str], *, maximo: float = 240, quieto: float = 20
) -> list[Burbuja]:
    """Respuestas del bot nuevas: espera la primera y luego ``quieto`` s sin más."""

    inicio = time.monotonic()
    ultima: float | None = None
    vistas = 0
    while time.monotonic() - inicio < maximo:
        del_bot = [b for b in nuevas(antes, burbujas(sesion)) if b.del_bot]
        if len(del_bot) != vistas:
            vistas, ultima = len(del_bot), time.monotonic()
        elif ultima is not None and time.monotonic() - ultima > quieto:
            return del_bot
        time.sleep(2)
    return [b for b in nuevas(antes, burbujas(sesion)) if b.del_bot]


def _primero(sesion: Sesion, selectores: tuple[str, ...]) -> str | None:
    return next((e for css in selectores if (e := sesion.buscar(css))), None)


# El propio elemento si ya es un botón; si no (un icono), el botón que lo contiene. Subir
# primero a un ``[role=button]`` pulsaba un contenedor y la nota de voz no salía.
_JS_PULSAR = """
const e = arguments[0];
const boton = (e.tagName === 'BUTTON' || e.getAttribute('role') === 'button')
  ? e : (e.closest('button') || e.closest('[role=button]') || e);
boton.click();
"""


def _pulsar(sesion: Sesion, elemento: str) -> None:
    sesion.ejecutar(_JS_PULSAR, sesion.referencia(elemento))


def copia_legible(archivo: Path, carpeta: Path) -> Path:
    """Copia el adjunto bajo ``carpeta`` (en el home): el Firefox de snap no lee ``/tmp``."""

    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / archivo.name
    shutil.copyfile(archivo, destino)
    return destino


def adjuntar_foto(sesion: Sesion, foto: Path) -> None:
    """Adjunta ``foto`` por «Fotos y videos» y la envía."""

    boton = _primero(sesion, ADJUNTAR)
    assert boton, "no se encontró el botón Adjuntar de WhatsApp Web"
    _pulsar(sesion, boton)
    entrada = sesion.esperar(
        lambda: next(
            (
                e
                for e in sesion.buscar_todos("input[type='file']")
                if "image"
                in str(sesion.ejecutar("return arguments[0].accept;", sesion.referencia(e)))
            ),
            None,
        ),
        timeout=10,
    )
    assert entrada, "no apareció el input de imágenes tras Adjuntar"
    sesion.escribir(entrada, str(foto))
    enviar = sesion.esperar(lambda: _primero(sesion, ENVIAR_ADJUNTO), timeout=30)
    assert enviar, "no apareció el botón para enviar la foto"
    time.sleep(1.5)
    _pulsar(sesion, enviar)


def pipewire_disponible() -> bool:
    return all(shutil.which(cmd) for cmd in ("pw-loopback", "pw-play", "pw-cli", "wpctl"))


def _fuente_por_defecto() -> str | None:
    salida = subprocess.run(
        ["wpctl", "inspect", "@DEFAULT_AUDIO_SOURCE@"], capture_output=True, text=True
    ).stdout
    found = re.match(r"id (\d+),", salida)
    return found.group(1) if found else None


def _id_nodo(nombre: str) -> str | None:
    salida = subprocess.run(["pw-cli", "ls", "Node"], capture_output=True, text=True).stdout
    for bloque in salida.split("\tid ")[1:]:
        if f'node.name = "{nombre}"' in bloque:
            return bloque.split(",")[0].strip()
    return None


@contextmanager
def microfono_virtual() -> Generator[str]:
    """Micrófono virtual de PipeWire como fuente por defecto; devuelve el sumidero donde sonar.

    Al salir vuelve la fuente que había y se cierra el lazo, pase lo que pase.
    """

    assert _id_nodo("ensayo_voz_mic") is None, (
        "ya hay un micrófono «ensayo_voz_mic» de otra corrida: cierra ese pw-loopback"
    )
    previa = _fuente_por_defecto()
    lazo = subprocess.Popen(
        [
            "pw-loopback",
            "--capture-props=media.class=Audio/Sink node.name=ensayo_voz_sink",
            "--playback-props=media.class=Audio/Source node.name=ensayo_voz_mic "
            "node.description=EnsayoVoz",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        nodo = None
        limite = time.monotonic() + 10
        while nodo is None and time.monotonic() < limite:
            time.sleep(0.5)
            nodo = _id_nodo("ensayo_voz_mic")
        assert nodo, "PipeWire no creó el micrófono virtual"
        subprocess.run(["wpctl", "set-default", nodo], check=True)
        yield "ensayo_voz_sink"
    finally:
        if previa:
            subprocess.run(["wpctl", "set-default", previa], check=False)
        lazo.terminate()
        try:
            lazo.wait(timeout=10)
        except subprocess.TimeoutExpired:
            lazo.kill()
            lazo.wait(timeout=10)


def nota_de_voz(sesion: Sesion, wav: Path) -> None:
    """Graba y envía una nota de voz real que dice lo que suena en ``wav``."""

    with microfono_virtual() as sumidero:
        time.sleep(1)
        boton = _primero(sesion, MICROFONO)
        assert boton, "no está el botón «Mensaje de voz» de WhatsApp Web"
        # Clic nativo de WebDriver: el del navegador exige un gesto real del usuario para
        # abrir el micrófono, y un ``click()`` desde JavaScript no lo es.
        sesion.clic(boton)
        # Grabando, el pie cambia y aparece «Enviar» en lugar del micrófono.
        enviar = sesion.esperar(lambda: _primero(sesion, ENVIAR_VOZ), timeout=10)
        assert enviar, "WhatsApp Web no empezó a grabar (¿permiso de micrófono?)"
        time.sleep(1)
        subprocess.run(["pw-play", "--target", sumidero, str(wav)], check=True)
        time.sleep(1.5)
        enviar = _primero(sesion, ENVIAR_VOZ) or enviar
        _pulsar(sesion, enviar)
        time.sleep(3)


def sin_numero(lista: list[str], digitos_numero: str) -> list[str]:
    """``lista`` sin el número dado (comparado por sus dígitos), en el mismo orden (pura)."""

    return [n for n in lista if re.sub(r"\D", "", n) != digitos_numero]
