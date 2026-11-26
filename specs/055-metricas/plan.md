# Plan: Métricas de operación (spec 055)

**Created**: 2026-11-26 · **Status**: Implemented

## Piezas

| Pieza | Archivo | Qué hace |
|---|---|---|
| Contadores en proceso | `packages/core/src/carvajal/infrastructure/metrics.py` | `CallStats` (llamadas, estados, tipos, tokens, p50/p95 sobre las últimas 1.000 latencias), `EventCounts` y `estimated_cost`; instancias `JEV_CALLS`, `DEEPSEEK_CALLS` y `WHATSAPP_EVENTS`. |
| Jev | `carvajal/adapters/judge/jev.py` | `JevClient.call` registra cada llamada (tipo, estado, latencia y tokens). |
| DeepSeek | `carvajal/adapters/llm/usage.py`, `deepseek.py` | Callback de LangChain `DEEPSEEK_USAGE` en todo `ChatOpenAI` de DeepSeek: latencia, tokens (`usage_metadata`) y errores, sin leer mensajes. |
| Puente de WhatsApp | `apps/mcp/bridge.py`, `apps/mcp/server.py` | Cuenta mensajes, fotos, notas de voz, PDF, errores, sin cupo y no autorizados; `/health` publica el bloque `metrics` y acepta el `Host` `mcp`. |
| Agregadores | `apps/api/metrics.py` | Una función por fuente que devuelve una `Card` y nunca lanza; `TtlCache` con dato anterior ante fallos; `MetricsBoard` consulta en paralelo. |
| Vista | `apps/api/views/metricas.py`, `templates/metricas.html`, `static/metricas.css` | Página con tarjetas, histograma SVG (la CSP no admite estilos en línea) y el saldo solo para el operador. |
| Operación | `compose.prod.yml`, `compose.yml`, `.env.example` | Healthcheck de Qdrant con bash y `/dev/tcp`; `service_healthy` para app, MCP y bootstrap; `MCP_TOKEN`, `METRICS_MCP_HEALTH_URL` y el precio de DeepSeek en la app. |

## Decisiones

1. **Contadores en memoria, no una tabla**: lo pedido son cifras de operación del día; una
   tabla añadiría escrituras en cada turno. Lo que sí debe sobrevivir al despliegue (uso por
   canal) se calcula de las tablas que ya existen.
2. **LangSmith por agregado**: `POST /runs/stats` devuelve cuentas, percentiles y tokens en una
   petición; descargar runs sería lento y agotaría el límite de la API (el ensayo vio 429 tras
   dos consultas seguidas). Caché de 10 minutos.
3. **MCP por la red interna**: la app ya comparte red con `mcp` en compose; se reutiliza el
   bearer del MCP en vez de abrir otro puerto.

## Verificación

`uv run pytest tests/test_metricas.py tests/test_mcp_whatsapp.py tests/test_deploy_packaging.py`,
`make check` y la página local contra Qdrant, LangSmith y la base reales.
