"""Ensayo general en el chat web: memoria, flujos definidos y conversación con foto.

Habla con ``/chat/stream`` y ``/chat/foto`` como el navegador (un visitante nuevo por
prueba, con su cookie y su cupo). Todas las pruebas de este módulo gastan tokens del agente
o de Jev y escriben en la base: llevan ``gasto`` y ``make ensayo`` no las corre.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import time
from typing import Any

import httpx
import pytest
from tests.e2e.clientes import Config, Turno, Web
from tests.e2e.correo_odoo import comprobar_correo_del_pedido
from tests.e2e.guiones import (
    EMPRESA,
    GUIONES,
    NOMBRE,
    OLVIDAR,
    PISTA_FOTO,
    PREGUNTA_MEMORIA,
    PRESENTACION,
    PROMESA_RECIBO,
    DesvioDelAgente,
    Guion,
    PedidosApagados,
    Respuesta,
    Transcripcion,
    con_reintento,
    correo_de_prueba,
    correo_dictado,
    ejecutar,
    ejecutar_pasos,
    ejecutar_pedido,
    ejecutar_pedido_mixto,
    nombre_de_prueba,
    oferta_ausente,
    pasos_tras_foto,
    recibo_en,
    registra,
)

pytestmark = pytest.mark.gasto

ENLACE_PDF = re.compile(r"/cotizaciones/[0-9a-f]{12}\.pdf")


@pytest.fixture
def agente(salud: dict[str, Any]) -> None:
    """Los flujos y la memoria (specs 044-054) son del agente con herramientas."""

    modo = (salud.get("agent") or {}).get("mode_expected")
    if modo != "tools":
        pytest.skip(f"el agente corre en modo {modo!r}, no 'tools': sin flujos ni memoria")


def respuesta_web(turno: Turno) -> Respuesta:
    assert turno.status == 200, turno.resumen()
    assert not turno.error, turno.resumen()
    assert turno.done, f"el turno no cerró con «done»\n{turno.resumen()}"
    return Respuesta(
        texto=turno.texto,
        awaiting=turno.awaiting,
        folio=turno.done.get("quote_id"),
        ruta=turno.traza.get("route") or turno.done.get("route"),
        detalle=turno.resumen(),
        crudo=turno,
    )


class CanalWeb:
    """El chat web como canal de los guiones: un hilo nuevo por intento."""

    nombre = "web"

    def __init__(self, web: Web) -> None:
        self.web = web
        self.hilo = ""

    def reiniciar(self) -> None:
        self.hilo = self.web.nuevo_hilo()

    def enviar(self, mensaje: str, *, voz: bool = False) -> Respuesta:
        return respuesta_web(self.web.turno(mensaje, self.hilo, voz=voz))

    def tras_registro(self, respuesta: Respuesta, folio: str) -> None:
        turno: Turno = respuesta.crudo
        assert turno.flujo.get("folio") == folio, turno.resumen()
        assert folio in (turno.done.get("quote_ids") or []), turno.resumen()
        if "Te envío el PDF" in turno.texto and not ENLACE_PDF.search(turno.texto + turno.tarjetas):
            # Hallazgo conocido (spec 054): la web promete el PDF y no ofrece el enlace.
            print(f"HALLAZGO web: el folio {folio} promete «Te envío el PDF» sin enlace al PDF")


def _olvido_correcto(turno: Turno) -> None:
    traza = turno.traza
    gatekeeper = traza.get("gatekeeper") or {}
    assert traza.get("path") == "shortcut", turno.resumen()
    assert gatekeeper.get("rule") == "memory_forget", turno.resumen()
    assert traza.get("memory") == 0, turno.resumen()


def _herramientas(turno: Turno) -> list[str]:
    return [nombre for nombre in str(turno.traza.get("tool") or "").split(",") if nombre]


@pytest.mark.usefixtures("agente")
def test_memoria_entre_dos_conversaciones_web(config: Config, web: Web) -> None:
    # Arrange: memoria vacía (atajo por reglas, sin LLM) y un segundo visitante.
    otro = Web(config.base_url, Web.visitante_para(f"{config.run_id}:memoria-web-otro"))
    try:
        _olvido_correcto(web.turno(OLVIDAR, web.nuevo_hilo()))

        # Act: conversación A se presenta; conversación B (hilo nuevo) pregunta.
        presentacion, _ = con_reintento(
            lambda: web.turno(PRESENTACION, web.nuevo_hilo()),
            lambda turno: int(turno.traza.get("memory") or 0) >= 2,
            aviso="la presentación no guardó nombre y empresa (trace.memory < 2)",
        )
        recuerdo, _ = con_reintento(
            lambda: web.turno(PREGUNTA_MEMORIA, web.nuevo_hilo()),
            lambda turno: NOMBRE in turno.texto and EMPRESA in turno.texto,
            aviso="la conversación B no nombró a Ana y su panadería",
        )
        aislado = otro.turno(PREGUNTA_MEMORIA, otro.nuevo_hilo())

        # Assert
        assert int(presentacion.traza.get("memory") or 0) >= 2, presentacion.resumen()
        assert "recordar_preferencia" in _herramientas(presentacion), presentacion.resumen()
        assert int(recuerdo.traza.get("memory") or 0) >= 2, recuerdo.resumen()
        assert "listar_memoria" in _herramientas(recuerdo), recuerdo.resumen()
        assert NOMBRE in recuerdo.texto, recuerdo.resumen()
        assert EMPRESA in recuerdo.texto, recuerdo.resumen()
        assert aislado.traza.get("memory") == 0, aislado.resumen()
        assert "Ana Prueba" not in aislado.texto, aislado.resumen()
        assert EMPRESA not in aislado.texto, aislado.resumen()
    finally:
        cierre = web.turno(OLVIDAR, web.nuevo_hilo())
        otro.cerrar()
    _olvido_correcto(cierre)


@pytest.mark.usefixtures("agente")
@pytest.mark.parametrize("guion", GUIONES, ids=lambda guion: guion.nombre)
def test_flujo_definido_en_web(config: Config, web: Web, guion: Guion) -> None:
    # Arrange
    canal = CanalWeb(web)
    registrar = registra(config.cotizar, guion.designado, pedido=config.pedido)

    # Act
    transcripcion = ejecutar(canal, guion, registrar=registrar)

    # Assert: cada turno ya se comprobó en ``ejecutar``; aquí, el recorrido completo.
    assert transcripcion.turnos, f"{guion.nombre}: no se envió ningún mensaje"
    sin_oferta = oferta_ausente(transcripcion) if config.expect_pedidos else None
    assert sin_oferta is None, (
        f"el folio #{sin_oferta} no ofrece el pedido y se esperaba (ENSAYO_EXPECT_PEDIDOS, "
        f"activo en producción): ¿falta ODOO_* en la app?\n{transcripcion.texto()}"
    )
    print(transcripcion.texto())


def _descargar_recibo(cliente: httpx.Client, ruta: str) -> httpx.Response:
    """GET del recibo; un 503 (Odoo lento al generar el PDF) se reintenta una vez."""

    respuesta = cliente.get(ruta, timeout=60)
    if respuesta.status_code == 503:
        print(f"AVISO web: el recibo {ruta} respondió 503; se reintenta en 10 s")
        time.sleep(10)
        respuesta = cliente.get(ruta, timeout=60)
    return respuesta


def _comprobar_recibo_web(config: Config, web: Web, turno: Turno, folio: str, numero: str) -> None:
    """SC-008 de la spec 056: el enlace al recibo abre con la cookie del dueño y con otra no."""

    recibo = recibo_en(turno.texto, "web")
    assert recibo is not None, (
        f"el pedido {numero} no trae el enlace «Descarga el recibo de tu pedido» a "
        f"/pedidos/{folio}.pdf\n{turno.resumen()}"
    )
    assert PROMESA_RECIBO not in turno.texto, turno.resumen()
    assert (recibo.folio, recibo.numero) == (folio, numero), recibo
    documento = _descargar_recibo(web.cliente, recibo.ruta)
    assert documento.status_code == 200, (
        f"el recibo {recibo.ruta} respondió {documento.status_code} con la cookie del dueño "
        "(503: Odoo no respondió; 404: el dueño o el PDF no se encontraron)"
    )
    assert documento.headers.get("content-type", "").startswith("application/pdf")
    assert documento.content.startswith(b"%PDF"), "el recibo no es un PDF"
    otro = Web(config.base_url, Web.visitante_para(f"{config.run_id}:recibo-ajeno"))
    try:
        ajeno = otro.cliente.get(recibo.ruta, timeout=30)
    finally:
        otro.cerrar()
    assert ajeno.status_code == 404, f"otro visitante abrió el recibo: {ajeno.status_code}"


@pytest.mark.usefixtures("agente", "pedido_permitido")
def test_pedido_en_odoo_desde_web(config: Config, web: Web) -> None:
    # Arrange: comprador ficticio del run; el pedido queda en Odoo (docs/ensayo.md).
    canal = CanalWeb(web)
    nombre = nombre_de_prueba(config.run_id)
    correo = correo_de_prueba(f"{config.run_id}-web")

    # Act
    try:
        pedido = ejecutar_pedido(canal, nombre=nombre, correo=correo)
    except PedidosApagados as motivo:
        if config.expect_pedidos:
            pytest.fail(f"{motivo}; se esperaba el pedido (ENSAYO_EXPECT_PEDIDOS)")
        pytest.skip(str(motivo))

    # Assert: cada turno ya se comprobó; el turno del pedido no registra otro folio.
    _, creado = pedido.transcripcion.turnos[-2]
    turno: Turno = creado.crudo
    assert turno.done.get("route") == "pedido", turno.resumen()
    assert not turno.done.get("quote_ids"), turno.resumen()
    _comprobar_recibo_web(config, web, turno, pedido.folio, pedido.numero)
    comprobar_correo_del_pedido(config, pedido.folio, "web")
    reintento = " · tras reintentar (Odoo falló al primer intento)" if pedido.reintentado else ""
    print(f"PEDIDO web: {pedido.numero} · folio #{pedido.folio} · {correo}{reintento}")
    print(pedido.transcripcion.texto())


def _foto_correcta(turno: Turno) -> None:
    """Primer turno con foto (spec 053): recomendación sin LLM, sin cotizar todavía."""

    traza = turno.traza
    assert turno.status == 200, turno.resumen()
    assert not turno.error, turno.resumen()
    assert traza.get("route") == "foto", turno.resumen()
    assert turno.done.get("route") == "foto", turno.resumen()
    assert turno.done.get("quote_ids") == [], turno.resumen()
    assert turno.done.get("quote_id") is None, turno.resumen()
    assert not turno.done.get("flow"), turno.resumen()
    assert re.search(r"[Rr]ecomend", turno.texto), turno.resumen()
    assert "**Alternativas:**" in turno.texto, turno.resumen()
    assert any(pista in turno.texto for pista in PISTA_FOTO), turno.resumen()
    assert "Elegir" in turno.tarjetas, "las tarjetas no traen los botones «Elegir»"
    assert 'data-pregunta="Dame esa"' in turno.tarjetas, "falta el botón de la recomendada"
    assert 'data-pregunta="Vamos con la alternativa 2"' in turno.tarjetas


@pytest.mark.usefixtures("agente")
def test_conversacion_que_empieza_con_foto_web(
    config: Config, web: Web, salud: dict[str, Any], foto_ajiaco: bytes
) -> None:
    # Arrange
    estado = (salud.get("food_photo") or {}).get("status")
    if estado != "ok":
        pytest.skip(f"fotos de alimentos no disponibles en la app (food_photo={estado!r})")
    canal = CanalWeb(web)
    registrar = registra(config.cotizar, designado=False)
    intentos: list[str] = []

    for _ in range(2):
        canal.reiniciar()
        transcripcion = Transcripcion("web")
        # Act: la foto es el primer mensaje del hilo.
        foto = web.foto(foto_ajiaco, hilo=canal.hilo)
        if (foto.traza.get("foto") or {}).get("status") != "ok":
            intentos.append(f"Jev no reconoció el ajiaco: {foto.traza.get('foto')}")
            continue
        # Assert
        _foto_correcta(foto)
        try:
            ejecutar_pasos(canal, pasos_tras_foto(registrar), transcripcion)
        except DesvioDelAgente as desvio:
            intentos.append(str(desvio))
            continue
        print(transcripcion.texto())
        return
    pytest.fail("la conversación con foto se desvió dos veces:\n\n" + "\n\n".join(intentos))


# --- Pedido con foto, texto y voz -------------------------------------------------------

# Lo que la voz del servidor lee de la confirmación guardada (apps/api/voice.py, ``_neutral``).
VOZ_NEUTRA = "correo que escribiste"
_RESUMEN_HILO = re.compile(r'data-voz-resumen="([^"]*)"')


def _ogg(mp3: bytes) -> bytes | None:
    """El MP3 de la voz del servidor en Ogg/Opus, como graba el navegador; sin ffmpeg, ``None``."""

    if shutil.which("ffmpeg") is None:
        return None
    proceso = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-c:a", "libopus", "-f", "ogg", "pipe:1"],
        input=mp3,
        capture_output=True,
        check=True,
        timeout=60,
    )
    return proceso.stdout


def _datos_del_comprador(nombre: str, correo: str) -> tuple[str, ...]:
    """El nombre y el correo en todas sus formas: escrito, su usuario y dictado (en minúscula)."""

    dictado = " ".join(correo_dictado(correo).split())
    return tuple(
        dato.casefold() for dato in (nombre, *nombre.split(), correo, correo.split("@")[0], dictado)
    )


def _hilo_sin_datos_en_la_voz(web: Web, hilo: str, nombre: str, correo: str) -> str:
    """FR-019 sin depender del audio: el texto que la voz del servidor leería del hilo.

    ``GET /chat?thread=…`` pinta en cada respuesta ``data-voz-resumen``, el mismo resumen
    guardado que sintetiza ``/api/voz/hablar``. El de la confirmación del pedido dice «a tu
    nombre, con el correo que escribiste»: ningún resumen del hilo trae el nombre ni el correo.
    Devuelve la página para las demás comprobaciones.
    """

    pagina = web.cliente.get("/chat", params={"thread": hilo})
    assert pagina.status_code == 200, pagina.text[:300]
    resumenes = [html.unescape(valor).casefold() for valor in _RESUMEN_HILO.findall(pagina.text)]
    assert any(VOZ_NEUTRA in resumen for resumen in resumenes), (
        f"el hilo no guarda la confirmación neutra («{VOZ_NEUTRA}»): {resumenes}"
    )
    for resumen in resumenes:
        for dato in _datos_del_comprador(nombre, correo):
            assert dato not in resumen, f"la voz guardada del hilo dice {dato!r}: {resumen!r}"
    return pagina.text


def _voz_del_servidor_oida(
    web: Web, salud: dict[str, Any], turno: Turno, nombre: str, correo: str
) -> str | None:
    """FR-019 con audio real: TTS de la confirmación (``/api/voz/hablar``) y su transcripción.

    Lo que se oye es la frase neutra y no el nombre. Devuelve lo transcrito, o ``None`` si la
    voz del servidor está apagada o falta ffmpeg: entonces solo avisa, porque la comprobación
    determinista (:func:`_hilo_sin_datos_en_la_voz`) ya cubrió el texto que se sintetiza.
    """

    if not (salud.get("voice") or {}).get("available"):
        print("AVISO web: voz del servidor apagada; no se oye la confirmación del pedido")
        return None
    mensaje = str(turno.done.get("message_id") or "")
    assert mensaje, turno.resumen()
    hablado = web.cliente.post(
        "/api/voz/hablar", data={"message_id": mensaje, "csrf_token": web.csrf}
    )
    assert hablado.status_code == 200, hablado.text[:300]
    audio = _ogg(hablado.content)
    if audio is None:
        print("AVISO web: sin ffmpeg; no se transcribe la confirmación del pedido")
        return None
    transcrito = web.cliente.post(
        "/api/voz/transcribir",
        files={"audio": ("nota.ogg", audio, "audio/ogg")},
        headers={"X-CSRF-Token": web.csrf},
    )
    assert transcrito.status_code == 200, transcrito.text[:300]
    texto = str(transcrito.json()["texto"]).casefold()
    assert "escribiste" in texto, f"la voz del servidor no dijo la frase neutra: {texto!r}"
    for dato in ("ensayo", "example", "arroba"):
        assert dato not in texto, f"la voz del servidor dijo {dato!r}: {texto!r}"
    return texto


@pytest.mark.usefixtures("agente", "pedido_permitido")
def test_pedido_con_foto_texto_y_voz_web(
    config: Config, web: Web, salud: dict[str, Any], foto_ajiaco: bytes
) -> None:
    # Arrange: la foto abre el hilo; luego texto, voz (dictado) y la confirmación por texto.
    estado = (salud.get("food_photo") or {}).get("status")
    if estado != "ok":
        pytest.skip(f"fotos de alimentos no disponibles en la app (food_photo={estado!r})")
    canal = CanalWeb(web)
    nombre = nombre_de_prueba(config.run_id)
    correo = correo_de_prueba(f"{config.run_id}-web-mixto")

    def foto() -> str | None:
        turno = web.foto(foto_ajiaco, hilo=canal.hilo)
        if (turno.traza.get("foto") or {}).get("status") != "ok":
            return f"Jev no reconoció el ajiaco: {turno.traza.get('foto')}"
        _foto_correcta(turno)
        return None

    # Act
    try:
        pedido = ejecutar_pedido_mixto(canal, foto=foto, nombre=nombre, correo=correo)
    except PedidosApagados as motivo:
        if config.expect_pedidos:
            pytest.fail(f"{motivo}; se esperaba el pedido (ENSAYO_EXPECT_PEDIDOS)")
        pytest.skip(str(motivo))

    # Assert: el pedido salió con su recibo y su correo.
    turnos = pedido.transcripcion.turnos
    _, creado = turnos[-1]
    turno: Turno = creado.crudo
    assert turno.done.get("route") == "pedido", turno.resumen()
    _comprobar_recibo_web(config, web, turno, pedido.folio, pedido.numero)
    comprobar_correo_del_pedido(config, pedido.folio, "web")
    # La voz del navegador (FR-038) lee los datos reales para que la persona los compruebe.
    confirmacion = next(r for p, r in turnos if p.voz and r.awaiting == "confirmar_pedido")
    resumen = str(confirmacion.crudo.done.get("voz_resumen") or "").casefold()
    assert nombre.casefold() in resumen, f"la voz del navegador no lee el nombre: {resumen!r}"
    dictado = " ".join(correo_dictado(correo).split()).casefold()
    assert dictado in resumen, f"la voz del navegador no lee el correo: {resumen!r}"
    # El servidor recibió los turnos por voz como tales (``voz=1``) y los de texto no.
    pagina = _hilo_sin_datos_en_la_voz(web, canal.hilo, nombre, correo)
    por_voz = sum(paso.voz for paso, _ in turnos)
    assert 0 < por_voz < len(turnos)
    # Solo las marcas visibles del hilo: la plantilla del stream trae una oculta.
    marcas = pagina.count("data-turno-por-voz>")
    assert marcas == por_voz, f"el hilo marca {marcas} turnos por voz y se enviaron {por_voz}"
    oido = _voz_del_servidor_oida(web, salud, confirmacion.crudo, nombre, correo)
    reintento = " · tras reintentar (Odoo falló al primer intento)" if pedido.reintentado else ""
    print(
        f"PEDIDO web (foto, texto y voz): {pedido.numero} · folio #{pedido.folio} · {correo}"
        f"{reintento}"
    )
    if oido is not None:
        print(f"VOZ DEL SERVIDOR en la confirmación: {oido!r}")
    print(pedido.transcripcion.texto())
