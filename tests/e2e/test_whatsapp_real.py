"""WhatsApp real: un mensaje desde un número fuera de la allowlist (docs/ensayo.md).

El número que OpenClaw tiene vinculado es el del owner y su ``dmPolicy`` es ``allowlist``:
un DM de cualquier número que no esté en ``allowFrom`` debe descartarse en el gateway, sin
que ningún agente responda. La prueba lo comprueba de punta a punta con el WhatsApp Web
que el owner tiene abierto en su Firefox con **otro** número (fuera de la allowlist):

1. Por SSH (solo lectura) confirma que ``dmPolicy`` sigue en ``allowlist`` y que el
   journal del gateway es legible; sin eso no envía nada.
2. Se engancha al Firefox ya abierto (``--marionette``) con geckodriver, abre una
   pestaña **propia**, comprueba que el número de WhatsApp Web no está en ``allowFrom`` y
   envía al bot un texto único (``ensayo allowlist <run>``).
3. Durante ``ENSAYO_WA_ESPERA`` segundos (90 por defecto) vigila que no llegue ninguna
   burbuja entrante después de la nuestra.
4. Lee el journal de ``openclaw-gateway`` desde el envío y busca la línea real del
   descarte, ``[whatsapp] Inbound message blocked (reason=dm_policy_not_allowlisted)``, y
   que no haya entrada aceptada ni sesión de agente para ese remitente.

La línea de bloqueo no trae el remitente (el owner recibe DMs de otros contactos todo el
día), así que la evidencia firme es la combinación: bloqueo en la ventana **y** ningún
``Inbound message <remitente> -> …`` ni ``sessionKey=…<remitente>…`` aceptado. Si falta la
línea de bloqueo pero el remitente se conoce y no hubo ni entrada ni sesión, la prueba
pasa con un aviso (evidencia indirecta).

Nunca se imprimen números: todo lo que sale en un fallo pasa por ``redactar``. Al terminar
se cierra solo la pestaña propia y se suelta el navegador sin cerrarlo.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import time
import uuid
import warnings
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from tests.e2e.clientes import Config, Ssh
from tests.e2e.webdriver import ENTER, Firefox, Sesion, WebDriverError, puerto_abierto

UNIDAD_GATEWAY = "openclaw-gateway"
WHATSAPP_WEB = "https://web.whatsapp.com"
ESPERA_DEFECTO = 90.0

# Selectores de WhatsApp Web, de más a menos estable: cambian a menudo.
COMPOSITOR = (
    "footer [contenteditable='true'][role='textbox']",
    "footer div[role='textbox']",
    "footer [contenteditable='true']",
    "#main div[role='textbox'][contenteditable='true']",
)
BOTON_ENVIAR = (
    "footer button[aria-label='Enviar']",
    "footer button[aria-label='Send']",
    "footer span[data-icon='send']",
    "footer span[data-icon='wds-ic-send-filled']",
)

_NUMERO = re.compile(r"\+?\d[\d\s().-]{5,}\d")
_BLOQUEO = re.compile(r"Inbound message blocked \(reason=([A-Za-z0-9_.-]+)\)")
_ENTRANTE = re.compile(r"Inbound message (\S+) -> (\S+) \(([^,)]+)")
_SESION = re.compile(r"sessionKey=(\S+)")

# Estado de la pestaña de WhatsApp Web: qr, otra-ventana, numero-invalido, listo o cargando.
_JS_ESTADO = """
const texto = ((document.body && document.body.innerText) || '').toLowerCase();
const invalido = ['direcci[oó]n url no es v[aá]lido', 'shared via url is invalid',
  'no est[aá] en whatsapp', "isn.t on whatsapp"];
if (new RegExp(invalido.join('|')).test(texto)) { return 'numero-invalido'; }
for (const css of arguments[0]) { if (document.querySelector(css)) { return 'listo'; } }
const qr = ['escanea el c[oó]digo', 'scan the qr', 'vincular con el n[uú]mero',
  'link with phone number', 'log into whatsapp', 'inicia sesi[oó]n en whatsapp'];
if (document.querySelector("canvas[aria-label], div[data-ref] canvas, [data-testid='qrcode']")
    || new RegExp(qr.join('|')).test(texto)) { return 'qr'; }
