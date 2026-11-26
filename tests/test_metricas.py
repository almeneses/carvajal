"""Página «Métricas» y sus agregadores (spec 055).

Cada fuente con red se prueba con ``httpx.MockTransport``; la base, con SQLite en memoria. Se
comprueba que una fuente caída no tumba la página, que el saldo de DeepSeek solo lo ve el
operador y que nada de lo publicado lleva texto de personas, ``owner_key`` ni secretos.
"""

import json
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from apps.api.metrics import (
    FAILURE_RETRY_TTL,
    LANGSMITH_TTL,
    Card,
    MetricsBoard,
    TtlCache,
    deepseek_balance,
    deepseek_card,
    jev_card,
    langsmith_card,
    mcp_health,
    qdrant_card,
    whatsapp_card,
)
from carvajal.adapters.llm.usage import MAX_PENDING, DeepSeekUsageCallback
from carvajal.adapters.storage import tables
from carvajal.infrastructure import metrics as process_metrics
from carvajal.infrastructure.metrics import CallStats, EventCounts, estimated_cost, percentile
from carvajal.infrastructure.settings import Settings

OPERATOR_TOKEN = "token-de-operador"
NOW = datetime(2026, 11, 26, 18, 0, tzinfo=UTC)

COLLECTION = {
    "result": {
        "status": "green",
        "optimizer_status": "ok",
        "points_count": 125,
        "indexed_vectors_count": 120,
        "segments_count": 6,
        "config": {
            "params": {"vectors": {"size": 768, "distance": "Cosine"}, "on_disk_payload": True},
            "hnsw_config": {"m": 16, "ef_construct": 100},
        },
    }
}
TELEMETRY = {
    "result": {
        "app": {"version": "1.15.4"},
        "memory": {"resident_bytes": 99_000_000},
        "collections": {
            "collections": [
                {
                    "id": "carvajal_chunks",
                    "shards": [
                        {"local": {"vectors_size_bytes": 384_000, "payloads_size_bytes": 16_000}}
                    ],
                },
                {"id": "otra", "shards": [{"local": {"vectors_size_bytes": 999}}]},
            ]
        },
        "requests": {
            "rest": {
                "responses": {
                    "POST /collections/{name}/points/query": {
                        "200": {
                            "count": 4,
                            "total_duration_micros": 40_000,
                            "max_duration_micros": 25_000,
                        }
                    },
                    "PUT /collections/{name}/points": {
                        "200": {"count": 99, "total_duration_micros": 1, "max_duration_micros": 1}
                    },
                }
            }
        },
    }
}
STATS = {
    "run_count": 200,
    "error_rate": 0.025,
    "latency_p50": 2.04,
    "latency_p99": 10.41,
    "prompt_tokens": 3_140_713,
    "completion_tokens": 32_562,
    "last_run_start_time": "2026-11-26T17:59:00+00:00",
}


def _settings(**values: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "qdrant_url": "http://qdrant:6333",
        "qdrant_api_key": "",
        "langsmith_api_key": "",
        "monitoring_enabled": False,
        "deepseek_api_key": "",
        "mcp_token": "",
        "mcp_health_token": "",
        "metrics_mcp_health_url": "",
        "operator_token": "",
    }
    base.update(values)
    return Settings(**base)


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- Contadores en proceso ---------------------------------------------------------------


def test_call_stats_cuenta_estados_tipos_tokens_y_percentiles() -> None:
    # Arrange
    stats = CallStats(window=5)

    # Act
    for latency in (10, 20, 30, 40, 50, 1000):
        stats.record(kind="route", status="ok", latency_ms=latency, input_tokens=3, output_tokens=1)
    stats.record(kind="claims", status="timeout", latency_ms=800)
    snapshot = stats.snapshot()

    # Assert: la ventana guarda las 5 últimas latencias (30, 40, 50, 1000, 800).
    assert snapshot["calls"] == 7
    assert snapshot["errors"] == 1
    assert snapshot["by_kind"] == {"claims": 1, "route": 6}
    assert snapshot["by_status"] == {"ok": 6, "timeout": 1}
    assert (snapshot["input_tokens"], snapshot["output_tokens"]) == (18, 6)
    assert snapshot["latency_ms"] == {"p50": 50, "p95": 1000, "samples": 5}


