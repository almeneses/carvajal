# Monitoreo del agente con LangSmith

> Spec 038. Trazas por turno, experimentos de evaluación y marcas del visitante.
> Complementa a Sentry (errores de la aplicación) y a los logs JSON (operación).

## Qué se envía y qué no

| Entorno | Contenido | Cuándo |
|---|---|---|
| Producción | Solo metadatos: ruta, enrutador, pasos, tokens, latencias, `slug`, `chunk_id`, `score`, `error_type` | `MONITORING_INCLUDE_CONTENT=false` (por defecto) |
| Desarrollo y evaluación | Pregunta, respuesta y fragmentos completos | `MONITORING_INCLUDE_CONTENT=true` |

En ningún entorno viajan la IP, las cookies, los tokens CSRF, las claves de API,
el `owner_key` ni el identificador crudo del visitante. El hilo se representa con
`thread_hash`: los primeros 16 hex del SHA-256 de su identificador.

Los errores se envían **solo como tipo** (`ValueError`, `TimeoutError`, …): el
mensaje de una excepción puede arrastrar texto del visitante o cabeceras de una
petición, así que no se sube.

## Encendido y apagado

```dotenv
MONITORING_ENABLED=true
LANGSMITH_API_KEY=lsv2_pt_…
LANGSMITH_PROJECT=carvajal
MONITORING_SAMPLE_RATE=0.2
MONITORING_INCLUDE_CONTENT=false
```

- **Apagar sin tocar el chat**: `MONITORING_ENABLED=false` y reiniciar. No hay
  que cambiar código ni variables del agente; sin monitoreo no se construye
  cliente ni se abre un socket, y el chat responde igual.
- `MONITORING_SAMPLE_RATE=0` equivale a apagado.
- `LANGSMITH_ENDPOINT` solo se usa para un servicio compatible o pruebas.
- El arranque fuerza `LANGSMITH_TRACING` y `LANGCHAIN_TRACING_V2` a `false`:
  el encendido es explícito y por turno, nunca global.

Un fallo del servicio (caído, lento o sin cuota) no altera la respuesta, la
persistencia ni la cuota del chat; queda en los logs como `monitoring.failed`.

## Plan y presupuesto

- El plan gratuito de LangSmith incluye **5.000 trazas al mes**.
- Con muestreo del **20 %** se cubren hasta **25.000 turnos al mes**; 1,0 solo en
  local y en evaluación.
- Una corrida de evaluación del enrutador sobre las 110 preguntas consume unas
  110 trazas y la de recuperación una por consulta.
- Si el consumo se acerca a 4.000 trazas en el mes, baja `MONITORING_SAMPLE_RATE`
  a `0.1` o apaga el monitoreo; el límite no se toca desde el código.

## Buscar un turno

1. Toma el `request_id` del evento `done` del stream o de los logs JSON
   (`request_id` está en cada línea de la petición).
2. En LangSmith, filtra el proyecto por ese `request_id` en los metadatos de la
   raíz `chat_turn`.
3. Para revisar un hilo completo, calcula `thread_hash`:

   ```bash
   python -c "import hashlib; print(hashlib.sha256(b'<thread_id>').hexdigest()[:16])"
   ```

## Experimento de evaluación

```bash
uv run carvajal eval-router --router rules --router llm --langsmith
uv run python scripts/eval_retrieval.py --langsmith
```

Cada corrida crea o reutiliza un dataset (`carvajal-<archivo de preguntas>`) y
registra un experimento con una fila por pregunta enlazada a su ejemplo, más los
agregados en los metadatos del experimento. En LangSmith, *Compare* alinea las
filas por pregunta y muestra qué cambió entre dos corridas. Sin `--langsmith` el
informe local se genera igual y no se toca la red. Ver
[`resultados-router.md`](resultados-router.md).

## Marcas del visitante

Con el monitoreo activo, cada respuesta ofrece «Útil / No útil». La marca se
guarda en `chat_feedback` con el `request_id` del turno (migración `0007`), sin
texto y sin forzar el muestreo: si el turno no se trazó, la marca se conserva
igual. Con el monitoreo apagado los botones no se muestran.

## Diagnóstico

`GET /health` incluye:

```json
"monitoring": {"enabled": true, "sample_rate": 0.2, "content": "metadata"}
```

Nunca incluye la llave ni el endpoint. Si `enabled` es `false` con
`MONITORING_ENABLED=true`, revisa `LANGSMITH_API_KEY` y el rango del muestreo:
el arranque lo avisa en los logs.
