"""Ensayo general, parte operativa y gratuita: app, repo, CI, servidor, gateway y audit.

Ninguna prueba de este archivo gasta tokens ni escribe en la base: solo lecturas HTTP (y dos
POST que la app rechaza antes de tocar nada), ``git``/``gh`` y comandos remotos de solo
lectura por SSH. Nunca se corre ``config set``, ``--fix``, reinicios ni ``compose up``.

Variables (docs/ensayo.md): ``ENSAYO_BASE_URL``, ``ENSAYO_APP_SSH``/``_PORT``/``_DIR``,
``ENSAYO_OPENCLAW_SSH``/``_USER`` y las expectativas ``ENSAYO_EXPECT_AGENT_MODE`` (tools),
``ENSAYO_EXPECT_WEBHOOK`` (exige 401), ``ENSAYO_EXPECT_JEV``, ``ENSAYO_EXPECT_PEDIDOS``
(Odoo configurado y alcanzable; por defecto en producción), ``ENSAYO_EXPECT_DIMENSIONS``
(768) y ``ENSAYO_REPO_LIMPIO`` (exige checkout sin cambios aunque no sea ``main``).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import time
import warnings
from collections.abc import Callable, Iterator
from functools import cache
from pathlib import Path
from typing import Any

import httpx
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
)
from tests.e2e.clientes import Config, Ssh

RAIZ = Path(__file__).resolve().parents[2]
BASE_AUDITORIA = RAIZ / "openclaw" / "auditoria-base.json"
# Cadenas con forma de credencial que nunca deben salir en una respuesta pública.
_SECRETO = re.compile(r"sk-[A-Za-z0-9_-]{8,}|lsv2_[A-Za-z0-9_]+|api[_-]?key", re.IGNORECASE)
_PENDIENTE = {"queued", "in_progress", "waiting", "pending", "requested"}


def _flag(nombre: str) -> bool:
    return os.environ.get(nombre, "").strip().lower() in {"1", "true", "si", "sí", "yes"}


@cache
def _base() -> dict[str, Any]:
    return json.loads(BASE_AUDITORIA.read_text(encoding="utf-8"))


# --- 1. App por HTTP --------------------------------------------------------------------


@pytest.fixture(scope="module")
def http(config: Config, salud: dict[str, Any]) -> Iterator[httpx.Client]:
    """Cliente sin cookies; ``salud`` salta el módulo si la app no responde."""

    with httpx.Client(base_url=config.base_url, timeout=30, follow_redirects=False) as cliente:
        yield cliente


def test_live_responde_ok(http: httpx.Client) -> None:
    # Arrange / Act
    respuesta = http.get("/live")

    # Assert
    assert respuesta.status_code == 200, respuesta.text[:300]
    assert respuesta.json() == {"status": "ok"}


def test_health_listo_con_indice_y_catalogo(http: httpx.Client) -> None:
    # Arrange
    dimensiones = int(os.environ.get("ENSAYO_EXPECT_DIMENSIONS", "768"))

    # Act
    respuesta = http.get("/health")
    datos = respuesta.json()

    # Assert
    assert respuesta.status_code == 200, datos
    assert datos["status"] == "ok", datos
    assert datos["documents"] > 0 and datos["chunks"] > 0, datos
    indice = datos["index"]
    assert indice["status"] == "ok", indice
    assert indice["points"] > 0, indice
    assert indice.get("dimensions", dimensiones) == dimensiones, indice
    catalogo = datos["catalog"]
    assert catalogo["status"] == "ok", catalogo
    assert catalogo["products"] > 0, catalogo
    assert catalogo.get("version"), catalogo


def test_health_modo_del_agente_esperado(salud: dict[str, Any]) -> None:
    # Arrange
    esperado = os.environ.get("ENSAYO_EXPECT_AGENT_MODE", "tools")

    # Act
    agente = salud.get("agent") or {}

    # Assert
    assert agente.get("mode_expected") == esperado, agente
    assert agente.get("tools_unavailable_reason") is None, agente


def test_health_jev_configurado_y_disponible(config: Config, salud: dict[str, Any]) -> None:
    # Arrange
    jev = salud.get("jev") or {}
    obligatorio = config.prod or _flag("ENSAYO_EXPECT_JEV")
    if not jev.get("configured") and not obligatorio:
        pytest.skip("Jev sin configurar en esta app local (ENSAYO_EXPECT_JEV=1 lo exige)")

    # Act
    resumen = {k: jev.get(k) for k in ("configured", "enabled", "available", "router", "model")}

    # Assert
    assert jev.get("configured") is True, resumen
    assert jev.get("enabled") is True, resumen
    assert jev.get("available") is True, {**resumen, "last_error": jev.get("last_error")}


def test_health_odoo_configurado_y_alcanzable(config: Config, salud: dict[str, Any]) -> None:
    # Arrange: sin Odoo el asistente cotiza igual pero no ofrece el pedido (spec 056), y
    # /health sigue en «ok»: este bloque es la única alarma barata de que se perdió.
    odoo = salud.get("odoo")
    if odoo is None and not config.expect_pedidos:
        pytest.skip("/health sin bloque «odoo»: la app corre una versión anterior a la spec 056")
    configurado = (odoo or {}).get("configured")
    if not configurado and not config.expect_pedidos:
        pytest.skip("Odoo sin configurar en esta app local (ENSAYO_EXPECT_PEDIDOS=1 lo exige)")

    # Act
    resumen = {k: (odoo or {}).get(k) for k in ("configured", "status", "reachable", "reason")}

    # Assert
    assert odoo is not None, (
        "/health no publica el bloque «odoo» (spec 056): la versión desplegada no lo trae o "
        "se perdió; sin él, que producción pierda Odoo pasa desapercibido"
    )
    assert configurado is True, (
        f"Odoo no está configurado en la app ({resumen}): falta ODOO_URL, ODOO_DB u "
        "ODOO_API_KEY en el .env del servidor y el folio no ofrecerá el pedido"
    )
    assert odoo.get("status") == "ok" and odoo.get("reachable") is True, (
        f"Odoo configurado pero no alcanzable ({resumen}): reason=auth es la clave del bot; "
        "unreachable, la red o Odoo caído (docs/odoo-pedidos.md)"
    )


def test_recibo_del_pedido_desplegado_y_sin_prueba_de_dueno_da_404(http: httpx.Client) -> None:
    # Arrange: un folio que no existe y sin cookie ni firma: se rechaza antes de la base.
    ruta = "/pedidos/000000000000.pdf"

    # Act
    respuesta = http.get(ruta)

    # Assert: la ruta existe (no redirige a HTTPS ni cae en otra vista) y no revela nada.
    assert respuesta.status_code == 404, (
        f"{ruta} respondió {respuesta.status_code} (3xx: la app redirige /pedidos a HTTPS, "
        "versión anterior a la spec 056; 503: sin base)",
        respuesta.text[:200],
    )
    assert "application/json" in respuesta.headers.get("content-type", ""), respuesta.text[:200]
    assert "no-store" in respuesta.headers.get("cache-control", "")


def test_health_foto_de_comida_lista(config: Config, salud: dict[str, Any]) -> None:
    # Arrange
    foto = salud.get("food_photo") or {}
    if foto.get("status") == "off" and not config.prod:
        pytest.skip("foto de comida apagada en local (sin TYPESAFE_API_KEY)")

    # Act
    estado = foto.get("status")

    # Assert
    assert estado == "ok", foto


@pytest.mark.parametrize("ruta", ["/live", "/health", "/api/health"])
def test_respuestas_publicas_sin_secretos(http: httpx.Client, ruta: str) -> None:
    # Arrange / Act
    respuesta = http.get(ruta)

    # Assert
    assert respuesta.status_code < 500, (ruta, respuesta.status_code)
    hallados = _SECRETO.findall(respuesta.text)
    assert not hallados, f"{ruta} expone {len(hallados)} cadenas con forma de credencial"


def test_cabeceras_de_seguridad_en_chat(http: httpx.Client) -> None:
    # Arrange / Act
    respuesta = http.get("/chat")
    cabeceras = respuesta.headers

    # Assert
    assert respuesta.status_code == 200
    csp = cabeceras.get("content-security-policy", "")
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp, csp
    assert cabeceras.get("x-frame-options") == "DENY"
    assert cabeceras.get("x-content-type-options") == "nosniff"
    assert "microphone=(self)" in cabeceras.get("permissions-policy", "")
    assert cabeceras.get("referrer-policy"), "falta Referrer-Policy"


def test_webhook_sin_firma_rechazado(http: httpx.Client) -> None:
    # Arrange
    exige = _flag("ENSAYO_EXPECT_WEBHOOK")
    cuerpo = b'{"message_id":"e2e-sin-firma","sender":"+15550100999","text":"hola"}'

    # Act
    sin_cabeceras = http.post(
        "/api/webhooks/whatsapp", content=cuerpo, headers={"Content-Type": "application/json"}
    )

    # Assert
    permitidos = {401} if exige else {401, 404}
    assert sin_cabeceras.status_code in permitidos, sin_cabeceras.text[:300]


def test_webhook_con_firma_falsa_rechazado(http: httpx.Client) -> None:
    # Arrange: sello vigente pero firma inventada; se rechaza antes del puente.
    cuerpo = b'{"message_id":"e2e-firma-falsa","sender":"+15550100999","text":"hola"}'
    cabeceras = {
        "Content-Type": "application/json",
        "X-Carvajal-Timestamp": str(int(time.time())),
        "X-Carvajal-Signature": "sha256=" + "0" * 64,
    }

    # Act
    respuesta = http.post("/api/webhooks/whatsapp", content=cuerpo, headers=cabeceras)

    # Assert
    if respuesta.status_code == 404 and not _flag("ENSAYO_EXPECT_WEBHOOK"):
        pytest.skip("webhook apagado (sin WEBHOOK_SECRET): 404")
    assert respuesta.status_code == 401, respuesta.text[:300]


def _eventos_sse(texto: str) -> list[str]:
    return re.findall(r"^event:\s*(\S+)", texto, flags=re.MULTILINE)


@pytest.mark.parametrize("caso", ["sin_token", "token_falso", "token_sin_cookie"])
def test_stream_sin_csrf_valido_rechazado(config: Config, http: httpx.Client, caso: str) -> None:
    # Arrange: el CSRF se valida antes de la cuota y del LLM (cero tokens).
    with httpx.Client(base_url=config.base_url, timeout=30) as navegador:
        datos = {"question": "hola", "client_turn_id": f"e2e-csrf-{caso}"}
        if caso == "token_falso":
            navegador.get("/api/csrf").raise_for_status()
            datos["csrf_token"] = "falso.0000"
        elif caso == "token_sin_cookie":
            with httpx.Client(base_url=config.base_url, timeout=30) as otro:
                datos["csrf_token"] = otro.get("/api/csrf").json()["token"]

        # Act
        respuesta = navegador.post("/chat/stream", data=datos)

    # Assert
    assert respuesta.status_code == 403, respuesta.text[:300]
    assert respuesta.headers.get("x-carvajal-error") == "1"
    assert _eventos_sse(respuesta.text) == ["error"], respuesta.text[:300]


def test_foto_sin_csrf_rechazada_sin_llamar_a_jev(http: httpx.Client) -> None:
    # Arrange: bytes mínimos; el 403 llega antes de leer la imagen.
    archivos = {"foto": ("plato.jpg", b"\xff\xd8\xff\xd9", "image/jpeg")}

    # Act
    respuesta = http.post("/chat/foto", data={"texto": ""}, files=archivos)

    # Assert
    assert respuesta.status_code == 403, respuesta.text[:300]
    assert '"reason": "csrf"' in respuesta.text or '"reason":"csrf"' in respuesta.text


# --- 2. Repo y CI ------------------------------------------------------------------------


def _git(*args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(RAIZ), *args], capture_output=True, text=True, timeout=timeout
    )


def _gh_json(*args: str) -> Any:
    proceso = subprocess.run(
        ["gh", *args], capture_output=True, text=True, timeout=60, cwd=RAIZ, check=False
    )
    if proceso.returncode != 0:
        pytest.fail(f"gh {' '.join(args[:3])} falló: {proceso.stderr.strip()[:300]}")
    return json.loads(proceso.stdout or "null")


@pytest.fixture(scope="session")
def gh() -> None:
    if shutil.which("gh") is None:
        pytest.skip("sin gh: no se consulta la CI")
    estado = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True, timeout=30)
    if estado.returncode != 0:
        pytest.skip("gh sin sesión (gh auth login o GH_TOKEN)")


@pytest.fixture(scope="session")
def origin_main() -> str:
    """SHA de ``origin/main`` recién traído (sin tocar la rama ni el árbol de trabajo)."""

    if shutil.which("git") is None or _git("rev-parse", "--git-dir").returncode != 0:
        pytest.skip("sin git o fuera de un checkout")
    traido = _git("fetch", "--quiet", "origin", "main", timeout=90)
    if traido.returncode != 0:
        pytest.skip(f"no se pudo traer origin/main: {traido.stderr.strip()[:200]}")
    return _git("rev-parse", "origin/main").stdout.strip()


def test_checkout_sin_cambios_en_archivos_versionados(origin_main: str) -> None:
    # Arrange
    rama = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    # Act
    lineas = _git("status", "--porcelain").stdout.splitlines()
    versionados = [linea for linea in lineas if not linea.startswith("??")]
    sin_versionar = [linea for linea in lineas if linea.startswith("??")]

    # Assert
    if sin_versionar:
        warnings.warn(
            f"checkout con {len(sin_versionar)} archivos sin versionar (se permite)", stacklevel=1
        )
    if versionados and rama != "main" and not _flag("ENSAYO_REPO_LIMPIO"):
        pytest.skip(
            f"rama de trabajo {rama!r} con {len(versionados)} cambios sin commit; el ensayo "
            "final se corre desde main limpio (ENSAYO_REPO_LIMPIO=1 lo exige aquí)"
        )
    assert not versionados, f"archivos versionados modificados: {versionados[:10]}"


def _ultimas_por_flujo(corridas: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ultimas: dict[str, dict[str, Any]] = {}
    for corrida in sorted(corridas, key=lambda c: c.get("createdAt", "")):
        ultimas[corrida["workflowName"]] = corrida
    return ultimas


@pytest.fixture(scope="session")
def corridas_main(gh: None, origin_main: str) -> dict[str, dict[str, Any]]:
    corridas = _gh_json(
        "run",
        "list",
        "--commit",
        origin_main,
        "--limit",
        "50",
        "--json",
        "workflowName,status,conclusion,createdAt,event,databaseId,headSha",
    )
    return _ultimas_por_flujo(corridas or [])


@pytest.mark.parametrize(
    ("flujo", "opcional"), [("CI", False), ("Despliegue", False), ("Imagen", True)]
)
def test_flujo_de_github_en_verde_en_origin_main(
    corridas_main: dict[str, dict[str, Any]], origin_main: str, flujo: str, opcional: bool
) -> None:
    # Arrange
    corrida = corridas_main.get(flujo)
    if corrida is None:
        if opcional:
            pytest.skip(f"{flujo} no corrió para {origin_main[:8]} (filtro por rutas)")
        pytest.skip(f"pendiente: {flujo} aún no tiene ejecución para {origin_main[:8]}")
    if corrida["status"] in _PENDIENTE:
        pytest.skip(f"pendiente: {flujo} está {corrida['status']} para {origin_main[:8]}")

    # Act
    conclusion = corrida.get("conclusion")

    # Assert
    assert corrida["status"] == "completed", corrida
    run = corrida["databaseId"]
    assert conclusion == "success", f"{flujo} terminó en {conclusion!r} (run {run})"


# --- 3. Servidor de la app (solo lectura) -----------------------------------------------


@pytest.fixture(scope="session")
def servidor(config: Config, ssh_app: Ssh) -> Callable[[str], subprocess.CompletedProcess[str]]:
    """Corre un comando de solo lectura en ``app_dir`` del servidor de la app."""

    if ssh_app.run("true", timeout=30).returncode == 255:
        pytest.fail(f"SSH al servidor de la app no conecta (puerto {config.app_ssh_port})")

    def correr(comando: str, *, timeout: float = 60) -> subprocess.CompletedProcess[str]:
        return ssh_app.run(f"cd {shlex.quote(config.app_dir)} && {comando}", timeout=timeout)

    return correr


def _contenedores(salida: str) -> dict[str, dict[str, Any]]:
    """``docker compose ps --format json`` en NDJSON (2.2x) o como lista (versiones nuevas)."""

    salida = salida.strip()
    filas = (
        json.loads(salida)
        if salida.startswith("[")
        else [json.loads(linea) for linea in salida.splitlines() if linea.strip().startswith("{")]
    )
    return {fila["Service"]: fila for fila in filas}


@pytest.fixture(scope="session")
def contenedores(servidor: Callable[..., Any]) -> dict[str, dict[str, Any]]:
    proceso = servidor("docker compose -f compose.prod.yml ps --format json")
    assert proceso.returncode == 0, proceso.stderr[:300]
    return _contenedores(proceso.stdout)


@pytest.mark.parametrize("servicio", ["app", "db", "mcp", "ollama", "qdrant"])
def test_contenedor_de_produccion_sano(
    contenedores: dict[str, dict[str, Any]], servicio: str
) -> None:
    # Arrange
    fila = contenedores.get(servicio)

    # Act
    estado = (fila or {}).get("State"), (fila or {}).get("Health")

    # Assert
    assert fila is not None, f"{servicio} no aparece en compose ps: {sorted(contenedores)}"
    assert estado[0] == "running", fila.get("Status")
    if servicio != "qdrant":  # qdrant no tiene healthcheck en compose.prod.yml
        assert estado[1] == "healthy", fila.get("Status")


def test_version_desplegada_coincide_con_el_ultimo_despliegue_y_main(
    servidor: Callable[..., Any],
    contenedores: dict[str, dict[str, Any]],
    gh: None,
    origin_main: str,
) -> None:
    # Arrange
    etiqueta = servidor("cat .deployed_tag").stdout.strip()
    ultimo = _gh_json(
        "run",
        "list",
        "--workflow",
        "Despliegue",
        "--branch",
        "main",
        "--status",
        "success",
        "--limit",
        "1",
        "--json",
        "headSha,databaseId",
    )
    assert ultimo, "no hay ningún Despliegue exitoso en main"
    esperado = f"sha-{ultimo[0]['headSha']}"

    # Act
    imagenes = {s: contenedores[s].get("Image", "") for s in ("app", "mcp") if s in contenedores}

    # Assert
    assert etiqueta == esperado, f".deployed_tag={etiqueta!r}; último Despliegue ok={esperado!r}"
    for servicio, imagen in imagenes.items():
        assert imagen.endswith(f":{etiqueta}"), f"{servicio} corre {imagen!r}, no {etiqueta!r}"
    if etiqueta != f"sha-{origin_main}":
        pytest.skip(
            f"pendiente: origin/main ({origin_main[:8]}) aún no está desplegado; "
            f"en el servidor sigue {etiqueta[4:12]}"
        )


def test_mcp_del_servidor_vivo_y_exige_token(servidor: Callable[..., Any]) -> None:
    # Arrange / Act
    vivo = servidor("curl -fsS --max-time 10 127.0.0.1:8765/live")
    sin_token = servidor(
        "curl -s -o /dev/null -w '%{http_code}' --max-time 10 -X POST 127.0.0.1:8765/mcp"
    )

    # Assert
    assert vivo.returncode == 0 and vivo.stdout.strip() == "ok", vivo.stderr[:200]
    assert sin_token.stdout.strip() == "401", sin_token.stdout


def test_carpeta_de_medios_montada_y_respondiendo(servidor: Callable[..., Any]) -> None:
    # Arrange: la ruta sale de OPENCLAW_MEDIA_DIR (no es un secreto) o del valor por defecto.
    ruta = (
        servidor(
            "grep -E '^OPENCLAW_MEDIA_DIR=' .env | tail -n1 | cut -d= -f2- | tr -d \"'\\\"\""
        ).stdout.strip()
        or "/home/deploy/openclaw-media"
    )

    # Act
    montaje = servidor(
        f"mountpoint -q {shlex.quote(ruta)} && timeout 10 stat -c %F {shlex.quote(ruta)}",
        timeout=30,
    )
    reenvio = servidor("ss -Htln 'sport = :2224'")

    # Assert
    assert montaje.returncode == 0, f"{ruta} sin montar o colgado (código {montaje.returncode})"
    assert montaje.stdout.strip() == "directory"
    assert reenvio.stdout.strip(), "el reenvío inverso :2224 (ovh-media-tunnel) no escucha"


def test_env_del_servidor_protegido_y_con_perfil_whatsapp(servidor: Callable[..., Any]) -> None:
    # Arrange
    claves = ("MCP_TOKEN", "SESSION_SECRET", "DEEPSEEK_API_KEY", "OPENCLAW_MEDIA_DIR")
    bucle = "; ".join(f"grep -qE '^{c}=.+' .env && echo {c}" for c in claves)

    # Act
    permisos = servidor("stat -c %a .env").stdout.strip()
    perfiles = servidor("grep -E '^COMPOSE_PROFILES=' .env | tail -n1").stdout.strip()
    presentes = set(servidor(bucle).stdout.split())

    # Assert
    assert permisos == "600", f".env con permisos {permisos}"
    assert "whatsapp" in perfiles.split("=", 1)[-1].split(","), perfiles
    assert presentes == set(claves), f"faltan en .env: {sorted(set(claves) - presentes)}"


def test_env_del_servidor_con_odoo(config: Config, servidor: Callable[..., Any]) -> None:
    # Arrange: solo se comprueba que las claves existan con valor; nunca se leen.
    if not config.expect_pedidos:
        pytest.skip("ENSAYO_EXPECT_PEDIDOS=0: no se exige Odoo en el servidor")
    claves = ("ODOO_URL", "ODOO_DB", "ODOO_API_KEY")
    bucle = "; ".join(f"grep -qE '^{c}=.+' .env && echo {c}" for c in claves)

    # Act
    presentes = set(servidor(bucle).stdout.split())

    # Assert
    assert presentes == set(claves), (
        f"faltan en .env: {sorted(set(claves) - presentes)}: el folio no ofrecerá el pedido "
        "(spec 056, docs/odoo-pedidos.md)"
    )


def test_ollama_tiene_el_modelo_de_embeddings(servidor: Callable[..., Any]) -> None:
    # Arrange / Act
    lista = servidor("docker compose -f compose.prod.yml exec -T ollama ollama list", timeout=60)

    # Assert
    assert lista.returncode == 0, lista.stderr[:300]
    assert "nomic-embed-text" in lista.stdout, lista.stdout[:500]


# --- 4. Gateway OpenClaw (solo lectura) ---------------------------------------------------


@pytest.fixture(scope="session")
def openclaw(config: Config, ssh_openclaw: Ssh) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Corre un comando como el usuario del gateway, con su ``systemd --user``."""

    if ssh_openclaw.run("true", timeout=30).returncode == 255:
        pytest.fail("SSH a la VM de OpenClaw no conecta")
    if ssh_openclaw.como(config.openclaw_user, "true", timeout=30).returncode != 0:
        pytest.fail(f"sudo -n -u {config.openclaw_user} no funciona en la VM de OpenClaw")

    def correr(comando: str, *, timeout: float = 90) -> subprocess.CompletedProcess[str]:
        return ssh_openclaw.como(config.openclaw_user, comando, timeout=timeout)

    return correr


