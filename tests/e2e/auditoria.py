"""Comparación del ``openclaw security audit --json`` y de la configuración contra la base.

Funciones puras, sin E/S: el ensayo (``test_operacion.py``) las alimenta con lo que lee por
SSH y ``tests/test_ensayo_auditoria.py`` las prueba con datos grabados en la CI normal.

La base versionada es ``openclaw/auditoria-base.json``. Reglas de ``comparar``:

- **fallo**: un hallazgo crítico que no está entre los críticos de la base, o más críticos
  en el resumen que en la base;
- **aviso**: un hallazgo ``warn`` que la base no tenía (se registra, no rompe el ensayo);
- **info**: hallazgos de la base que desaparecieron o críticos que bajaron (conviene
  actualizar la base), y hallazgos ``info`` nuevos.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

CRITICO = "critical"
AVISO = "warn"
INFO = "info"
SEVERIDADES = (CRITICO, AVISO, INFO)

# Nombres que distintas versiones (o herramientas) usan para la misma severidad.
_ALIAS_SEVERIDAD = {
    "critical": CRITICO,
    "crit": CRITICO,
    "critico": CRITICO,
    "crítico": CRITICO,
    "high": CRITICO,
    "error": CRITICO,
    "warn": AVISO,
    "warning": AVISO,
    "warnings": AVISO,
    "medium": AVISO,
    "aviso": AVISO,
    "info": INFO,
    "information": INFO,
    "low": INFO,
    "note": INFO,
}

# Números de teléfono (E.164 o con espacios) y JID de WhatsApp: nunca en los mensajes.
_TELEFONO = re.compile(r"\+?\d[\d ]{7,}\d")
_JID = re.compile(r"[\w.+-]+@(?:g\.us|s\.whatsapp\.net|lid)\b")


class AuditoriaInvalida(ValueError):
    """La salida del audit no tiene la forma esperada."""


@dataclass(frozen=True)
class Hallazgo:
    check_id: str
    severidad: str


@dataclass(frozen=True)
class Auditoria:
    """Resultado normalizado de un audit: resumen por severidad y hallazgos."""

    resumen: dict[str, int]
    hallazgos: tuple[Hallazgo, ...]

    def ids(self, severidad: str) -> set[str]:
        return {h.check_id for h in self.hallazgos if h.severidad == severidad}

    def todos(self) -> set[str]:
        return {h.check_id for h in self.hallazgos}


@dataclass
class Resultado:
    fallos: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.fallos


def severidad(valor: Any) -> str:
    """Severidad normalizada (``critical``, ``warn`` o ``info``)."""

    nombre = str(valor or "").strip().lower()
    if nombre not in _ALIAS_SEVERIDAD:
        raise AuditoriaInvalida(f"severidad desconocida: {valor!r}")
    return _ALIAS_SEVERIDAD[nombre]


def parsear(datos: Any) -> Auditoria:
    """Normaliza un audit (o la base): acepta ``checkId`` o ``id`` y alias de severidad.

    Si falta el resumen, se cuenta a partir de los hallazgos.
    """

    if not isinstance(datos, Mapping):
        raise AuditoriaInvalida("el audit no es un objeto JSON")
    crudos = datos.get("findings")
    if not isinstance(crudos, list):
        raise AuditoriaInvalida("el audit no trae la lista 'findings'")
    hallazgos: list[Hallazgo] = []
    for crudo in crudos:
        if not isinstance(crudo, Mapping):
            raise AuditoriaInvalida("un hallazgo no es un objeto")
        check_id = crudo.get("checkId") or crudo.get("id")
        if not isinstance(check_id, str) or not check_id.strip():
            raise AuditoriaInvalida("un hallazgo no trae 'checkId' ni 'id'")
        hallazgos.append(Hallazgo(check_id.strip(), severidad(crudo.get("severity"))))
    resumen = dict.fromkeys(SEVERIDADES, 0)
    resumen_crudo = datos.get("summary")
    if isinstance(resumen_crudo, Mapping) and resumen_crudo:
        for clave, valor in resumen_crudo.items():
            try:
                nombre = severidad(clave)
            except AuditoriaInvalida:
                continue
            if not isinstance(valor, int) or isinstance(valor, bool):
                raise AuditoriaInvalida(f"conteo no entero en summary.{clave}: {valor!r}")
            resumen[nombre] = valor
    else:
        for hallazgo in hallazgos:
            resumen[hallazgo.severidad] += 1
    return Auditoria(resumen, tuple(hallazgos))


def extraer_json(texto: str) -> Any:
    """Primer objeto JSON de una salida que puede traer avisos antes o después."""

    inicio = texto.find("{")
    if inicio < 0:
        raise AuditoriaInvalida("la salida no contiene JSON")
    try:
        valor, _ = json.JSONDecoder().raw_decode(texto[inicio:])
    except json.JSONDecodeError as error:
        raise AuditoriaInvalida(f"JSON ilegible: {error.msg}") from error
    return valor


def comparar(base: Any, actual: Any) -> Resultado:
    """Compara el audit en vivo con la base (ver reglas en el docstring del módulo)."""

    esperado, vivo = parsear(base), parsear(actual)
    resultado = Resultado()
    criticos_base = esperado.ids(CRITICO)
    for nuevo in sorted(vivo.ids(CRITICO) - criticos_base):
        resultado.fallos.append(f"hallazgo crítico nuevo: {nuevo}")
    antes, ahora = esperado.resumen[CRITICO], vivo.resumen[CRITICO]
    if ahora > antes:
        resultado.fallos.append(f"los críticos subieron de {antes} a {ahora}")
    elif ahora < antes:
        resultado.info.append(f"los críticos bajaron de {antes} a {ahora}: actualiza la base")
    conocidos = esperado.todos()
    for nuevo in sorted(vivo.ids(AVISO) - conocidos):
        resultado.avisos.append(f"aviso nuevo: {nuevo}")
    for nuevo in sorted(vivo.ids(INFO) - conocidos):
        resultado.info.append(f"hallazgo informativo nuevo: {nuevo}")
    for ido in sorted(conocidos - vivo.todos()):
        resultado.info.append(f"desapareció de la auditoría: {ido} (actualiza la base)")
    return resultado


# --- Configuración (``openclaw config get``) ---------------------------------------------

SEPARADOR = "@@ruta "


def leer_valores(texto: str) -> dict[str, Any]:
    """Valores por ruta de la salida de varios ``config get`` separados por ``@@ruta <p>``.

    Cada bloque se interpreta como JSON; si no lo es (``config get`` imprime las cadenas sin
    comillas) se toma el texto. Una ruta sin definir queda como ``None``.
    """

    valores: dict[str, Any] = {}
    ruta, lineas = "", []

    def cerrar() -> None:
        if not ruta:
            return
        cuerpo = "\n".join(lineas).strip()
        if not cuerpo or cuerpo.startswith("Unknown config path"):
            valores[ruta] = None
            return
        try:
            valores[ruta] = json.loads(cuerpo)
        except json.JSONDecodeError:
            valores[ruta] = cuerpo

    for linea in texto.splitlines():
        if linea.startswith(SEPARADOR):
            cerrar()
            ruta, lineas = linea.removeprefix(SEPARADOR).strip(), []
        elif ruta:
            lineas.append(linea)
    cerrar()
    return valores


def rutas_de_config(base_config: Mapping[str, Any]) -> list[str]:
    """Rutas que hay que leer para comprobar ``base_config`` (sección ``config``)."""

    rutas = list(base_config.get("valores", {}))
    for clave in ("deny_minimo", "plugins_permitidos", "allow_from", "bindings"):
        seccion = base_config.get(clave)
        if isinstance(seccion, Mapping) and "ruta" in seccion:
            rutas.append(str(seccion["ruta"]))
    rutas.extend(str(ruta) for ruta in base_config.get("informativo", []))
    return list(dict.fromkeys(rutas))


def desviaciones_config(base_config: Mapping[str, Any], leidos: Mapping[str, Any]) -> list[str]:
    """Diferencias entre la configuración en vivo y la base; nunca incluyen números ni JID."""

    problemas: list[str] = []
    for ruta, esperado in base_config.get("valores", {}).items():
        valor = leidos.get(ruta)
        if valor != esperado:
            problemas.append(f"{ruta}: se esperaba {esperado!r} y está {redactar(repr(valor))}")

    deny = base_config.get("deny_minimo")
    if isinstance(deny, Mapping):
        vivo = leidos.get(deny["ruta"])
        if not isinstance(vivo, list):
            problemas.append(f"{deny['ruta']}: no es una lista ({type(vivo).__name__})")
        else:
            faltan = sorted(set(deny["contiene"]) - {str(v) for v in vivo})
            if faltan:
                problemas.append(f"{deny['ruta']}: faltan en la lista de denegadas {faltan}")

    plugins = base_config.get("plugins_permitidos")
    if isinstance(plugins, Mapping):
        vivo = leidos.get(plugins["ruta"])
        if not isinstance(vivo, list):
            problemas.append(f"{plugins['ruta']}: no es una lista")
        else:
            nuevos = sorted({str(v) for v in vivo} - set(plugins["maximo"]))
            if nuevos:
                problemas.append(
                    f"{plugins['ruta']}: plugins fuera de la base {nuevos} "
                    "(si es intencional, actualiza openclaw/auditoria-base.json)"
                )

    allow = base_config.get("allow_from")
    if isinstance(allow, Mapping):
        vivo = leidos.get(allow["ruta"])
        minimo = int(allow.get("minimo", 1))
        if not isinstance(vivo, list) or len(vivo) < minimo:
            cuantos = len(vivo) if isinstance(vivo, list) else "no es lista"
            problemas.append(f"{allow['ruta']}: se esperaban ≥{minimo} números ({cuantos})")
        elif "maximo" in allow and len(vivo) > int(allow["maximo"]):
            problemas.append(
                f"{allow['ruta']}: {len(vivo)} números, más que los {allow['maximo']} de la base"
            )

    enlaces = base_config.get("bindings")
    if isinstance(enlaces, Mapping):
        problemas.extend(_desviaciones_bindings(enlaces, leidos.get(enlaces["ruta"])))
    return problemas


def _mapa(valor: Any) -> Mapping[str, Any]:
    return valor if isinstance(valor, Mapping) else {}


def _desviaciones_bindings(regla: Mapping[str, Any], vivo: Any) -> list[str]:
    """Solo mensajes directos (2026-11-27): ``agente`` atiende todo el ``canal``.

    El agente lleva un único enlace de canal, sin chat fijo y con una sesión por número
    (``dm_scope``); cualquier otro agente del canal solo puede estar atado a un chat de tipo
    ``otros.tipo`` con id fijo (el número de la propia cuenta, que sigue con ``main``). Así
    ningún otro agente se queda con los mensajes de los números autorizados.
    """

    ruta, agente, canal = regla["ruta"], regla["agente"], regla["canal"]
    if not isinstance(vivo, list):
        return [f"{ruta}: no es una lista"]
    del_canal = [
        b for b in vivo if isinstance(b, Mapping) and _mapa(b.get("match")).get("channel") == canal
    ]
    propios = [b for b in del_canal if b.get("agentId") == agente]
    otros = [b for b in del_canal if b.get("agentId") != agente]
    if not propios:
        return [f"{ruta}: el agente {agente!r} no está enlazado al canal {canal!r}"]

    problemas: list[str] = []
    if len(propios) > 1:
        problemas.append(f"{ruta}: {agente!r} tiene {len(propios)} enlaces en {canal!r} (uno solo)")
    for indice, enlace in enumerate(propios):
        if _mapa(enlace.get("match")).get("peer"):
            problemas.append(
                f"{ruta}: el enlace #{indice} de {agente!r} está atado a un chat; "
                f"debe cubrir todo el canal {canal!r}"
            )
        alcance = _mapa(enlace.get("session")).get("dmScope")
        if alcance != regla["dm_scope"]:
            problemas.append(
                f"{ruta}: el enlace #{indice} de {agente!r} tiene dmScope {alcance!r}; se espera "
                f"{regla['dm_scope']!r} (una sesión por número)"
            )

    excepcion = _mapa(regla.get("otros"))
    for enlace in otros:
        peer = _mapa(_mapa(enlace.get("match")).get("peer"))
        if peer.get("kind") != excepcion.get("tipo") or not peer.get("id"):
            problemas.append(
                f"{ruta}: {enlace.get('agentId')!r} recibe {canal!r}/{peer.get('kind')!r}; "
                f"fuera de {agente!r} solo se permite {excepcion.get('tipo')!r} con id fijo"
            )
    maximo = excepcion.get("maximo")
    if maximo is not None and len(otros) > int(maximo):
        problemas.append(
            f"{ruta}: {len(otros)} enlaces de otros agentes en {canal!r} (máximo {maximo})"
        )
    return problemas


def redactar(texto: str) -> str:
    """Quita números de teléfono y JID de WhatsApp de un texto antes de mostrarlo."""

    return _TELEFONO.sub("<número>", _JID.sub("<jid>", texto))


def lineas_con(texto: str, palabras: Iterable[str]) -> list[str]:
    """Líneas (redactadas) que contienen todas las ``palabras``, sin distinguir mayúsculas."""

    buscadas = [p.lower() for p in palabras]
    return [
        redactar(linea.strip())
        for linea in texto.splitlines()
        if all(p in linea.lower() for p in buscadas)
    ]
