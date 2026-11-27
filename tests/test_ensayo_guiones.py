"""Guiones del ensayo general (tests/e2e/guiones.py) contra el servicio del chat, sin red.

El ensayo no importa la app y copia sus textos; aquí se corren sus pasos contra
``ChatService`` con el agente y Odoo simulados, con y sin pedidos (spec 056), para que un
cambio de plantilla rompa esta prueba y no el ensayo en producción.
"""

import sys
from collections.abc import Iterator
from dataclasses import replace
from itertools import count
from typing import Any, cast

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from tests.e2e import correo_odoo
from tests.e2e.clientes import Config, Ssh
from tests.e2e.guiones import (
    ACEPTAR_PEDIDO,
    CONFIRMAR_DATOS,
    CONFIRMAR_PEDIDO,
    GUIONES,
    OFERTA_PEDIDO,
    PEDIDO_FALLIDO,
    PROMESA_RECIBO,
    REGISTRO,
    Paso,
    PedidosApagados,
    Recibo,
    Respuesta,
    Transcripcion,
    correo_de_prueba,
    ejecutar,
    ejecutar_pedido,
    formato,
    inferir_awaiting,
    nombre_de_prueba,
    oferta_ausente,
    recibo_en,
    registra,
)
from tests.test_pedidos_odoo import FakeOrders, _service

from apps.api.chat.service import ChatService
from apps.api.quote_links import order_pdf_url
from apps.mcp.bridge import WhatsAppBridge, to_whatsapp
from carvajal.adapters.crm import OdooError, OrderResult
from carvajal.adapters.storage import tables
from carvajal.quotation import render
from carvajal.quotation.order import Buyer, OrderRequest, has_buyer_data, merge_buyer
from carvajal.quotation.render import ORDER_OFFER, RECEIPT_PROMISE

GUION_REGISTRO = next(guion for guion in GUIONES if guion.designado)
_DUENOS = count()
SECRETO = b"secreto-del-ensayo"
MEDIOS = "https://medios.ensayo.test"
WEB = "https://carvajal.ensayo.test"
FOLIO = "0123456789ab"


def _adjunto(folio: str, dueno: str) -> str:
    return order_pdf_url(MEDIOS, folio, dueno, 4_102_444_800, SECRETO)


def _enlace(folio: str, dueno: str) -> str:
    return order_pdf_url(WEB, folio, dueno, 4_102_444_800, SECRETO)


def _puente(**enlaces: Any) -> WhatsAppBridge:
    """El puente de WhatsApp solo para entregar el recibo (``_with_receipt``)."""

    return WhatsAppBridge(chat=cast(Any, object()), secret=SECRETO, **enlaces)


@pytest.fixture()
def engine() -> Iterator[Any]:
    motor = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables.metadata.create_all(motor)
    with motor.begin() as conexion:
        conexion.execute(text("DROP INDEX IF EXISTS uq_messages_turn"))
        conexion.execute(text("DROP INDEX IF EXISTS uq_messages_turn_user"))
    try:
        yield motor
    finally:
        motor.dispose()


class CanalServicio:
    """Un canal del ensayo sobre ``ChatService``: web tal cual o WhatsApp (``*x*``, sin flujo).

    En WhatsApp el estado se deduce del texto, como en el ensayo; solo el turno del agente
    simulado (que no trae el texto del carrito) usa el ``awaiting`` del servicio. Cada canal
    entrega el recibo del pedido como en producción: la web con ``render.receipt_offer`` (la
    vista del stream) y WhatsApp con el adjunto del puente (``ADJUNTAR_PDF:``).
    """

    def __init__(self, service: ChatService, nombre: str = "web") -> None:
        self.service = service
        self.nombre = nombre
        self.dueno = ""
        self.registros: list[str] = []
        self.puente = _puente(order_attachment=_adjunto)

    def reiniciar(self) -> None:
        prefijo = "wa_" if self.nombre == "whatsapp" else "visitante-"
        self.dueno = f"{prefijo}ensayo-{next(_DUENOS)}"

    def enviar(self, mensaje: str) -> Respuesta:
        eventos = {evento.name: evento.data for evento in self.service.stream(self.dueno, mensaje)}
        fijo = eventos.get("fallback")
        texto = (fijo or {}).get("text", "")
        cierre = eventos.get("done") or {}
        awaiting = (cierre.get("flow") or {}).get("awaiting")
        if self.nombre == "whatsapp":
            plano = texto.replace("**", "*")
            plano = self.puente._with_receipt(plano, cierre.get("flow") or {}, self.dueno)
            return Respuesta(texto=plano, awaiting=inferir_awaiting(plano) if fijo else awaiting)
        texto = render.receipt_offer(texto, lambda folio: f"{WEB}/pedidos/{folio}.pdf")
        return Respuesta(
            texto=texto,
            awaiting=awaiting,
            folio=cierre.get("quote_id"),
            ruta=(eventos.get("trace") or {}).get("route") or cierre.get("route"),
        )

    def tras_registro(self, respuesta: Respuesta, folio: str) -> None:
        self.registros.append(folio)