def test_percentil_sin_datos_y_costo_estimado() -> None:
    assert percentile([], 0.5) is None
    assert percentile([5.0], 0.95) == 5.0
    assert estimated_cost(
        1_000_000, 100_000, input_per_million=0.30, output_per_million=1.20
    ) == pytest.approx(0.42)


def test_event_counts_guarda_conteos_y_la_hora_del_ultimo() -> None:
    counts = EventCounts(clock=lambda: NOW)

    counts.count("mensajes")
    counts.count("mensajes")
    counts.count("errores")

    assert counts.snapshot() == {
        "since": "2026-11-26T18:00:00Z",
        "last_at": "2026-11-26T18:00:00Z",
        "counts": {"errores": 1, "mensajes": 2},
    }


def test_callback_de_deepseek_cuenta_latencia_tokens_y_errores_sin_mensajes() -> None:
    # Arrange
    reloj = iter([1.0, 1.25, 2.0, 2.5])
    stats = CallStats()
    callback = DeepSeekUsageCallback(stats, clock=lambda: next(reloj))
    mensaje = AIMessage(
        content="secreto del cliente",
        usage_metadata={"input_tokens": 900, "output_tokens": 40, "total_tokens": 940},
    )
    primero, segundo = uuid4(), uuid4()

    # Act
    callback.on_chat_model_start(
        {}, [[]], run_id=primero, invocation_params={"model": "deepseek-flash"}
    )
    callback.on_llm_end(LLMResult(generations=[[ChatGeneration(message=mensaje)]]), run_id=primero)
    callback.on_chat_model_start({}, [[]], run_id=segundo, invocation_params={"model": "x"})
    callback.on_llm_error(httpx.ReadTimeout("lento"), run_id=segundo)
    snapshot = stats.snapshot()

    # Assert
    assert snapshot["by_status"] == {"ok": 1, "timeout": 1}
    assert snapshot["by_kind"] == {"deepseek-flash": 1, "x": 1}
    assert (snapshot["input_tokens"], snapshot["output_tokens"]) == (900, 40)
    assert snapshot["latency_ms"]["p50"] == 250
    assert "secreto" not in json.dumps(snapshot)


def test_callback_de_deepseek_descarta_las_abandonadas_mas_antiguas_y_no_la_en_curso() -> None:
    # Arrange: casi el tope de llamadas abandonadas, una en curso y dos más que empiezan.
    stats = CallStats()
    callback = DeepSeekUsageCallback(stats, clock=lambda: 1.0)
    for _ in range(MAX_PENDING - 1):
        callback.on_chat_model_start({}, [[]], run_id=uuid4(), invocation_params={"model": "a"})
    en_curso = uuid4()
    callback.on_chat_model_start({}, [[]], run_id=en_curso, invocation_params={"model": "m"})
    for _ in range(2):
        callback.on_chat_model_start({}, [[]], run_id=uuid4(), invocation_params={"model": "b"})

    # Act
    callback.on_llm_end(LLMResult(generations=[[]]), run_id=en_curso)
    snapshot = stats.snapshot()

    # Assert: la llamada en curso conserva su modelo y su latencia.
    assert snapshot["by_kind"] == {"m": 1}
    assert snapshot["latency_ms"]["samples"] == 1


def test_callback_de_deepseek_cuenta_las_llamadas_sin_uso_informado() -> None:
    # Arrange: en streaming sin stream_usage la respuesta no trae usage_metadata.
    stats = CallStats()
    callback = DeepSeekUsageCallback(stats)
    run_id = uuid4()

    # Act
    callback.on_chat_model_start({}, [[]], run_id=run_id, invocation_params={"model": "m"})
    callback.on_llm_end(
        LLMResult(generations=[[ChatGeneration(message=AIMessage(content="hola"))]]),
        run_id=run_id,
    )
    snapshot = stats.snapshot()

    # Assert
    assert snapshot["calls"] == 1
    assert snapshot["without_usage"] == 1
    assert (snapshot["input_tokens"], snapshot["output_tokens"]) == (0, 0)


