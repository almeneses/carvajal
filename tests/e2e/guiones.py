"""Guiones del ensayo general, iguales para el chat web y para WhatsApp (specs 053 a 055).

Un guion es una lista de pasos: el mensaje del cliente, el estado ``awaiting`` que debe
quedar, las marcas de texto de la plantilla (escritas con el formato web, ``**x**``; en
WhatsApp se comparan como ``*x*``) y si el paso es determinista (reglas del flujo, sin LLM)
o depende del agente. Los pasos deterministas se comprueban estrictos; los del agente,
contra un conjunto de estados permitidos y, si se desvían, el guion se repite una vez en un
hilo nuevo. Nunca se comparan precios: cambian con el catálogo y la ciudad.

Con el CRM configurado (spec 056) el folio termina ofreciendo el pedido y deja el flujo en
``awaiting="pedido"``: un «sí» ya no repite el folio, acepta el pedido. :func:`cierre` lo
rechaza con «ahora no» cuando hay oferta, y el pedido completo es un escenario aparte
(:func:`ejecutar_pedido`: nombre y correo, la confirmación de los datos y el pedido) que crea
un pedido real en Odoo y solo corre con ``ENSAYO_PEDIDO``.

Los canales (``test_web.py`` y ``test_whatsapp.py``) implementan :class:`Canal`; este módulo
no sabe de HTTP ni de MCP.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

# --- Textos fijos de la app (copiados a propósito: el ensayo no importa la app) ---------

BROMA_RECHAZO = "Arrancado, vaya cómprele a su madre entonces 😂"
CANCELADA = "Listo, cancelé la cotización"
FOTO_RECHAZADA = "No pude abrir esa foto"
FOTO_NO_DISPONIBLE = "No pude analizar la foto"
PISTA_FOTO = ("¿Te cotizo alguna?", "¿Te la cotizo?")
FOLIO = re.compile(r"Cotización #([0-9a-f]{12}) registrada")
SOLO_FOLIO = re.compile(r"^[0-9a-f]{12}$")
# Spec 056: la oferta del pedido tras el folio (``render.ORDER_OFFER``), la confirmación de
# los datos (``render.confirm_buyer``), el fallo de Odoo (``render.order_failed``) y el número
# del pedido en Odoo (``render.order_placed``).
OFERTA_PEDIDO = "¿Quieres hacer el pedido?"
ACEPTAR_PEDIDO = "Sí, quiero hacer el pedido"  # lo que dice el botón «Hacer el pedido»
CONFIRMAR_DATOS = "¿Lo confirmo? Si algo está mal, escríbeme el dato correcto."
CONFIRMAR_PEDIDO = "Sí, confírmalo"  # lo que dice el botón «Confirmar pedido»
PEDIDO_FALLIDO = "No pude crear el pedido"
PEDIDO_CREADO = re.compile(r"Pedido (\S+) confirmado")
# El recibo del pedido (spec 056). La promesa cruda (``render.RECEIPT_PROMISE``) nunca llega
# al cliente: la web la cambia por el enlace (``render.receipt_offer``) y WhatsApp por el
# adjunto o el enlace firmado (``bridge._receipt_block``), o la quita si no puede entregarlo.
PROMESA_RECIBO = "Te envío el recibo del pedido en PDF."
RECIBO_WEB = re.compile(
    r"\[Descarga el recibo de tu pedido ([^\]\s]+)\]\(([^)\s]*/pedidos/([0-9a-f]{12})\.pdf)\)"
)
RECIBO_WHATSAPP = re.compile(
    r"📄 (?:Te envío el recibo|Recibo) de tu pedido (\S+) en PDF\.[^\n]*\n+"
    r"(?:ADJUNTAR_PDF:\s*)?(\S*/pedidos/([0-9a-f]{12})\.pdf\S*)"
)
# Segundos antes de reintentar con «sí» si Odoo falló al crear el pedido (``order_failed``
# pide responder «sí» en unos minutos; el borrador conserva los datos y el folio).
PAUSA_REINTENTO_PEDIDO = 30.0

# Pedido explícito (cantidad, producto y ciudad) para que el agente llame a ``cotizar``.
# Deben ser productos con precio de referencia en Bogotá en el catálogo vigente
# (data/catalog/empaques/precios.csv, #258): sin precio, el agente no abre el flujo.
PEDIDO_OPCIONES = "Cotízame 1.000 vasos para bebida fría en Bogotá"
PEDIDO_COMPARACION = "¿Cuánto me salen 500 o 1.000 portacomidas espumados C1 Wau en Bogotá?"
PEDIDO_CAMBIO = "No, mejor cotízame 500 platos hondos en Bogotá"
PEDIDO_FOTO = "la 2, son 300 para Bogotá"

# Memoria entre conversaciones (spec 045): datos ficticios, nunca de una persona real.
OLVIDAR = "Olvida todo lo que sabes de mí"
PRESENTACION = (
    "Hola, soy Ana Prueba de la Panadería La Espiga y suelo comprar en Cali. "
    "Recuérdalo para la próxima."
)
PREGUNTA_MEMORIA = "¿Qué sabes de mí?"
NOMBRE = "Ana"
EMPRESA = "Espiga"
OLVIDO = re.compile(r"olvid|no tengo nada|borr|ya no", re.IGNORECASE)

CUALQUIERA: frozenset[str | None] | None = None


def formato(marca: str, canal: str) -> str:
    """La marca con la negrita del canal: ``**x**`` en web, ``*x*`` en WhatsApp."""

    return marca.replace("**", "*") if canal == "whatsapp" else marca


def inferir_awaiting(texto: str) -> str | None:
    """Estado del flujo deducido del texto (WhatsApp no recibe ``done.flow``).

    Usa la última pregunta de la plantilla que aparece en el texto: es la que deja el turno.
    """

    marcas = (
        ("¿Cuál te cotizo?", "eleccion"),
        ("No tengo la opción", "eleccion"),
        ("¿Con cuál cantidad la armo?", "cantidad"),
        ("¿La confirmo y te envío el PDF?", "confirmar"),
        ("¿Qué hago con eso?", "cambio_producto"),
        ("La opción más económica que tengo", "oferta"),
        ("¿Algo más", "algo_mas"),
        (OFERTA_PEDIDO, "pedido"),
        ("¿Seguimos con el pedido", "pedido"),
        ("Para el pedido envíame tu", "datos_pedido"),
        ("te envío la confirmación del pedido?", "datos_pedido"),
        ("de quién hago el pedido?", "datos_pedido"),
        ("No alcancé a leer tu nombre y tu correo", "datos_pedido"),
        ("Para el pedido me faltan", "datos_pedido"),
        # La confirmación de los datos y el fallo de Odoo dejan el pedido por confirmar.
        ("Voy a hacer el pedido con estos datos", "confirmar_pedido"),
        ("¿Lo confirmo? Si algo está mal", "confirmar_pedido"),
        ("¿Confirmo el pedido con los datos", "confirmar_pedido"),
        ("lo intento de nuevo, o *ahora no*", "confirmar_pedido"),
        ("lo intento de nuevo, o **ahora no**", "confirmar_pedido"),
        # «No, …» o «sí, pero…» ante la confirmación (``render.which_correction``).
        ("¿Qué dato corrijo?", "confirmar_pedido"),
    )
    encontradas = [(texto.rfind(marca), estado) for marca, estado in marcas if marca in texto]
    return max(encontradas)[1] if encontradas else None


def registra(politica: str, designado: bool, *, pedido: bool = False) -> bool:
    """``ENSAYO_COTIZAR``: ``todas`` registra siempre, ``una`` solo el guion designado.

    Con ``pedido`` (``ENSAYO_PEDIDO``), el folio único de ``una`` lo registra el escenario del
    pedido: el guion designado se cierra con «cancela todo» para no dejar dos por canal.
    """

    if politica == "todas":
        return True
    if politica == "una":
        return designado and not pedido
    return False


def nombre_de_prueba(run_id: str) -> str:
    """Nombre del comprador del ensayo: «Ensayo» y el ``run_id`` escrito con letras.

    El flujo descarta como nombre un texto con dígitos (``order._name_from``), así que cada
    dígito pasa a una letra (0→a … 9→j). También descarta o recorta muletillas y respuestas
    («ok», «sí», «ya», «gracias»…) y partículas como «de»: un ``run_id`` corto (menos de seis
    letras, p. ej. ``ENSAYO_RUN_ID=ok``) lleva «prueba» al final para no caer en una de ellas.
    El de por defecto (hora UTC, catorce letras de la «a» a la «j») nunca es una palabra. El
    correo (:func:`correo_de_prueba`) lleva el ``run_id`` tal cual.
    """

    letras = "".join(
        "abcdefghij"[int(caracter)] if caracter.isdigit() else caracter
        for caracter in run_id.lower()
        if caracter.isascii() and caracter.isalnum()
    )
    if len(letras) < 6:
        letras += "prueba"
    return f"Ensayo {letras.capitalize()}"


def correo_de_prueba(run_id: str) -> str:
    """Correo del comprador del ensayo, en ``example.com`` (RFC 2606): nunca le llega nada."""

    limpio = re.sub(r"[^a-z0-9-]+", "-", run_id.lower()).strip("-") or "prueba"
    return f"e2e-{limpio}@example.com"


@dataclass(frozen=True)
class Recibo:
    """El recibo del pedido que trajo un turno: su URL, el folio y el número en Odoo."""

    url: str
    folio: str
    numero: str

    @property
    def ruta(self) -> str:
        """``/pedidos/<folio>.pdf`` con su consulta (``?t=…``), sin el host del enlace."""

        return self.url[self.url.index("/pedidos/") :]


def recibo_en(texto: str, canal: str) -> Recibo | None:
    """El recibo del pedido en el texto del turno, o ``None`` si no lo trae.

    Web: «📄 [Descarga el recibo de tu pedido S…](…/pedidos/<folio>.pdf)», que se abre con la
    cookie del visitante. WhatsApp: «📄 Te envío el recibo de tu pedido S… en PDF.» con la
    línea ``ADJUNTAR_PDF:`` o «📄 Recibo de tu pedido S… en PDF. Ábrelo aquí:» con el enlace
    firmado (``?t=…``).
    """

    if canal == "whatsapp":
        encontrado = RECIBO_WHATSAPP.search(texto)
        if encontrado is None:
            return None
        numero, url, folio = encontrado.groups()
        return Recibo(url=url, folio=folio, numero=numero)
    encontrado = RECIBO_WEB.search(texto)
    if encontrado is None:
        return None
    numero, url, folio = encontrado.groups()
    return Recibo(url=url, folio=folio, numero=numero)


@dataclass(frozen=True)
class Paso:
    """Un mensaje del cliente y lo que se espera del turno."""

    mensaje: str
    espera: frozenset[str | None] | None = CUALQUIERA  # estados ``awaiting`` permitidos
    marcas: tuple[str, ...] = ()  # subcadenas (formato web); ``{folio}`` se sustituye
    ausentes: tuple[str, ...] = ()  # subcadenas que no deben aparecer
    determinista: bool = True
    registra: bool = False
    si_previo: frozenset[str | None] | None = None  # solo si el turno anterior quedó así
    ruta: str | None = None  # ``trace.route`` esperado (solo web)


@dataclass
class Respuesta:
    """Lo que un canal devolvió en un turno, normalizado."""

    texto: str
    awaiting: str | None
    folio: str | None = None  # web: ``done.quote_id``; WhatsApp: ``None`` (solo texto)
    ruta: str | None = None  # web: ``trace.route``; WhatsApp: ``None``
    detalle: str = ""
    crudo: Any = None

    def resumen(self) -> str:
        return self.detalle or f"awaiting={self.awaiting}\n--- texto ---\n{self.texto[:1500]}"


class Canal(Protocol):
    """Un canal capaz de mantener una conversación del guion."""

    nombre: str  # "web" o "whatsapp"

    def reiniciar(self) -> None:
        """Abre una conversación nueva del mismo cliente."""

    def enviar(self, mensaje: str) -> Respuesta:
        """Envía un mensaje en la conversación activa."""

    def tras_registro(self, respuesta: Respuesta, folio: str) -> None:
        """Comprobaciones propias del canal tras registrar (PDF, tarjeta, folio)."""


@dataclass(frozen=True)
class Guion:
    """Un flujo definido de punta a punta."""

    nombre: str
    flujo: str  # referencia a la spec (F4/F5, F6, F8…)
    pasos: Callable[[bool], tuple[Paso, ...]]  # recibe si este guion registra
    designado: bool = False  # el que registra con ``ENSAYO_COTIZAR=una``


class DesvioDelAgente(AssertionError):
    """Un paso del agente cayó fuera de los estados permitidos (se reintenta una vez)."""


@dataclass
class Transcripcion:
    canal: str
    turnos: list[tuple[Paso, Respuesta]] = field(default_factory=list)

    def texto(self) -> str:
        partes = []
        for numero, (paso, respuesta) in enumerate(self.turnos, start=1):
            partes.append(f"[{self.canal} #{numero}] «{paso.mensaje}»\n{respuesta.resumen()}")
        return "\n\n".join(partes)


REGISTRO = Paso("sí", marcas=("Cotización #", "registrada"), registra=True)


def cierre(registrar: bool) -> tuple[Paso, ...]:
    """Desde ``confirmar``: «sí» registra (y un segundo «sí» no duplica) o «cancela todo».

    Si el folio ofrece el pedido (spec 056, ``awaiting="pedido"``), un «sí» lo aceptaría y
    pediría nombre y correo: antes del segundo «sí», «ahora no» rechaza el pedido. Sin el CRM
    ese paso se salta y el guion queda como en la spec 054.
    """

    if registrar:
        return (
            REGISTRO,
            Paso(
                "ahora no",
                espera=frozenset({None}),
                marcas=("tu cotización #{folio} queda registrada",),
                si_previo=frozenset({"pedido"}),
                ruta="pedido",
            ),
            Paso("sí", marcas=("ya quedó registrada con el folio **#{folio}**",)),
        )
    return (Paso("cancela todo", espera=frozenset({None}), marcas=(CANCELADA,)),)


def _resumen() -> Paso:
    return Paso(
        "eso es todo",
        espera=frozenset({"confirmar"}),
        marcas=("**Resumen de tu cotización**", "¿La confirmo y te envío el PDF?"),
    )


def _resumen_y_cierre(registrar: bool) -> tuple[Paso, ...]:
    return (_resumen(), *cierre(registrar))


def _elegir_opcion() -> tuple[Paso, ...]:
    """Pide opciones al agente y elige la recomendada (queda ``algo_mas``)."""

    eleccion = frozenset({"eleccion"})
    return (
        Paso(PEDIDO_OPCIONES, espera=frozenset({"eleccion", "algo_mas"}), determinista=False),
        Paso(
            "la 5",
            espera=eleccion,
            marcas=("No tengo la opción 5",),
            si_previo=eleccion,
            ruta="eleccion",
        ),
        Paso(
            "dame esa",
            espera=frozenset({"algo_mas"}),
            marcas=("Agregué **", "¿Algo más para tu cotización?"),
            si_previo=eleccion,
        ),
    )


def _opciones(registrar: bool) -> tuple[Paso, ...]:
    return (*_elegir_opcion(), *_resumen_y_cierre(registrar))


def _insulto(_registrar: bool) -> tuple[Paso, ...]:
    return (Paso("está muy caro, muy ladrones", marcas=(BROMA_RECHAZO,), ruta="rechazo"),)


def _retomar_y_cambio(_registrar: bool) -> tuple[Paso, ...]:
    eleccion = frozenset({"eleccion"})
    return (
        Paso(PEDIDO_OPCIONES, espera=frozenset({"eleccion", "algo_mas"}), determinista=False),
        Paso(
            "dame esa", espera=frozenset({"algo_mas"}), marcas=("Agregué **",), si_previo=eleccion
        ),
        Paso(
            "después te escribo",
            espera=frozenset({None}),
            marcas=("Guardé lo que armamos", "**retomar**"),
            ruta="salida",
        ),
        Paso(
            "retomar",
            espera=frozenset({"algo_mas"}),
            marcas=("↩️ **Tu cotización · 1 producto**",),
        ),
        Paso(
            PEDIDO_CAMBIO,
            espera=frozenset({"cambio_producto"}),
            marcas=("Llevábamos **", "¿Qué hago con eso?", "**Cancelar**", "**Conservar**"),
        ),
        # Tras cancelar, el pedido pendiente va al agente: su respuesta no es fija.
        Paso(
            "cancélalos",
            espera=frozenset({None, "eleccion", "cantidad", "algo_mas"}),
            ausentes=("Llevábamos",),
            determinista=False,
        ),
        Paso("cancela todo", espera=frozenset({None}), marcas=(CANCELADA,)),
    )


def _comparacion(registrar: bool) -> tuple[Paso, ...]:
    return (
        Paso(PEDIDO_COMPARACION, espera=frozenset({"cantidad"}), determinista=False),
        Paso(
            "con 500",
            espera=frozenset({"algo_mas"}),
            marcas=("Agregué **500 ", "¿Algo más para tu cotización?"),
        ),
        *_resumen_y_cierre(registrar),
    )


GUIONES: tuple[Guion, ...] = (
    Guion("opciones_y_registro", "F4/F5 → T4 → T5 → T6", _opciones, designado=True),
    Guion("insulto_por_precio", "F6 (broma fija)", _insulto),
    Guion("retomar_y_cambiar_producto", "F6 retomar + F8 cambio", _retomar_y_cambio),
    Guion("comparacion_por_cantidad", "F3 → T4 → T5", _comparacion),
)


def pasos_tras_foto(registrar: bool) -> tuple[Paso, ...]:
    """Lo que sigue a una conversación que empezó con la foto de un ajiaco (spec 053)."""

    return (
        Paso(
            "la 9",
            marcas=("No tengo la alternativa 9", "Te mostré estas opciones"),
            ruta="eleccion",
        ),
        Paso(
            PEDIDO_FOTO, espera=frozenset({"algo_mas"}), marcas=("Agregué **",), determinista=False
        ),
        *_resumen_y_cierre(registrar),
    )


# --- Pedido en Odoo (spec 056) ----------------------------------------------------------


def _hasta_el_folio(_registrar: bool) -> tuple[Paso, ...]:
    return (*_elegir_opcion(), _resumen(), REGISTRO)


GUION_FOLIO_PARA_PEDIDO = Guion("folio_para_pedido", "F4/F5 → T4 → T5 → T6", _hasta_el_folio)


def pasos_datos_pedido(nombre: str, correo: str) -> tuple[Paso, ...]:
    """Tras la oferta del folio: aceptar y dar nombre y correo en dos mensajes.

    Con los dos datos llega la confirmación («📋 Voy a hacer el pedido con estos datos…
    ¿Lo confirmo?», ``awaiting="confirmar_pedido"``): todavía no hay pedido. Todos los pasos
    son reglas del flujo (``route="pedido"``): el nombre y el correo nunca llegan a Jev ni al
    modelo.
    """

    datos = frozenset({"datos_pedido"})
    return (
        Paso(
            ACEPTAR_PEDIDO,
            espera=datos,
            marcas=("Para el pedido envíame tu **nombre** y tu **correo**",),
            ruta="pedido",
        ),
        Paso(
            nombre,
            espera=datos,
            marcas=(f"Gracias, {nombre}.", "¿A qué **correo** te envío la confirmación"),
            ruta="pedido",
        ),
        Paso(
            correo,
            espera=frozenset({"confirmar_pedido"}),
            marcas=(
                "📋 Voy a hacer el pedido con estos datos:",
                f"- Nombre: **{nombre}**",
                f"- Correo: **{correo}**",
                CONFIRMAR_DATOS,
            ),
            ausentes=("🧾", PEDIDO_FALLIDO),
            ruta="pedido",
        ),
    )


def paso_pedido_creado(correo: str, mensaje: str = CONFIRMAR_PEDIDO) -> Paso:
    """«Sí, confírmalo» (o el «sí» del reintento): el pedido creado en Odoo con su número."""

    return Paso(
        mensaje,
        espera=frozenset({None}),
        marcas=(
            "🧾 **Pedido ",
            " confirmado** · cotización #{folio}",
            f"La confirmación del pedido llega a **{correo}**",
        ),
        # La promesa cruda del recibo no llega: cada canal la cambia por el PDF o la quita.
        ausentes=(PEDIDO_FALLIDO, PROMESA_RECIBO),
        ruta="pedido",
    )


# Odoo falló: el pedido sigue por confirmar con los mismos datos y un «sí» lo reintenta.
PASO_PEDIDO_FALLIDO = Paso(
    CONFIRMAR_PEDIDO,
    espera=frozenset({"confirmar_pedido"}),
    marcas=(PEDIDO_FALLIDO, "responde **sí** en unos minutos y lo intento de nuevo"),
    ausentes=("🧾",),
    ruta="pedido",
)
PASO_PEDIDO_REPETIDO = Paso(
    "quiero comprarla",
    espera=frozenset({None}),
    marcas=("Ese pedido ya quedó creado con el número **",),
    ruta="pedido",
)


def oferta_ausente(transcripcion: Transcripcion) -> str | None:
    """El folio de un turno que registró sin ofrecer el pedido, o ``None`` si no hay.

    Con ``ODOO_*`` cada folio termina con la oferta (``render.ORDER_OFFER``); un folio sin
    ella en una app que debía tener Odoo (``ENSAYO_EXPECT_PEDIDOS``) es un fallo, no un salto.
    """

    for paso, respuesta in transcripcion.turnos:
        encontrado = FOLIO.search(respuesta.texto) if paso.registra else None
        if encontrado and OFERTA_PEDIDO not in respuesta.texto:
            return encontrado.group(1)
    return None


class PedidosApagados(Exception):
    """El folio no ofreció el pedido: la app no tiene ``ODOO_*`` (el escenario se salta)."""


@dataclass
class Pedido:
    """Lo que dejó el escenario del pedido: la conversación, el folio y el número en Odoo.

    ``reintentado``: Odoo falló al primer «Sí, confírmalo» y el pedido salió con el «sí»
    del reintento (queda en el informe).
    """

    transcripcion: Transcripcion
    folio: str
    numero: str
    reintentado: bool = False


def _confirmar_pedido(
    canal: Canal, transcripcion: Transcripcion, *, correo: str, folio: str, pausa: float
) -> bool:
    """«Sí, confírmalo»; si Odoo falla, comprueba el fallo y reintenta una vez con «sí».

    Devuelve si hubo que reintentar. Un segundo fallo hace fallar el escenario con un mensaje
    que nombra el folio (el pedido no quedó en Odoo).
    """

    creado = paso_pedido_creado(correo)
    respuesta = canal.enviar(creado.mensaje)
    fallo = PEDIDO_FALLIDO in respuesta.texto
    paso = PASO_PEDIDO_FALLIDO if fallo else creado
    transcripcion.turnos.append((paso, respuesta))
    _comprobar(canal, paso, respuesta, folio, transcripcion.texto())
    if not fallo:
        return False
    print(
        f"AVISO del ensayo: [{canal.nombre}] Odoo no creó el pedido del folio #{folio}; "
        f"se reintenta con «sí» en {pausa:g} s"
    )
    if pausa > 0:
        time.sleep(pausa)
    reintento = paso_pedido_creado(correo, "sí")
    respuesta = canal.enviar(reintento.mensaje)
    transcripcion.turnos.append((reintento, respuesta))
    if PEDIDO_FALLIDO in respuesta.texto:
        raise AssertionError(
            f"[{canal.nombre}] Odoo no creó el pedido del folio #{folio} ni al reintentar con "
            f"«sí»: revisa el log pedido.fallido de la app y que Odoo esté arriba\n"
            f"{transcripcion.texto()}"
        )
    _comprobar(canal, reintento, respuesta, folio, transcripcion.texto())
    return True


def ejecutar_pedido(
    canal: Canal, *, nombre: str, correo: str, pausa: float = PAUSA_REINTENTO_PEDIDO
) -> Pedido:
    """Folio → «Sí, quiero hacer el pedido» → nombre → correo → «¿Lo confirmo?» →
    «Sí, confírmalo» → pedido; luego «quiero comprarla» no duplica.

    Llegar al folio depende del agente y se reintenta una vez (:func:`ejecutar`); el resto es
    determinista. Si Odoo falla al confirmar, el flujo conserva los datos y, tras ``pausa``
    segundos, un «sí» lo reintenta con el mismo folio (una sola vez). Si el folio no trae la
    oferta, lanza :class:`PedidosApagados` sin enviar nada más.
    """

    transcripcion = ejecutar(canal, GUION_FOLIO_PARA_PEDIDO, registrar=True)
    _, registro = transcripcion.turnos[-1]
    encontrado = FOLIO.search(registro.texto)
    assert encontrado, f"no se registró el folio\n{transcripcion.texto()}"
    folio = encontrado.group(1)
    if OFERTA_PEDIDO not in registro.texto:
        raise PedidosApagados(
            f"el folio #{folio} no ofrece el pedido: la app no tiene ODOO_URL/ODOO_DB/"
            "ODOO_API_KEY (spec 056)"
        )
    ejecutar_pasos(
        canal,
        pasos_datos_pedido(nombre, correo),
        transcripcion,
        previo=registro.awaiting,
        folio=folio,
    )
    reintentado = _confirmar_pedido(canal, transcripcion, correo=correo, folio=folio, pausa=pausa)
    ejecutar_pasos(canal, (PASO_PEDIDO_REPETIDO,), transcripcion, previo=None, folio=folio)
    _, creado = transcripcion.turnos[-2]
    numero = PEDIDO_CREADO.search(creado.texto)
    assert numero, f"no se leyó el número del pedido\n{transcripcion.texto()}"
    _, repetido = transcripcion.turnos[-1]
    assert formato(f"**{numero.group(1)}**", canal.nombre) in repetido.texto, (
        f"el segundo «quiero comprarla» no nombra el pedido {numero.group(1)}\n"
        f"{transcripcion.texto()}"
    )
    return Pedido(transcripcion, folio, numero.group(1), reintentado)


# --- Ejecución --------------------------------------------------------------------------


def _comprobar(
    canal: Canal, paso: Paso, respuesta: Respuesta, folio: str | None, contexto: str
) -> str | None:
    """Comprueba un turno; devuelve el folio registrado (o el que ya había)."""

    nombre = canal.nombre
    texto = respuesta.texto
    if paso.espera is not None and respuesta.awaiting not in paso.espera:
        origen = "inferido del texto" if nombre == "whatsapp" else "done.flow.awaiting"
        mensaje = (
            f"«{paso.mensaje}»: awaiting={respuesta.awaiting!r} ({origen}), se esperaba "
            f"{sorted(map(str, paso.espera))}\n{contexto}"
        )
        raise AssertionError(mensaje)
    for marca in paso.marcas:
        esperada = formato(marca.replace("{folio}", folio or "?"), nombre)
        assert esperada in texto, f"«{paso.mensaje}»: falta {esperada!r}\n{contexto}"
    for marca in paso.ausentes:
        assert marca not in texto, f"«{paso.mensaje}»: sobra {marca!r}\n{contexto}"
    if respuesta.ruta is not None:
        if paso.ruta is not None:
            assert respuesta.ruta == paso.ruta, (
                f"«{paso.mensaje}»: ruta {respuesta.ruta!r}, se esperaba {paso.ruta!r}\n{contexto}"
            )
        if paso.determinista:
            assert respuesta.ruta != "agent", (
                f"«{paso.mensaje}» debía resolverse por reglas, sin el agente\n{contexto}"
            )
    if paso.registra:
        encontrado = FOLIO.search(texto)
        assert encontrado, f"«{paso.mensaje}»: no registró la cotización\n{contexto}"
        nuevo = encontrado.group(1)
        assert SOLO_FOLIO.fullmatch(nuevo)
        if nombre == "web":
            assert respuesta.folio == nuevo, (
                f"done.quote_id={respuesta.folio!r} no coincide con el folio {nuevo}\n{contexto}"
            )
        canal.tras_registro(respuesta, nuevo)
        return nuevo
    if folio is None:
        # SC-002 de la spec 054: nada se registra antes del «sí».
        assert respuesta.folio is None, f"«{paso.mensaje}»: registró antes del «sí»\n{contexto}"
        assert not FOLIO.search(texto), f"«{paso.mensaje}»: registró antes del «sí»\n{contexto}"
    else:
        assert respuesta.folio in {None, folio}, (
            f"«{paso.mensaje}»: un segundo «sí» emitió otro folio\n{contexto}"
        )
    return folio


def ejecutar_pasos(
    canal: Canal,
    pasos: Sequence[Paso],
    transcripcion: Transcripcion,
    *,
    previo: str | None = None,
    folio: str | None = None,
) -> str | None:
    """Corre los pasos en la conversación activa; devuelve el folio si registró.

    ``folio`` es el que ya registró la conversación, si sigue de un tramo anterior.
    """

    for paso in pasos:
        if paso.si_previo is not None and previo not in paso.si_previo:
            continue
        respuesta = canal.enviar(paso.mensaje)
        transcripcion.turnos.append((paso, respuesta))
        try:
            folio = _comprobar(canal, paso, respuesta, folio, transcripcion.texto())
        except DesvioDelAgente:
            raise
        except AssertionError as error:
            if paso.determinista:
                raise
            raise DesvioDelAgente(str(error)) from error
        previo = respuesta.awaiting
    return folio


def con_reintento[T](
    accion: Callable[[], T], aceptable: Callable[[T], bool], *, aviso: str
) -> tuple[T, bool]:
    """Un turno del agente con un reintento (en un hilo nuevo, lo abre ``accion``).

    Devuelve el último resultado y si hubo que reintentar, para dejarlo en el informe.
    """

    resultado = accion()
    if aceptable(resultado):
        return resultado, False
    print(f"AVISO del ensayo: {aviso}; se reintenta una vez en un hilo nuevo")
    return accion(), True


def ejecutar(canal: Canal, guion: Guion, *, registrar: bool) -> Transcripcion:
    """Corre el guion; si el agente se desvía, lo repite una vez en un hilo nuevo."""

    pasos = guion.pasos(registrar)
    intentos: list[Transcripcion] = []
    for _ in range(2):
        transcripcion = Transcripcion(canal.nombre)
        intentos.append(transcripcion)
        canal.reiniciar()
        try:
            ejecutar_pasos(canal, pasos, transcripcion)
        except DesvioDelAgente:
            continue
        return transcripcion
    anteriores = "\n\n=== intento siguiente ===\n\n".join(t.texto() for t in intentos)
    raise AssertionError(
        f"[{canal.nombre}] {guion.nombre}: el agente se desvió en los dos intentos\n{anteriores}"
    )
