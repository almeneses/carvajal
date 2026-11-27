"""El pedido del ensayo en Odoo y su correo de confirmación (spec 056, SC-001), por SSH.

Tras crear el pedido, el ensayo pregunta a Odoo, **solo con lecturas**, desde el contenedor
``app`` del servidor (que ya tiene ``ODOO_*`` en su entorno): el ``sale.order`` del folio
(``client_order_ref``), sus ``mail.mail`` y los mensajes del chatter con adjunto. Exige que
haya un solo pedido, confirmado (``state=sale``), y que ningún correo quedó en excepción
(SMTP rechazado). La clave de Odoo nunca sale del contenedor: el script solo imprime una
línea ``RESUMEN_ODOO {json}`` con nombres, estados y conteos (sin correos ni direcciones).

Solo corre contra producción con ``ENSAYO_APP_SSH``; si no, avisa y deja la revisión manual
de docs/ensayo.md («Correo de confirmación»).
"""

from __future__ import annotations

import json
import shlex
import time
from collections.abc import Callable, Mapping
from typing import Any

from tests.e2e.clientes import Config, Ssh

PREFIJO = "RESUMEN_ODOO "
SOLO_FOLIO = frozenset("0123456789abcdef")
# Segundos entre lecturas mientras un correo sigue en la cola (``outgoing``).
PAUSA_COLA = 10.0
INTENTOS_COLA = 3

# Corre en el contenedor ``app`` con ``python -c``; ``sys.argv[1]`` es el folio. Solo
# ``search_read``: nunca escribe ni envía nada.
SCRIPT = """
import json
import sys

from carvajal.adapters.crm.odoo import OdooClient, OdooError
from carvajal.infrastructure.settings import Settings

folio = sys.argv[1]
settings = Settings()
resumen = {"configurado": bool(settings.odoo_configured)}
if settings.odoo_configured:
    cliente = OdooClient(
        settings.odoo_url,
        settings.odoo_db,
        settings.odoo_api_key.get_secret_value(),
        timeout=20,
    )

    def leer(modelo, dominio, campos):
        try:
            return cliente.search_read(modelo, dominio, campos)
        except OdooError as error:
            return {"error": error.status}

    try:
        pedidos = leer("sale.order", [["client_order_ref", "=", folio]], ["id", "name", "state"])
        if isinstance(pedidos, dict):
            resumen["pedidos"] = pedidos
        else:
            resumen["pedidos"] = [
                {"name": fila.get("name"), "state": fila.get("state")} for fila in pedidos
            ]
        if isinstance(pedidos, list) and pedidos:
            registro = [["model", "=", "sale.order"], ["res_id", "=", pedidos[0]["id"]]]
            correos = leer("mail.mail", registro, ["state"])
            resumen["correos"] = (
                correos if isinstance(correos, dict) else [fila.get("state") for fila in correos]
            )
            adjuntos = leer("mail.message", [*registro, ["attachment_ids", "!=", False]], ["id"])
            resumen["mensajes_con_adjunto"] = (
                adjuntos if isinstance(adjuntos, dict) else len(adjuntos)
            )
    finally:
        cliente.close()
print("RESUMEN_ODOO " + json.dumps(resumen))
"""


def leer_resumen(salida: str) -> dict[str, Any] | None:
    """La última línea ``RESUMEN_ODOO {json}`` de la salida, o ``None`` si no hay."""

    for linea in reversed(salida.splitlines()):
        if linea.startswith(PREFIJO):
            try:
                datos = json.loads(linea.removeprefix(PREFIJO))
            except ValueError:
                return None
            return datos if isinstance(datos, dict) else None
    return None