def test_el_cliente_de_jev_cuenta_cada_llamada_en_el_proceso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    from carvajal.adapters.judge import jev as jev_module

    stats = CallStats()
    monkeypatch.setattr(jev_module, "JEV_CALLS", stats)
    respuesta = {"model": "jev-1.14.0", "usage": {"input_tokens": 50, "output_tokens": 2}}
    client = jev_module.JevClient(
        api_key="clave",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=respuesta)),
    )

    # Act
    client.call("router", {}, {}, lambda payload: (payload["model"], {}))
    snapshot = stats.snapshot()

    # Assert
    assert snapshot["by_kind"] == {"router": 1}
    assert snapshot["by_status"] == {"ok": 1}
    assert (snapshot["input_tokens"], snapshot["output_tokens"]) == (50, 2)


def test_el_cliente_de_jev_cuenta_los_cortes_por_cupo_sin_contarlos_como_llamadas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: cupo de una llamada al día.
    from carvajal.adapters.judge import jev as jev_module

    stats = CallStats()
    monkeypatch.setattr(jev_module, "JEV_CALLS", stats)
    respuesta = {"model": "jev-1.14.0", "usage": {"input_tokens": 1, "output_tokens": 1}}
    client = jev_module.JevClient(
        api_key="clave",
        max_calls_per_day=1,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=respuesta)),
    )

    # Act
    client.call("router", {}, {}, lambda payload: (payload["model"], {}))
    for _ in range(2):
        with pytest.raises(jev_module.JudgeUnavailable):
            client.call("router", {}, {}, lambda payload: (payload["model"], {}))
    snapshot = stats.snapshot()

    # Assert
    assert snapshot["calls"] == 1
    assert snapshot["blocked"] == {"cap": 2}


def test_jev_sin_clave_no_muestra_el_gatekeeper_encendido() -> None:
    # Arrange: la configuración pide «on», pero falta TYPESAFE_API_KEY.
    jev = {"configured": False, "enabled": True, "router": "on"}
    claims = {"mode": "on"}

    # Act
    card = jev_card(_settings(), jev=jev, claims=claims, photo={"status": "off"})

    # Assert
    assert card.status == "off"
    assert (card.data["router"], card.data["claims_mode"]) == ("off", "off")


# --- Qdrant ------------------------------------------------------------------------------


def test_qdrant_resume_la_coleccion_el_tamano_y_las_busquedas() -> None:
    # Arrange
    vistas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        vistas.append(request.url.path)
        assert request.headers["api-key"] == "clave-qdrant"
        if request.url.path == "/telemetry":
            return httpx.Response(200, json=TELEMETRY)
        return httpx.Response(200, json=COLLECTION)

    # Act
    card = qdrant_card(_settings(qdrant_api_key="clave-qdrant"), _client(handler))

    # Assert
    assert card.status == "ok"
    assert vistas == ["/collections/carvajal_chunks", "/telemetry"]
    assert card.data["points"] == 125
    assert card.data["indexed_vectors"] == 120
    assert (card.data["dimensions"], card.data["distance"]) == (768, "Cosine")
    assert card.data["size_bytes"] == 400_000
    assert card.data["queries"] == {"count": 4, "avg_ms": 10.0, "max_ms": 25.0}
    assert card.data["version"] == "1.15.4"
    assert "clave-qdrant" not in json.dumps(card.data)


def test_qdrant_sin_telemetria_sigue_con_la_coleccion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/telemetry":
            return httpx.Response(403)
        return httpx.Response(200, json=COLLECTION)

    card = qdrant_card(_settings(), _client(handler))

    assert card.status == "ok"
    assert card.data["points"] == 125
    assert "403" in card.data["telemetry_unavailable"]


