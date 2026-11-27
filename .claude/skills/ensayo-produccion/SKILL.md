---
name: ensayo-produccion
description: Corre todas las pruebas contra producción de Carvajal (ensayo completo, pedidos reales en Odoo con foto, texto y voz, exploración de ~30 guiones por web y WhatsApp, WhatsApp real desde el Firefox del owner con foto y nota de voz grabada, suites locales y retrieval contra el Qdrant de prod), reúne las métricas (logs, Jev, compuerta, LangSmith, Qdrant, DeepSeek, OpenClaw), analiza las transcripciones y entrega un informe en PDF. Úsala cuando pidan "corre todas las pruebas contra prod", "ensayo general", "prueba con WhatsApp real", "informe de pruebas en PDF" o tras desplegar un cambio grande antes de una demo o entrega.
---

# Ensayo contra producción con informe

El detalle de cada prueba está en [`docs/ensayo.md`](../../../docs/ensayo.md); esta skill es el
orden de ejecución y lo que se aprendió corriéndolo. Si discrepan, manda `docs/ensayo.md`.

## Reglas

- **Nunca imprimas secretos ni números de teléfono.** Los comandos remotos leen las claves dentro
  del contenedor; todo lo que se lee del gateway o de WhatsApp Web pasa por `redactar`.
- **Producción solo se lee**, salvo dos excepciones conocidas: los pedidos reales de
  `make ensayo-pedido` (compradores `@example.com`) y `make ensayo-whatsapp-real`, que agrega el
  número de WhatsApp Web a `allowFrom` solo durante la prueba. No toques `groupPolicy`, las
  `bindings` ni el grupo: si `make ensayo` marca deriva del gateway, **repórtala**, no la
  "arregles" (otra persona puede estar configurándolo).
- **No escribas en el grupo de WhatsApp** ni a otro número que no sea el bot.
- Trabaja en un worktree propio en `origin/main` (`.worktrees/<nombre>`) con el
  `.env.ensayo` copiado: `test_operacion` exige el checkout igual a lo desplegado.
- Cada llamada al shell empieza sin variables: todo comando que use `$ENSAYO_*` (o un `pytest`
  directo) va precedido de `set -a && . ./.env.ensayo && set +a &&` en la **misma** línea. Los
  objetivos `make ensayo*` ya lo cargan solos; sin `ENSAYO_BASE_URL`, `pytest` apunta a
  `127.0.0.1`.
- `make eval-agent` y los jueces LLM de `eval-router`/`eval-gatekeeper` gastan DeepSeek; si el
  clasificador de permisos los bloquea, no lo rodees: dilo en el informe.

## 1. Prerrequisitos (dos minutos)

```bash
git fetch -q origin && git worktree add --detach .worktrees/ensayo origin/main
cp <checkout principal>/.env.ensayo .worktrees/ensayo/ && cd .worktrees/ensayo && uv sync --all-extras
set -a; . ./.env.ensayo; set +a
ssh -o BatchMode=yes -p "$ENSAYO_APP_SSH_PORT" "$ENSAYO_APP_SSH" 'cat /opt/carvajal/.deployed_tag'  # = origin/main
curl -s "$ENSAYO_BASE_URL/health"            # status ok, jev, odoo, food_photo
ss -ltn | grep 2828                           # Firefox con --marionette (WhatsApp real)
which ffmpeg pw-loopback pw-play pw-cli wpctl # voz grabada (PipeWire)
```

Guarda `/health` y `/metricas` **antes** (la página es pública; extrae su texto) y la hora de
inicio en UTC: los logs y LangSmith se filtran desde ahí.

## 2. Corridas en paralelo (cada una con su `ENSAYO_RUN_ID`)

En segundo plano, a la vez:

```bash
make ensayo-completo ENSAYO_RUN_ID=<x>completo                      # ~9 min; incluye WhatsApp real (allowlist)
make ensayo-pedido ENSAYO_RUN_ID=<x>pedido                          # 4 pedidos reales en Odoo
make ensayo-exploracion ENSAYO_RUN_ID=<x>explora ENSAYO_EXPLORA_DIR=<dir>/exploracion
set -a && . ./.env.ensayo && set +a && ENSAYO_COTIZAR=todas ENSAYO_PEDIDO=1 \
  ENSAYO_RUN_ID=<x>expedido ENSAYO_EXPLORA_DIR=<dir>/exploracion-pedido uv run pytest tests/e2e/test_exploracion.py -m exploracion -k explorar_pedido -q -s -rA -p no:cacheprovider
make check; make eval-cotizador; env -u LLM_PROVIDER uv run carvajal eval-router --output <dir>/router.md
uv run carvajal eval-gatekeeper --judge rules --output <dir>/gatekeeper.md; node --test tests/js/*.cjs
```

