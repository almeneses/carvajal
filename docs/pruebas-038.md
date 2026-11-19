# Pruebas del monitoreo (spec 038): smoke y end-to-end

> Complementa `make check` (unitarias, sin red ni credenciales) con dos suites
> que sí tocan el mundo real. Ninguna se ejecuta por defecto.

## Qué hay y cuándo usarlas

| Suite | Comando | Tiempo | Necesita | Qué comprueba |
|---|---|---|---|---|
| Unitaria (`make check`) | `make check` | ~17 s | nada | Contratos del módulo, trazas con dobles, marcas, experimentos, `/health` |
| **Smoke** | `make smoke` | ~3 s | nada (opcional: llave para validarla) | La app arranca, `/live` y `/health`, un turno publica `request_id` y deja raíz, la traza no lleva texto en producción, las marcas exigen CSRF |
| **End-to-end** | `make e2e` | ~70 s | PostgreSQL + Qdrant + proveedor de LLM + LangSmith | Las cuatro rutas reales dejan traza, sin PII, con tokens, y **la corrida se registra como experimento** con métricas; además mide SC-003 con el servicio caído |

Marcadores de pytest: `smoke` y `e2e`. `make test` y `make check` los excluyen
(`-m "not integration and not e2e and not smoke"`). El CI ejecuta las unitarias
(`not integration and not e2e`) y **además el smoke** en un paso propio, porque
es hermético y dura 3 s; el `e2e` nunca corre en CI (necesita credenciales y
servicios reales).

## Smoke (`tests/test_monitoring_smoke.py`)

```bash
make smoke
# 5 passed, 1269 deselected
```

Qué valida, caso por caso:

1. **`/live` y `/health`**: la app arranca y el bloque `monitoring` tiene
   `{enabled, sample_rate, content}` sin filtrar la llave.
2. **Turno completo**: un turno de la ruta `tool` produce una raíz `chat_turn`
   (con `router`, `request_id`, `thread_hash` de 16 hex) más el hijo `llm`, y el
   navegador recibe `request_id` en el evento `done`.
3. **Producción sin texto**: con `MONITORING_INCLUDE_CONTENT=false` el payload
   enviado no contiene la pregunta ni la respuesta; `inputs` de la raíz es `{}`.
4. **Marcas**: `POST /chat/feedback` responde 403 sin CSRF y 204 con él.
5. **Credencial** (si hay `LANGSMITH_API_KEY`): el proyecto configurado existe y
   responde; si no hay llave, el caso se omite.

El doble de cliente aplica `redact_run`, igual que el cliente real, para que la
prueba de privacidad mida lo que realmente viaja. La salud y las marcas se
inyectan (`Handlers` y `app.state.feedback`), así que no necesita PostgreSQL y
puede correr en CI.

## End-to-end (`tests/test_monitoring_e2e.py`)

```bash
make e2e
# 2 passed in ~70 s
```

Requisitos: `.env` con `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`,
`LLM_PROVIDER=deepseek` y `DEEPSEEK_API_KEY`; PostgreSQL y Qdrant levantados. La
ruta `rag` necesita Ollama con `nomic-embed-text`: si no está, ese caso se
registra como «sin respuesta» y la prueba lo informa, sin ocultar el resto.

**Prueba 1 — cuatro rutas + métricas.** Envía una pregunta por ruta (`direct`,
`rag`, `tool`, `commercial`) con `MONITORING_SAMPLE_RATE=1.0` y
`MONITORING_INCLUDE_CONTENT=false`, espera la ingesta (es asíncrona) y, por cada
`request_id`, comprueba en LangSmith:

- existe la raíz `chat_turn` y tiene `route`;
- la traza completa no contiene nombre, teléfono, `owner_key` ni `thread_id`;
- los tokens se cuentan en los runs del modelo (no en el marcador `llm`).

Después **registra un experimento** en el dataset `carvajal-monitoreo-e2e` con
una fila por ruta (`route`, `trace`, `runs`, `tokens`, `pii_free`, latencia al
primer fragmento) y estos agregados en los metadatos del experimento:

```json
{"rutas_con_traza": 4, "rutas_totales": 4, "trazas_con_pii": 0,
 "tokens_totales": 7033, "ttft_p95_ms": 59.1}
```

Cada corrida crea un experimento `monitoreo-<fecha>` en el mismo dataset, así que
en LangSmith puedes abrir *Compare* y ver la evolución (tokens, latencia, PII,
rutas cubiertas) entre fechas.

**Prueba 2 — SC-003.** Veinte turnos con `LANGSMITH_ENDPOINT` inalcanzable y
otros veinte con el monitoreo apagado, con calentamiento previo, y exige que el
p95 al primer fragmento no empeore más de 100 ms.

## Cómo leer los resultados

- **Local**: `make smoke` antes de tocar el monitoreo; `make e2e` antes de una
  demo o al cambiar el modelo, el enrutador o el modo de contenido.
- **LangSmith**: proyecto `carvajal` para las trazas del chat; dataset
  `carvajal-monitoreo-e2e` para comparar corridas del e2e.
- **Si el e2e falla por timeout de ingesta**, sube `espera_s` en `_traza()`
  (por defecto 20 s); no es un fallo del monitoreo.
- **Verás avisos `Failed to multipart ingest`** en la salida de la prueba de
  SC-003: los escribe el SDK por cada envío al endpoint inalcanzable. Es
  exactamente lo que la prueba provoca; no es un fallo.

## Por qué no están en `make check`

El CI no tiene credenciales ni servicios externos (FR-010). Estas suites existen
para verificar el comportamiento real —incluida la latencia con el servicio
caído— sin convertir el flujo diario en algo caro o dependiente de la red.