@pytest.mark.parametrize(
    ("respuesta", "motivo"),
    [
        (httpx.Response(404), "no existe"),
        (httpx.ReadTimeout("lento"), "a tiempo"),
        (httpx.ConnectError("caído"), "conectar"),
    ],
)
def test_qdrant_caido_dice_por_que(respuesta: Any, motivo: str) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        if isinstance(respuesta, Exception):
            raise respuesta
        return respuesta

    card = qdrant_card(_settings(), _client(handler))

    assert card.status == "unavailable"
    assert motivo in card.reason


# --- LangSmith ---------------------------------------------------------------------------


def test_langsmith_apagado_no_consulta_la_red() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no debe consultar LangSmith")

    card = langsmith_card(_settings(), _client(handler))

    assert card.status == "off"
    assert "MONITORING_ENABLED" in card.reason


def test_langsmith_usa_el_agregado_de_24_h_y_7_dias_filtrado_por_entorno() -> None:
    # Arrange
    peticiones: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        peticiones.append(request)
        assert request.headers["x-api-key"] == "lsv2-clave"
        if request.url.path == "/sessions":
            return httpx.Response(200, json=[{"id": "p-1", "name": "carvajal"}])
        assert request.url.path == "/runs/stats"
        return httpx.Response(200, json=STATS)

    settings = _settings(
        monitoring_enabled=True,
        langsmith_api_key="lsv2-clave",
        langsmith_agent_environment="produccion",
    )

    # Act
    card = langsmith_card(settings, _client(handler), now=NOW)

    # Assert: tres peticiones y ninguna lista de runs.
    assert card.status == "ok"
    assert [p.url.path for p in peticiones] == ["/sessions", "/runs/stats", "/runs/stats"]
    cuerpos = [json.loads(p.content) for p in peticiones[1:]]
    assert [c["start_time"][:10] for c in cuerpos] == ["2026-11-25", "2026-11-19"]
    assert all(c["is_root"] is True and c["session"] == ["p-1"] for c in cuerpos)
    assert all('"produccion"' in c["filter"] for c in cuerpos)
    ventana = card.data["windows"]["24h"]
    assert (ventana["traces"], ventana["errors"], ventana["p50_s"]) == (200, 5, 2.04)
    assert "lsv2-clave" not in json.dumps(card.data)


def test_langsmith_que_pide_esperar_queda_no_disponible() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sessions":
            return httpx.Response(200, json=[{"id": "p-1"}])
        return httpx.Response(429)

    settings = _settings(monitoring_enabled=True, langsmith_api_key="k")

    card = langsmith_card(settings, _client(handler))

    assert card.status == "unavailable"
    assert "429" in card.reason


# --- DeepSeek ----------------------------------------------------------------------------


def test_deepseek_estima_el_costo_con_el_precio_configurado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    stats = CallStats()
    stats.record(kind="deepseek-flash", input_tokens=2_000_000, output_tokens=500_000)
    monkeypatch.setattr("apps.api.metrics.DEEPSEEK_CALLS", stats)
    settings = _settings(
        deepseek_api_key="sk-x",
        deepseek_price_input_per_million=0.5,
        deepseek_price_output_per_million=2.0,
    )

    # Act
    card = deepseek_card(settings)

    # Assert
    assert card.status == "ok"
    assert card.data["cost_usd"] == pytest.approx(2.0)
    assert card.data["balance"] is None


def test_deepseek_sin_clave_queda_apagado() -> None:
    assert deepseek_card(_settings()).status == "off"


def test_saldo_de_deepseek_usa_la_url_sin_v1_y_no_expone_la_clave() -> None:
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.deepseek.com/user/balance"
        assert request.headers["authorization"] == "Bearer sk-secreta"
        return httpx.Response(
            200,
            json={
                "is_available": True,
                "balance_infos": [
                    {
                        "currency": "USD",
                        "total_balance": "4.20",
                        "granted_balance": "0.00",
                        "topped_up_balance": "4.20",
                    }
                ],
            },
        )

    # Act
    balance = deepseek_balance(_settings(deepseek_api_key="sk-secreta"), _client(handler))

    # Assert
    assert balance["status"] == "ok"
    assert balance["balances"][0]["total"] == "4.20"
    assert "sk-secreta" not in json.dumps(balance, default=str)


