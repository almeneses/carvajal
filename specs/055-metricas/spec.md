# Feature Specification: Métricas de operación

**Feature Branch**: `feat/metricas-y-compositor`

**Created**: 2026-11-26

**Status**: Implemented (2026-11-26). Plan breve: [plan.md](./plan.md).

**Input**: User description: «healthcheck para Qdrant. En la versión web, crea una sección
llamada "Métricas" en donde se puedan ver las métricas más importantes tanto de Qdrant, como de
Jev, LangSmith, Deepseek y OpenClaw» (punto 9 del informe del ensayo del 2026-11-26).

## Propósito

Hoy la única forma de saber cómo están el índice, los jueces de TypeSafe, el modelo y el canal
de WhatsApp es leer `/health` (JSON), los logs del contenedor o la consola de LangSmith. El
ensayo del 2026-11-26 tuvo que reconstruir a mano la tasa de sustitución del verificador, la
latencia de `buscar_corpus` y los turnos por WhatsApp. La página `/metricas` junta en un solo
lugar, y en el estilo de la app, las cifras agregadas que ya existen o que el proceso puede
contar, sin exponer datos de las personas.

## Alcance

- Una página nueva, `/metricas`, enlazada en la navegación junto a «Chat» y «Corpus».
- Una tarjeta por fuente: **Qdrant**, **Jev (TypeSafe)**, **LangSmith**, **DeepSeek** y
  **WhatsApp (OpenClaw)**.
- Contadores en proceso nuevos para Jev (por tipo de llamada), DeepSeek (llamadas, tokens y
  latencia) y el puente de WhatsApp (mensajes, fotos, notas de voz, PDF, errores, sin cupo,
  no autorizados).
- El `/health` del servidor MCP publica esos contadores (bloque `metrics`) para que la app
  los lea por la red interna de compose.
- Un healthcheck para el contenedor de Qdrant en `compose.prod.yml` y `compose.yml`; los
  servicios que buscan en el índice esperan a que esté sano.

## User Scenarios & Testing *(mandatory)*

### US1 – El owner revisa la salud del sistema en una página (P1)

**Given** la app desplegada, **When** el owner abre `/metricas`, **Then** ve las cinco
tarjetas con su estado («Disponible», «Datos anteriores», «Apagado» o «No disponible»), las
cifras principales y cuándo se consultó cada fuente.
*Evidencia*: `tests/test_metricas.py::test_la_pagina_es_publica_y_una_fuente_caida_no_la_tumba`.

### US2 – Una fuente caída no tumba la página (P1)

**Given** Qdrant (o LangSmith, o el MCP) no responde, **When** se abre `/metricas`, **Then**
su tarjeta dice «No disponible» y por qué («No respondió a tiempo.», «No se pudo conectar.»,
«Pidió esperar… (HTTP 429)»), y el resto se muestra. Si había un dato anterior, se muestra
marcado como «Datos anteriores». La fuente caída no se vuelve a consultar en cada visita.
*Evidencia*: `test_qdrant_caido_dice_por_que`, `test_langsmith_que_pide_esperar_queda_no_disponible`,
`test_la_cache_sirve_lo_vigente_y_ante_un_fallo_el_dato_anterior`,
`test_la_cache_guarda_el_fallo_y_no_consulta_la_fuente_caida_en_cada_visita`,
`test_langsmith_en_429_no_se_vuelve_a_consultar_en_diez_minutos`.

### US3 – Solo el operador ve el saldo de DeepSeek (P1)

**Given** un visitante sin sesión de operador, **When** abre `/metricas`, **Then** la tarjeta
de DeepSeek dice «Visible solo para el operador» y la app no consulta el saldo. **Given** el
operador (cookie de Configuración, `X-Operator-Token`, Bearer o Basic), **Then** ve el saldo
de `GET /user/balance`.
*Evidencia*: `test_el_operador_ve_el_saldo_de_deepseek` y el transporte de la prueba pública,
que falla si se pide el saldo.

### US4 – El canal de WhatsApp se ve aunque OpenClaw viva en otra VM (P2)

