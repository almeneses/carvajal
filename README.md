# Carvajal — Asistente comercial con RAG, herramientas y WhatsApp

[![CI](https://github.com/almeneses/carvajal/actions/workflows/ci.yml/badge.svg)](https://github.com/almeneses/carvajal/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://docs.astral.sh/uv/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://docs.astral.sh/ruff/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> Asistente de **Carvajal** que responde con fuentes citadas sobre la empresa, recomienda y
> cotiza empaques con precios fechados y de fuente declarada, y atiende por la web y por **WhatsApp** con el
> mismo agente.

## Contenido

1. [Qué es](#qué-es)
2. [Demo rápida](#demo-rápida)
3. [Arquitectura](#arquitectura)
4. [Requisitos de la entrega cubiertos](#requisitos-de-la-entrega-cubiertos)
5. [Instalación](#instalación)
6. [Uso](#uso)
7. [Despliegue](#despliegue)
8. [Evaluación](#evaluación)
9. [Configuración](#configuración)
10. [Corpus y datos](#corpus-y-datos)
11. [Estructura](#estructura)
12. [Documentación](#documentación)
13. [Licencia y créditos](#licencia-y-créditos)

## Qué es

**Problema.** Responder preguntas sobre una empresa y cotizar sus productos exige datos
fiables: la información de Carvajal está dispersa en el sitio corporativo, en PDFs, en
prensa y en tiendas de terceros, y un modelo de lenguaje sin contexto inventa cifras,
precios y sedes.

**Solución.** Un agente LangGraph que **decide qué consultar** entre 20 herramientas
deterministas (corpus, catálogo, precios, sedes, indicadores, historia, memoria…), redacta
con un LLM en la nube (DeepSeek) y pasa cada respuesta por **compuertas de evidencia**: sin
datos consultados no hay afirmación, y lo que no está respaldado se sustituye por un texto
neutro. El mismo turno se sirve por el chat web (FastAPI + HTMX con SSE), por un servidor
MCP propio que usa OpenClaw como gateway de WhatsApp y por un webhook firmado.

| Pieza | Qué aporta |
|---|---|
| Corpus citable | `data/gold/` (núcleo curado) más capas extendidas, sincronizadas con PostgreSQL y Qdrant |
| Agente con herramientas | 20 consultas `@tool` con esquema Pydantic; DeepSeek elige y encadena |
| Router y System One (Jev) | Reglas primero, gatekeeper Jev Choice, compuerta Noul de afirmaciones |
| Memoria | Resumen acumulado de la conversación y memoria del dueño por categorías, con olvido |
| Canales | Chat web, WhatsApp (OpenClaw → MCP) y webhook HMAC con allowlist E.164 |
| Voz | Dictado y lectura en el chat (navegador u OpenAI por el servidor) y notas de voz en WhatsApp |
| Cotizaciones | Carrito de hasta 10 líneas, propuesta con IVA y envío y «¿La confirmo?»; con el «sí», folio y PDF firmado descargable |
| Pedidos | Tras el folio: nombre y correo, «¿Lo confirmo?», pedido de venta confirmado en Odoo 19 con correo de confirmación y recibo en PDF por la web y WhatsApp (spec 056) |

## Demo rápida

Solo necesita Docker y una clave de DeepSeek:

```bash
git clone https://github.com/almeneses/carvajal.git
cd carvajal
cp .env.example .env          # completa DEEPSEEK_API_KEY (el .env no se versiona)
make up                       # app + Postgres + Qdrant + Ollama (embeddings) + corpus
# abre http://localhost:8000/chat
make down                     # detiene el stack conservando los volúmenes
```

Preguntas para probar en el chat (añade `?debug=1` a la URL para ver la traza del turno:
herramientas usadas, decisión del gatekeeper y veredicto de la compuerta):

| Mensaje | Qué se ve |
|---|---|
| «hola» | Atajo por reglas: responde sin consultar nada y sin llamar a Jev |
| «¿En qué año y dónde se fundó Carvajal?» | `historia_carvajal` / `buscar_corpus`, respuesta con citas |
| «Necesito 2 mil vasitos para tinto en Bogotá» | `buscar_producto` + `cotizar`: propuesta con IVA, envío y sedes, y «¿La confirmo?»; con «sí», folio y PDF |
| «¿Mi cotización sigue vigente?» | `consultar_cotizacion` sobre el folio guardado en Postgres |
| «Llámame Ana, trabajo en una cafetería en Cali» | `recordar_preferencia`; en otro hilo, «¿qué sabes de mí?» lo recupera |
| «Olvida todo lo que sabes de mí» | Borra la memoria del dueño y el resumen |
| «¿Cuál es el teléfono de Carvajal en Tokio?» | Abstención declarada: el corpus no lo trae |

Por WhatsApp, el mismo flujo responde desde un número real a través de OpenClaw (ver
[WhatsApp y MCP](#whatsapp-servidor-mcp-y-webhook)); al confirmar la cotización con «sí», el
PDF llega adjunto en el mismo mensaje.

## Arquitectura

Los diagramas se generan con [Archify](https://github.com/tt-a1i/archify). La fuente tipada
de cada uno es un JSON de [`docs/arquitectura/`](docs/arquitectura/). Cada componente cita
archivos y líneas del commit `c730180`, y Archify comprueba esas citas al generar el
diagrama. La imagen es el SVG exportado; al hacer clic se abre el diagrama interactivo
(`.html`). Ese diagrama ofrece tema claro y oscuro, la explicación de cada nodo, las
condiciones de activación y los enlaces al código. GitHub muestra el código fuente del
`.html`: para verlo como diagrama, descárgalo o ábrelo desde una copia local del
repositorio.

[![Arquitectura general de Carvajal](docs/arquitectura/general.svg)](docs/arquitectura/general.html)

El chat web entra por FastAPI. WhatsApp entra por OpenClaw y el servidor MCP propio, o por el
webhook firmado como camino alterno. Todos llegan al mismo `ChatService`. Este consulta
primero el flujo de cotización y pedido (reglas y, con `TYPESAFE_API_KEY`, Jev Choice). Si
ese flujo no decide, llama al agente LangGraph con sus 20 herramientas. Las flechas punteadas
son caminos condicionados:
- voz con `VOICE_PROVIDER=openai`;
- LangSmith con `MONITORING_ENABLED`;
- Jev en las compuertas con `JEV_ENABLED`;
- pedido en Odoo con `ODOO_*`.

El diagrama muestra el modo `AGENT_MODE=tools`. En la versión interactiva, cada componente
con diagrama propio enlaza con él.

Flujo de un turno:

1. **Entrada.** La web envía la pregunta por `POST /chat/stream` (SSE); WhatsApp llega por
   OpenClaw al servidor MCP propio o por el webhook firmado. Los tres caminos ejecutan el
   mismo `ChatService` ([`apps/api/chat/service.py`](apps/api/chat/service.py)), que cobra el
   cupo diario, abre o recupera el hilo y persiste el turno.
2. **`recall`** arma el contexto: la ventana de turnos recientes, el resumen acumulado de
   los anteriores y la memoria del dueño ([`agent/toolagent/graph.py`](packages/core/src/carvajal/agent/toolagent/graph.py)).
3. **`shortcut` / gatekeeper.** Las reglas deciden primero: saludos, despedidas y «olvida
   todo» van a `direct` sin consultar. Si no deciden, Jev clasifica el turno y limita las
   herramientas disponibles; solo una `respuesta_directa` con confianza ≥ 0,8 va a `direct`
   ([`agent/gatekeeper/decide.py`](packages/core/src/carvajal/agent/gatekeeper/decide.py)).
4. **`agent` ⇄ `tools`.** DeepSeek elige y encadena herramientas (máximo
   `AGENT_MAX_TOOL_CALLS`, 4 por defecto); cada consulta publica una línea de estado
   («Estoy calculando cuánto te salen 2.000 vasos en Bogotá…») y registra sus fuentes.
5. **Compuertas.** La compuerta determinista abstiene el turno si ninguna consulta dio
   evidencia; después, la compuerta Noul de Jev sustituye por el texto neutro la respuesta
   con afirmaciones no respaldadas ([`apps/api/chat/agent.py`](apps/api/chat/agent.py)). La
   respuesta de `direct` no consulta datos y Noul no la juzga.
6. **Salida.** En la web: texto con citas y tarjetas de producto y de cotización. En
   WhatsApp: texto con formato de WhatsApp y fuentes; al confirmar una cotización, su PDF
   va adjunto.
7. **Pedido (opcional).** Con `ODOO_*`, el folio ofrece hacer el pedido. Los pasos del
   pedido (nombre y correo, «¿Lo confirmo?» y el «sí») los deciden reglas por paso
   (`quotation/flow.py`), sin el modelo ni Jev, y el «sí» crea el pedido en Odoo y devuelve
   el recibo
   ([`docs/odoo-pedidos.md`](docs/odoo-pedidos.md)).


### Componentes

| Diagrama | Qué responde | Tipo |
|---|---|---|
| [Turno del agente](docs/arquitectura/turno.html) | Qué pasa con un mensaje desde que entra hasta que se responde | Flujo |
| [WhatsApp](docs/arquitectura/whatsapp.html) | Cómo viaja un mensaje entre WhatsApp, OpenClaw, el MCP y `ChatService` | Secuencia |
| [Pedido en Odoo](docs/arquitectura/pedido.html) | Por qué estados pasa el pedido después del folio | Ciclo de vida |
| [Corpus](docs/arquitectura/corpus.html) | Cómo llega el sitio web a Qdrant, a PostgreSQL y a las respuestas | Flujo de datos |
| [Despliegue](docs/arquitectura/despliegue.html) | Cómo llega el código a producción y qué corre en cada máquina | Arquitectura |

#### Turno del agente

[![Turno del agente con herramientas](docs/arquitectura/turno.svg)](docs/arquitectura/turno.html)

Muestra un turno con `AGENT_MODE=tools`, de la entrada (web o WhatsApp) a la salida. Las
flechas verdes son el camino principal:
1. hilo del dueño;
2. flujo de cotización y pedido;
3. `recall`;
4. gatekeeper;
5. `agent` ⇄ `tools`;
6. compuerta de evidencia;
7. compuerta Noul;
8. guardado y salida.

Hay tres desvíos:
- el gatekeeper deriva a `direct`;
- el flujo responde sin LLM;
- si el agente falla antes del primer token, responde el respaldo `routes` (línea
  discontinua).

#### WhatsApp de punta a punta

[![Mensaje de WhatsApp de punta a punta](docs/arquitectura/whatsapp.svg)](docs/arquitectura/whatsapp.html)

El cliente escribe en el grupo, y OpenClaw (agente «ventas») atiende solo a los números de
`groupAllowFrom`. OpenClaw llama a `consultar_carvajal` en el servidor MCP con el Bearer
`MCP_TOKEN`. `WhatsAppBridge` calcula el `owner_key` y cobra el cupo. Si piden «PDF» o el
recibo y no hay una cotización por confirmar, responde sin turno. En los demás casos ejecuta
el mismo turno de `ChatService` que la web. Cuando la respuesta trae `ADJUNTAR_PDF:`, OpenClaw
descarga el documento y lo adjunta.

#### Pedido en Odoo

[![Ciclo de vida del pedido en Odoo](docs/arquitectura/pedido.svg)](docs/arquitectura/pedido.html)

Solo se ofrece con `ODOO_URL`, `ODOO_DB` y `ODOO_API_KEY`. El camino feliz es:
1. oferta;
2. datos del comprador;
3. «¿Lo confirmo?»;
4. pedido confirmado en Odoo;
5. recibo con el PDF.

Los tres pasos del marco punteado los deciden reglas, sin el modelo. Desde ese marco se
puede salir:
- por un rechazo, que se retoma en el mensaje siguiente;
- por otro producto;
- por un segundo mensaje ajeno al pedido.

Si Odoo falla, el «sí» reintenta. Con precios vencidos no se llama a Odoo.

#### Corpus: del sitio web a las respuestas

[![Flujo de datos del corpus](docs/arquitectura/corpus.svg)](docs/arquitectura/corpus.html)

- **Captura.** `carvajal capture` descarga las semillas: respeta `robots.txt`, espera 1 s
  por host y escribe Bronze, Silver y Gold.
- **Cargas del operador.** Lo que se sube en Configuración queda en Gold sin publicar.
- **Sincronización.** `carvajal sync` lleva a PostgreSQL y a Qdrant solo lo que cambió, con
  embeddings de Ollama.
- **Consumo.** El agente busca en Qdrant (primero en el núcleo) y lee los datos curados de
  `data/`. PostgreSQL alimenta la vista `/corpus` y las citas del chat.

#### Despliegue

[![Despliegue en producción](docs/arquitectura/despliegue.svg)](docs/arquitectura/despliegue.html)

Una PR pasa por el check `Calidad` y se fusiona en `main`. Entonces `deploy.yml` publica la
imagen `sha-<commit>` en GHCR y entra por SSH al VPS. Allí, `deploy.sh` migra y recrea los
contenedores de `compose.prod.yml`. Si fallan la migración, `/live` o `/health`, vuelve al tag
anterior.

Las dos entradas son:
- el navegador, por el nginx del host;
- WhatsApp, por OpenClaw y el túnel SSH hasta `mcp`.

Odoo 19 vive en el mismo VPS ([`deploy/odoo/`](deploy/odoo/README.md)) y se despliega a mano.

#### Regenerar los diagramas

Edita el JSON y vuelve a generar el HTML y el SVG. `<archify>` es la carpeta `archify/` del
repositorio de Archify; el SVG necesita Chrome o Chromium.

```bash
node <archify>/bin/archify.mjs finalize <tipo> docs/arquitectura/<nombre>.json \
  docs/arquitectura/<nombre>.html --repo-root . --quality showcase --json
node <archify>/bin/export-svg.mjs docs/arquitectura/<nombre>.html /tmp/<nombre>.svg auto
cp /tmp/<nombre>.svg docs/arquitectura/<nombre>.svg   # export-svg no sobrescribe
```

Los tipos son:
- `architecture`: `general` y `despliegue`;
- `workflow`: `turno`;
- `sequence`: `whatsapp`;
- `lifecycle`: `pedido`;
- `dataflow`: `corpus`.

Si se cita código nuevo, actualiza también `meta.repository.revision`.

## Requisitos de la entrega cubiertos

| Requisito | Dónde se cumple | Evidencia |
|---|---|---|
| Modelo por API en la nube con clave y cupo | DeepSeek por defecto (`LLM_PROVIDER=deepseek`, `DEEPSEEK_API_KEY`), cupo `DAILY_REQUESTS_LIMIT` por visitante o chat | [`settings.py`](packages/core/src/carvajal/infrastructure/settings.py), [`.env.example`](.env.example) |
| ≥ 20 herramientas integradas | 20 `@tool` con `args_schema` Pydantic, visibles en la traza | [Herramientas](#20-herramientas-integradas), [`registry.py`](packages/core/src/carvajal/agent/toolagent/tools/registry.py) |
| Router y System One (Jev) | Reglas → Jev Choice → LLM; compuerta Noul; cautela con confianza baja | [Router](#router-y-system-one-jev), [`specs/046-jev-router/evidencia.md`](specs/046-jev-router/evidencia.md) |
| WhatsApp con número real | OpenClaw como gateway → servidor MCP propio → mismo `ChatService`; PDF de cotizaciones | [WhatsApp](#whatsapp-servidor-mcp-y-webhook), [`docs/whatsapp-openclaw.md`](docs/whatsapp-openclaw.md) |
| Workspace del agente de WhatsApp | `SOUL.md`, `IDENTITY.md`, `AGENTS.md`, `USER.md`, `MEMORY.md` y bitácora diaria `memory/AAAA-MM-DD.md` del agente «ventas» (Carva), versionados y desplegados en la VPS de OpenClaw | [`openclaw/`](openclaw/README.md) |
| Backend con webhooks y persistencia | FastAPI, webhook HMAC, PostgreSQL con `PostgresSaver` y `PostgresStore` | [Backend](#backend-fastapi-y-persistencia), [`docs/webhooks.md`](docs/webhooks.md) |
| Memoria de corto y largo plazo | Resumen acumulado + memoria del dueño por categorías, datos sensibles y olvido | [Memoria](#memoria-de-corto-y-largo-plazo), [`specs/045-memoria-herramientas/evidencia.md`](specs/045-memoria-herramientas/evidencia.md) |
| RAG con citas y evaluación | Qdrant con citas por fragmento; Recall@10/nDCG@10 y evaluación del agente | [Evaluación](#evaluación), [`docs/eval/`](docs/eval/) |
| Servidor MCP propio | `apps/mcp` (Streamable HTTP, Bearer, solo loopback) | [`apps/mcp/server.py`](apps/mcp/server.py) |
| Voz | Chat web: OpenAI por el servidor (`VOICE_PROVIDER=openai`) con respaldo en la voz del navegador; WhatsApp: `voz=true` con texto hablado corto | [Voz](#voz), [`docs/voz-web.md`](docs/voz-web.md), [`docs/whatsapp-openclaw.md`](docs/whatsapp-openclaw.md#mensajes-de-voz) |
| Fotos de alimentos | Foto en la web o WhatsApp → Jev (Noul, Choice y Score con imagen) → tabla determinista → una recomendada y hasta cuatro alternativas, sin LLM; se eligen por voz o texto y la cotización se confirma antes de registrarse (spec 053) | [Fotos](#fotos-de-alimentos), [`specs/051-fotos-alimentos/`](specs/051-fotos-alimentos/spec.md) |
| Seguridad | Compuertas de evidencia, filtro de sensibles, token MCP, firma, allowlist, errores sin trazas | [Seguridad](#seguridad) |
| Licencia, despliegue y reproducibilidad | MIT, guía de despliegue, `.env` fuera del repo, `uv sync`, `make check` y diagramas de Archify verificados contra el código | [Licencia](#licencia-y-créditos), [Despliegue](#despliegue), [Instalación](#instalación) |

### Modelo por API en la nube

- El chat y el agente con herramientas usan **DeepSeek** por API (`deepseek-flash`,
  evaluado en las specs 044–046) con la clave `DEEPSEEK_API_KEY` y temperatura 0. El modo
  `tools` arranca solo con `LLM_PROVIDER=deepseek` y la clave; si falta, la app lo avisa
  (`agent.tools_mode_unavailable`) y `/health` lo publica en el bloque `agent`.
- **Ollama** solo calcula los embeddings del corpus (`nomic-embed-text`, 768 dimensiones)
  para indexar y para buscar en Qdrant. Cada petición pide `keep_alive` de 24 h
  (`OLLAMA_EMBED_KEEP_ALIVE_S`), la app y el MCP cargan el modelo al arrancar en segundo
  plano (`EMBED_WARM_UP`) y las consultas repetidas salen de una caché LRU del proceso
  (`EMBED_CACHE_SIZE`, 512).
- **Cupo diario**: `DAILY_REQUESTS_LIMIT` (20 por defecto) por visitante anónimo de la web
  y por chat de WhatsApp, con medianoche en `QUOTA_TIMEZONE` (`America/Bogota`).
- Si DeepSeek falla o vence `AGENT_TURN_TIMEOUT_S`, responde un respaldo determinista sin
  LLM (evento `fallback`).

### 20 herramientas integradas

Cada herramienta se declara con `@tool(name, description=..., args_schema=<modelo
Pydantic>)` en [`tools/runner.py`](packages/core/src/carvajal/agent/toolagent/tools/runner.py)
a partir del registro único
[`tools/registry.py`](packages/core/src/carvajal/agent/toolagent/tools/registry.py). Son
deterministas, devuelven `{status, message, missing, ...datos}` y cada dato que entregan
aparece en el extracto de una fuente numerada `[n]`. Contratos completos:
[044](specs/044-agente-tool-calling/contracts/agent-tools.md) y
[045](specs/045-memoria-herramientas/contracts/agent-tools-045.md).

| # | Herramienta | Para qué sirve | Fuente de datos |
|---|---|---|---|
| 1 | `buscar_corpus` | Busca en los documentos públicos de Carvajal y devuelve fragmentos citables | Qdrant `carvajal_chunks` (núcleo `data/gold/` y capas extendidas), embeddings de Ollama |
| 2 | `buscar_producto` | Busca empaques por uso, temperatura, microondas, capacidad o nombre | [`data/catalog/empaques/`](data/catalog/empaques/) (`referencias.csv`, `materiales.csv`, `precios.csv`) |
| 3 | `cotizar` | Cotiza en Colombia producto, cantidad y ciudad con el precio publicado de un vendedor; emite folio | Precios del catálogo, [`data/commercial/proveedores.csv`](data/commercial/proveedores.csv), motor [`quotation/engine.py`](packages/core/src/carvajal/quotation/engine.py); guarda en Postgres `quotes` |
| 4 | `buscar_sedes` | Lista las sedes de distribuidores de una ciudad, con dirección y teléfono | `distribuidores.csv` y `data/commercial/proveedores.csv` |
| 5 | `indicador_financiero` | Da un indicador financiero publicado para un año o año-mes | [`data/indicators.json`](data/indicators.json) (prensa económica citada) |
| 6 | `sin_consulta` | Declara que el paso no consulta datos (saludo, dato faltante, fuera de alcance) | Ninguna |
| 7 | `ficha_producto` | Ficha de una referencia concreta: material, capacidad, presentación y precios | `referencias.csv`, `precios.csv`, `materiales.csv` |
| 8 | `comparar_referencias` | Compara 2 a 4 referencias lado a lado, con precio por unidad | `referencias.csv`, `precios.csv`, `materiales.csv` |
| 9 | `guia_materiales` | Temperatura, microondas y nivel de cada material por aplicación | `materiales.csv` y `manifest.json` del catálogo |
| 10 | `convertir_presentacion` | Convierte cajas, paquetes o pacas a unidades | Presentaciones de la ficha y de las ofertas publicadas |
| 11 | `ciudades_con_cobertura` | Ciudades con distribuidores y, con una referencia, dónde tiene precio | Directorio oficial de distribuidores (`distribuidores.csv`, `proveedores.csv`) |
| 12 | `consultar_cotizacion` | Lee una cotización emitida por folio o las últimas del hilo | PostgreSQL, tabla `quotes` (solo del mismo dueño) |
| 13 | `indicadores_disponibles` | Lista qué indicadores y períodos hay, sin valores | `data/indicators.json` |
| 14 | `historia_carvajal` | Hitos por año o rango de años, con texto literal | `data/gold/historia.md` |
| 15 | `empresas_del_grupo` | Unidades de negocio y marcas del grupo, por nombre o alias | [`data/institucional/empresas.json`](data/institucional/empresas.json) (cada fila con cita del núcleo) |
| 16 | `preguntas_frecuentes` | Prepara preguntas frecuentes sobre un tema, cada una verificada contra su pasaje | Corpus núcleo + LLM; solo respalda el pasaje literal |
| 17 | `fecha_actual` | Fecha de hoy en Colombia, día hábil, festivo y próximo día hábil | Reloj del servidor y [`quotation/calendar.py`](packages/core/src/carvajal/quotation/calendar.py) (fuente `sistema`) |
| 18 | `recordar_preferencia` | Guarda nombre, empresa, ciudad habitual, formato preferido o una nota | `PostgresStore` (memoria del dueño) |
| 19 | `listar_memoria` | Lista lo que el asistente recuerda de la persona | `PostgresStore` |
| 20 | `olvidar_preferencia` | Borra una categoría, una nota o toda la memoria | `PostgresStore` |

**Visibles en la traza.** Cada turno emite el evento SSE `trace` con `tool` (las
herramientas llamadas, en orden), `path`, `gatekeeper` y `claims`; el chat lo muestra con
`?debug=1`. La persona ve además la línea de estado de cada consulta (evento `status`). Los
logs JSON registran `tool_agent.turn_streamed` con la lista de herramientas, y con LangSmith
encendido cada llamada es un hijo `tool` del turno
([`docs/monitoreo-langsmith.md`](docs/monitoreo-langsmith.md)). En las herramientas de
memoria la traza lleva solo la categoría, nunca el valor.

Resultado medido: en el conjunto de 26 preguntas de herramienta y memoria, **25/26**
eligieron la consulta esperada (26/26 auditado), con 0 violaciones de material y 0 datos sin
fuente ([`specs/045-memoria-herramientas/evidencia.md`](specs/045-memoria-herramientas/evidencia.md) §4).

### Router y System One (Jev)

El gatekeeper corre en el nodo `shortcut`, antes del primer paso del modelo
([contrato](specs/046-jev-router/contracts/jev-gatekeeper.md),
[`agent/gatekeeper/`](packages/core/src/carvajal/agent/gatekeeper/)):

1. **Reglas primero** ([`rules.py`](packages/core/src/carvajal/agent/gatekeeper/rules.py)):
   saludos, capacidades, despedidas y «olvida todo» se responden por el atajo **sin buscar,
   sin herramientas y sin llamar a Jev**.
2. **Jev Choice**: si las reglas no deciden, una pregunta Choice de Jev (TypeSafe System
   One, `jev-1.14.0`) clasifica el turno en `herramienta_estructurada`, `rag`, `mixta` o
   `respuesta_directa`. La clase limita las herramientas del turno: `rag` solo ve las
   documentales, `herramienta_estructurada` las de catálogo, precios, sedes, cifras, fechas
   y memoria, y `mixta` todas ([`registry.py`](packages/core/src/carvajal/agent/toolagent/tools/registry.py),
   `TOOLS_BY_SCOPE`). Si Jev elige `rag` pero la pregunta nombra un producto, un precio, una
   cantidad o dónde comprar, el turno se trata como `mixta` para no perder el catálogo. Una
   `respuesta_directa` con confianza suficiente se responde por `direct`, sin herramientas.
3. **Confianza baja → cautela**: por debajo de `JEV_ROUTER_MIN_CONFIDENCE` (0,6), por
   debajo de `JEV_ROUTER_DIRECT_MIN_CONFIDENCE` (0,8) para la respuesta directa, o si Jev
   contradice una señal estructurada fuerte de las reglas, el turno ve las 20 herramientas y
   el bloque `<cautela>`: consulta antes de afirmar o pide la aclaración.
4. **Compuerta Noul**: después de la compuerta determinista, una pregunta Noul de Jev juzga
   si las afirmaciones de la respuesta están respaldadas por las consultas del turno; por
   debajo de `JEV_CLAIMS_THRESHOLD` (0,3) se sustituye por el texto neutro
   ([`claims.py`](packages/core/src/carvajal/agent/gatekeeper/claims.py)).
5. **Sin Jev → reglas + LLM**: sin clave, apagado, con el tope diario agotado, circuito
   abierto, plazo (`JEV_TIMEOUT_S`, 0,8 s) o error, el turno sigue con las reglas y DeepSeek
   eligiendo entre las 20 herramientas, y rige solo la compuerta determinista. Ningún fallo
   de Jev corta el turno.
6. **Privacidad mínima** ([`privacy.py`](packages/core/src/carvajal/agent/gatekeeper/privacy.py)):
   a Jev viaja la pregunta con documentos, teléfonos, correos, tarjetas, cuentas, nombre y
   empresa de la persona enmascarados; a la compuerta, la respuesta sin los datos de la
   persona y extractos mínimos de evidencia (`JEV_MAX_EVIDENCE_CHARS`). Los logs, la traza y
   el libro de llamadas llevan solo la decisión y las probabilidades, nunca texto.

Jev se enciende con `JEV_ENABLED=true` y `TYPESAFE_API_KEY`; `/health` publica su estado en
el bloque `jev`. Resultados de la medición
([`specs/046-jev-router/evidencia.md`](specs/046-jev-router/evidencia.md)):

| Medida | Resultado |
|---|---|
| Exactitud del gatekeeper (20 preguntas) | **Jev 20/20**, LLM 18/20, reglas 14/20; 0 preguntas con datos enviadas a la respuesta directa |
| Compuerta de afirmaciones (16 respuestas) | **10/10** no respaldadas detectadas y **0/6** falsas alarmas, igual con el umbral vigente 0,3 que con el 0,5 medido al principio |
| Generalización v3 (40 ítems) con y sin Jev | 28/40 con Jev frente a 29/40 sin Jev (estricto); 0 violaciones de material en ambos |
| Latencia por llamada a Jev | p50 149 ms, p95 231 ms (n = 75) |
| Fin del turno p95 | 5,84 s con Jev frente a 5,30 s sin Jev |
| Costo de Jev | ≈ 0,07 USD por 1 000 turnos (≈ 1 553 tokens por turno) |

### WhatsApp, servidor MCP y webhook

- **Número real vía OpenClaw.** OpenClaw actúa solo como gateway: recibe el mensaje de
  WhatsApp y llama a la herramienta MCP
  `consultar_carvajal(mensaje, conversacion, nueva, voz, foto)`
  del servidor propio [`apps/mcp/server.py`](apps/mcp/server.py). La herramienta ejecuta
  **el mismo turno del chat web** con [`WhatsAppBridge`](apps/mcp/bridge.py) y devuelve el
  texto final en formato de WhatsApp con sus fuentes; OpenClaw lo reenvía tal cual.
- **Servidor MCP propio**: Streamable HTTP, `Authorization: Bearer <MCP_TOKEN>` comparado en
  tiempo constante (sin token no arranca), solo cabeceras `Host` de loopback, puerto 8765
  publicado solo en loopback y alcanzado por túnel SSH; servicio `mcp` del perfil `whatsapp`
  en [`compose.prod.yml`](compose.prod.yml).
- **Webhook firmado** `POST /api/webhooks/whatsapp`
  ([`apps/api/views/webhooks.py`](apps/api/views/webhooks.py),
  [`docs/webhooks.md`](docs/webhooks.md)): HMAC-SHA256 de `<timestamp>.<cuerpo>` con
  `WEBHOOK_SECRET`, ventana de ±5 minutos, allowlist E.164 (`WEBHOOK_ALLOWED_NUMBERS`;
  vacía rechaza a todos), idempotencia por `message_id` y 404 sin secreto. Ejecuta el mismo
  `WhatsAppBridge`, así que un número cae en el mismo hilo por MCP y por webhook.
- **Quién puede escribir**: en OpenClaw, los mensajes directos están en `dmPolicy:
  allowlist` y el grupo en `groupPolicy: allowlist`: el bot solo contesta a los números E.164
  de `groupAllowFrom`. Los invitados de una demo se agregan y se quitan en vivo, sin
  reiniciar, con [`openclaw/invitados.sh`](openclaw/invitados.sh). `openclaw security audit`
  da 0 críticos, fijado en [`openclaw/auditoria-base.json`](openclaw/auditoria-base.json).
  En OpenClaw, el grupo comparte una sola sesión entre quienes escriben.
- **Identidad y cupo**: el número nunca se guarda en claro; el dueño es
  `HMAC-SHA256(SESSION_SECRET, número E.164)`, con su propio hilo, memoria y cupo diario.
- **PDF de cotizaciones**: al confirmar con «sí», la cotización se registra con su folio y
  el PDF se adjunta en el mismo mensaje (`ADJUNTAR_PDF:` con `MEDIA_BASE_URL`). Sin
  `MEDIA_BASE_URL`, va como enlace firmado `/cotizaciones/{folio}.pdf`
  ([`cotizacion_pdf.py`](apps/api/views/cotizacion_pdf.py),
  [`quote_pdf.py`](apps/api/quote_pdf.py)). Si hay una cotización pendiente, «PDF» la
  confirma; si no, devuelve la última registrada. El enlace vence con la vigencia de la
  cotización. El PDF se genera al vuelo con fpdf2: logo, folio, producto, importes, vendedor
  y fuente del precio.
  En el chat web, la cotización registrada enlaza el mismo `/cotizaciones/{folio}.pdf` en el
  texto y en la tarjeta, sin firma: la ruta lo entrega si la cookie del visitante es la del
  dueño y responde 404 a cualquier otro.
- **Pedido y recibo** (spec 056): tras el folio, la herramienta ofrece el pedido, pide nombre
  y correo y pregunta «¿Lo confirmo?»; el agente de OpenClaw pasa cada respuesta tal cual. El
  pedido confirmado llega como recibo con el PDF del pedido de Odoo, que tiene el formato de
  la cotización del asistente (addon `carvajal_sale_report`). El recibo lleva datos
  personales, así que se adjunta solo si `MEDIA_BASE_URL` es https o loopback. Si no, va
  como enlace https firmado de `PUBLIC_BASE_URL`; nunca viaja por http público. A mitad del
  pedido, *PDF* manda la
  cotización y recuerda el paso; con el pedido hecho, *PDF* o «mándame el recibo» reenvían
  el recibo. Ver [`docs/odoo-pedidos.md`](docs/odoo-pedidos.md).

### Voz

- **Chat web.** El dictado y la lectura en voz alta usan el navegador (spec 040) o, con
  `VOICE_PROVIDER=openai` y `OPENAI_API_KEY`, OpenAI a través del servidor (spec 047):
  `POST /api/voz/transcribir` recibe el audio de `MediaRecorder` (tipo, firma y tamaño
  validados; no se guarda) y devuelve el texto para revisarlo, con los miles al estilo
  colombiano («1,800» → «1.800»); `POST /api/voz/hablar` sintetiza **solo** una respuesta
  guardada de la conversación de quien llama, por su id, nunca texto libre. Lo que se lee
  es un **resumen hablado** de unos 15-20 s (extractivo, sin LLM: primera frase, la lista
  en una frase, la pregunta final y «El detalle completo está en pantalla.»), también en
  la voz de respaldo del navegador. CSRF, la identidad del chat, límites por sesión, por IP y global, la
  duración máxima del audio, un tope diario y un circuito protegen el gasto. Solo la
  respuesta a una pregunta **hablada** (borrador dictado o manos libres) trae audio: su
  **nota de voz** (spec 050), o «Escuchar» con la voz del navegador, suena sola al
  terminar. Una pregunta escrita, o la foto con su texto aunque se dicte, no trae audio
  ni marca «por voz». La pregunta hablada se guarda con la marca `voice` (también si la
  respuesta se corta), así que el historial recargado aplica la misma regla. No hay
  casilla de lectura automática. Un autoplay bloqueado deja la nota para tocarla sin
  cambiar de voz; solo un 503 o un 401/403 pasan la lectura a la voz del navegador, y
  por 60 s.
  Detalle en [`docs/voz-web.md`](docs/voz-web.md#notas-de-voz-por-respuesta).
- **WhatsApp.** Con `voz=true`, `consultar_carvajal` devuelve un resumen para la nota de
  voz (sin citas, Markdown ni URL) y, si omitió algo, la respuesta escrita completa tras
  la línea `TEXTO` para enviarla como mensaje; si no, los enlaces aparte. La transcripción
  entra con los miles al estilo colombiano. La cotización hablada dura unos 15 s,
  el folio se dice por sus cuatro últimos caracteres y el pedido «PDF» acepta lo que un STT
  suele oír («pe de efe», «impedefe»). Ver
  [`docs/whatsapp-openclaw.md`](docs/whatsapp-openclaw.md#mensajes-de-voz).

### Fotos de alimentos

La persona adjunta la foto de un plato o una bebida (botón «Foto» del chat web, o una foto
en WhatsApp) y recibe un empaque recomendado y hasta cuatro alternativas del catálogo de su
país aptos para ese alimento (specs 051 y 053, [`carvajal/food/`](packages/core/src/carvajal/food/)):

1. **Preparación** sin guardar nada: se endereza, se escala (lado ≤ 1568 px) y se
   re-codifica sin metadatos (ubicación, dispositivo).
2. **Clasificación** con una sola petición de Jev con la imagen: ¿es comida? (Noul), tipo
   entre 11 opciones, consistencia y temperatura (Choice) y porción (Score). Con confianza
   baja el asistente pregunta en vez de adivinar; si no es comida, lo dice.
3. **Recomendación determinista**: una tabla traduce el tipo a uso, familias y temperatura,
   y `check_material` (la guía de materiales) descarta lo no apto. La respuesta cita la ficha
   de cada referencia, explica la aptitud del material y queda en el hilo. En la web, la
   recomendada va destacada y las alternativas en una cuadrícula de 2×2.
4. **Elegir y cotizar** (spec 053): «dame esa», «la alternativa 1» o «la 2», escrito o
   dictado, se resuelve con reglas a la referencia y el asistente pide la cantidad y la
   ciudad si faltan. La cotización sale como **propuesta** sin folio; con «sí» o el botón
   «Confirmar cotización» se registra con folio y PDF. Esto vale para toda cotización, venga
   o no de una foto.
5. **Hacer el pedido** (spec 056, con `ODOO_*`): el folio pregunta «🛒 ¿Quieres hacer el
   pedido?» (botones «Hacer el pedido» / «Ahora no» en la web). El asistente pide el nombre y
   el correo, los muestra con «¿Lo confirmo?» y solo un «sí» limpio crea en Odoo un pedido de
   venta confirmado: Odoo envía el correo de confirmación con el PDF y el chat entrega el
   recibo (en la web, el enlace a `/pedidos/{folio}.pdf`). Pago y entrega los coordina un
   asesor, que recibe la actividad del pedido en Odoo. Ver
   [`docs/odoo-pedidos.md`](docs/odoo-pedidos.md).

En la web la foto se ve en el compositor y en el mensaje enviado (como en WhatsApp), se
puede arrastrar o pegar, y mientras se analiza Carva aparece con una lupa («Mirando tu
foto…»). El historial guarda solo una miniatura de 320 px sin metadatos; la foto original no
se guarda.

Activa con `TYPESAFE_API_KEY` (sin depender de `JEV_ENABLED`); `FOOD_PHOTO_ENABLED=false` la
apaga y oculta el botón. El chat avisa que la foto se analiza con un servicio externo
(TypeSafe, EE. UU.). En WhatsApp el agente de OpenClaw pasa la referencia del adjunto en
`foto` ([guía](docs/whatsapp-openclaw.md#fotos-de-alimentos-spec-051)). Evaluación con fotos
de licencia libre en [`specs/051-fotos-alimentos/evaluacion/`](specs/051-fotos-alimentos/evaluacion/).

### Backend FastAPI y persistencia

- **FastAPI** en un solo proceso (`make api`, puerto 8000) sirve el shell Jinja2/HTMX, la API
  JSON y el streaming SSE ([`apps/api/main.py`](apps/api/main.py)).
- **PostgreSQL** guarda conversaciones, mensajes, cotizaciones, cupos y entregas del
  webhook; las migraciones viven en [`migrations/`](migrations/) (Alembic).
- **`PostgresSaver`** es el checkpointer de LangGraph (`thread_id` = conversación,
  verificado contra el dueño) y **`PostgresStore`** guarda la memoria del dueño y el resumen
  acumulado ([`apps/api/agent_runtime.py`](apps/api/agent_runtime.py)). Un reinicio del
  backend recupera el mismo hilo.
- `/live` comprueba el proceso. `/api/health` publica los conteos del corpus y el estado de
  Qdrant y del catálogo. `/health` publica lo mismo y suma estos bloques:
  - `agent`, `jev`, `claims` (contadores de la compuerta de afirmaciones), `voice`,
    `food_photo` y `monitoring`;
  - `odoo`: si está configurado y alcanzable, sin URL ni secretos. No cambia el estado
    global.

Contrato del turno (`POST /api/chat/stream` y `/chat/stream`, eventos SSE):

| Evento | Contenido |
|---|---|
| `open` | Apertura del stream. |
| `status` | Línea de estado de cada consulta («Estoy revisando la guía de materiales…»). |
| `meta` | `sources[]` con `chunk_id`, `slug`, `title`, `url`, `source_type`, `score` y extracto. |
| `token` | Fragmento incremental de la respuesta. |
| `trace` | `thread_id`, `mode`, `path`, `tool`, `memory`, `summary`, `retrieved`, `gatekeeper`, `claims`. |
| `products` | Tarjetas de producto y de cotización (HTML renderizado y escapado en la API). |
| `fallback` | Respuesta determinista completa si el proveedor falla. |
| `done` | `conversation_id`, `message_id`, `thread_id`, `quote_ids`, `elapsed_ms`, `request_id`. |
| `error` | Mensaje accionable para la persona, sin detalles internos. |

### Memoria de corto y largo plazo

Contrato: [`specs/045-memoria-herramientas/contracts/memory.md`](specs/045-memoria-herramientas/contracts/memory.md).

- **Corto plazo**: la ventana de los últimos `AGENT_HISTORY_TURNS` turnos (8) y un
  **resumen acumulado** de los turnos que salen de ella
  ([`agent/memory/summary.py`](packages/core/src/carvajal/agent/memory/summary.py)). Lo
  calcula un worker en segundo plano después de cada turno, sin retrasar la respuesta, y
  viaja en el bloque `<resumen_conversacion>` del sistema. El modo por defecto es
  `AGENT_SUMMARY_MODE=deterministic`.
- **Largo plazo**: memoria del dueño en `PostgresStore`
  ([`agent/memory/store.py`](packages/core/src/carvajal/agent/memory/store.py)) con
  categorías cerradas: `nombre`, `empresa`, `ciudad_habitual`, `formato_preferido` y
  `nota`. Se recupera en cualquier hilo del mismo dueño, no cruza a otro visitante y caduca
  a los `AGENT_MEMORY_DAYS` (180).
- **Datos sensibles**: documento, teléfono, correo, tarjeta (Luhn), cuenta y claves se
  detectan por forma y nunca se guardan en la memoria ni en el resumen
  ([`agent/memory/sensitive.py`](packages/core/src/carvajal/agent/memory/sensitive.py)).
- **Olvidar**: `olvidar_preferencia` borra una categoría, una nota o todo; «olvida todo lo
  que sabes de mí» lo atiende el atajo sin LLM y borra también el resumen.
- **Defensa ante inyección**: solo se guarda lo que la persona dijo en su pregunta, y solo
  se olvida con un verbo de olvido explícito y no negado. La memoria aplica como contexto y
  estilo; nunca respalda datos de Carvajal.
- La ciudad habitual solo se sugiere («¿O lo hago en Cali, tu ciudad habitual?»): nunca
  cotiza ni busca sedes con una ciudad que la persona no dijo en la conversación.

### Seguridad

| Control | Implementación |
|---|---|
| Compuerta de evidencia | Sin evidencia consultada, abstención declarada; el modelo no redacta datos de Carvajal sin consulta ([`toolagent/evidence.py`](packages/core/src/carvajal/agent/toolagent/evidence.py)) |
| Compuerta de afirmaciones | Jev Noul sustituye por el texto neutro lo no respaldado ([`claims.py`](packages/core/src/carvajal/agent/gatekeeper/claims.py)) |
| Datos sensibles | Filtro determinista en memoria, resumen y lo que viaja a Jev ([`sensitive.py`](packages/core/src/carvajal/agent/memory/sensitive.py), [`privacy.py`](packages/core/src/carvajal/agent/gatekeeper/privacy.py)) |
| Token MCP | Bearer obligatorio, comparación en tiempo constante, Host de loopback, puerto publicado solo en loopback |
| Firma del webhook | HMAC-SHA256 con timestamp firmado, ventana de 5 minutos e idempotencia por `message_id` |
| Allowlist | Webhook: `WEBHOOK_ALLOWED_NUMBERS` en E.164; vacía rechaza a todos (403). OpenClaw: mensajes directos y grupo en allowlist (`groupAllowFrom`), invitados en vivo con [`invitados.sh`](openclaw/invitados.sh) y `security audit` con 0 críticos |
| Sin trazas internas al usuario | Errores con mensaje legible en web y WhatsApp; `trace` solo con `?debug=1`; Sentry con `send_default_pii=False` e `include_local_variables=False` ([`observability.py`](apps/api/observability.py)) |
| Web | CSRF, CSP y cabeceras de seguridad ([`security.py`](apps/api/security.py)), Markdown saneado con `nh3`, guard SSRF en la captura ([`urlguard.py`](apps/api/urlguard.py)), escritura del corpus solo con `OPERATOR_TOKEN` |
| Contenedores | Usuario sin privilegios, `read_only`, `cap_drop: ALL`, `no-new-privileges` en [`compose.prod.yml`](compose.prod.yml) |
| Secretos | `.env` fuera del repositorio (en `.gitignore`); la plantilla es [`.env.example`](.env.example) |

Operación segura del corpus: [`docs/seguridad-operacion-corpus.md`](docs/seguridad-operacion-corpus.md).

## Instalación

Requisitos: Python 3.13, [`uv`](https://docs.astral.sh/uv/) y Docker (para Postgres y
Qdrant). Toda la gestión de dependencias pasa por `uv`.

```bash
git clone https://github.com/almeneses/carvajal.git
cd carvajal
uv sync --all-extras                 # entorno reproducible desde uv.lock
cp .env.example .env                 # DEEPSEEK_API_KEY y SESSION_SECRET
make db                              # Postgres 16 + pgvector
make qdrant                          # Qdrant con volumen persistente
uv run alembic upgrade head          # migraciones
ollama pull nomic-embed-text         # embeddings locales de 768 dimensiones
uv run carvajal migrate-gold         # importa documentos y fragmentos a Postgres
make index                           # indexa el núcleo y las capas extendidas en Qdrant
make api                             # http://localhost:8000
```

`--all-extras` instala también `capture` (captura HTTP y extracción HTML para la CLI) y
`legacy` (FAISS para la CLI histórica). La app web y su imagen Docker solo necesitan las
dependencias base. El camino solo con Docker es `make up` ([Demo rápida](#demo-rápida)).

Calidad:

```bash
make dev        # entorno + hooks de pre-commit
make check      # Ruff (lint y formato) + ty + pytest
make test       # pytest (sin integración, e2e ni smoke)
make eval-cotizador  # 21 casos dorados del cotizador
```

`make check` corre la parte de Python del CI. El job `Calidad` de
[`.github/workflows/ci.yml`](.github/workflows/ci.yml) suma las pruebas JavaScript del chat
(`node --test tests/js/*.cjs`) y el smoke del monitoreo.
Las pruebas no usan red: los proveedores (DeepSeek, Jev, Qdrant, LangSmith) se sustituyen por
dobles. En la medición de la spec 046 pasaron **4 413 pruebas**
([evidencia §1](specs/046-jev-router/evidencia.md)).

## Uso

```bash
make api                             # chat web y API en http://localhost:8000/chat
AGENT_MODE=routes make api           # enrutador por reglas (interruptor de emergencia)
uv run carvajal sync --layer all     # Gold → PostgreSQL → Qdrant, incremental
uv run carvajal sync --verify        # compara huellas y conteos sin escribir
uv run carvajal status               # estado del corpus
uv run carvajal capture              # captura el sitio oficial y la prensa (respeta robots.txt)
uv run python -m apps.mcp            # servidor MCP en loopback, puerto 8765 (requiere MCP_TOKEN)
```

La CLI completa (`capture`, `sync`, `index-qdrant`, `status`, `list`, `ask`, `eval`,
`summarize`, `faq`, `import`, `migrate-gold`, `catalog-build`, `catalog-prices`,
`eval-router`, `eval-agent`, `simulate-chat`, `eval-gatekeeper`, `eval-claims`) requiere
los extras: `uv sync --all-extras` en el repositorio o
`uv tool install "carvajal[capture,legacy]"` fuera de él.

La interfaz web tiene tres secciones:
- **Chat**: respuestas con fuentes, tarjetas de producto y de cotizaciones, hilos
  anteriores, voz y foto.
- **Corpus**: explorador y ficha de cada documento.
- **Métricas** (`/metricas`, spec 055): una tarjeta por fuente (Qdrant, Jev, LangSmith,
  DeepSeek y WhatsApp). Si una fuente falla, su tarjeta lo dice. El saldo de DeepSeek solo
  lo ve el operador.
`/configuracion` es la consola del operador para cargar archivos (`.pdf`, `.xml`, `.json`,
`.csv`, `.html`, `.md`…) o capturar URLs; exige `OPERATOR_TOKEN` y deja lo nuevo en Gold
para revisión antes de publicarlo con `carvajal sync`. Para volúmenes grandes:
`uv run python -m apps.api.ingest_cli /ruta --source-type oficial`.

## Despliegue

- **Guía del servidor**: [`docs/despliegue-ovh.md`](docs/despliegue-ovh.md) (VPS, `.env`,
  GHCR, secretos de GitHub Actions, primer despliegue, corpus, respaldos y rollback).
- **WhatsApp**: [`docs/whatsapp-openclaw.md`](docs/whatsapp-openclaw.md) (perfil `whatsapp`,
  túnel SSH y PDF) y [`docs/webhooks.md`](docs/webhooks.md) (firma y contrato).
- **Odoo 19**: [`deploy/odoo/`](deploy/odoo/README.md) es un proyecto de compose aparte en el
  mismo VPS, con el addon `carvajal_sale_report`. Fusionar en `main` no lo despliega: los
  addons se copian y se instalan a mano.
- **Automático**: al fusionar en `main`, GitHub Actions publica la imagen en GHCR con el tag
  `sha-<commit>` y [`scripts/deploy.sh`](scripts/deploy.sh) la descarga, aplica migraciones
  y comprueba `/live` y `/health`.
- **Manual con Docker**:

```bash
make image                  # imagen multi-etapa del runtime web
export SESSION_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
make prod-up                # app + Postgres + Qdrant + Ollama en APP_PORT (8080)
make prod-bootstrap         # modelos, migraciones, corpus e índice (una vez)
COMPOSE_PROFILES=whatsapp docker compose -f compose.prod.yml up -d mcp   # servidor MCP
```

`compose.prod.yml` exige `SESSION_SECRET`; el `.env` del servidor vive fuera del repositorio
y parte de [`.env.example`](.env.example).

## Evaluación

Cada medición con proveedor real se hizo una vez y quedó registrada con su comando,
entorno, criterios y artefactos. Las tablas completas, incluidos los criterios que no se
alcanzaron y su análisis, están en cada evidencia.

### Retrieval (RAG sobre Qdrant)

`data/eval/retrieval_questions.json` trae 31 consultas etiquetadas a mano (30 con evidencia
y una sin evidencia). [`scripts/eval_retrieval.py`](scripts/eval_retrieval.py) mide
Recall@10 y nDCG@10 sin llamar al LLM:

```bash
make index-eval     # indexa todas las capas y ejecuta scripts/eval_retrieval.py
```

| Índice | Recall@10 | nDCG@10 | Informe |
|---|---:|---:|---|
| Núcleo, 125 fragmentos (2026-11-17) | 0,800 | 0,779 | [`docs/eval/retrieval-report.md`](docs/eval/retrieval-report.md) |
| Corpus ampliado, 40 792 fragmentos (2026-11-23) | 0,578 | 0,491 | [`specs/044-agente-tool-calling/evaluacion/retrieval-indice-ampliado.md`](specs/044-agente-tool-calling/evaluacion/retrieval-indice-ampliado.md) |

El código de recuperación es el mismo en ambas filas; la diferencia la explica el corpus:
174 de los 217 documentos del índice ampliado son informes y estados financieros en PDF que
desplazan del top-10 a las páginas etiquetadas sobre el corpus anterior
([evidencia 044 §6](specs/044-agente-tool-calling/evidencia.md)). La consulta sin evidencia
(`q31`, teléfono en Tokio) se abstiene en ambos índices. Cada respuesta cita su fragmento con URL y tipo de
fuente (oficial, tercero, colaborador o sistema).

### Agente

| Medición | Comando | Resultado | Evidencia |
|---|---|---|---|
| `routes` frente a `tools`, 57 preguntas (v2) | `make eval-agent` | Resueltos **89,5 % (51/57)** en `tools` frente a 54,4 % en `routes`; primer texto p95 3,55 s | [044](specs/044-agente-tool-calling/evidencia.md) |
| 20 herramientas y memoria, 26 ítems | `uv run carvajal eval-agent --mode tools --questions data/eval/herramientas_045.json` | **25/26** (26/26 auditado), 0 violaciones, 0 datos sin fuente | [045 §4](specs/045-memoria-herramientas/evidencia.md) |
| Generalización v3, 40 ítems | `uv run carvajal eval-agent --mode tools --questions data/eval/generalization_v3.json` | **37/40** auditado (13/40 en `routes`) | [045 §5](specs/045-memoria-herramientas/evidencia.md) |
| Simulación de conversaciones, 12 escenarios y 32 turnos | `uv run carvajal simulate-chat --scenarios data/eval/simulacion_escenarios_r3.json` | 0 respaldos ni errores, 0 turnos con dato sin fuente, 1 violación de garantía dudosa, primer texto p95 4,71 s | [045 §6](specs/045-memoria-herramientas/evidencia.md) |
| Gatekeeper, 20 preguntas | `uv run carvajal eval-gatekeeper --judge rules --judge llm --judge jev --ledger <libro>` | Jev 20/20, LLM 18/20, reglas 14/20 | [046 §2](specs/046-jev-router/evidencia.md) |
| Compuerta de afirmaciones, 16 respuestas | `uv run carvajal eval-claims --ledger <libro>` | 10/10 detectadas, 0/6 falsas alarmas | [046 §3](specs/046-jev-router/evidencia.md) |
| v3 con y sin Jev | `eval-agent … --jev-ledger <libro>` | 28/40 frente a 29/40 (estricto); latencia y costo en la evidencia | [046 §4](specs/046-jev-router/evidencia.md) |
| Cotizador, casos dorados | `make eval-cotizador` | **21/21** | [`cotizacion_cases.json`](data/eval/cotizacion_cases.json) |

`eval-agent`, `simulate-chat`, `eval-gatekeeper` y `eval-claims` llaman a los proveedores
reales y gastan créditos; cuando llaman a Jev exigen un libro de llamadas (`--ledger`, o
`--jev-ledger` en `eval-agent`) que anota cada llamada sin texto. Los conjuntos están en
[`data/eval/`](data/eval/).

El **ensayo general** ([`docs/ensayo.md`](docs/ensayo.md), `tests/e2e/`) prueba el sistema
desplegado de punta a punta: la app, el MCP, el gateway y la allowlist. Recorre también los
flujos y las fotos, y crea pedidos reales en Odoo por la web y por WhatsApp, con foto, texto
y voz. Se corre con `make ensayo`, `make ensayo-completo`, `make ensayo-exploracion` y
`make ensayo-pedido`.

## Configuración

Copia la plantilla (`.env` está en `.gitignore`) y completa las claves:

```bash
cp .env.example .env
```

| Variable | Por defecto | Qué controla |
|---|---|---|
| `LLM_PROVIDER` | `deepseek` | Proveedor del chat (DeepSeek por API). Ollama calcula los embeddings con cualquier valor |
| `DEEPSEEK_API_KEY` | vacío | Clave de DeepSeek; el modo `tools` la exige |
| `DEEPSEEK_MODEL` | `deepseek-chat` | Modelo; `.env.example` trae `deepseek-flash`, el evaluado |
| `DAILY_REQUESTS_LIMIT` | `20` | Cupo diario por visitante o chat de WhatsApp |
| `QUOTA_TIMEZONE` | `America/Bogota` | Zona de la medianoche del cupo |
| `AGENT_MODE` | `tools` | `tools` (agente con herramientas) o `routes` (enrutador por reglas) |
| `AGENT_MAX_TOOL_CALLS` | `4` | Consultas por turno (`sin_consulta` no cuenta) |
| `AGENT_TURN_TIMEOUT_S` | `45` | Plazo hasta el primer token; después responde el respaldo |
| `AGENT_MAX_QUOTES` | `4` | Cotizaciones por turno (comparativo de cantidades o varios productos) |
| `AGENT_HISTORY_TURNS` | `8` | Ventana de turnos de la memoria de corto plazo |
| `AGENT_SUMMARY_MODE` | `deterministic` | Resumen acumulado: `deterministic`, `llm` u `off` |
| `AGENT_MEMORY_DAYS` | `180` | Caducidad de la memoria del dueño |
| `JEV_ENABLED` | `false` | Enciende el gatekeeper y la compuerta con Jev (requiere `TYPESAFE_API_KEY`) |
| `JEV_MODEL` | `jev-1.14.0` | Modelo de Jev fijado (los umbrales se midieron con él) |
| `JEV_ROUTER_MIN_CONFIDENCE` / `JEV_ROUTER_DIRECT_MIN_CONFIDENCE` | `0.6` / `0.8` | Umbrales de cautela del gatekeeper |
| `JEV_CLAIMS_MODE` / `JEV_CLAIMS_THRESHOLD` | `enforce` / `0.3` | Compuerta de afirmaciones |
| `JEV_MAX_CALLS_PER_DAY` | `1000` | Tope diario de llamadas a Jev por proceso |
| `FOOD_PHOTO_ENABLED` / `FOOD_PHOTO_MAX_CALLS_PER_DAY` | `true` / `500` | Fotos de alimentos con Jev (requieren `TYPESAFE_API_KEY`) y su tope diario |
| `FOOD_PHOTO_TIMEOUT_S` / `FOOD_PHOTO_MIN_CONFIDENCE` | `4.0` / `0.6` | Plazo de Jev con imagen y umbral para preguntar en vez de sugerir |
| `OPENCLAW_MEDIA_DIR` | vacío | Carpeta de adjuntos de OpenClaw montada en solo lectura en `mcp` (fotos por WhatsApp) |
| `MCP_TOKEN` | vacío | Bearer del servidor MCP; sin él no arranca |
| `MCP_HEALTH_TOKEN` | vacío | Bearer que solo abre el `/health` del MCP; lo usa `/metricas` de la app (que nunca recibe `MCP_TOKEN`) |
| `PUBLIC_BASE_URL` | vacío | URL pública para los enlaces al PDF, al recibo del pedido y a las fotos del catálogo |
| `MEDIA_BASE_URL` | vacío | URL desde la que OpenClaw descarga el PDF de la cotización y el recibo para adjuntarlos en WhatsApp; el recibo solo se adjunta si es https o loopback |
| `ODOO_URL` / `ODOO_DB` / `ODOO_API_KEY` | vacío | Odoo 19 para los pedidos (spec 056); sin las tres no se ofrece el pedido ([guía](docs/odoo-pedidos.md)) |
| `ODOO_TIMEOUT_S` / `ODOO_SALES_LOGIN` | `20` / `admin` | Plazo de las llamadas a Odoo y usuario de ventas que recibe la actividad «Coordinar pago y entrega del pedido» |
| `HTTPS_REDIRECT` | `true` | Con `PUBLIC_BASE_URL` https, redirige (307) al dominio público las navegaciones que llegan por IP:puerto |
| `WEBHOOK_SECRET` | vacío | Secreto HMAC del webhook; vacío lo deshabilita (404) |
| `WEBHOOK_ALLOWED_NUMBERS` | vacío | Remitentes E.164 autorizados; vacía rechaza a todos (403) |
| `SESSION_SECRET` | aleatorio | Firma la cookie anónima, el dueño de WhatsApp y los enlaces al PDF; obligatorio en producción |
| `OPERATOR_TOKEN` | vacío | Token de la consola de operador; vacío deshabilita la escritura web |
| `QDRANT_URL` / `QDRANT_COLLECTION` | `http://127.0.0.1:6333` / `carvajal_chunks` | Índice vectorial |
| `RAG_TOP_K` / `RAG_MIN_SCORE` | `6` / `0.30` | Fragmentos por consulta y umbral de similitud |
| `MONITORING_ENABLED` | `false` | Trazas en LangSmith, solo metadatos en producción ([guía](docs/monitoreo-langsmith.md)) |
| `VOICE_PROVIDER` / `OPENAI_API_KEY` | `browser` / vacío | Voz del chat por el servidor con OpenAI ([guía](docs/voz-web.md)); sin clave, voz del navegador |
| `VOICE_MAX_CALLS_PER_DAY` / `VOICE_RATE_LIMIT` | `500` / `20` por 10 min | Tope diario por proceso y límite por sesión de la voz del servidor |
| `VOICE_IP_RATE_LIMIT` / `VOICE_GLOBAL_RATE_PER_MIN` | `60` por 10 min / `30` | Límites de la voz del servidor por IP y para todo el proceso |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | IPs de proxy en las que confía uvicorn; detrás de nginx, la puerta de enlace de Docker ([despliegue](docs/despliegue-ovh.md)) |

La lista completa, con los límites de la captura web y del monitoreo, está comentada en
[`.env.example`](.env.example).

## Corpus y datos

```text
semillas (sitio oficial y prensa)
   │  robots.txt + 1 s por host + User-Agent propio + timeout 30 s
   ▼
Bronze  data/raw/*.html          original tal como llegó
   ▼
Silver  data/processed/*.md      Markdown limpio (sin menús, header ni footer)
   ▼
Gold    data/gold/*.md           Markdown + front matter (url, title, section, domain,
                                 source_type, captured_at, hash, chars) e index.jsonl
   ▼
carvajal sync                    PostgreSQL (texto) y Qdrant (vectores), incremental
```

| Capa | Ruta | Uso |
|---|---|---|
| Núcleo | `data/gold/` | Corpus versionado del sitio oficial y de prensa; fuente de verdad |
| Extendida | `data/external/<fuente>/gold/` | Filiales y colaboradores; solo recuperación, después del núcleo (`carvajal import`) |
| Catálogo | `data/catalog/empaques/` | Referencias, materiales, precios con fecha y fuente, distribuidores |
| Comercial | `data/commercial/` | Sedes geocodificadas y evidencia de disponibilidad |
| Institucional | `data/institucional/` | Empresas y marcas del grupo, cada fila con cita del núcleo |
| Indicadores | `data/indicators.json` | Cifras financieras publicadas por la prensa, con medio y fecha |

- **Identidad y deduplicación**: cada documento se identifica por su fuente
  (`url:`, `file:`, `upload:`); la huella del original deduplica antes de escribir y la del
  texto decide la versión. `carvajal sync --prune` elimina lo que ya no está en Gold.
- **Procedencia**: `source_type` distingue `oficial` (`carvajal.com`), `tercero` (prensa),
  `colaborador` (capa extendida) y `sistema` (fecha y festivos calculados por el servidor).
- **Precios**: siempre fechados y con procedencia. En Colombia, 29 son precios publicados
  por un distribuidor, con su nombre, página y fecha. Los demás (1 024) son «precio de
  referencia del catálogo», con procedencia `sistema`. Son de dos clases: copias de México
  convertidas a COP con una tasa fechada, y precios proyectados para las referencias sin
  precio. No tienen vendedor ni existencias y nunca se atribuyen a un distribuidor.

## Estructura

```text
carvajal/
├── apps/
│   ├── api/            # FastAPI: shell Jinja2/HTMX, API, SSE, webhook y PDF
│   │   └── chat/       # ChatService, adaptador del agente, compuertas y resumen
│   └── mcp/            # servidor MCP propio (consultar_carvajal) y WhatsAppBridge
├── packages/core/src/carvajal/
│   ├── agent/
│   │   ├── toolagent/  # grafo del modo tools, 20 herramientas, evidencia y evaluación
│   │   ├── gatekeeper/ # reglas, Jev Choice, compuerta Noul y privacidad
│   │   └── memory/     # resumen acumulado, memoria del dueño y datos sensibles
│   ├── adapters/       # storage (Postgres), vector (Qdrant), llm, embeddings, judge (Jev), speech (OpenAI) y crm (Odoo)
│   ├── application/ domain/              # casos de uso, puertos y reglas puras
│   ├── catalog/ commercial/ quotation/   # catálogo, directorio, cotizador, borrador y pedido
│   ├── food/           # foto de alimento → empaque (spec 051)
│   └── indexing/       # Gold → Qdrant
├── data/               # gold, catálogo, datos curados y conjuntos de evaluación
├── openclaw/           # workspace del agente «ventas» de WhatsApp, invitados.sh y auditoría base
├── deploy/odoo/        # Odoo 19 propio: compose, configuración y addon carvajal_sale_report
├── docs/               # guías, evaluación de retrieval, decisiones y diagramas
│   └── arquitectura/   # diagramas de Archify: fuente JSON, HTML interactivo y SVG
├── specs/              # especificaciones Spec Kit con su evidencia
├── migrations/         # Alembic
├── scripts/            # evaluación de retrieval, despliegue y curaduría
├── tests/              # pytest, sin red
├── compose.yml         # desarrollo (perfil full: stack completo)
├── compose.prod.yml    # producción (app, Postgres, Qdrant, Ollama y perfil whatsapp)
├── Dockerfile          # imagen multi-etapa del runtime web
└── pyproject.toml · uv.lock · Makefile
```

## Documentación

- [`CONSTITUTION.md`](CONSTITUTION.md) — principios, stack y decisiones del proyecto.
- [`docs/arquitectura/`](docs/arquitectura/) — diagramas de Archify vigentes: general, turno
  del agente, WhatsApp, pedido, corpus y despliegue (ver [Arquitectura](#arquitectura)).
- [`specs/`](specs/) — especificaciones por funcionalidad (`spec`, `plan`, `tasks`,
  contratos y evidencia); índice en [`specs/README.md`](specs/README.md).
- [`docs/whatsapp-openclaw.md`](docs/whatsapp-openclaw.md) — WhatsApp con el servidor MCP.
- [`docs/odoo-pedidos.md`](docs/odoo-pedidos.md) — pedido tras el folio en Odoo 19: pasos,
  recibo, privacidad y operación (spec 056); instalación en
  [`deploy/odoo/README.md`](deploy/odoo/README.md).
- [`openclaw/README.md`](openclaw/README.md) — workspace del agente de WhatsApp, allowlist del
  grupo e invitados.
- [`docs/ensayo.md`](docs/ensayo.md) — ensayo general de punta a punta (`make ensayo*`).
- [`docs/webhooks.md`](docs/webhooks.md) — webhook firmado: contrato, seguridad y firma.
- [`docs/voz-web.md`](docs/voz-web.md) — voz del chat web por el servidor y su respaldo.
- [`docs/despliegue-ovh.md`](docs/despliegue-ovh.md) — despliegue, operación y rollback.
- [`docs/monitoreo-langsmith.md`](docs/monitoreo-langsmith.md) — monitoreo opcional.
- [`docs/prompts.md`](docs/prompts.md) — prompts, variantes probadas y decisiones.
- [`docs/eval/`](docs/eval/) — informes de retrieval.
- [`docs/informe.md`](docs/informe.md) y [`docs/presentacion.md`](docs/presentacion.md) —
  informe del proyecto y guion de la sustentación.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) y [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

### Arquitectura histórica

Diagramas de entregas anteriores, conservados como registro (los vigentes están en
[Arquitectura](#arquitectura)):

- **Entrega M3** (asistente de empaques con enrutador por reglas y corpus incremental):
  [`docs/arquitectura-m3.html`](docs/arquitectura-m3.html), fuente en
  [`docs/arquitectura-m3.json`](docs/arquitectura-m3.json).
- **Primera entrega** (pipeline de captura y RAG original):
  [`docs/arquitectura.html`](docs/arquitectura.html), fuente en
  [`docs/arquitectura.json`](docs/arquitectura.json) y versión Mermaid en
  [`docs/arquitectura.md`](docs/arquitectura.md).

## Licencia y créditos

Distribuido bajo la licencia [MIT](LICENSE), © 2026 Marko David Garcia y Alejandro
Meneses. Proyecto del curso *Taller de Aplicaciones de IA y Machine Learning* (UAO).

- **Corpus**: contenido público de [carvajal.com](https://www.carvajal.com), citado con su
  URL y fecha de captura. Las cifras financieras provienen de
  [La República](https://www.larepublica.co), [El País](https://www.elpais.com.co) y
  [Caliescribe](https://caliescribe.com), y se atribuyen en cada respuesta.
- **Herramientas**: [uv](https://docs.astral.sh/uv/), [Ruff](https://docs.astral.sh/ruff/),
  [ty](https://github.com/astral-sh/ty), [FastAPI](https://fastapi.tiangolo.com/),
  [LangGraph](https://www.langchain.com/langgraph), [Qdrant](https://qdrant.tech/),
  [Ollama](https://ollama.com/), [DeepSeek](https://www.deepseek.com/),
  [OpenClaw](https://docs.openclaw.ai) y [Archify](https://github.com/tt-a1i/archify).
