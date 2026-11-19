"""Pruebas del monitoreo opcional con LangSmith (spec 038, US1/US2).

Cubren la redacción del contenido (FR-005, FR-006), el muestreo (FR-007) y la
resistencia a fallos del servicio (FR-008) sin red ni credenciales (FR-010).
"""

import logging
from collections.abc import Iterator
from typing import Any

import pytest

from carvajal.infrastructure.monitoring import (
    NoopTracer,
    build_client,
    build_tracer,
    monitoring_status,
    redact,
    thread_hash,
)
from carvajal.infrastructure.settings import Settings

PREGUNTA_CON_PII = (
    "Hola, soy Ana Pérez, mi teléfono es 300 123 4567 y busco vasos de 12 oz para café"
)
TELEFONO = "300 123 4567"


class ClienteFalso:
    """Doble de ``langsmith.Client`` que guarda lo que se le envía."""

    def __init__(self, *, falla: bool = False) -> None:
        self.runs: list[dict[str, Any]] = []
        self.actualizaciones: list[dict[str, Any]] = []
        self.falla = falla

    def create_run(self, **kwargs: Any) -> None:
        if self.falla:
            raise RuntimeError("servicio de monitoreo caído")
        self.runs.append(kwargs)

    def update_run(self, **kwargs: Any) -> None:
        if self.falla:
            raise RuntimeError("servicio de monitoreo caído")
        self.actualizaciones.append(kwargs)


def _settings(**overrides: Any) -> Settings:
    """Configuración aislada del ``.env`` del repositorio."""

    base: dict[str, Any] = {
        "_env_file": None,
        "monitoring_enabled": True,
        "langsmith_api_key": "lsv2_pt_prueba",
        "monitoring_sample_rate": 1.0,
    }
    base.update(overrides)
    return Settings(**base)


class TestRedaccion:
    """Lista blanca de claves: lo demás no viaja al servicio."""

    def test_conserva_la_lista_blanca(self) -> None:
        """Metadatos, métricas y errores tipados sobreviven sin cambios."""

        payload = {
            "route": "rag",
            "intent": "informacion",
            "tool": None,
            "tool_name": "quote",
            "tool_period": "2026-11",
            "preferences_count": 3,
            "product_count": 2,
            "slug": "empaques/vasos",
            "chunk_id": "abc-123",
            "layer": "core",
            "source_type": "gold",
            "score": 0.87,
            "scores": [0.87, 0.5],
            "model": "gpt-4o-mini",
            "model_name": "gpt-4o-mini",
            "provider": "openai",
            "token_usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
            "duration_ms": 12.5,
            "question_chars": len(PREGUNTA_CON_PII),
            "answer_chars": 40,
            "error_type": "TimeoutError",
            "status": "failed",
            "request_id": "req-1",
            "thread_hash": "0123456789abcdef",
            "router": "rules",
            "outcome": "ok",
        }

        assert redact(payload, include_content=False) == payload

    def test_elimina_contenido_y_pii(self) -> None:
        """Pregunta, respuesta, prompts, fragmentos y datos del visitante no salen."""

        payload = {
            "route": "commercial",
            "question": PREGUNTA_CON_PII,
            "answer": "Claro, Ana, te recomiendo el vaso de 12 oz",
            "prompt": "Eres un asistente…",
            "system": "instrucciones",
            "content": "texto",
            "excerpt": "fragmento del corpus",
            "messages": [{"role": "user", "content": PREGUNTA_CON_PII}],
            "inputs": {"question": PREGUNTA_CON_PII},
            "outputs": {"answer": "respuesta"},
            "visitor": {"name": "Ana Pérez", "phone": TELEFONO},
            "ip": "192.0.2.10",
            "cookies": {"session": "abc"},
            "csrf_token": "token-csrf",
            "api_key": "sk-secreta",
            "owner_key": "owner-hilo-crudo",
            "error": f"falló con {PREGUNTA_CON_PII}",
        }

        limpio = redact(payload, include_content=False)

        assert limpio == {"route": "commercial"}
        serializado = repr(limpio)
        for secreto in (TELEFONO, "Ana", "csrf", "sk-secreta", "owner-hilo-crudo", "vaso"):
            assert secreto not in serializado

    def test_con_contenido_activo_no_redacta(self) -> None:
        """En desarrollo y evaluación el contenido viaja completo (FR-006)."""

        payload = {"question": PREGUNTA_CON_PII, "answer": "respuesta", "route": "rag"}

        assert redact(payload, include_content=True) == payload

    def test_es_recursivo_en_listas_y_diccionarios(self) -> None:
        """La lista blanca se aplica sin importar la profundidad (FR-006)."""

        payload = {
            "route": "rag",
            "steps": [
                {"slug": "a", "text": "fragmento A"},
                {"slug": "b", "chunk_id": "c-2", "excerpt": "fragmento B"},
            ],
            "usage": {"input_tokens": 5, "prompt": "no debe sobrevivir"},
        }

        assert redact(payload, include_content=False) == {
            "route": "rag",
            "steps": [{"slug": "a"}, {"slug": "b", "chunk_id": "c-2"}],
            "usage": {"input_tokens": 5},
        }

    def test_no_rompe_los_tipos_hoja(self) -> None:
        """Valores nulos, booleanos y números bajo claves permitidas se conservan."""

        payload = {"tool": None, "preferences_count": 0, "score": 0.0, "status": True}

        assert redact(payload, include_content=False) == payload