const botones = Array.from(document.querySelectorAll("button, div[role='button']"));
if (botones.some(b => /^(usar aqu[ií]|use here)$/i.test((b.innerText || '').trim()))) {
  return 'otra-ventana';
}
return 'cargando';
"""

# «Usar aquí»: WhatsApp Web solo deja una pestaña activa por navegador.
_JS_USAR_AQUI = """
const botones = Array.from(document.querySelectorAll("button, div[role='button']"));
const boton = botones.find(b => /^(usar aqu[ií]|use here)$/i.test((b.innerText || '').trim()));
if (boton) { boton.click(); return true; }
return false;
"""

# Burbujas del chat abierto, en orden del documento: dirección y texto.
_JS_MENSAJES = """
const raiz = document.querySelector('#main') || document;
const nodos = raiz.querySelectorAll(
  ".message-in, .message-out, [data-id^='true_'], [data-id^='false_'], [data-pre-plain-text]");
const vistos = new Set();
const salida = [];
for (const nodo of nodos) {
  const fila = nodo.closest('[data-id]') || nodo.closest("[role='row']") || nodo;
  if (vistos.has(fila)) { continue; }
  vistos.add(fila);
  const id = fila.getAttribute('data-id') || '';
  let dir = '?';
  // Desde 2026-11 el data-id ya no empieza por true_/false_: la cola de la burbuja dice el lado.
  if (fila.matches('.message-in') || fila.querySelector(".message-in, [data-icon='tail-in']")
      || id.startsWith('false_')) {
    dir = 'in';
  } else if (fila.matches('.message-out')
             || fila.querySelector(".message-out, [data-icon='tail-out']")
             || id.startsWith('true_')) {
    dir = 'out';
  }
  const cuerpo = fila.querySelector('[data-pre-plain-text]') || fila;
  salida.push({dir: dir, texto: (cuerpo.innerText || '').slice(0, 400)});
}
return salida;
"""

# Número con el que está abierta la sesión de WhatsApp Web ("57300…:12@c.us").
_JS_MI_NUMERO = """
for (const clave of ['last-wid-md', 'last-wid']) {
  const valor = window.localStorage.getItem(clave);
  if (valor) { return valor; }
}
return '';
"""


# --- utilidades puras -------------------------------------------------------------------


def digitos(numero: str) -> str:
    """Solo los dígitos del número (sin ``+``, dispositivo ``:12`` ni dominio ``@c.us``)."""

    base = numero.strip().strip('"').split("@", 1)[0].split(":", 1)[0]
    return re.sub(r"\D", "", base)


def redactar(texto: str) -> str:
    """Sustituye cualquier secuencia con forma de teléfono por ``<número>``."""

    return _NUMERO.sub("<número>", texto)


def url_envio(numero: str, texto: str) -> str:
    return f"{WHATSAPP_WEB}/send?phone={digitos(numero)}&text={quote(texto)}"


def lista_json(salida: str) -> list[Any]:
    """La lista JSON que imprime ``openclaw config get`` (ignora avisos alrededor)."""

    inicio, fin = salida.find("["), salida.rfind("]")
    if inicio < 0 or fin < inicio:
        return []
    valor = json.loads(salida[inicio : fin + 1])
    return valor if isinstance(valor, list) else []


def valor_config(salida: str) -> str | None:
    """Valor escalar de ``openclaw config get``; ``None`` si la ruta no está definida."""

    texto = salida.strip()
    if not texto or "unset" in texto.lower():
        return None
    valor = texto.splitlines()[-1].strip().strip('"')
    return None if not valor or " " in valor else valor


@dataclass(frozen=True)
class Evidencia:
    """Lo que dejó el gateway en su journal desde el envío."""

    bloqueos_dm: int
    entrantes_remitente: int
    sesiones_remitente: int
    entrantes_directos: int

    def resumen(self) -> str:
        return (
            f"bloqueos_dm={self.bloqueos_dm} entrantes_remitente={self.entrantes_remitente} "
            f"sesiones_remitente={self.sesiones_remitente} "
            f"entrantes_directos={self.entrantes_directos}"
        )


def analizar_journal(texto: str, remitente: str) -> Evidencia:
    """Cuenta bloqueos por ``dmPolicy`` y entradas o sesiones aceptadas del ``remitente``.

    ``remitente`` son los dígitos del número de WhatsApp Web, o vacío si no se conocen.
    """

    bloqueos = entrantes_remitente = sesiones_remitente = directos = 0
    for linea in texto.splitlines():
        if bloqueo := _BLOQUEO.search(linea):
            bloqueos += bloqueo.group(1).startswith("dm_")
            continue
        if entrada := _ENTRANTE.search(linea):
            origen, _, tipo = entrada.groups()
            if tipo.strip() != "group":
                directos += 1
                entrantes_remitente += bool(remitente) and digitos(origen) == remitente
        for sesion in _SESION.findall(linea):
            sesiones_remitente += bool(remitente) and remitente in sesion
    return Evidencia(bloqueos, entrantes_remitente, sesiones_remitente, directos)


def burbujas_despues(mensajes: list[dict[str, str]], marca: str) -> list[dict[str, str]] | None:
    """Burbujas posteriores a nuestro mensaje con ``marca``; ``None`` si no aparece."""

    propias = [i for i, m in enumerate(mensajes) if m.get("dir") != "in" and marca in m["texto"]]
    return mensajes[propias[-1] + 1 :] if propias else None


# --- fixtures ---------------------------------------------------------------------------


def _geckodriver() -> str | None:
    encontrado = shutil.which("geckodriver")
    if encontrado:
        return encontrado
    snap = Path("/snap/bin/geckodriver")
    return str(snap) if snap.exists() else None


@pytest.fixture
def firefox(config: Config) -> Iterator[Sesion]:
    """Sesión WebDriver sobre el Firefox del owner (``firefox --marionette``)."""

    if not config.wa_bot:
        pytest.skip("sin ENSAYO_WA_BOT: falta el número del bot en E.164 (docs/ensayo.md)")
    if not re.fullmatch(r"[1-9]\d{7,14}", digitos(config.wa_bot)):
        pytest.skip("ENSAYO_WA_BOT no parece un número E.164 (¿sigue el marcador de ejemplo?)")
    puerto = int(config.firefox_marionette or 2828)
    if not puerto_abierto(puerto):
        pytest.skip(
            f"Firefox no escucha Marionette en 127.0.0.1:{puerto}: ciérralo y ábrelo con "
            "«firefox --marionette» (docs/ensayo.md)"
        )
    binario = _geckodriver()
    if not binario:
        pytest.skip("geckodriver no está instalado (en Ubuntu viene con el snap de Firefox)")
    with ExitStack() as pila:
        try:
            sesion = pila.enter_context(Firefox(marionette=puerto, binario=binario).conectar())
        except (RuntimeError, WebDriverError) as error:
            pytest.skip(f"no se pudo abrir la sesión de WebDriver con Firefox: {error}")
        yield sesion


@pytest.fixture
def pestana(firefox: Sesion) -> Iterator[Sesion]:
    """Una pestaña propia: al final se cierra solo ella y se vuelve a la del usuario."""

    original = firefox.ventana_actual()
    propia = firefox.nueva_ventana("tab")
    try:
        yield firefox
    finally:
        try:
            if propia in firefox.ventanas():
                firefox.cambiar_a(propia)
                firefox.cerrar_ventana()
            firefox.cambiar_a(original)
        except WebDriverError:
            pass


def _espera_whatsapp() -> float:
    try:
        return float(os.environ.get("ENSAYO_WA_ESPERA", "") or ESPERA_DEFECTO)
    except ValueError:
        return ESPERA_DEFECTO


def _gateway(ssh: Ssh, usuario: str, comando: str, *, saltar: bool = True) -> str:
    """Comando de solo lectura en la VM de OpenClaw; antes del envío, sin SSH se salta."""

    proceso = ssh.como(usuario, comando, timeout=60)
    if proceso.returncode != 0:
        motivo = f"no se pudo consultar OpenClaw por SSH: {redactar(proceso.stderr)[:300]}"
        if saltar:
            pytest.skip(motivo)
        pytest.fail(motivo + " (el mensaje ya se envió)")
    return proceso.stdout


# --- pruebas ----------------------------------------------------------------------------


def test_analiza_el_journal_del_gateway_sin_confundir_remitentes() -> None:
    # Arrange: líneas con la forma real del journal de OpenClaw 2026.11.6.
    journal = "\n".join(
        [
            "2026-11-26T01:15:49.234+00:00 [whatsapp] Inbound message blocked "
            "(reason=dm_policy_not_allowlisted)",
            "2026-11-26T01:21:46.840+00:00 [whatsapp] Inbound message 120363000000000001@g.us "
            "-> +15550100001 (group, 101 chars)",
            "2026-11-26T01:21:54.484+00:00 [auto-reply/reply-resolver-timing] surface=whatsapp "
            "sessionKey=agent:ventas:whatsapp:group:120363000000000001@g.us agentId=ventas",
            "2026-11-26T01:30:00.000+00:00 [whatsapp] Inbound message +15550100009 "
            "-> +15550100001 (direct, 12 chars)",
            "2026-11-26T01:30:01.000+00:00 [auto-reply] "
            "sessionKey=agent:main:whatsapp:direct:+15550100009 agentId=main",
        ]
    )

    # Act
    ajeno = analizar_journal(journal, "15550100777")
    aceptado = analizar_journal(journal, "15550100009")

    # Assert
    assert ajeno == Evidencia(1, 0, 0, 1)
    assert aceptado == Evidencia(1, 1, 1, 1)
    assert digitos('"15550100009:12@c.us"') == "15550100009"
    assert redactar("de +57 300 000 0009 a 15550100001") == "de <número> a <número>"
    assert url_envio("+15550100001", "ensayo allowlist 1").endswith(
        "phone=15550100001&text=ensayo%20allowlist%201"
    )


@pytest.mark.gasto
def test_mensaje_desde_numero_fuera_de_la_allowlist_no_recibe_respuesta(
    config: Config, ssh_openclaw: Ssh, pestana: Sesion
) -> None:
    # Arrange: la política sigue en allowlist y el journal del gateway es legible.
    usuario = config.openclaw_user
    politica = valor_config(
        _gateway(ssh_openclaw, usuario, "openclaw config get channels.whatsapp.dmPolicy")
    )
    cuenta = valor_config(
        _gateway(
            ssh_openclaw,
            usuario,
            "openclaw config get channels.whatsapp.accounts.default.dmPolicy 2>&1 || true",
        )
    )
    assert (cuenta or politica) == "allowlist", (
        f"dmPolicy de WhatsApp es {cuenta or politica!r}: cualquier número podría hablar con "
        "el agente (docs/whatsapp-openclaw.md)"
    )
    permitidos = {
        digitos(str(n))
        for n in lista_json(
            _gateway(ssh_openclaw, usuario, "openclaw config get channels.whatsapp.allowFrom")
        )
    }
    journal_base = f"journalctl --user -u {UNIDAD_GATEWAY} --no-pager -o cat"
    if not _gateway(ssh_openclaw, usuario, f"{journal_base} -n 1").strip():
        pytest.skip(f"el journal de {UNIDAD_GATEWAY} está vacío o no es legible")

    espera = _espera_whatsapp()
    marca = f"ensayo allowlist {config.run_id}-{uuid.uuid4().hex[:4]}"
    pestana.navegar(url_envio(config.wa_bot, marca))
    estado = pestana.esperar(
        lambda: _estado_listo(pestana), timeout=90, intervalo=1
    ) or pestana.ejecutar(_JS_ESTADO, list(COMPOSITOR))
    if estado == "qr":
        pytest.skip("WhatsApp Web no tiene sesión en este Firefox (pide el código QR)")
    assert estado != "numero-invalido", "WhatsApp Web dice que ENSAYO_WA_BOT no es válido"
    assert estado == "listo", f"WhatsApp Web no mostró el compositor en 90 s (estado={estado})"

    remitente = digitos(str(pestana.ejecutar(_JS_MI_NUMERO) or ""))
    if remitente and remitente == digitos(config.wa_bot):
        pytest.skip("WhatsApp Web está abierto con el número del bot: hace falta otro número")
    if remitente and remitente in permitidos:
        pytest.skip("el número de WhatsApp Web está en allowFrom: hace falta uno de fuera")

    # Act: enviar la marca y vigilar el chat durante la espera.
    inicio = time.time()
    _enviar(pestana, marca)
    enviados = pestana.esperar(
        lambda: burbujas_despues(pestana.ejecutar(_JS_MENSAJES), marca) is not None,
        timeout=30,
    )
    assert enviados, "no apareció nuestra burbuja en el chat: el mensaje no salió"
    limite = time.monotonic() + espera
    entrantes: list[dict[str, str]] = []
    while time.monotonic() < limite and not entrantes:
        despues = burbujas_despues(pestana.ejecutar(_JS_MENSAJES), marca) or []
        entrantes = [m for m in despues if m.get("dir") == "in"]
        time.sleep(2)
    desde = int(inicio) - 5
    journal = _gateway(
        ssh_openclaw,
        usuario,
        f"{journal_base} --since @{desde} | grep -E {shlex.quote('Inbound message|sessionKey=')}"
        " || true",
        saltar=False,
    )
    evidencia = analizar_journal(journal, remitente)

    # Assert
    assert not entrantes, (
        f"llegó una respuesta en {espera:.0f} s a un número fuera de la allowlist: "
        f"{redactar(entrantes[0]['texto'])[:200]!r}"
    )
    if remitente:
        assert evidencia.entrantes_remitente == 0 and evidencia.sesiones_remitente == 0, (
            "el gateway aceptó el DM del número fuera de la allowlist: " + evidencia.resumen()
        )
    else:
        assert evidencia.entrantes_directos == 0, (
            "el gateway aceptó un DM en la ventana del ensayo (no se conoce el remitente; puede "
            "ser una coincidencia con el número permitido): " + evidencia.resumen()
        )
    assert evidencia.bloqueos_dm >= 1 or remitente, (
        "sin «Inbound message blocked (reason=dm_policy_not_allowlisted)» desde el envío y sin "
        "remitente conocido: no hay evidencia del descarte. " + evidencia.resumen()
    )
    if evidencia.bloqueos_dm == 0:
        warnings.warn(
            "el journal no registró el bloqueo; la evidencia es indirecta (ni entrada ni sesión "
            "del remitente). " + evidencia.resumen(),
            stacklevel=1,
        )


def _estado_listo(sesion: Sesion) -> str | None:
    """Devuelve el estado cuando es definitivo; pulsa «Usar aquí» si hace falta."""

    estado = sesion.ejecutar(_JS_ESTADO, list(COMPOSITOR))
    if estado == "otra-ventana":
        sesion.ejecutar(_JS_USAR_AQUI)
        return None
    return estado if estado in {"listo", "qr", "numero-invalido"} else None


def _enviar(sesion: Sesion, marca: str) -> None:
    """Escribe la marca (si ``?text=`` no la dejó puesta) y la envía con Enter."""

    compositor = next((elemento for css in COMPOSITOR if (elemento := sesion.buscar(css))), None)
    assert compositor, "no se encontró el compositor de WhatsApp Web"
    precargado = sesion.esperar(
        lambda: (
            marca
            in str(sesion.ejecutar("return arguments[0].innerText;", sesion.referencia(compositor)))
        ),
        timeout=10,
    )
    if not precargado:
        sesion.clic(compositor)
        sesion.teclear(marca)
    sesion.escribir(compositor, ENTER)
    if sesion.esperar(
        lambda: burbujas_despues(sesion.ejecutar(_JS_MENSAJES), marca) is not None, timeout=5
    ):
        return
    for css in BOTON_ENVIAR:
        if boton := sesion.buscar(css):
            sesion.ejecutar(
                "const b = arguments[0].closest('button') || arguments[0]; b.click();",
                sesion.referencia(boton),
            )
            return


def test_teclear_manda_cada_letra_como_accion_de_teclado() -> None:
    # Arrange: una sesión sin geckodriver que registra los comandos.
    llamadas: list[tuple[str, str, Any]] = []
    sesion = Sesion.__new__(Sesion)
    sesion.comando = lambda metodo, ruta, cuerpo=None: llamadas.append((metodo, ruta, cuerpo))  # type: ignore[method-assign]

    # Act
    sesion.teclear("sí" + ENTER)

    # Assert: keyDown y keyUp por letra (con la tilde y Enter) y luego se sueltan las teclas.
    (metodo, ruta, cuerpo), soltar = llamadas
    assert (metodo, ruta) == ("POST", "/actions")
    teclas = cuerpo["actions"][0]["actions"]
    assert [t["value"] for t in teclas if t["type"] == "keyDown"] == ["s", "í", ENTER]
    assert len(teclas) == 6
    assert soltar[:2] == ("DELETE", "/actions")
