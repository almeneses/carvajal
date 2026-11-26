"""Ensayo general en el chat web: memoria, flujos definidos y conversación con foto.

Habla con ``/chat/stream`` y ``/chat/foto`` como el navegador (un visitante nuevo por
prueba, con su cookie y su cupo). Todas las pruebas de este módulo gastan tokens del agente
o de Jev y escriben en la base: llevan ``gasto`` y ``make ensayo`` no las corre.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from tests.e2e.clientes import Config, Turno, Web
from tests.e2e.guiones import (
    EMPRESA,
    GUIONES,
    NOMBRE,
    OLVIDAR,
    PISTA_FOTO,
    PREGUNTA_MEMORIA,
    PRESENTACION,
    DesvioDelAgente,
    Guion,
    PedidosApagados,
    Respuesta,
    Transcripcion,
    con_reintento,
    correo_de_prueba,
    ejecutar,
    ejecutar_pasos,
    ejecutar_pedido,
    nombre_de_prueba,
    pasos_tras_foto,
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

    def enviar(self, mensaje: str) -> Respuesta:
        return respuesta_web(self.web.turno(mensaje, self.hilo))

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
    print(transcripcion.texto())


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
        pytest.skip(str(motivo))

    # Assert: cada turno ya se comprobó; el turno del pedido no registra otro folio.
    _, creado = pedido.transcripcion.turnos[-2]
    turno: Turno = creado.crudo
    assert turno.done.get("route") == "pedido", turno.resumen()
    assert not turno.done.get("quote_ids"), turno.resumen()
    print(f"PEDIDO web: {pedido.numero} · folio #{pedido.folio} · {correo}")
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