class TestEstadoDelMonitoreo:
    """Salud y construcción del cliente (FR-001, FR-011)."""

    def test_apagado_por_defecto(self) -> None:
        """Sin configuración explícita no hay monitoreo ni cliente."""

        settings = Settings(_env_file=None)

        assert settings.monitoring_enabled is False
        assert monitoring_status(settings).enabled is False
        assert build_client(settings) is None
        assert isinstance(build_tracer(settings), NoopTracer)

    def test_sin_llave_no_se_activa(self, caplog: pytest.LogCaptureFixture) -> None:
        """Encender el monitoreo sin llave es un error de despliegue, no una fuga."""

        settings = _settings(langsmith_api_key="")

        estado = monitoring_status(settings)

        with caplog.at_level(logging.WARNING):
            cliente = build_client(settings)

        assert estado.enabled is False
        assert cliente is None
        assert any("monitoring.disabled" in registro.message for registro in caplog.records)

    def test_la_salud_no_expone_credenciales(self) -> None:
        """El bloque de salud lleva solo activo, muestreo y modo de contenido."""

        settings = _settings(langsmith_endpoint="https://langsmith.interno/")

        salud = monitoring_status(settings).as_health()

        assert salud == {"enabled": True, "sample_rate": 1.0, "content": "metadata"}
        assert "langsmith.interno" not in repr(salud)
        assert "lsv2_pt_prueba" not in repr(salud)

    def test_modo_de_contenido(self) -> None:
        """``MONITORING_INCLUDE_CONTENT`` decide entre metadatos y contenido."""

        assert monitoring_status(_settings()).content == "metadata"
        assert monitoring_status(_settings(monitoring_include_content=True)).content == "full"

    def test_muestreo_cero_equivale_a_apagado(self) -> None:
        """Una tasa de 0 no construye cliente ni abre trazas (FR-007)."""

        settings = _settings(monitoring_sample_rate=0.0)

        assert monitoring_status(settings).enabled is False
        assert build_client(settings) is None

    def test_thread_hash_es_pseudonimo_y_estable(self) -> None:
        """El hilo se representa con 16 hex del SHA-256, nunca con el valor crudo."""

        assert thread_hash("hilo-123") == thread_hash("hilo-123")
        assert thread_hash("hilo-123") != thread_hash("hilo-124")
        assert len(thread_hash("hilo-123")) == 16
        assert "hilo-123" not in thread_hash("hilo-123")