def test_version_de_openclaw_igual_a_la_base(
    openclaw: Callable[..., Any], record_property: Callable[[str, Any], None]
) -> None:
    # Arrange
    esperada = _base()["openclaw_version"]

    # Act
    salida = openclaw("openclaw --version").stdout
    encontrada = re.search(r"(\d{4}\.\d+\.\d+)", salida)
    version = encontrada.group(1) if encontrada else salida.strip()[:40]
    record_property("openclaw_version", version)
    print(f"OpenClaw en vivo: {version} (base {esperada})")

    # Assert: una versión distinta cambia el audit, pero solo se avisa.
    assert encontrada, f"no se reconoce la versión: {redactar(salida[:200])!r}"
    if version != esperada:
        warnings.warn(
            f"OpenClaw {version} difiere de la base {esperada}: revisa el audit", stacklevel=1
        )


def test_servicios_del_gateway_activos_y_con_linger(
    config: Config, openclaw: Callable[..., Any]
) -> None:
    # Arrange
    unidades = os.environ.get("ENSAYO_OPENCLAW_UNITS", "").split() or _base()["unidades"]

    # Act
    activos = openclaw(f"systemctl --user is-active {' '.join(unidades)}").stdout.split()
    linger = openclaw(f"loginctl show-user {config.openclaw_user} -p Linger").stdout.strip()

    # Assert
    assert dict(zip(unidades, activos, strict=False)) == dict.fromkeys(unidades, "active")
    assert linger == "Linger=yes", linger