class OrdersQueFallaUnaVez(FakeOrders):
    """Odoo que falla al primer pedido y responde al reintento (como un corte breve)."""

    def place(self, request: OrderRequest) -> OrderResult:
        self.requests.append(request)
        if len(self.requests) == 1:
            raise OdooError("Odoo no responde (ConnectError).")
        return OrderResult(name="S00001", order_id=1, lead_id=2)


def _mensajes(transcripcion: Any) -> list[str]:
    return [paso.mensaje for paso, _ in transcripcion.turnos]


@pytest.mark.parametrize("canal", ["web", "whatsapp"])
def test_el_guion_de_registro_rechaza_el_pedido_antes_del_segundo_si(
    engine: Any, canal: str
) -> None:
    # Arrange: Odoo configurado: el folio ofrece el pedido.
    orders = FakeOrders()
    service, _ = _service(engine, orders)
    ensayo = CanalServicio(service, canal)

    # Act
    transcripcion = ejecutar(ensayo, GUION_REGISTRO, registrar=True)

    # Assert
    mensajes = _mensajes(transcripcion)
    assert mensajes[-3:] == ["sí", "ahora no", "sí"]
    _, registro = transcripcion.turnos[-3]
    assert OFERTA_PEDIDO in registro.texto
    assert registro.awaiting == "pedido"
    assert len(ensayo.registros) == 1
    assert orders.requests == []


@pytest.mark.parametrize("canal", ["web", "whatsapp"])
def test_sin_odoo_el_guion_de_registro_sigue_como_en_la_spec_054(engine: Any, canal: str) -> None:
    # Arrange
    service, _ = _service(engine, None)
    ensayo = CanalServicio(service, canal)

    # Act
    transcripcion = ejecutar(ensayo, GUION_REGISTRO, registrar=True)

    # Assert: sin oferta, «ahora no» se salta y el segundo «sí» repite el folio.
    mensajes = _mensajes(transcripcion)
    assert "ahora no" not in mensajes
    assert mensajes[-2:] == ["sí", "sí"]
    assert OFERTA_PEDIDO not in transcripcion.turnos[-2][1].texto


@pytest.mark.parametrize("canal", ["web", "whatsapp"])
def test_el_pedido_del_ensayo_se_crea_con_el_comprador_del_run(engine: Any, canal: str) -> None:
    # Arrange
    orders = FakeOrders()
    service, runner = _service(engine, orders)
    ensayo = CanalServicio(service, canal)
    nombre = nombre_de_prueba("20261126103000")
    correo = correo_de_prueba(f"20261126103000-{canal}")

    # Act
    pedido = ejecutar_pedido(ensayo, nombre=nombre, correo=correo)

    # Assert: los datos se confirman antes del pedido y «Sí, confírmalo» lo crea.
    assert pedido.numero == "S00001"
    assert pedido.folio == ensayo.registros[0]
    assert not pedido.reintentado
    assert _mensajes(pedido.transcripcion)[-5:] == [
        ACEPTAR_PEDIDO,
        nombre,
        correo,
        CONFIRMAR_PEDIDO,
        "quiero comprarla",
    ]
    _, confirmacion = pedido.transcripcion.turnos[-3]
    assert confirmacion.awaiting == "confirmar_pedido"
    assert formato(f"- Correo: **{correo}**", canal) in confirmacion.texto
    _, creado = pedido.transcripcion.turnos[-2]
    assert formato(f"La confirmación del pedido llega a **{correo}**", canal) in creado.texto
    recibo = recibo_en(creado.texto, canal)
    assert recibo is not None
    assert (recibo.folio, recibo.numero) == (pedido.folio, "S00001")
    assert PROMESA_RECIBO not in creado.texto
    (request,) = orders.requests
    assert request.folio == pedido.folio
    assert request.buyer == Buyer(nombre, correo)
    assert request.channel == ("WhatsApp" if canal == "whatsapp" else "chat web")
    assert len(runner.calls) == 1  # solo la cotización pasó por el modelo