**Given** el gateway de OpenClaw en otra VM, **When** se abre la tarjeta, **Then** dice que el
gateway no es accesible desde la app y muestra lo que guarda la base (mensajes y
conversaciones por canal en 24 h y 7 días, chats distintos, cotizaciones y último mensaje) y,
con `METRICS_MCP_HEALTH_URL` y `MCP_HEALTH_TOKEN`, lo que observa el servidor MCP.
*Evidencia*: `test_whatsapp_cuenta_por_canal_sin_textos_ni_owner_keys`,
`test_whatsapp_lee_el_health_del_mcp_con_su_bearer`,
`tests/test_mcp_whatsapp.py::test_health_del_mcp_publica_las_metricas_del_proceso_sin_texto`.

### US5 – Qdrant tiene healthcheck (P1)

**Given** `compose.prod.yml`, **When** arranca Qdrant, **Then** su healthcheck abre
`/readyz` con bash y `/dev/tcp` (la imagen no trae curl ni wget) y la app, el MCP y el
bootstrap esperan `service_healthy`.
*Evidencia*: `tests/test_deploy_packaging.py::test_qdrant_tiene_healthcheck_sin_curl_y_quien_busca_espera_a_que_este_sano`;
el comando se ejecutó en el contenedor `qdrant/qdrant:v1.15.4` local (código 0 sano, 1 con el
puerto cerrado).

### Edge Cases

- **Sin monitoreo**: LangSmith queda «Apagado» con el motivo y no hay red.
- **Sin clave de DeepSeek o con otro proveedor**: «Apagado»; no se consulta el saldo.
- **Sin el perfil `whatsapp`**: la URL por defecto del MCP no resuelve y su bloque dice «No se
  pudo conectar.»; los conteos de la base siguen.
- **LangSmith limita la frecuencia (429)**: la consulta se repite como mucho cada 10 minutos,
  aunque haya un dato anterior que mostrar.
- **Una fuente falla por otro motivo**: el fallo se guarda un minuto; las visitas de ese
  minuto ven «No disponible» o los datos anteriores sin consultarla, y la tarjeta se recupera
  al minuto siguiente, no a los diez.
- **Varias visitas con el dato caducado**: la recarga de cada fuente va de a una; las demás
  visitas esperan su resultado en vez de lanzar otra consulta.
- **OpenClaw reintenta un turno ya atendido** (`client_turn_id`): cuenta como reintento, no
  como mensaje nuevo, para que el contador del MCP cuadre con la base.
- **Jev sin cupo o con el circuito abierto**: la llamada no sale; se cuenta como corte, sin
  entrar en los percentiles ni en «Con error».
- **DeepSeek en streaming sin `stream_usage`**: la llamada cuenta, sus tokens no; la tarjeta
  avisa de cuántas llamadas no informaron uso y de que el costo se queda corto.
- **Muchos runs**: se usa el agregado `POST /runs/stats`; nunca se listan runs.

## Requirements *(mandatory)*

### Fuentes por métrica

| Tarjeta | Métricas | Fuente | Frescura |
|---|---|---|---|
| Qdrant | estado y optimizador, puntos, vectores indexados, segmentos, dimensión, distancia, HNSW (`m`, `ef_construct`), tamaño (vectores + payload), memoria residente, búsquedas (cuenta, media y máxima), versión | `GET /collections/{colección}` y `GET /telemetry?details_level=3`, plazo 2 s | caché 60 s |
| Jev | modelo, modos del gatekeeper y la compuerta, umbral, cupo del día y de fotos, llamadas por tipo y estado, latencia p50/p95, tokens, último error; compuerta: juzgadas, sustituidas, tasa, no respaldadas e histograma de `p` | bloques `jev`, `claims` y `food_photo` de `/health` y `JEV_CALLS` (en proceso, sin red) | en cada carga |
| LangSmith | trazas raíz, errores y tasa, latencia p50/p99, tokens de entrada y salida, última traza, en 24 h y 7 días | `GET /sessions?name=` y dos `POST /runs/stats` (filtro por `agent_environment` si está configurado), plazo 4 s | caché 10 min |
| DeepSeek | llamadas, errores, latencia p50/p95, tokens, costo estimado (USD por millón configurable, 0,30/1,20); **saldo solo para el operador** | callback de LangChain en el `ChatOpenAI` (`DEEPSEEK_CALLS`); `GET /user/balance`, plazo 3 s | en cada carga; saldo con caché 5 min |
| WhatsApp (OpenClaw) | por canal (WhatsApp y web): mensajes y conversaciones en 24 h y 7 días, chats distintos, cotizaciones, último mensaje; del MCP: mensajes, fotos, notas de voz, PDF, errores, sin cupo, no autorizados, fotos rechazadas, última actividad, sustituidas y llamadas a Jev/DeepSeek | consultas agregadas a `messages`, `conversations` y `quotes` (canal por el prefijo `wa_` del dueño); `GET /health` del MCP con `MCP_HEALTH_TOKEN`, plazo 2 s | caché 60 s |

