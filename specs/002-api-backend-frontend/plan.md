# Implementation Plan: Aplicación web del asistente *(as-built)*

**Spec**: [`spec.md`](./spec.md) · **Tasks**: [`tasks.md`](./tasks.md) ·
**Estado**: entregado en las olas 0–6 · **Ventana real**: 2026-11-08 → 2026-11-14 ·
**Evidencia**: `CONSTITUTION.md` §9, [`docs/aceptacion-m2.md`](../../docs/aceptacion-m2.md)

> Plan **conforme a obra**: describe cómo se resolvió la spec después de implementarla. No es
> el plan que se aprobó en la ola 0; las diferencias con aquel borrador están en §Desviaciones.

## Summary

Migrar el prototipo Streamlit a un servicio web con backend y frontend desplegables: corpus en
Postgres, índice vectorial en Qdrant, retrieval y generación detrás de puertos, chat con citas
y streaming, pantallas de corpus y documento, agente con memoria y herramienta de indicadores,
y operación en Docker sobre una sola máquina.

## Technical Context

| Aspecto | Decisión | Evidencia |
|---|---|---|
| Lenguaje | Python 3.13, src-layout en `packages/core` | `pyproject.toml` |
| Env y dependencias | `uv` + `uv.lock` (prohibido `pip`) | §9 (2026-10-21) |
| Web | FastAPI + Jinja2 + HTMX (§) y frontend propio con JS sin framework | §9 (2026-11-13) |
| Persistencia | Postgres 16 + SQLAlchemy Core + Alembic (migraciones 0001–0005) | §9 (2026-11-10) |
| Vectores | Qdrant en el M2; pgvector como adaptador heredado | §9 (2026-11-13) |
| Agente | LangGraph con `PostgresSaver` y `PostgresStore` | §9 (2026-11-13) |
| LLM | Puerto `LLMProvider`: DeepSeek y Ollama | §9 (2026-11-11) |
| Embeddings | Ollama `nomic-embed-text` (768 dimensiones) | §9 (2026-11-11) |
| Despliegue | Imagen multi-etapa + compose de producción en VPS, por IP y HTTP | [`docs/despliegue-ovh.md`](../../docs/despliegue-ovh.md) |
| Pruebas | pytest (577), Ruff, `ty`, pre-commit, GitHub Actions | `tests/`, `.github/workflows/ci.yml` |

## Constitution Check

| Principio | Cumplimiento |
|---|---|
| I. La constitución manda | Cada ola registró su decisión en §9 el mismo día de la entrega |
| II. El corpus es el contrato | `data/gold/` versionado; importador idempotente con poda autoritativa |
| III. Sin fuentes no hay respuesta | `AskQuestion` sin contexto no llama al LLM; citas con `slug`, `domain` y `source_type`; abstención verificada (q31) |
| IV. Calidad automatizada | `CI` con pre-commit, `ty` y pytest en cada PR; 5 migraciones con un solo *head* |
| V. Captura responsable | `robots.txt`, rate limit por host, guard SSRF con DNS fijado y límite de 24 URLs por ejecución |
| VI. Cambios por Pull Request | Todo entró por PR a `dev`; `main` solo recibe releases aprobados |

**Violaciones conocidas y aceptadas**: el despliegue quedó por IP y HTTP, sin dominio ni TLS; la
autenticación por usuario está aplazada a la ola 4 y hoy solo existe cuota por visitante
anónimo; la captura programada por cron no se entregó.

## Olas entregadas

| Ola | Contenido | Cierre | Evidencia |
|---|---|---|---|
| 0 | Mover el paquete, extraer dominio, configuración tipada y puertos | 2026-11-08 | PRs #33, #35, #36, #37, #38 |
| 1 | Postgres con pgvector, esquema Alembic, versiones, repositorios, carga de Gold, `migrate-gold` e importador idempotente | 2026-11-10 | PRs #39, #50, #53, #54, #55, #56, #60 |
| 2 | `PgVectorStore`, puerto `LLMProvider` con adaptadores, caso de uso `AskQuestion`, shell FastAPI con `/health` y `/ask` | 2026-11-12 | §9 (2026-11-11); PRs #64, #65, #66 |
| 3 | Pantallas de corpus y documento, identidad de visitante, chat persistente, cuota diaria, SSE, Qdrant, agente LangGraph, herramienta de indicadores, RAG híbrido, SPA, seguridad y evaluación del retrieval | 2026-11-13 | §9 (2026-11-13); migraciones 0003–0005 |
| 3b | Chat HTMX con hilos, Configuración, sistema visual, escala accesible, captura y carga públicas, FAQ por lotes | 2026-11-14 | §9 (2026-11-14); PRs #114–#120 |
| 6 | Imagen Docker multi-etapa, `/live` separado de `/health`, compose de producción y runbook de despliegue | 2026-11-13 | §9 (2026-11-13); `docs/despliegue-ovh.md` |

## Decisiones de diseño no obvias

- **Handlers diferidos**: importar la aplicación no abre conexiones ni crea clientes; el
  embedder y el LLM se construyen en la primera pregunta.
- **`/live` no toca la base**: la vitalidad del proceso no depende de Postgres; `/health` es el
  *readiness* con conteos y causa.
- **La identidad no es autenticación**: hash de 256 bits en el navegador, cookie firmada como
  respaldo y `owner_key` derivado por HMAC. Solo separa conversaciones anónimas.
- **Qdrant para vectores, Postgres para el resto**: documentos, fragmentos, chat, checkpoints y
  Store siguen en Postgres; el índice se construye aparte con `carvajal index-qdrant`.
- **Persistencia exacta del stream**: una respuesta se guarda una sola vez; si el stream falla
  antes de emitir, se completa por la vía normal y se marca `incomplete` cuando se corta.
- **Checkpointer fuera de la transacción**: `setup()` se inicializa antes de abrir la
  transacción de la petición para no bloquear el índice concurrente.
- **Cuota antes del LLM**: la reserva diaria es atómica y un fallo de base responde 503; el
  límite se evalúa antes de gastar tokens.
- **Dos frontends, un solo RAG**: el shell HTMX y la SPA consumen los mismos casos de uso y el
  mismo Markdown saneado; no hay lógica duplicada.
- **Cinco migraciones aditivas**: 0001 esquema, 0002 embeddings, 0003 conversaciones, 0004
  cuota, 0005 estado e idempotencia del turno.

## Desviaciones respecto del borrador original

| Borrador (2026-11-08) | Entregado (2026-11-14) |
|---|---|
| «No hay SPA» | Hay frontend propio con pestañas; el shell HTMX se conserva como compatibilidad |
| «El índice vectorial vive en la misma base» | Qdrant como índice del M2; pgvector queda como adaptador heredado |
| «La captura nunca se ejecuta desde la interfaz» | Captura web y carga de documentos desde Configuración |
| «La demo se protege con una clave» | Gate eliminado el 2026-11-14; defensa por límite por IP, guard SSRF y tamaños |
| HTTPS en el despliegue | Demo por IP y HTTP; TLS queda para cuando haya dominio |

## Deuda conocida y pendiente

- Autenticación y cuotas por usuario (ola 4): `user_id` se añadirá de forma aditiva.
- Retiro de Streamlit y FAISS (ola 7): siguen en `pyproject.toml` como extras.
- Captura programada por cron: no entregada.
- Endurecimiento del despliegue: TLS, dominio y respaldos automatizados.
- Ver las specs futuras del inventario: [`specs/README.md`](../README.md) §7 (029–034).