def test_sin_odoo_el_escenario_del_pedido_se_salta_tras_el_folio(engine: Any) -> None:
    # Arrange
    service, _ = _service(engine, None)
    ensayo = CanalServicio(service)

    # Act / Assert
    with pytest.raises(PedidosApagados, match="ODOO_URL"):
        ejecutar_pedido(ensayo, nombre="Ensayo Prueba", correo="e2e-x@example.com")
    assert len(ensayo.registros) == 1


@pytest.mark.parametrize("canal", ["web", "whatsapp"])
def test_si_odoo_falla_una_vez_el_si_reintenta_con_el_mismo_folio(engine: Any, canal: str) -> None:
    # Arrange
    orders = OrdersQueFallaUnaVez()
    service, _ = _service(engine, orders)
    ensayo = CanalServicio(service, canal)

    # Act
    pedido = ejecutar_pedido(ensayo, nombre="Ensayo Prueba", correo="e2e-x@example.com", pausa=0)

    # Assert: el fallo deja el pedido por confirmar y el «sí» lo crea sin otro folio.
    assert pedido.reintentado
    assert pedido.numero == "S00001"
    assert _mensajes(pedido.transcripcion)[-3:] == [CONFIRMAR_PEDIDO, "sí", "quiero comprarla"]
    _, fallido = pedido.transcripcion.turnos[-3]
    assert PEDIDO_FALLIDO in fallido.texto
    assert fallido.awaiting == "confirmar_pedido"
    assert [request.folio for request in orders.requests] == [pedido.folio, pedido.folio]
    assert orders.requests[1].buyer == Buyer("Ensayo Prueba", "e2e-x@example.com")
    assert len(ensayo.registros) == 1


def test_si_odoo_sigue_fallando_el_escenario_del_pedido_falla(engine: Any) -> None:
    # Arrange
    orders = FakeOrders(fail=True)
    service, _ = _service(engine, orders)
    ensayo = CanalServicio(service)

    # Act / Assert: un solo reintento; el segundo fallo hace fallar el escenario.
    with pytest.raises(AssertionError, match="ni al reintentar"):
        ejecutar_pedido(ensayo, nombre="Ensayo Prueba", correo="e2e-x@example.com", pausa=0)
    assert len(orders.requests) == 2
    assert len({request.folio for request in orders.requests}) == 1


@pytest.mark.parametrize(
    ("run_id", "nombre"),
    [
        ("20261126103000", "Ensayo Cacgbbcgbadaaa"),
        ("corrida-7", "Ensayo Corridah"),
        ("", "Ensayo Prueba"),
        # Cortos: sin «prueba», el flujo los leería como muletilla o partícula.
        ("ok", "Ensayo Okprueba"),
        ("si", "Ensayo Siprueba"),
        ("ya", "Ensayo Yaprueba"),
        ("es", "Ensayo Esprueba"),
        ("34", "Ensayo Deprueba"),
        ("mejor", "Ensayo Mejorprueba"),
    ],
)
def test_el_comprador_de_prueba_lo_lee_el_flujo(run_id: str, nombre: str) -> None:
    # Arrange: el correo del canal, como en test_web.py y test_whatsapp.py.
    correo = correo_de_prueba(f"{run_id}-web")

    # Act
    comprador = merge_buyer(merge_buyer(Buyer(), nombre_de_prueba(run_id)), correo)

    # Assert
    assert nombre_de_prueba(run_id) == nombre
    assert comprador == Buyer(nombre, correo)
    assert has_buyer_data(nombre) and has_buyer_data(correo)
    assert correo.startswith("e2e-") and correo.endswith("-web@example.com")