# --- WhatsApp (base y MCP) ---------------------------------------------------------------


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    motor = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables.metadata.create_all(motor)
    try:
        yield sessionmaker(bind=motor)
    finally:
        tables.metadata.drop_all(motor)
        motor.dispose()


def _poblar(factory: sessionmaker[Session]) -> None:
    ahora = datetime.now(UTC)
    with factory.begin() as session:
        session.execute(
            insert(tables.conversations),
            [
                {"id": "c-wa", "owner_key": "wa_0123456789abcdef", "created_at": ahora},
                {"id": "c-web", "owner_key": "anon-visitante", "created_at": ahora},
            ],
        )
        filas = [
            ("m1", "c-wa", "user", ahora - timedelta(hours=1)),
            ("m2", "c-wa", "assistant", ahora - timedelta(hours=1)),
            ("m3", "c-wa", "user", ahora - timedelta(days=3)),
            ("m4", "c-web", "user", ahora - timedelta(hours=2)),
            ("m5", "c-web", "user", ahora - timedelta(days=10)),
        ]
        session.execute(
            insert(tables.messages),
            [
                {
                    "id": mid,
                    "conversation_id": cid,
                    "role": rol,
                    "content": "Mi número es 3100000000",
                    "created_at": cuando,
                }
                for mid, cid, rol, cuando in filas
            ],
        )


def test_whatsapp_cuenta_por_canal_sin_textos_ni_owner_keys(
    factory: sessionmaker[Session],
) -> None:
    # Arrange
    _poblar(factory)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("sin URL del MCP no se consulta")

    # Act
    card = whatsapp_card(_settings(), _client(handler), factory)

    # Assert
    assert card.status == "ok"
    uso = card.data["usage"]
    assert (uso["wa"]["messages_24h"], uso["wa"]["messages_7d"]) == (1, 2)
    assert (uso["web"]["messages_24h"], uso["web"]["messages_7d"]) == (1, 1)
    assert uso["wa"]["chats_7d"] == 1
    assert card.data["mcp"]["status"] == "off"
    assert "OpenClaw" in card.data["note"]
    publicado = json.dumps(card.data, default=str)
    assert "3100000000" not in publicado
    assert "wa_0123" not in publicado


def test_whatsapp_lee_el_health_del_mcp_con_su_bearer(factory: sessionmaker[Session]) -> None:
    # Arrange
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://mcp:8765/health"
        assert request.headers["authorization"] == "Bearer token-mcp"
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "claims": {"judged": 10, "substituted": 2, "substitution_rate": 0.2},
                "metrics": {
                    "whatsapp": {"counts": {"mensajes": 7, "no_autorizados": 1}},
                    "jev": {"calls": 3},
                    "deepseek": {"calls": 5},
                },
            },
        )

    settings = _settings(
        metrics_mcp_health_url="http://mcp:8765/health", mcp_health_token="token-mcp"
    )

    # Act
    card = whatsapp_card(settings, _client(handler), factory)

    # Assert
    mcp = card.data["mcp"]
    assert mcp["status"] == "ok"
    assert mcp["whatsapp"]["counts"]["mensajes"] == 7
    assert mcp["claims"]["substitution_rate"] == 0.2
    assert "token-mcp" not in json.dumps(card.data, default=str)


def test_whatsapp_no_usa_el_bearer_de_mcp_para_leer_el_health() -> None:
    """MCP_TOKEN abre /mcp; aunque la app lo tuviera, /metricas solo envía MCP_HEALTH_TOKEN."""

    # Arrange
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("sin MCP_HEALTH_TOKEN no se consulta el MCP")

    settings = _settings(metrics_mcp_health_url="http://mcp:8765/health", mcp_token="token-mcp")

    # Act
    mcp = mcp_health(settings, _client(handler))

    # Assert
    assert mcp["status"] == "off"
    assert "MCP_HEALTH_TOKEN" in mcp["reason"]