def test_whatsapp_conectado_con_dm_en_allowlist(openclaw: Callable[..., Any]) -> None:
    # Arrange / Act
    salida = openclaw("openclaw channels status --probe 2>&1", timeout=120).stdout
    lineas = lineas_con(salida, ["whatsapp"])

    # Assert
    assert lineas, "channels status no muestra la cuenta de WhatsApp"
    sana = [linea for linea in lineas if all(p in linea for p in ("linked", "connected"))]
    assert sana, f"WhatsApp sin enlazar o desconectado: {lineas}"
    assert any("dm:allowlist" in linea for linea in sana), f"DM sin allowlist: {sana}"


def test_configuracion_del_gateway_sin_deriva(
    openclaw: Callable[..., Any], record_property: Callable[[str, Any], None]
) -> None:
    # Arrange
    base_config = _base()["config"]
    rutas = rutas_de_config(base_config)
    comando = "; ".join(
        f"printf '%s\\n' {shlex.quote(SEPARADOR + ruta)}; "
        f"openclaw config get {shlex.quote(ruta)} 2>&1"
        for ruta in rutas
    )

    # Act
    leidos = leer_valores(openclaw(comando, timeout=240).stdout)
    problemas = desviaciones_config(base_config, leidos)
    for ruta in base_config.get("informativo", []):
        record_property(ruta, leidos.get(ruta))
        print(f"{ruta} = {redactar(repr(leidos.get(ruta)))}")

    # Assert
    faltan = [ruta for ruta in rutas if ruta not in leidos]
    assert not faltan, f"no se pudieron leer: {faltan}"
    assert not problemas, "deriva respecto a openclaw/auditoria-base.json:\n" + "\n".join(problemas)