@pytest.mark.parametrize(
    ("politica", "designado", "pedido", "esperado"),
    [
        ("todas", False, True, True),
        ("una", True, False, True),
        ("una", True, True, False),
        ("una", False, False, False),
        ("ninguna", True, False, False),
    ],
)
def test_con_pedido_el_folio_unico_lo_registra_el_pedido(
    politica: str, designado: bool, pedido: bool, esperado: bool
) -> None:
    assert registra(politica, designado, pedido=pedido) is esperado


def test_la_oferta_del_ensayo_es_la_de_la_app() -> None:
    assert OFERTA_PEDIDO in ORDER_OFFER
    assert inferir_awaiting(ORDER_OFFER) == "pedido"
    assert CONFIRMAR_DATOS in render.confirm_buyer("Ana", "ana@correo.com").text
    assert PEDIDO_FALLIDO in render.order_failed().text


@pytest.mark.parametrize("canal", ["web", "whatsapp"])
@pytest.mark.parametrize(
    ("mensaje", "esperado"),
    [
        (render.order_offer(render.Message("✅ **Cotización #abc registrada**", "")), "pedido"),
        (render.ask_buyer(), "datos_pedido"),
        (render.buyer_missing("Ana", None), "datos_pedido"),
        (render.buyer_missing(None, "ana@correo.com"), "datos_pedido"),
        (render.buyer_missing(None, None), "datos_pedido"),
        (render.confirm_buyer("Ensayo Prueba", "e2e-x@example.com"), "confirmar_pedido"),
        (render.order_failed(), "confirmar_pedido"),
        (render.order_reminder("pedido"), "pedido"),
        (render.order_reminder("datos_pedido"), "datos_pedido"),
        (render.order_reminder("confirmar_pedido"), "confirmar_pedido"),
        (render.order_placed("S00001", "e2e-x@example.com", "abc"), None),
        (render.already_ordered("S00001"), None),
        (render.order_declined("abc"), None),
        # «No, …» o «sí, pero…» ante la confirmación: el pedido sigue por confirmar.
        (render.which_correction(), "confirmar_pedido"),
        (render.order_expired(["Vaso rígido"]), None),
        (render.order_cancel_request("S00001"), None),
        (render.order_status("S00001"), None),
    ],
)
def test_whatsapp_deduce_cada_paso_del_pedido_del_texto(
    mensaje: render.Message | None, esperado: str | None, canal: str
) -> None:
    # Arrange: WhatsApp recibe la plantilla con la negrita de un solo «*» (bridge.to_whatsapp).
    assert mensaje is not None
    texto = to_whatsapp(mensaje.text) if canal == "whatsapp" else mensaje.text

    # Act
    awaiting = inferir_awaiting(texto)

    # Assert
    assert awaiting == esperado


# --- Recibo del pedido (spec 056, SC-008) -------------------------------------------------


def test_el_recibo_de_la_web_se_lee_del_enlace_de_la_vista() -> None:
    # Arrange: el texto que la vista del stream entrega con la cookie del dueño.
    confirmado = render.order_placed("S00042", "e2e-x@example.com", FOLIO).text
    texto = render.receipt_offer(confirmado, lambda folio: f"{WEB}/pedidos/{folio}.pdf")

    # Act
    recibo = recibo_en(texto, "web")

    # Assert
    assert recibo == Recibo(url=f"{WEB}/pedidos/{FOLIO}.pdf", folio=FOLIO, numero="S00042")
    assert recibo.ruta == f"/pedidos/{FOLIO}.pdf"
    assert PROMESA_RECIBO not in texto
    assert PROMESA_RECIBO in RECEIPT_PROMISE
    assert recibo_en(confirmado, "web") is None  # sin enlace (la promesa cruda)


@pytest.mark.parametrize("entrega", ["order_attachment", "order_link"])
def test_el_recibo_de_whatsapp_se_lee_del_adjunto_y_del_enlace_del_puente(entrega: str) -> None:
    # Arrange: el pedido confirmado tal como lo entrega el puente (adjunto o enlace firmado).
    enlaces = {"order_attachment": _adjunto, "order_link": _enlace}
    puente = _puente(**{entrega: enlaces[entrega]})
    escrito = to_whatsapp(render.order_placed("S00042", "e2e-x@example.com", FOLIO).text)
    flujo = {"order": {"name": "S00042", "folio": FOLIO}}

    # Act
    texto = puente._with_receipt(escrito, flujo, "wa_ensayo")
    recibo = recibo_en(texto, "whatsapp")

    # Assert
    assert recibo is not None
    assert (recibo.folio, recibo.numero) == (FOLIO, "S00042")
    assert recibo.url == enlaces[entrega](FOLIO, "wa_ensayo")
    assert recibo.ruta.startswith(f"/pedidos/{FOLIO}.pdf?t=")
    assert PROMESA_RECIBO not in texto
    assert ("ADJUNTAR_PDF: " in texto) is (entrega == "order_attachment")