def test_whatsapp_sin_base_ni_mcp_queda_no_disponible() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("caído")

    settings = _settings(metrics_mcp_health_url="http://mcp:8765/health", mcp_health_token="t")

    card = whatsapp_card(settings, _client(handler), None)

    assert card.status == "unavailable"
    assert card.data["mcp"]["reason"] == "No se pudo conectar."


# --- Caché -------------------------------------------------------------------------------


def test_la_cache_sirve_lo_vigente_y_ante_un_fallo_el_dato_anterior() -> None:
    # Arrange
    reloj = [0.0]
    cache = TtlCache(clock=lambda: reloj[0])
    llamadas: list[str] = []

    def bueno() -> Card:
        llamadas.append("bueno")
        return Card("q", "Q", "ok", data={"points": 1})

    def malo() -> Card:
        llamadas.append("malo")
        return Card("q", "Q", "unavailable", "No respondió a tiempo.")

    # Act
    primero = cache.get("q", 60, bueno)
    segundo = cache.get("q", 60, malo)
    reloj[0] = 61
    tercero = cache.get("q", 60, malo)

    # Assert
    assert llamadas == ["bueno", "malo"]
    assert primero is segundo
    assert tercero.stale is True
    assert tercero.data == {"points": 1}
    assert tercero.reason == "No respondió a tiempo."


def test_la_cache_guarda_el_fallo_y_no_consulta_la_fuente_caida_en_cada_visita() -> None:
    # Arrange: dato bueno, caduca y la fuente cae.
    reloj = [0.0]
    cache = TtlCache(clock=lambda: reloj[0])
    llamadas: list[str] = []
    estado = {"ok": True}

    def loader() -> Card:
        llamadas.append("ok" if estado["ok"] else "falla")
        if estado["ok"]:
            return Card("q", "Q", "ok", data={"points": 1}, ttl_s=600)
        return Card("q", "Q", "unavailable", "No se pudo conectar.")

    cache.get("q", 600, loader)
    reloj[0] = 601
    estado["ok"] = False

    # Act: diez visitas en 50 s y otra pasado el minuto de reintento.
    vistas = []
    for _ in range(10):
        reloj[0] += 5
        vistas.append(cache.get("q", 600, loader))
    reloj[0] += 60
    estado["ok"] = True
    recuperada = cache.get("q", 600, loader)

    # Assert: una sola consulta durante el fallo, datos anteriores marcados y recuperación.
    assert llamadas == ["ok", "falla", "ok"]
    assert all(v.stale and v.data == {"points": 1} for v in vistas)
    assert vistas[-1].ttl_s == FAILURE_RETRY_TTL
    assert recuperada.stale is False


def test_un_fallo_sin_dato_previo_se_reintenta_al_minuto_y_no_a_los_diez() -> None:
    # Arrange
    reloj = [0.0]
    cache = TtlCache(clock=lambda: reloj[0])
    estado = {"ok": False}

    def loader() -> Card:
        if estado["ok"]:
            return Card("q", "Q", "ok", data={"points": 1})
        return Card("q", "Q", "unavailable", "No respondió a tiempo.")

    # Act
    primera = cache.get("q", 600, loader)
    estado["ok"] = True
    reloj[0] = 30
    durante = cache.get("q", 600, loader)
    reloj[0] = FAILURE_RETRY_TTL + 1
    despues = cache.get("q", 600, loader)

    # Assert
    assert (primera.status, durante.status, despues.status) == ("unavailable",) * 2 + ("ok",)


def test_varias_visitas_con_el_dato_caducado_recargan_una_sola_vez() -> None:
    # Arrange: el loader tarda; ocho hilos piden la misma clave a la vez.
    cache = TtlCache()
    llamadas: list[int] = []
    puerta = threading.Event()

    def loader() -> Card:
        llamadas.append(1)
        puerta.wait(timeout=2)
        return Card("q", "Q", "ok")

    # Act
    with ThreadPoolExecutor(max_workers=8) as pool:
        futuros = [pool.submit(cache.get, "q", 60, loader) for _ in range(8)]
        time.sleep(0.1)
        puerta.set()
        resultados = [f.result(timeout=5) for f in futuros]

    # Assert
    assert len(llamadas) == 1
    assert all(r.status == "ok" for r in resultados)


