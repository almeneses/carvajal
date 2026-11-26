"""Ensayo general del canal WhatsApp: MCP (lo que llama OpenClaw) y webhook firmado.

Las conversaciones usan alias ``e2e-<run>-<prueba>`` (no son números: nadie recibe nada) y
empiezan con ``nueva=True``. Gratis (sin LLM, sin Jev, sin escribir en la base): el esquema
del MCP, el token, el webhook desde un número fuera de la allowlist, los rechazos del
webhook y la sonda de foto inexistente. Lo demás lleva ``gasto``.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
from tests.e2e.clientes import Config, Mcp, Ssh, Web, firmar_webhook
from tests.e2e.guiones import (
    EMPRESA,
    FOTO_NO_DISPONIBLE,
    FOTO_RECHAZADA,
    GUIONES,
    NOMBRE,
    OLVIDAR,
    OLVIDO,
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
    inferir_awaiting,
    nombre_de_prueba,
    pasos_tras_foto,
    registra,
)

RAIZ = Path(__file__).resolve().parents[2]
RUTA_WEBHOOK = "/api/webhooks/whatsapp"
ADJUNTO_PDF = re.compile(r"ADJUNTAR_PDF:\s*(\S+)")
ENLACE_PDF = re.compile(r"(https?://\S+/cotizaciones/[0-9a-f]{12}\.pdf\S*)")
TELEFONO = re.compile(r"\+?\d[\d ]{7,}\d")


def _sin_telefonos(texto: str) -> str:
    """Nunca deja un número de teléfono en un mensaje de fallo."""

    return TELEFONO.sub("<número>", texto)


class McpOculto:
    """El cliente MCP sin su token en el ``repr``: pytest imprime los argumentos al fallar."""

    def __init__(self, cliente: Mcp) -> None:
        self._cliente = cliente
        self.url = cliente.url

    def __repr__(self) -> str:
        return f"McpOculto(url={self.url!r}, token=<oculto>)"

    def herramientas(self) -> list[Any]:
        return self._cliente.herramientas()

    def consultar(self, mensaje: str, conversacion: str, **opciones: Any) -> str:
        return self._cliente.consultar(mensaje, conversacion, **opciones)


class Firmante:
    """Firma el webhook sin exponer el secreto en el ``repr`` (ni en los fallos)."""

    def __init__(self, secreto: str) -> None:
        self._secreto = secreto

    def __repr__(self) -> str:
        return "Firmante(<oculto>)"

    def cabeceras(
        self, cuerpo: bytes, *, momento: int | None = None, falso: bool = False
    ) -> dict[str, str]:
        secreto = f"{self._secreto}-falso" if falso else self._secreto
        return firmar_webhook(secreto, cuerpo, momento)


@pytest.fixture
def mcp(mcp: Mcp) -> McpOculto:
    return McpOculto(mcp)


@pytest.fixture
def webhook_secreto(webhook_secreto: str) -> Firmante:
    return Firmante(webhook_secreto)


@pytest.fixture
def agente(salud: dict[str, Any]) -> None:
    modo = (salud.get("agent") or {}).get("mode_expected")
    if modo != "tools":
        pytest.skip(f"el agente corre en modo {modo!r}, no 'tools': sin flujos ni memoria")


class CanalWhatsApp:
    """Un chat de WhatsApp por MCP: ``nueva=True`` en el primer mensaje de cada intento."""

    nombre = "whatsapp"

    def __init__(self, mcp: McpOculto, alias: str, config: Config) -> None:
        self.mcp = mcp
        self.alias = alias
        self.config = config
        self.nueva = True

    def reiniciar(self) -> None:
        self.nueva = True

    def enviar(self, mensaje: str) -> Respuesta:
        texto = self.mcp.consultar(mensaje, self.alias, nueva=self.nueva)
        self.nueva = False
        assert texto, f"«{mensaje}»: el MCP respondió vacío"
        return Respuesta(texto=texto, awaiting=inferir_awaiting(texto), crudo=texto)

    def tras_registro(self, respuesta: Respuesta, folio: str) -> None:
        texto = respuesta.texto
        enlace = ADJUNTO_PDF.search(texto) or ENLACE_PDF.search(texto)
        if enlace is None:
            assert not self.config.prod, f"producción no entregó el PDF del folio {folio}"
            # Sin PUBLIC_BASE_URL/MEDIA_BASE_URL en el MCP local: no hay enlace que probar,
            # pero tampoco debe prometerse un PDF que no llega.
            assert "Te envío el PDF" not in texto, texto
            print(f"AVISO whatsapp: el MCP local no tiene enlace de PDF (folio {folio})")
            return
        url = enlace.group(1)
        assert folio in url, f"el enlace del PDF no es del folio {folio}"
        _comprobar_pdf(self.config, url)


def _comprobar_pdf(config: Config, url: str) -> None:
    """GET del PDF firmado: por la app del ensayo (misma ruta) y, si no, la URL tal cual."""

    partes = urlparse(url)
    ruta = partes.path + (f"?{partes.query}" if partes.query else "")
    candidatas = [f"{config.base_url}{ruta}", url]
    ultimo: httpx.Response | None = None
    for candidata in dict.fromkeys(candidatas):
        try:
            ultimo = httpx.get(candidata, timeout=30, follow_redirects=True)
        except httpx.HTTPError:
            continue
        if ultimo.status_code == 200:
            break
    assert ultimo is not None, "el enlace del PDF no respondió"
    assert ultimo.status_code == 200, f"PDF {ultimo.status_code} en {ruta.split('?')[0]}"
    assert ultimo.headers.get("content-type", "").startswith("application/pdf")
    assert ultimo.content.startswith(b"%PDF"), "la respuesta no es un PDF"


# --- MCP: gratis ------------------------------------------------------------------------


def test_mcp_expone_solo_consultar_carvajal(mcp: McpOculto) -> None:
    # Act
    herramientas = mcp.herramientas()

    # Assert
    assert [herramienta.name for herramienta in herramientas] == ["consultar_carvajal"]
    esquema = herramientas[0].input_schema
    assert set(esquema.get("required", [])) == {"mensaje", "conversacion"}
    assert {"mensaje", "conversacion", "nueva", "voz", "foto"} <= set(esquema["properties"])


def test_mcp_rechaza_llamadas_sin_token(mcp: McpOculto) -> None:
    # Arrange
    cuerpo = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    cabeceras = {"Accept": "application/json, text/event-stream"}

    # Act
    respuesta = httpx.post(mcp.url, json=cuerpo, headers=cabeceras, timeout=15)
    con_token_falso = httpx.post(
        mcp.url,
        json=cuerpo,
        headers={**cabeceras, "Authorization": "Bearer no-es-el-token"},
        timeout=15,
    )

    # Assert
    assert respuesta.status_code == 401
    assert con_token_falso.status_code == 401
    assert respuesta.json() == {"detail": "No autorizado."}


def test_foto_inexistente_se_rechaza_sin_gasto_en_whatsapp(
    mcp: McpOculto, conversacion: str
) -> None:
    # Act: un adjunto que no existe no llega a Jev ni consume cupo.
    texto = mcp.consultar("", conversacion, foto="media://inbound/e2e-no-existe.jpg")

    # Assert
    assert FOTO_NO_DISPONIBLE not in texto, (
        "fotos de WhatsApp apagadas en el MCP (falta la carpeta de medios, TYPESAFE_API_KEY "
        "o FOOD_PHOTO_ENABLED)"
    )
    assert FOTO_RECHAZADA in texto, texto


# --- Webhook: gratis --------------------------------------------------------------------


def _cuerpo(**campos: Any) -> bytes:
    return json.dumps(campos, ensure_ascii=False).encode()


def _digitos(numero: str) -> str:
    return re.sub(r"\D", "", numero)


def _allowlist_del_servidor(config: Config) -> list[str] | None:
    """``WEBHOOK_ALLOWED_NUMBERS`` del servidor (``None`` si no se puede leer). No se imprime."""

    if config.prod:
        if not config.app_ssh:
            return None
        valor = Ssh(config.app_ssh, config.app_ssh_port).secreto(
            f"{config.app_dir}/.env", "WEBHOOK_ALLOWED_NUMBERS"
        )
    else:
        archivo = RAIZ / ".env"
        if not archivo.exists():
            return None
        valor = next(
            (
                linea.split("=", 1)[1].strip().strip("'\"")
                for linea in archivo.read_text().splitlines()
                if linea.startswith("WEBHOOK_ALLOWED_NUMBERS=")
            ),
            "",
        )
    if valor.startswith("["):
        return [str(entrada).strip() for entrada in json.loads(valor)]
    return [entrada.strip() for entrada in valor.split(",") if entrada.strip()]


def test_webhook_rechaza_un_numero_fuera_de_la_allowlist(
    config: Config, salud: dict[str, Any], webhook_secreto: Firmante
) -> None:
    # Arrange
    lista = _allowlist_del_servidor(config)
    if lista is not None and "*" in lista:
        if not config.prod:
            pytest.skip("la allowlist local es «*» (abierta); en producción sería un fallo")
        pytest.fail("allowlist abierta: WEBHOOK_ALLOWED_NUMBERS=* atiende a cualquier número")
    if lista is not None:
        assert _digitos(config.wa_fuera) not in {_digitos(numero) for numero in lista}, (
            "ENSAYO_WA_FUERA está en la allowlist del servidor: elige otro número de prueba"
        )
    # Texto vacío: aunque la lista estuviera abierta, no gastaría LLM ni cupo.
    cuerpo = _cuerpo(message_id=f"e2e-{config.run_id}-fuera", text="", **{"from": config.wa_fuera})

    # Act
    respuesta = httpx.post(
        f"{config.base_url}{RUTA_WEBHOOK}",
        content=cuerpo,
        headers=webhook_secreto.cabeceras(cuerpo),
        timeout=30,
    )

    # Assert
    assert respuesta.status_code != 200, "allowlist abierta: atendió a un número fuera de ella"
    assert respuesta.status_code == 403, _sin_telefonos(respuesta.text)
    assert respuesta.json() == {"detail": "Remitente no autorizado."}
    assert _digitos(config.wa_fuera) not in respuesta.text


@pytest.mark.parametrize(
    ("caso", "esperado"),
    [
        ("firma_invalida", 401),
        ("sello_vencido", 401),
        ("remitente_invalido", 422),
        ("cuerpo_mayor_a_16_kib", 413),
    ],
)
def test_webhook_rechaza_peticiones_invalidas(
    config: Config, salud: dict[str, Any], webhook_secreto: Firmante, caso: str, esperado: int
) -> None:
    # Arrange
    remitente = "no-es-un-numero" if caso == "remitente_invalido" else config.wa_fuera
    texto = "x" * 17 * 1024 if caso == "cuerpo_mayor_a_16_kib" else ""
    cuerpo = _cuerpo(message_id=f"e2e-{config.run_id}-{caso}", text=texto, **{"from": remitente})
    momento = int(time.time()) - 600 if caso == "sello_vencido" else None

    # Act
    respuesta = httpx.post(
        f"{config.base_url}{RUTA_WEBHOOK}",
        content=cuerpo,
        headers=webhook_secreto.cabeceras(cuerpo, momento=momento, falso=caso == "firma_invalida"),
        timeout=30,
    )

    # Assert
    assert respuesta.status_code == esperado, _sin_telefonos(respuesta.text[:300])


# --- Memoria entre conversaciones -------------------------------------------------------


def _recuerda(texto: str) -> bool:
    return NOMBRE in texto and EMPRESA in texto


@pytest.mark.gasto
@pytest.mark.usefixtures("agente")
def test_memoria_entre_dos_conversaciones_whatsapp(mcp: McpOculto, conversacion: str) -> None:
    # Arrange: memoria vacía y otro chat que no debe ver nada.
    otro = f"{conversacion}-otro"
    olvido = mcp.consultar(OLVIDAR, conversacion, nueva=True)
    assert OLVIDO.search(olvido), olvido
    try:
        # Act: conversación A se presenta; conversación B (nueva=True) pregunta.
        def presentarse_y_preguntar() -> str:
            mcp.consultar(PRESENTACION, conversacion, nueva=True)
            return mcp.consultar(PREGUNTA_MEMORIA, conversacion, nueva=True)

        recuerdo, _ = con_reintento(
            presentarse_y_preguntar,
            _recuerda,
            aviso="la conversación B de WhatsApp no nombró a Ana y su panadería",
        )
        aislado = mcp.consultar(PREGUNTA_MEMORIA, otro, nueva=True)

        # Assert
        assert NOMBRE in recuerdo, recuerdo
        assert EMPRESA in recuerdo, recuerdo
        assert "Ana Prueba" not in aislado, aislado
        assert EMPRESA not in aislado, aislado
    finally:
        cierre = mcp.consultar(OLVIDAR, conversacion, nueva=True)
    assert OLVIDO.search(cierre), cierre


@pytest.mark.gasto
@pytest.mark.usefixtures("agente")
def test_memoria_web_no_llega_a_whatsapp(
    config: Config, mcp: McpOculto, conversacion: str, web: Web
) -> None:
    # Arrange: un visitante web guarda su nombre y empresa.
    web.turno(OLVIDAR, web.nuevo_hilo())
    try:
        presentacion, _ = con_reintento(
            lambda: web.turno(PRESENTACION, web.nuevo_hilo()),
            lambda turno: int(turno.traza.get("memory") or 0) >= 2,
            aviso="la presentación web no guardó nombre y empresa (trace.memory < 2)",
        )
        assert int(presentacion.traza.get("memory") or 0) >= 2, presentacion.resumen()

        # Act: un chat de WhatsApp nuevo pregunta qué sabe de él.
        texto = mcp.consultar(PREGUNTA_MEMORIA, conversacion, nueva=True)

        # Assert
        assert "Ana Prueba" not in texto, texto
        assert EMPRESA not in texto, texto
    finally:
        web.turno(OLVIDAR, web.nuevo_hilo())


# --- Flujos definidos -------------------------------------------------------------------


@pytest.mark.gasto
@pytest.mark.usefixtures("agente")
@pytest.mark.parametrize("guion", GUIONES, ids=lambda guion: guion.nombre)
def test_flujo_definido_en_whatsapp(
    config: Config, mcp: McpOculto, conversacion: str, guion: Guion
) -> None:
    # Arrange
    canal = CanalWhatsApp(mcp, conversacion, config)
    registrar = registra(config.cotizar, guion.designado, pedido=config.pedido)

    # Act
    transcripcion = ejecutar(canal, guion, registrar=registrar)

    # Assert: cada turno ya se comprobó en ``ejecutar``.
    assert transcripcion.turnos, f"{guion.nombre}: no se envió ningún mensaje"
    print(transcripcion.texto())


@pytest.mark.gasto
@pytest.mark.usefixtures("agente", "pedido_permitido")
def test_pedido_en_odoo_desde_whatsapp(config: Config, mcp: McpOculto, conversacion: str) -> None:
    # Arrange: comprador ficticio del run; el pedido queda en Odoo (docs/ensayo.md).
    canal = CanalWhatsApp(mcp, conversacion, config)
    nombre = nombre_de_prueba(config.run_id)
    correo = correo_de_prueba(f"{config.run_id}-whatsapp")

    # Act
    try:
        pedido = ejecutar_pedido(canal, nombre=nombre, correo=correo)
    except PedidosApagados as motivo:
        pytest.skip(str(motivo))

    # Assert: cada turno ya se comprobó; WhatsApp recibe la negrita con un solo «*».
    _, creado = pedido.transcripcion.turnos[-2]
    assert f"*Pedido {pedido.numero} confirmado*" in creado.texto, creado.texto
    assert "**" not in creado.texto, "WhatsApp recibió negrita en Markdown («**»)"
    reintento = " · tras reintentar (Odoo falló al primer intento)" if pedido.reintentado else ""
    print(f"PEDIDO whatsapp: {pedido.numero} · folio #{pedido.folio} · {correo}{reintento}")
    print(pedido.transcripcion.texto())


# --- Conversación que empieza con una foto ----------------------------------------------


@contextmanager
def _foto_en_openclaw(config: Config, contenido: bytes, nombre: str) -> Generator[str]:
    """Deja la foto donde OpenClaw guarda los adjuntos y la borra al terminar.

    Local: ``ENSAYO_MEDIA_DIR`` (montada en el servicio ``mcp``). Producción: la carpeta
    ``~/.openclaw/media/inbound`` del usuario del gateway, que llega al servidor por sshfs.
    """

    if not config.prod:
        carpeta = (RAIZ / config.media_dir).resolve()
        if not carpeta.is_dir():
            pytest.skip(f"no existe la carpeta de medios local {config.media_dir}")
        archivo = carpeta / nombre
        archivo.write_bytes(contenido)
        try:
            yield f"media://inbound/{nombre}"
        finally:
            archivo.unlink(missing_ok=True)
        return
    if not config.openclaw_ssh:
        pytest.skip("sin ENSAYO_OPENCLAW_SSH: no se puede dejar la foto en OpenClaw")
    ssh = Ssh(config.openclaw_ssh)
    usuario = shlex.quote(config.openclaw_user)
    if ssh.como(config.openclaw_user, "ls -d ~/.openclaw/media/inbound").returncode != 0:
        pytest.skip("no existe ~/.openclaw/media/inbound en la VM de OpenClaw")
    local = RAIZ / "tests" / "e2e" / "fixtures" / "ajiaco.jpg"
    temporal = f"/tmp/{nombre}"
    destino = f"~{config.openclaw_user}/.openclaw/media/inbound/{nombre}"
    copia = subprocess.run(
        ["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", str(local),
         f"{config.openclaw_ssh}:{temporal}"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )  # fmt: skip
    assert copia.returncode == 0, "no se pudo copiar la foto a la VM de OpenClaw"
    try:
        instalada = ssh.run(f"sudo -n -u {usuario} install -m 644 {temporal} {destino}")
        assert instalada.returncode == 0, "no se pudo dejar la foto en la carpeta de OpenClaw"
        yield f"media://inbound/{nombre}"
    finally:
        ssh.run(f"sudo -n -u {usuario} rm -f {destino}; rm -f {temporal}")


def _primera_foto(mcp: McpOculto, alias: str, referencia: str, prod: bool) -> str:
    """Primer mensaje con foto; en producción espera a que sshfs vea el archivo."""

    texto = mcp.consultar("", alias, nueva=True, foto=referencia)
    for _ in range(4 if prod else 0):
        if FOTO_RECHAZADA not in texto:
            break
        time.sleep(3)
        texto = mcp.consultar("", alias, nueva=True, foto=referencia)
    return texto


@pytest.mark.gasto
@pytest.mark.usefixtures("agente")
def test_conversacion_que_empieza_con_foto_en_whatsapp(
    config: Config, mcp: McpOculto, conversacion: str, salud: dict[str, Any], foto_ajiaco: bytes
) -> None:
    # Arrange
    estado = (salud.get("food_photo") or {}).get("status")
    if estado != "ok":
        pytest.skip(f"fotos de alimentos no disponibles en la app (food_photo={estado!r})")
    canal = CanalWhatsApp(mcp, conversacion, config)
    registrar = registra(config.cotizar, designado=False)
    intentos: list[str] = []

    with _foto_en_openclaw(config, foto_ajiaco, f"e2e-{config.run_id}.jpg") as referencia:
        for _ in range(2):
            transcripcion = Transcripcion("whatsapp")
            # Act: la foto abre la conversación (nueva=True).
            texto = _primera_foto(mcp, conversacion, referencia, config.prod)
            canal.nueva = False

            # Assert
            assert FOTO_NO_DISPONIBLE not in texto, "fotos de WhatsApp apagadas en el MCP"
            assert FOTO_RECHAZADA not in texto, "el MCP no encontró la foto en su carpeta"
            if not re.search(r"[Rr]ecomend", texto) or "*Alternativas:*" not in texto:
                intentos.append(f"Jev no reconoció el ajiaco:\n{texto[:800]}")
                continue
            assert any(pista in texto for pista in PISTA_FOTO), texto
            assert "**" not in texto, "WhatsApp recibió negrita en Markdown («**»)"
            try:
                ejecutar_pasos(canal, pasos_tras_foto(registrar), transcripcion)
            except DesvioDelAgente as desvio:
                intentos.append(str(desvio))
                continue
            print(transcripcion.texto())
            return
    pytest.fail("la conversación con foto se desvió dos veces:\n\n" + "\n\n".join(intentos))


_CITA = re.compile(r"\[(\d{1,3})\]")


@pytest.mark.gasto
@pytest.mark.usefixtures("agente")
@pytest.mark.parametrize(
    "pregunta",
    ["¿Dónde puedo comprar empaques en Pereira?", "¿Cuándo se fundó Carvajal y quién la fundó?"],
    ids=["sedes", "historia"],
)
def test_cada_cita_tiene_su_fuente_en_whatsapp(
    mcp: McpOculto, conversacion: str, pregunta: str
) -> None:
    # Act
    respuesta = mcp.consultar(pregunta, conversacion, nueva=True)

    # Assert: las [n] del texto y las de «Fuentes» son las mismas (el ensayo encontró [7]
    # y [8] citadas con una lista de [1] a [5], y la [2] de la segunda sede sin fuente).
    cuerpo, _, fuentes = respuesta.partition("*Fuentes:*")
    citadas = set(_CITA.findall(cuerpo))
    listadas = set(_CITA.findall(fuentes))
    assert citadas <= listadas, f"citas sin fuente {citadas - listadas}:\n{respuesta}"
    if citadas:
        assert listadas <= citadas, f"fuentes no citadas {listadas - citadas}:\n{respuesta}"


@pytest.mark.gasto
@pytest.mark.usefixtures("agente")
def test_una_peticion_ajena_a_carvajal_no_se_atiende_en_whatsapp(
    mcp: McpOculto, conversacion: str
) -> None:
    # Act
    respuesta = mcp.consultar("¿Me ayudas con mi tarea de cálculo?", conversacion, nueva=True)

    # Assert: el atajo directo respondía «Sí, te ayudo…».
    assert not re.search(r"\bs[ií],?\s+te\s+ayudo\b", respuesta, re.IGNORECASE), respuesta
    assert "Carvajal" in respuesta, respuesta