def test_sin_enlace_whatsapp_quita_la_promesa_y_no_hay_recibo() -> None:
    # Arrange
    escrito = to_whatsapp(render.order_placed("S00042", "e2e-x@example.com", FOLIO).text)

    # Act
    texto = _puente()._with_receipt(escrito, {"order": {"name": "S00042", "folio": FOLIO}}, "x")

    # Assert
    assert recibo_en(texto, "whatsapp") is None
    assert PROMESA_RECIBO not in texto


def test_un_folio_sin_oferta_se_detecta() -> None:
    # Arrange
    con_oferta = Transcripcion(
        "web", [(REGISTRO, Respuesta(f"Cotización #{FOLIO} registrada\n{ORDER_OFFER}", "pedido"))]
    )
    sin_oferta = Transcripcion(
        "web", [(REGISTRO, Respuesta(f"Cotización #{FOLIO} registrada", None))]
    )
    sin_registro = Transcripcion("web", [(Paso("hola"), Respuesta("Hola", None))])

    # Act / Assert
    assert oferta_ausente(con_oferta) is None
    assert oferta_ausente(sin_oferta) == FOLIO
    assert oferta_ausente(sin_registro) is None


@pytest.mark.parametrize(
    ("base", "valor", "esperado"),
    [
        ("https://carvajal.example", "", True),
        ("https://carvajal.example", "0", False),
        ("http://127.0.0.1:8000", "", False),
        ("http://127.0.0.1:8000", "1", True),
    ],
)
def test_en_produccion_se_esperan_los_pedidos(
    monkeypatch: pytest.MonkeyPatch, base: str, valor: str, esperado: bool
) -> None:
    # Arrange
    monkeypatch.setenv("ENSAYO_BASE_URL", base)
    monkeypatch.delenv("ENSAYO_TARGET", raising=False)
    monkeypatch.setenv("ENSAYO_EXPECT_PEDIDOS", valor)

    # Act
    config = Config.desde_entorno()

    # Assert
    assert config.expect_pedidos is esperado


# --- Correo de confirmación en Odoo (spec 056, SC-001) ------------------------------------

PEDIDO_OK = {
    "configurado": True,
    "pedidos": [{"name": "S00042", "state": "sale"}],
    "correos": ["sent"],
    "mensajes_con_adjunto": 1,
}


def _nombra(mensajes: list[str], marca: str | None) -> bool:
    """Sin ``marca``, ninguno; con ella, alguno de los mensajes la nombra."""

    return any(marca in mensaje for mensaje in mensajes) if marca else not mensajes


@pytest.mark.parametrize(
    ("resumen", "fallo", "aviso"),
    [
        (PEDIDO_OK, None, None),
        ({**PEDIDO_OK, "correos": []}, None, None),  # auto_delete: el correo ya salió
        ({**PEDIDO_OK, "correos": ["exception"]}, "excepción", None),
        ({**PEDIDO_OK, "correos": ["outgoing"]}, None, "cola"),
        ({**PEDIDO_OK, "correos": {"error": 403}}, None, "mail.mail"),
        ({**PEDIDO_OK, "mensajes_con_adjunto": 0}, None, "PDF adjunto"),
        ({**PEDIDO_OK, "pedidos": [{"name": "S00042", "state": "draft"}]}, "draft", None),
        ({**PEDIDO_OK, "pedidos": [PEDIDO_OK["pedidos"][0]] * 2}, "2 pedidos", None),
        ({**PEDIDO_OK, "pedidos": []}, "ningún sale.order", None),
        ({**PEDIDO_OK, "pedidos": {"error": 401}}, "status 401", None),
        ({"configurado": False}, "ODOO_URL", None),
        (None, "no se pudo leer Odoo", None),
    ],
)
def test_el_veredicto_del_correo_del_pedido(
    resumen: dict[str, Any] | None, fallo: str | None, aviso: str | None
) -> None:
    # Act
    fallos, avisos = correo_odoo.veredicto(resumen, FOLIO)

    # Assert
    assert _nombra(fallos, fallo), fallos
    assert _nombra(avisos, aviso), avisos