- **FR-001**: `/metricas` es pública y solo muestra agregados. El saldo de DeepSeek requiere
  `operator_authorized` (el mismo mecanismo que Configuración).
- **FR-002**: Nunca se publican texto de mensajes, `owner_key`, teléfonos, claves, tokens ni
  URLs con credenciales. Los conteos por canal se calculan en SQL sin devolver identificadores.
- **FR-003**: Cada fuente con red tiene plazo corto y caché; si falla, la tarjeta lo dice y la
  página responde 200. Con un dato anterior, se muestra marcado.
- **FR-004**: Los contadores en proceso son seguros entre hilos, no guardan texto y conservan
  como mucho las últimas 1.000 latencias.
- **FR-005**: El `/health` del MCP exige un bearer (`MCP_TOKEN` o `MCP_HEALTH_TOKEN`) y
  acepta, además del loopback, el `Host` del servicio `mcp` de compose; `/mcp` sigue aceptando
  solo loopback y solo `MCP_TOKEN`. La app web recibe `MCP_HEALTH_TOKEN` y nunca `MCP_TOKEN`,
  que abre `consultar_carvajal` con cualquier número de WhatsApp; si ambos coinciden, el MCP
  descarta el de `/health` (`mcp.health_token_reused`).
- **FR-007**: Una fuente que falla se guarda en la caché con vigencia corta (60 s, o la de la
  tarjeta si la fuente pidió esperar) y la recarga va de a una por fuente.
- **FR-006**: El healthcheck de Qdrant no depende de herramientas que la imagen no trae.

### Qué es público y qué es del operador

| Público | Solo operador | Nunca |
|---|---|---|
| Todas las tarjetas y sus cifras agregadas | Saldo de la cuenta de DeepSeek | Texto de personas, `owner_key`, teléfonos, claves (`DEEPSEEK_API_KEY`, `TYPESAFE_API_KEY`, `LANGSMITH_API_KEY`, `QDRANT_API_KEY`, `MCP_TOKEN`, `MCP_HEALTH_TOKEN`) |

## Limitaciones

- Los contadores de Jev, DeepSeek y el MCP son **en memoria por proceso**: empiezan de cero
  con cada despliegue o reinicio, y la app web y el MCP llevan los suyos (WhatsApp corre el
  agente en el MCP).
- Las búsquedas de Qdrant son las de todo el servidor desde que arrancó (la telemetría no las
  separa por colección).
- LangSmith publica p50 y p99, no p95; la tarjeta lo dice.
- El costo de DeepSeek es una estimación con el precio configurado: no descuenta los tokens
  servidos desde la caché del proveedor.
- **OpenClaw queda fuera de alcance directo**: el gateway vive en otra VM y la app no lo
  alcanza. La página lo dice y muestra lo que ven la base y el MCP.

## Success Criteria *(mandatory)*

- **SC-001**: `/metricas` responde 200 con las cinco tarjetas aunque una fuente falle
  (prueba de vista con Qdrant caído).
- **SC-002**: Un visitante no ve el saldo y la app no lo consulta; el operador sí lo ve.
- **SC-003**: Ninguna respuesta de la página ni del `/health` del MCP contiene el texto de un
  mensaje, un `owner_key` ni una clave (pruebas con un teléfono y claves sembrados).
- **SC-004**: El healthcheck de Qdrant pasa en la imagen `v1.15.4` y falla con el puerto
  cerrado.
- **SC-005**: Con caché fría, la página responde en menos de 3 s en local (medido: 1,9 s con
  LangSmith, Qdrant y la base reales).