- La exploración normal **salta** 8 guiones del pedido en producción (`ENSAYO_COTIZAR=una`): por
  eso la segunda línea los corre con folios. `pedido_creado` crea 2 pedidos más.
- `informe.md` vacío en la exploración significa **cero alertas**, no un fallo.

## 3. WhatsApp real con foto y voz (después de `ensayo-completo`)

`ensayo-completo` comprueba que el número de Firefox **no** recibe respuesta y que el gateway no
tiene deriva; corre la conversación real **después**:

```bash
make ensayo-whatsapp-real ENSAYO_RUN_ID=<x>wa   # texto, foto del ajiaco, nota de voz grabada, «cancela todo»
```

Deja `data/ensayo-whatsapp/<run_id>/conversacion.md`. Lo que ya resuelve la prueba
(`tests/e2e/whatsapp_web.py`), por si hay que depurarla:

- Las respuestas se reconocen por `data-id` nuevos (WhatsApp Web virtualiza la lista) y por la
  cola `tail-in` o el prefijo del agente («[Carva]»).
- El Firefox de snap no lee `/tmp`: los adjuntos se copian a `data/ensayo-whatsapp/medios`.
- La voz se graba con `pw-loopback` (micrófono virtual) y la fuente por defecto se restaura.
- `allowFrom` se restaura quitando **solo** el número de prueba de la lista vigente.
- Un mensaje real tarda ~8 s en llegar al MCP; la prueba espera hasta 240 s por paso.

Si una respuesta sale de «[main] …» en vez del agente de Carvajal, las `bindings` del gateway
no mandan ese DM al agente de ventas: es configuración, repórtalo.

## 4. Métricas

```bash
set -a && . ./.env.ensayo && set +a && INI=<hora de inicio UTC> && for c in app:web mcp:whatsapp; do
  ssh -o BatchMode=yes -p "$ENSAYO_APP_SSH_PORT" "$ENSAYO_APP_SSH" \
    "docker logs --since $INI carvajal-prod-${c%%:*}-1 2>&1" \
    | python3 -I .claude/skills/ensayo-produccion/scripts/agregar_logs.py ${c##*:} > <dir>/logs-${c##*:}.md
done
```

- **LangSmith** (dentro del contenedor, sin imprimir la clave): `list_runs(project_name=…,
  start_time=INI, is_root=True)` con `itertools.islice` (el límite por página es 100) → trazas,
  errores, p50/p95/p99 y tokens. El panel de `/metricas` usa caché (24 h ~2 min, 7 días ~10 min).
- **Qdrant de prod**: no está publicado; túnel de solo lectura al IP del contenedor
  (`docker inspect … carvajal-prod-qdrant-1`) y `QDRANT_URL=http://127.0.0.1:<puerto>
  uv run python scripts/eval_retrieval.py`. Cierra el túnel con `kill <pid>` (nunca `pkill -f`
  con el patrón: mata tu propio shell).
- `/health` y `/metricas` **después**: Jev (llamadas, p50/p95, cupo), compuerta (juzgadas,
  sustituidas, histograma), DeepSeek (llamadas, tokens, costo), MCP (mensajes, fotos, notas de
  voz, PDF, rechazos).

## 5. Análisis de las transcripciones

Las ~68 transcripciones (`exploracion/` y `exploracion-pedido/`) se leen con un workflow de
lectores por grupos de guiones (web y WhatsApp juntos) y verificadores escépticos que
releen la cita y buscan la causa en el código. Prompts, esquema y grupos en
[`analisis.md`](analisis.md). Revisa tú mismo los hallazgos de gravedad alta antes de
publicarlos.

## 6. Informe en PDF

HTML con CSS de impresión (A4, `print-color-adjust: exact`) y Chrome headless:

```bash
google-chrome --headless=new --no-pdf-header-footer --print-to-pdf=<dir>/informe.pdf file://<dir>/informe.html
pdftoppm -r 60 -png <dir>/informe.pdf <dir>/png/p   # revisa las páginas antes de entregar
```

Contenido mínimo: veredicto y KPIs; tabla de suites con resultados; WhatsApp real paso a paso;
métricas (tráfico y latencia por ruta, Jev y compuerta con histograma, herramientas, Qdrant,
DeepSeek, OpenClaw/MCP); comparación con el ensayo anterior; evaluación técnica por área;
hallazgos priorizados con causa y arreglo; pendientes operativos (los **pedidos creados en
Odoo** por número, para cancelarlos); lo que no se pudo correr y por qué. Cuidado con `$` en
heredocs del shell: escapa los precios o escribe los datos con Python.

Copia el PDF y las transcripciones a `data/ensayo-exploracion/<carpeta>/` (ignorada por git) y
entrega las rutas completas.