LLAMADAS_ODOO: list[tuple[str, Any]] = []


class OdooDeLectura:
    """``OdooClient`` de solo lectura para el script del contenedor (sin red)."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def search_read(self, model: str, domain: Any, fields: Any) -> Any:
        LLAMADAS_ODOO.append((model, domain))
        if model == "sale.order":
            return [{"id": 7, "name": "S00042", "state": "sale", "partner_id": [1, "Ana"]}]
        if model == "mail.mail":
            raise OdooError("Acceso denegado", 403)
        return [{"id": 1}]

    def close(self) -> None:
        pass


class AjustesConOdoo:
    odoo_configured = True
    odoo_url = "https://odoo.ensayo.test"
    odoo_db = "ensayo"

    odoo_api_key = SecretStr("clave-que-no-sale")


def test_el_script_del_contenedor_solo_lee_y_no_imprime_la_clave(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Arrange
    import carvajal.adapters.crm.odoo as odoo
    import carvajal.infrastructure.settings as ajustes

    LLAMADAS_ODOO.clear()
    monkeypatch.setattr(odoo, "OdooClient", OdooDeLectura)
    monkeypatch.setattr(ajustes, "Settings", AjustesConOdoo)
    monkeypatch.setattr(sys, "argv", ["-c", FOLIO])

    # Act
    exec(compile(correo_odoo.SCRIPT, "<script>", "exec"), {"__name__": "__main__"})
    salida = capsys.readouterr().out

    # Assert
    resumen = correo_odoo.leer_resumen(f"ruido de docker\n{salida}")
    assert resumen == {
        "configurado": True,
        "pedidos": [{"name": "S00042", "state": "sale"}],
        "correos": {"error": 403},
        "mensajes_con_adjunto": 1,
    }
    assert "clave-que-no-sale" not in salida and "Ana" not in salida
    assert [modelo for modelo, _ in LLAMADAS_ODOO] == [
        "sale.order",
        "mail.mail",
        "mail.message",
    ]
    assert LLAMADAS_ODOO[0][1] == [["client_order_ref", "=", FOLIO]]
    fallos, avisos = correo_odoo.veredicto(resumen, FOLIO)
    assert not fallos and len(avisos) == 1


def _config(**cambios: Any) -> Config:
    return replace(Config.desde_entorno(), **cambios)


def test_un_correo_en_la_cola_se_vuelve_a_leer_hasta_que_sale() -> None:
    # Arrange
    lecturas = iter([{**PEDIDO_OK, "correos": ["outgoing"]}, PEDIDO_OK])
    leidos: list[str] = []

    def leer(folio: str) -> dict[str, Any]:
        leidos.append(folio)
        return next(lecturas)

    # Act
    correo_odoo.comprobar_correo_del_pedido(_config(), FOLIO, "web", leer=leer, pausa=0)

    # Assert
    assert leidos == [FOLIO, FOLIO]


def test_un_correo_en_excepcion_hace_fallar_el_ensayo_del_pedido() -> None:
    # Arrange
    resumen = {**PEDIDO_OK, "correos": ["sent", "exception"]}

    # Act / Assert
    with pytest.raises(AssertionError, match="excepción"):
        correo_odoo.comprobar_correo_del_pedido(
            _config(), FOLIO, "whatsapp", leer=lambda _folio: resumen, pausa=0
        )


def test_sin_ssh_el_correo_queda_para_la_revision_manual(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Act
    correo_odoo.comprobar_correo_del_pedido(_config(target="prod", app_ssh=""), FOLIO, "web")

    # Assert
    assert "revísalo a mano" in capsys.readouterr().out


def test_el_folio_del_script_se_valida_antes_del_ssh() -> None:
    with pytest.raises(ValueError, match="folio inválido"):
        correo_odoo.leer_pedido(Ssh("nadie@ensayo.invalid"), "/opt/carvajal", "x; rm -rf /")
