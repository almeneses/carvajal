"""Pruebas del comparador del ensayo general (tests/e2e/auditoria.py), sin red.

Corren en ``make check`` y en la CI: el ensayo en vivo solo aporta la salida del gateway.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from tests.e2e.auditoria import (
    SEPARADOR,
    AuditoriaInvalida,
    comparar,
    desviaciones_config,
    extraer_json,
    leer_valores,
    lineas_con,
    parsear,
    redactar,
    rutas_de_config,
    severidad,
)

BASE = Path(__file__).resolve().parents[1] / "openclaw" / "auditoria-base.json"


def _audit(*hallazgos: tuple[str, str], resumen: dict[str, int] | None = None) -> dict[str, Any]:
    datos: dict[str, Any] = {
        "ts": 1795659058926,
        "findings": [{"checkId": i, "severity": s, "title": "t"} for i, s in hallazgos],
        "secretDiagnostics": [],
    }
    if resumen is not None:
        datos["summary"] = resumen
    else:
        conteo = {"critical": 0, "warn": 0, "info": 0}
        for _, nombre in hallazgos:
            conteo[severidad(nombre)] += 1
        datos["summary"] = conteo
    return datos


@pytest.fixture
def base() -> dict[str, Any]:
    return _audit(("crit.a", "critical"), ("warn.a", "warn"), ("info.a", "info"))


# --- comparar ---------------------------------------------------------------------------


def test_audit_identico_a_la_base_no_reporta_nada(base: dict[str, Any]) -> None:
    # Arrange
    actual = copy.deepcopy(base)

    # Act
    resultado = comparar(base, actual)

    # Assert
    assert resultado.ok
    assert (resultado.fallos, resultado.avisos, resultado.info) == ([], [], [])


def test_critico_nuevo_rompe_el_ensayo(base: dict[str, Any]) -> None:
    # Arrange
    actual = _audit(
        ("crit.a", "critical"), ("crit.b", "critical"), ("warn.a", "warn"), ("info.a", "info")
    )

    # Act
    resultado = comparar(base, actual)

    # Assert
    assert not resultado.ok
    assert "hallazgo crítico nuevo: crit.b" in resultado.fallos
    assert any("subieron de 1 a 2" in fallo for fallo in resultado.fallos)


def test_aviso_que_escala_a_critico_rompe_el_ensayo(base: dict[str, Any]) -> None:
    # Arrange
    actual = _audit(("crit.a", "critical"), ("warn.a", "critical"), ("info.a", "info"))

    # Act
    resultado = comparar(base, actual)

    # Assert
    assert "hallazgo crítico nuevo: warn.a" in resultado.fallos


def test_mas_criticos_en_el_resumen_rompe_aunque_los_ids_sean_conocidos(
    base: dict[str, Any],
) -> None:
    # Arrange: mismo id crítico, pero el resumen cuenta dos (dos instancias del mismo check).
    actual = copy.deepcopy(base)
    actual["summary"]["critical"] = 2

    # Act
    resultado = comparar(base, actual)

    # Assert
    assert resultado.fallos == ["los críticos subieron de 1 a 2"]


def test_aviso_nuevo_se_registra_sin_fallar(base: dict[str, Any]) -> None:
    # Arrange
    actual = _audit(
        ("crit.a", "critical"), ("warn.a", "warn"), ("warn.b", "warning"), ("info.a", "info")
    )

    # Act
    resultado = comparar(base, actual)

    # Assert
    assert resultado.ok
    assert resultado.avisos == ["aviso nuevo: warn.b"]


def test_hallazgo_que_desaparece_queda_como_info(base: dict[str, Any]) -> None:
    # Arrange: se corrigió el crítico (p. ej. groupPolicy=allowlist).
    actual = _audit(("warn.a", "warn"), ("info.a", "info"))

    # Act
    resultado = comparar(base, actual)

    # Assert
    assert resultado.ok
    assert resultado.avisos == []
    assert any("desapareció de la auditoría: crit.a" in linea for linea in resultado.info)
    assert any("bajaron de 1 a 0" in linea for linea in resultado.info)


@pytest.mark.parametrize(
    "malo",
    [
        [],
        {"summary": {"critical": 0}},
        {"findings": "nada"},
        {"findings": [{"severity": "warn"}]},
        {"findings": [{"checkId": "x", "severity": "rarísima"}]},
        {"findings": ["texto"]},
        {"findings": [], "summary": {"critical": "dos"}},
    ],
)
def test_audit_malformado_se_rechaza_con_error_claro(base: dict[str, Any], malo: Any) -> None:
    # Arrange / Act / Assert
    with pytest.raises(AuditoriaInvalida):
        comparar(base, malo)


def test_parser_acepta_id_y_alias_de_severidad_y_cuenta_sin_resumen() -> None:
    # Arrange
    datos = {"findings": [{"id": "x", "severity": "CRITICAL"}, {"id": "y", "severity": "Warning"}]}

    # Act
    auditoria = parsear(datos)

    # Assert
    assert auditoria.ids("critical") == {"x"}
    assert auditoria.ids("warn") == {"y"}
    assert auditoria.resumen == {"critical": 1, "warn": 1, "info": 0}


def test_extraer_json_ignora_ruido_alrededor() -> None:
    # Arrange
    texto = 'Aviso de doctor\n{"findings": [], "summary": {"critical": 0}}\nTip: algo'

    # Act
    datos = extraer_json(texto)

    # Assert
    assert datos == {"findings": [], "summary": {"critical": 0}}


def test_extraer_json_sin_objeto_falla() -> None:
    # Arrange / Act / Assert
    with pytest.raises(AuditoriaInvalida):
        extraer_json("Gateway not reachable")


# --- configuración ------------------------------------------------------------------------


def _config_base() -> dict[str, Any]:
    return json.loads(BASE.read_text(encoding="utf-8"))["config"]


def _config_viva() -> dict[str, Any]:
    config = _config_base()
    leidos: dict[str, Any] = dict(config["valores"])
    leidos[config["deny_minimo"]["ruta"]] = [*config["deny_minimo"]["contiene"], "extra"]
    leidos[config["plugins_permitidos"]["ruta"]] = config["plugins_permitidos"]["maximo"][:3]
    leidos[config["allow_from"]["ruta"]] = ["+15550100000"]
    leidos[config["bindings"]["ruta"]] = [
        {
            "agentId": "main",
            "match": {
                "channel": "whatsapp",
                "accountId": "*",
                "peer": {"kind": "direct", "id": "+15550100001"},
            },
        },
        {
            "agentId": "ventas",
            "match": {"channel": "whatsapp", "accountId": "*"},
            "session": {"dmScope": "per-channel-peer"},
        },
        {"agentId": "main", "match": {"channel": "telegram"}},
    ]
    return leidos


def test_base_versionada_es_coherente() -> None:
    # Arrange
    datos = json.loads(BASE.read_text(encoding="utf-8"))

    # Act
    auditoria = parsear(datos)

    # Assert
    assert auditoria.resumen["critical"] == len(auditoria.ids("critical"))
    assert datos["openclaw_version"]
    assert "agents.entries.ventas.tools.deny" in rutas_de_config(datos["config"])
    assert "channels.whatsapp.groupPolicy" in rutas_de_config(datos["config"])


def test_config_igual_a_la_base_no_tiene_desviaciones() -> None:
    # Arrange
    leidos = _config_viva()

    # Act
    problemas = desviaciones_config(_config_base(), leidos)

    # Assert
    assert problemas == []


def test_config_con_exec_abierto_y_deny_incompleto_se_detecta() -> None:
    # Arrange
    leidos = _config_viva()
    leidos["agents.entries.ventas.tools.exec.mode"] = "ask"
    leidos["agents.entries.ventas.tools.deny"] = ["group:web"]
    leidos["tools.elevated.enabled"] = None  # ruta sin definir

    # Act
    problemas = desviaciones_config(_config_base(), leidos)

    # Assert
    texto = "\n".join(problemas)
    assert "exec.mode" in texto
    assert "group:runtime" in texto and "gateway" in texto
    assert "tools.elevated.enabled" in texto


def test_config_con_plugin_nuevo_o_allowlist_vacia_se_detecta() -> None:
    # Arrange
    leidos = _config_viva()
    leidos["plugins.allow"] = ["whatsapp", "shell-tools"]
    leidos["channels.whatsapp.allowFrom"] = []

    # Act
    problemas = desviaciones_config(_config_base(), leidos)

    # Assert
    texto = "\n".join(problemas)
    assert "shell-tools" in texto
    assert "allowFrom" in texto


def test_ventas_atado_a_un_solo_chat_se_detecta_sin_mostrar_el_numero() -> None:
    # Arrange: los demás autorizados caerían en otro agente.
    leidos = _config_viva()
    leidos["bindings"] = [
        {
            "agentId": "ventas",
            "match": {"channel": "whatsapp", "peer": {"kind": "direct", "id": "+573001234567"}},
            "session": {"dmScope": "per-channel-peer"},
        }
    ]

    # Act
    problemas = desviaciones_config(_config_base(), leidos)

    # Assert
    texto = "\n".join(problemas)
    assert "atado a un chat" in texto
    assert "3001234567" not in texto


def test_ventas_sin_sesion_por_numero_se_detecta() -> None:
    # Arrange: con dmScope «main» todos los autorizados compartirían una sesión.
    leidos = _config_viva()
    leidos["bindings"] = [
        {"agentId": "ventas", "match": {"channel": "whatsapp", "accountId": "*"}},
    ]

    # Act
    problemas = desviaciones_config(_config_base(), leidos)

    # Assert
    assert any("dmScope None" in p for p in problemas)


def test_otro_agente_con_todos_los_directos_o_un_grupo_se_detecta() -> None:
    # Arrange: «main» sin id se llevaría los directos; el grupo volvería a abrirse.
    leidos = _config_viva()
    leidos["bindings"] = [
        *leidos["bindings"],
        {"agentId": "main", "match": {"channel": "whatsapp", "peer": {"kind": "direct"}}},
        {
            "agentId": "ventas",
            "match": {"channel": "whatsapp", "peer": {"kind": "group", "id": "120363@g.us"}},
            "session": {"dmScope": "per-channel-peer"},
        },
    ]

    # Act
    problemas = desviaciones_config(_config_base(), leidos)

    # Assert
    texto = "\n".join(problemas)
    assert "'main' recibe 'whatsapp'/'direct'" in texto
    assert "'ventas' tiene 2 enlaces" in texto
    assert "máximo 1" in texto
    assert "120363" not in texto


def test_leer_valores_separa_json_texto_y_rutas_sin_definir() -> None:
    # Arrange
    texto = "\n".join(
        [
            f"{SEPARADOR}channels.whatsapp.dmPolicy",
            "allowlist",
            f"{SEPARADOR}tools.elevated.enabled",
            "false",
            f"{SEPARADOR}plugins.allow",
            "[",
            '  "whatsapp"',
            "]",
            f"{SEPARADOR}agents.nope",
            "Unknown config path: agents.nope. Run openclaw config schema to inspect valid paths.",
        ]
    )

    # Act
    valores = leer_valores(texto)

    # Assert
    assert valores == {
        "channels.whatsapp.dmPolicy": "allowlist",
        "tools.elevated.enabled": False,
        "plugins.allow": ["whatsapp"],
        "agents.nope": None,
    }


def test_redactar_quita_telefonos_y_jid() -> None:
    # Arrange
    texto = "allow:Colombia · +57 300 123 4567 (id: 573001234567) grupo 120363000000@g.us"

    # Act
    limpio = redactar(texto)

    # Assert
    assert "300" not in limpio and "@g.us" not in limpio
    assert "Colombia" in limpio


def test_lineas_con_filtra_y_redacta() -> None:
    # Arrange
    texto = "Gateway reachable.\n- WhatsApp default: linked, connected, dm:allowlist, +573001234567"

    # Act
    lineas = lineas_con(texto, ["whatsapp", "connected"])

    # Assert
    assert lineas == ["- WhatsApp default: linked, connected, dm:allowlist, <número>"]