class TestTrazas:
    """Muestreo, contenido de la raíz y tolerancia a fallos (FR-007, FR-008)."""

    def test_tasa_uno_crea_la_traza(self) -> None:
        """Con muestreo del 100 % se publica una raíz con su identificador."""

        cliente = ClienteFalso()
        tracer = build_tracer(_settings(), client=cliente, random_fn=lambda: 0.0)

        with tracer.turn(
            request_id="req-1", thread_hash="0123456789abcdef", router="rules", question="hola"
        ):
            pass

        assert len(cliente.runs) == 1
        raiz = cliente.runs[0]
        assert raiz["name"] == "chat_turn"
        assert raiz["extra"]["metadata"]["request_id"] == "req-1"
        assert raiz["extra"]["metadata"]["thread_hash"] == "0123456789abcdef"
        assert raiz["extra"]["metadata"]["router"] == "rules"
        assert cliente.actualizaciones[0]["outputs"] == {"outcome": "ok"}

    def test_tasa_cero_no_crea_traza(self) -> None:
        """Con la tasa en 0 el turno se ejecuta sin cliente ni raíz."""

        cliente = ClienteFalso()
        tracer = build_tracer(_settings(monitoring_sample_rate=0.0), client=cliente)

        with tracer.turn(
            request_id="req-2", thread_hash="0123456789abcdef", router="rules", question="hola"
        ) as turno:
            turno.add_metadata(route="direct")

        assert cliente.runs == []
        assert cliente.actualizaciones == []

    def test_el_muestreo_usa_la_funcion_inyectada(self) -> None:
        """La decisión sale de la función inyectada, no de ``random`` global."""

        llamadas: list[int] = []

        def azar() -> float:
            llamadas.append(1)
            return 0.75

        cliente = ClienteFalso()
        tracer = build_tracer(_settings(monitoring_sample_rate=0.5), client=cliente, random_fn=azar)

        with tracer.turn(
            request_id="req-3", thread_hash="0123456789abcdef", router="rules", question="hola"
        ):
            pass

        assert llamadas == [1]
        assert cliente.runs == []

    def test_un_turno_no_muestreado_no_construye_cliente(self) -> None:
        """Sin muestreo no se crea cliente: no hay coste ni red (FR-007)."""

        construcciones: list[int] = []

        def fabrica(_settings: Settings) -> Any:
            construcciones.append(1)
            return ClienteFalso()

        tracer = build_tracer(
            _settings(monitoring_sample_rate=0.5), client_factory=fabrica, random_fn=lambda: 0.9
        )

        with tracer.turn(
            request_id="req-4", thread_hash="0123456789abcdef", router="rules", question="hola"
        ):
            pass

        assert construcciones == []

    def test_muestreado_construye_el_cliente_una_vez(self) -> None:
        """El cliente se construye de forma perezosa y se reutiliza entre turnos."""

        construcciones: list[int] = []

        def fabrica(_settings: Settings) -> Any:
            construcciones.append(1)
            return cliente

        cliente = ClienteFalso()
        tracer = build_tracer(
            _settings(monitoring_sample_rate=1.0), client_factory=fabrica, random_fn=lambda: 0.0
        )

        for _ in range(2):
            with tracer.turn(
                request_id="req-5",
                thread_hash="0123456789abcdef",
                router="rules",
                question="hola",
            ):
                pass

        assert construcciones == [1]
        assert len(cliente.runs) == 2

    def test_un_cliente_que_falla_no_rompe_el_turno(self, caplog: pytest.LogCaptureFixture) -> None:
        """Un servicio caído deja la respuesta intacta y el fallo en los logs (FR-008)."""

        cliente = ClienteFalso(falla=True)
        tracer = build_tracer(_settings(), client=cliente, random_fn=lambda: 0.0)

        with (
            caplog.at_level(logging.WARNING),
            tracer.turn(
                request_id="req-6",
                thread_hash="0123456789abcdef",
                router="rules",
                question="hola",
            ) as turno,
        ):
            turno.add_metadata(route="direct")

        assert any("monitoring.failed" in registro.message for registro in caplog.records)

    def test_un_error_del_turno_marca_la_traza(self) -> None:
        """La excepción del turno se propaga y la traza queda como fallida."""

        cliente = ClienteFalso()
        tracer = build_tracer(_settings(), client=cliente, random_fn=lambda: 0.0)

        with (
            pytest.raises(RuntimeError),
            tracer.turn(
                request_id="req-7",
                thread_hash="0123456789abcdef",
                router="rules",
                question="hola",
            ),
        ):
            raise RuntimeError("falló el grafo")

        assert cliente.actualizaciones[0]["error"] == "RuntimeError"

    def test_el_contenido_de_la_raiz_respeta_el_modo(self) -> None:
        """En producción la raíz no lleva el texto; en desarrollo sí (FR-006)."""

        produccion = ClienteFalso()
        tracer = build_tracer(_settings(), client=produccion, random_fn=lambda: 0.0)

        with tracer.turn(
            request_id="req-8",
            thread_hash="0123456789abcdef",
            router="rules",
            question=PREGUNTA_CON_PII,
        ):
            pass

        assert TELEFONO not in repr(produccion.runs)
        assert produccion.runs[0]["extra"]["metadata"]["question_chars"] == len(PREGUNTA_CON_PII)

        desarrollo = ClienteFalso()
        tracer = build_tracer(
            _settings(monitoring_include_content=True),
            client=desarrollo,
            random_fn=lambda: 0.0,
        )

        with tracer.turn(
            request_id="req-9",
            thread_hash="0123456789abcdef",
            router="rules",
            question=PREGUNTA_CON_PII,
        ):
            pass

        assert desarrollo.runs[0]["inputs"]["question"] == PREGUNTA_CON_PII


@pytest.fixture(autouse=True)
def _sin_red(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Ninguna prueba debe abrir sockets (FR-010)."""

    import socket

    def prohibido(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("las pruebas de monitoreo no deben salir a la red")

    monkeypatch.setattr(socket.socket, "connect", prohibido)
    monkeypatch.setattr(socket.socket, "connect_ex", prohibido)
    yield
