# Evidencia del piloto — Spec 043

- **Fecha**: 2026-11-23
- **Base**: `cff48fd` en `feat/chat-comercial-unificado`; los cambios de esta evidencia quedan en el worktree para revisión.
- **Entorno**: Linux, Python 3.13, `uv`, Docker con Postgres 16/pgvector, Qdrant 1.15.4 y Ollama 0.40.16.
- **Catálogo**: versión `2026-11-18` (`python -m carvajal.commercial version`).

## Verificado

| Comprobación | Resultado |
| --- | --- |
| `uv run python -m carvajal.commercial validar` | 200 productos, 29 ofertas CO, 98 MX, 29 sedes, 0 evidencias de stock. |
| `uv run python -m carvajal.commercial paridad-mx` | `PARIDAD OK`, 98/98. |
| `make check` | Ruff, formato, `ty` y **1795 pruebas** pasan (62 deselectionadas por integración/e2e/smoke). |
| Migraciones | `0001 → 0010` en base limpia; `0009 → 0010` conserva una cotización histórica y el downgrade vuelve a `0009`. |
| Pruebas de contrato y motor | 300/301 vasos, envío con costo cero/sin precio, presentación, vigencia ausente, 29 CO y paridad MX. |
| Pruebas de chat | Turno mixto con citas documentales, contexto comercial entre turnos, concurrencia del primer hilo, reenvío y aislamiento. |
| Pruebas de histórico | Real, simulado y ambiguo con dos identidades; texto y tarjetas contaminados se excluyen. |
| Voz e institucional | El hilo único conserva dictado/lectura y responde una consulta institucional con su fuente. |
| Imagen Docker | `docker build -t carvajal-api:043-check .` termina correctamente. |
| Arranque limpio parcial | Stack con Postgres/Qdrant/Ollama, migraciones a `0010`, catálogo copiado desde `/seed` y `/health` con `catalog.status=ok`; `/live` 200, `/chat` 200, `/cotizador` y `/faq` 303 al chat. El 503 restante es solo `index.status=missing`, que el despliegue distingue de un catálogo inválido. |
| `docker compose -f compose.prod.yml config` | Válido con `SESSION_SECRET`; el comando de bootstrap muestra la separación catálogo/comercial/Gold. |

## No verificado en este entorno

- Indexación completa en Qdrant con `nomic-embed-text` y recorridos de navegador del piloto.
- Actualización sobre un volumen con datos reales de la 042 y rollback a una imagen anterior.
- Diez sesiones concurrentes reales y medición de mediana/p95 del primer contenido útil y del cierre.
- Disponibilidad externa de vendedores o proveedores.

Estos puntos no se presentan como aprobados. La validación de T021 queda parcial y debe repetirse en el entorno autorizado con el runbook de despliegue.

## Umbral propuesto para SC-005 (T029, pendiente del owner)

No se midió ninguna muestra; la siguiente es una propuesta de aceptación, no un resultado:

- **Primer contenido útil**: mediana ≤ 2.500 ms y p95 ≤ 6.000 ms.
- **Cierre del turno**: p95 ≤ 20.000 ms.
- **Muestra mínima**: 30 turnos mixtos y 10 sesiones concurrentes, registrando fecha, commit, modelo, proveedor y hardware.
- Se aceptan valores distintos si el owner los fija antes de la medición; hasta entonces T029 queda pendiente.

### Cómo medir SC-005

1. Levantar el stack autorizado con el runbook de despliegue y registrar commit, versión comercial, modelo/proveedor y hardware.
2. Ejecutar al menos 30 turnos mixtos de `quickstart.md` y una tanda de 10 sesiones concurrentes.
3. Medir por turno tres marcas: inicio de la petición, primer contenido útil (`token`/tarjeta) y cierre (`done`).
4. Calcular mediana y p95 de las dos últimas; anotar fallos y descartes, no solo los éxitos.
5. Pegar la tabla de resultados en esta evidencia; T029 se marca solo cuando el owner apruebe el umbral y la muestra.

## Archivos de referencia

- `specs/043-chat-comercial-unificado/quickstart.md` — escenarios.
- `tests/test_commercial_contract.py`, `tests/test_quotation_engine.py` — datos e importes.
- `tests/test_chat_service.py`, `tests/test_api_chat.py` — turnos, concurrencia y replay.
- `tests/test_api_cotizador.py`, `tests/test_cotizador_store.py` — histórico autorizado y procedencia.
- `scripts/deploy.sh`, `compose.prod.yml`, `Dockerfile` — aprovisionamiento y readiness.