def test_tunel_al_mcp_desde_openclaw(openclaw: Callable[..., Any]) -> None:
    # Arrange / Act: ovh-tunnel reenvía 127.0.0.1:8765 de la VM al MCP del servidor.
    vivo = openclaw("curl -fsS --max-time 10 127.0.0.1:8765/live")
    sin_token = openclaw(
        "curl -s -o /dev/null -w '%{http_code}' --max-time 10 -X POST 127.0.0.1:8765/mcp"
    )

    # Assert
    assert vivo.returncode == 0 and vivo.stdout.strip() == "ok", vivo.stderr[:200]
    assert sin_token.stdout.strip() == "401", sin_token.stdout


def test_auditoria_de_seguridad_sin_regresiones(
    openclaw: Callable[..., Any], record_property: Callable[[str, Any], None]
) -> None:
    # Arrange
    base = _base()
    proceso = openclaw("openclaw security audit --json", timeout=180)

    # Act
    try:
        actual = extraer_json(proceso.stdout)
        resumen = parsear(actual).resumen
    except AuditoriaInvalida as error:
        pytest.fail(f"audit ilegible (código {proceso.returncode}): {error}")
    resultado = comparar(base, actual)
    record_property("audit_summary", json.dumps(resumen))
    print(f"audit en vivo: {resumen} (base {base['summary']})")
    for aviso in resultado.avisos:
        warnings.warn(aviso, stacklevel=1)
    for linea in resultado.info:
        print(f"info: {linea}")

    # Assert
    assert resultado.ok, "regresión de seguridad en OpenClaw:\n" + "\n".join(resultado.fallos)