def test_langsmith_en_429_no_se_vuelve_a_consultar_en_diez_minutos() -> None:
    # Arrange: primera carga buena; al caducar, LangSmith responde 429 (spec 055, edge case).
    reloj = [0.0]
    peticiones: list[str] = []
    estado = {"ok": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != "api.smith.langchain.com":
            return httpx.Response(503)
        peticiones.append(request.url.path)
        if not estado["ok"]:
            return httpx.Response(429)
        if request.url.path == "/sessions":
            return httpx.Response(200, json=[{"id": "p-1"}])
        return httpx.Response(200, json=STATS)

    board = MetricsBoard(
        settings=_settings(monitoring_enabled=True, langsmith_api_key="k"),
        transport=httpx.MockTransport(handler),
        cache=TtlCache(clock=lambda: reloj[0]),
    )
    vista = {"operator": False, "jev": {}, "claims": {}, "photo": {}}
    board.cards(**vista)
    reloj[0] = LANGSMITH_TTL + 1
    estado["ok"] = False
    peticiones.clear()

    # Act: diez visitas en 50 s.
    for _ in range(10):
        reloj[0] += 5
        tarjeta = next(c for c in board.cards(**vista) if c.key == "langsmith")

    # Assert: una sola petición (la que recibió el 429) y los datos anteriores marcados.
    assert peticiones == ["/sessions"]
    assert tarjeta.status == "ok" and tarjeta.stale is True
    assert "429" in tarjeta.reason
    assert tarjeta.ttl_s == LANGSMITH_TTL


def test_una_respuesta_con_otra_forma_deja_su_tarjeta_no_disponible_y_no_las_demas() -> None:
    # Arrange: Qdrant responde 200 con un JSON que no es el esperado (``result`` es lista).
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "qdrant":
            return httpx.Response(200, json={"result": ["inesperado"]})
        raise AssertionError(f"petición inesperada: {request.url}")

    board = MetricsBoard(settings=_settings(), transport=httpx.MockTransport(handler))

    # Act
    tarjetas = {c.key: c for c in board.cards(operator=False, jev={}, claims={}, photo={})}

    # Assert: la tarjeta de Qdrant lo dice y las otras cuatro siguen.
    assert set(tarjetas) == {"qdrant", "jev", "langsmith", "deepseek", "whatsapp"}
    assert tarjetas["qdrant"].status == "unavailable"
    assert tarjetas["qdrant"].reason == "Respuesta inesperada (AttributeError)."


def test_cada_fuente_en_paralelo_usa_su_propio_cliente_http() -> None:
    # Arrange: se registra qué cliente atiende a cada fuente.
    usados: dict[str, set[int]] = {}
    candado = threading.Lock()

    class Tablero(MetricsBoard):
        def _client(self) -> httpx.Client:
            cliente = super()._client()
            original = cliente.send

            def send(request: httpx.Request, **kwargs: Any) -> httpx.Response:
                with candado:
                    usados.setdefault(request.url.host, set()).add(id(cliente))
                return original(request, **kwargs)

            cliente.send = send  # type: ignore[method-assign]
            return cliente

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "qdrant":
            return httpx.Response(200, json=COLLECTION)
        if request.url.path == "/user/balance":
            return httpx.Response(200, json={"is_available": True, "balance_infos": []})
        if request.url.host == "mcp":
            return httpx.Response(200, json={"metrics": {}})
        if request.url.path == "/sessions":
            return httpx.Response(200, json=[{"id": "p-1"}])
        return httpx.Response(200, json=STATS)

    board = Tablero(
        settings=_settings(
            monitoring_enabled=True,
            langsmith_api_key="k",
            deepseek_api_key="sk",
            metrics_mcp_health_url="http://mcp:8765/health",
            mcp_health_token="t",
        ),
        transport=httpx.MockTransport(handler),
    )

    # Act
    board.cards(operator=True, jev={}, claims={}, photo={})

    # Assert: un cliente por fuente, ninguno compartido entre hilos.
    clientes = list(usados.values())
    assert len(usados) == 4
    assert all(len(ids) == 1 for ids in clientes)
    assert len(set().union(*clientes)) == 4


# --- Vista -------------------------------------------------------------------------------


def _transporte(saldo: bool = True) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/balance":
            assert saldo, "el saldo solo se consulta para el operador"
            return httpx.Response(
                200,
                json={
                    "is_available": True,
                    "balance_infos": [
                        {
                            "currency": "USD",
                            "total_balance": "4.20",
                            "granted_balance": "0.00",
                            "topped_up_balance": "4.20",
                        }
                    ],
                },
            )
        if request.url.host == "qdrant":
            raise httpx.ConnectError("Qdrant caído")
        raise AssertionError(f"petición inesperada: {request.url}")

    return httpx.MockTransport(handler)


@pytest.fixture()
def app_metricas(
    monkeypatch: pytest.MonkeyPatch, factory: sessionmaker[Session]
) -> Callable[..., TestClient]:
    monkeypatch.setenv("OPERATOR_TOKEN", OPERATOR_TOKEN)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-secreta")
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("MONITORING_ENABLED", "false")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant:6333")
    monkeypatch.setenv("METRICS_MCP_HEALTH_URL", "")
    _poblar(factory)
    process_metrics.DEEPSEEK_CALLS.reset()

    def construir(saldo: bool = True) -> TestClient:
        from apps.api.main import Handlers, HealthStatus, create_app

        app = create_app(Handlers(health=lambda: HealthStatus(1, 2)))
        app.state.metrics = MetricsBoard(
            settings=app.state.settings,
            session_factory=lambda: factory,
            transport=_transporte(saldo),
        )
        return TestClient(app)

    return construir


def test_la_pagina_es_publica_y_una_fuente_caida_no_la_tumba(
    app_metricas: Callable[..., TestClient],
) -> None:
    # Arrange
    cliente = app_metricas(saldo=False)

    # Act
    respuesta = cliente.get("/metricas")

    # Assert
    assert respuesta.status_code == 200
    assert "no-store" in respuesta.headers["cache-control"]
    html = respuesta.text
    for titulo in ("Qdrant", "Jev (TypeSafe)", "LangSmith", "DeepSeek", "WhatsApp (OpenClaw)"):
        assert titulo in html
    assert "No disponible" in html  # Qdrant caído
    assert "No se pudo conectar." in html
    assert "Visible solo para el operador." in html
    assert "4.20" not in html
    assert "sk-secreta" not in html
    assert "wa_0123" not in html
    assert "3100000000" not in html


def test_el_operador_ve_el_saldo_de_deepseek(app_metricas: Callable[..., TestClient]) -> None:
    # Arrange
    cliente = app_metricas()

    # Act
    html = cliente.get("/metricas", headers={"X-Operator-Token": OPERATOR_TOKEN}).text

    # Assert
    assert "Saldo USD" in html
    assert "4.20" in html
    assert "sk-secreta" not in html


def test_el_saldo_en_cache_del_operador_no_se_sirve_a_un_visitante(
    app_metricas: Callable[..., TestClient],
) -> None:
    # Arrange: el operador abre la página y deja el saldo en la caché.
    cliente = app_metricas()
    operador = cliente.get("/metricas", headers={"X-Operator-Token": OPERATOR_TOKEN}).text
    assert "4.20" in operador

    # Act: un visitante sin sesión entra dentro de la vigencia del saldo.
    visitante = cliente.get("/metricas").text

    # Assert
    assert "4.20" not in visitante
    assert "Visible solo para el operador." in visitante


def test_la_navegacion_enlaza_metricas(app_metricas: Callable[..., TestClient]) -> None:
    cliente = app_metricas()

    chat = cliente.get("/chat").text
    metricas = cliente.get("/metricas").text

    assert 'href="/metricas"' in chat
    assert 'href="/metricas"\n            aria-current="page"' in metricas
