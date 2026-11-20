"""Servidor mínimo para la validación de navegador de la 040.

Usa los dobles de chat y el SSE de este directorio; no toca base de datos, LLM
ni Qdrant. Ejecutar con ``PYTHONPATH=<raíz del repo>``.
"""

import os
from collections.abc import Iterator
from dataclasses import asdict

import uvicorn

from apps.api.chat.identity import SignedCookieIdentity
from apps.api.chat.streaming import ChatEvent
from apps.api.main import Handlers, HealthStatus, create_app
from carvajal.application.ask import Source
from carvajal.application.quota import QuotaResult

SOURCE = Source("manual", "Manual de empaques", "manual", "core", 0.9, "Fragmento del manual.")

PRODUCTOS = """
<section class="productos" aria-label="Referencias del catálogo">
  <ul class="productos-lista">
    <li class="producto">
      <h3 class="producto-nombre" data-campo="producto-nombre">Vaso papel 12 oz bebida caliente</h3>
      <dl class="producto-datos">
        <div><dt>SKU</dt><dd><code>FX-VASO-PAPEL-12</code></dd></div>
        <div><dt>Cajas</dt><dd data-campo="producto-cajas">5</dd></div>
      </dl>
      <p class="producto-precio">
        <strong data-campo="producto-precio">7250.00 MXN</strong>
        <span class="producto-precio-detalle">en total</span>
      </p>
      <p class="producto-fuente">
        <a href="https://ejemplo.test/vaso">Ver ficha ↗</a>
        · <span data-campo="producto-fecha">capturada el 2026-10-23</span>
      </p>
    </li>
  </ul>
</section>
"""


class FakeChat:
    def history(self, owner_key: str, thread_id: str | None = None) -> list[object]:
        return []

    def conversations(self, owner_key: str) -> list[object]:
        return []

    def new_conversation(self, owner_key: str) -> object:
        from datetime import UTC, datetime

        from carvajal.adapters.storage.chat import Conversation

        ahora = datetime(2026, 11, 20, tzinfo=UTC)
        return Conversation(
            id="hilo-e2e",
            owner_key=owner_key,
            created_at=ahora,
            updated_at=ahora,
        )

    def ask(self, owner_key: str, question: str, **kwargs: object) -> object:
        raise AssertionError("el flujo de voz usa el stream")

    def stream(self, owner_key: str, question: str, **kwargs: object) -> Iterator[ChatEvent]:
        yield ChatEvent("open", {"stream": "chat"})
        yield ChatEvent("meta", {"sources": [asdict(SOURCE)]})
        yield ChatEvent("products", {"html": PRODUCTOS})
        yield ChatEvent("token", {"text": "Tenemos vasos de papel para café [1]."})
        yield ChatEvent(
            "done",
            {
                "conversation_id": "hilo-e2e",
                "message_id": "msg-e2e",
                "thread_id": "hilo-e2e",
                "request_id": "req-e2e",
                "elapsed_ms": 12,
            },
        )


app = create_app(Handlers(health=lambda: HealthStatus(0, 0)))
app.state.chat = FakeChat()
app.state.chat_identity = SignedCookieIdentity(b"secreto-e2e")
app.state.quota = lambda owner: QuotaResult(allowed=True)

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("PORT", "8123")),
        log_level="warning",
    )