def veredicto(resumen: Mapping[str, Any] | None, folio: str) -> tuple[list[str], list[str]]:
    """``(fallos, avisos)`` del pedido del folio en Odoo y de su correo de confirmación."""

    if resumen is None:
        return [f"no se pudo leer Odoo desde el contenedor app (folio #{folio})"], []
    if not resumen.get("configurado"):
        return ["la app del servidor no tiene ODOO_URL/ODOO_DB/ODOO_API_KEY"], []
    pedidos = resumen.get("pedidos")
    if isinstance(pedidos, Mapping):
        return [f"Odoo rechazó la lectura de sale.order (status {pedidos.get('error')})"], []
    if not pedidos:
        return [f"Odoo no tiene ningún sale.order con client_order_ref={folio}"], []
    fallos: list[str] = []
    avisos: list[str] = []
    if len(pedidos) > 1:
        nombres = ", ".join(str(fila.get("name")) for fila in pedidos)
        fallos.append(f"el folio #{folio} tiene {len(pedidos)} pedidos en Odoo ({nombres})")
    estado = pedidos[0].get("state")
    if estado != "sale":
        fallos.append(f"el pedido {pedidos[0].get('name')} quedó en state={estado!r}, no 'sale'")
    correos = resumen.get("correos")
    if isinstance(correos, Mapping):
        avisos.append(
            f"la clave del bot no puede leer mail.mail (status {correos.get('error')}): "
            "revisa a mano Ajustes → Técnico → Correos"
        )
    elif correos is not None:
        if "exception" in correos:
            fallos.append(
                "el correo de confirmación quedó en excepción (SMTP): Ajustes → Técnico → "
                "Correos, filtro «Excepción», y el chatter del pedido"
            )
        if "outgoing" in correos:
            avisos.append("el correo de confirmación sigue en la cola de Odoo (outgoing)")
    adjuntos = resumen.get("mensajes_con_adjunto")
    if isinstance(adjuntos, Mapping):
        avisos.append(f"no se pudo leer el chatter del pedido (status {adjuntos.get('error')})")
    elif adjuntos == 0:
        avisos.append("el chatter del pedido no tiene ningún mensaje con el PDF adjunto")
    return fallos, avisos


def leer_pedido(ssh: Ssh, app_dir: str, folio: str) -> dict[str, Any] | None:
    """Corre :data:`SCRIPT` en el contenedor ``app`` del servidor (solo lectura)."""

    if not folio or set(folio) - SOLO_FOLIO:
        raise ValueError(f"folio inválido: {folio!r}")
    comando = (
        f"cd {shlex.quote(app_dir)} && docker compose -f compose.prod.yml exec -T app "
        f"python -c {shlex.quote(SCRIPT)} {shlex.quote(folio)}"
    )
    proceso = ssh.run(comando, timeout=90)
    return leer_resumen(proceso.stdout)


def comprobar_correo_del_pedido(
    config: Config,
    folio: str,
    canal: str,
    *,
    leer: Callable[[str], dict[str, Any] | None] | None = None,
    pausa: float = PAUSA_COLA,
) -> None:
    """Falla si el pedido del folio no está confirmado en Odoo o su correo quedó en excepción.

    Sin ``ENSAYO_APP_SSH`` o fuera de producción solo avisa (la revisión queda manual). Un
    correo todavía en la cola se vuelve a leer hasta :data:`INTENTOS_COLA` veces.
    """

    if leer is None:
        if not (config.prod and config.app_ssh):
            print(
                f"AVISO {canal}: sin ENSAYO_APP_SSH en producción no se revisa el correo del "
                f"pedido del folio #{folio} en Odoo; revísalo a mano (docs/ensayo.md)"
            )
            return
        ssh = Ssh(config.app_ssh, config.app_ssh_port)

        def por_ssh(numero: str) -> dict[str, Any] | None:
            return leer_pedido(ssh, config.app_dir, numero)

        leer = por_ssh
    resumen = leer(folio)
    for _ in range(INTENTOS_COLA - 1):
        if "outgoing" not in ((resumen or {}).get("correos") or ()):
            break
        if pausa > 0:
            time.sleep(pausa)
        resumen = leer(folio)
    fallos, avisos = veredicto(resumen, folio)
    for aviso in avisos:
        print(f"AVISO {canal}: {aviso} (folio #{folio})")
    assert not fallos, f"[{canal}] pedido del folio #{folio} en Odoo: " + "; ".join(fallos)
