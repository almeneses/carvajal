# Feature Specification: Métricas de operación

**Feature Branch**: `feat/metricas-y-compositor`

**Created**: 2026-11-26

**Status**: Implemented (2026-11-26). Plan breve: [plan.md](./plan.md). Revisado el
2026-11-26: límite de LangSmith (429) y «Reintentar» (US6).

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
«LangSmith limita cuántas consultas acepta por minuto (HTTP 429). Se reintenta en N s.»), y el
resto se muestra. Si había un dato anterior, se muestra marcado como «Datos anteriores». La
fuente caída no se vuelve a consultar en cada visita.
*Evidencia*: `test_qdrant_caido_dice_por_que`, `test_langsmith_que_pide_esperar_queda_no_disponible`,
`test_la_cache_sirve_lo_vigente_y_ante_un_fallo_el_dato_anterior`,
`test_la_cache_guarda_el_fallo_y_no_consulta_la_fuente_caida_en_cada_visita`,
`test_langsmith_en_429_se_reintenta_a_los_30_s_y_no_a_los_diez_minutos`,
`test_langsmith_si_fallan_las_dos_conserva_el_dato_anterior_y_reintenta_en_30_s`.

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

### US6 – Reintentar una fuente sin esperar a la caché (P2)

**Given** una tarjeta de red (LangSmith siempre; Qdrant y WhatsApp si están caídas o con datos
anteriores; el saldo de DeepSeek, solo el operador) **When** se pulsa «Reintentar» **Then** el
formulario hace `POST /metricas/reintentar` con el token CSRF, el servidor da esa fuente por
caducada y responde 303 a `/metricas`, cuya carga la consulta de nuevo; la página dice «Se volvió
a consultar …». **Given** que la fuente se consultó hace menos de 15 s (por cualquier visita),
**Then** no se consulta y la página dice «Espera N s para reintentar». Funciona sin JS; el botón
tiene texto («Reintentar» + el nombre de la fuente para lectores de pantalla), foco visible y un
estado con `role="status"`; como tras el 303 ese estado ya viene escrito y una región viva no
anuncia lo que trae la página al cargar, `metricas.js` lo vacía y lo vuelve a escribir para que
se lea (sin JS se ve igual). Si el reintento de LangSmith falla, sus ventanas muestran el dato
anterior marcado «Desactualizada» con el motivo y «Se reintenta en N s» (30 s), no «Disponible».
*Evidencia*: `test_reintentar_con_csrf_fuerza_la_recarga_y_luego_pide_esperar`,
`test_reintentar_sin_csrf_se_rechaza_y_no_consulta_la_fuente`,
`test_reintentar_solo_acepta_fuentes_conocidas_y_el_saldo_es_del_operador`,
`test_un_reintento_de_langsmith_fuerza_las_ventanas_sin_saltarse_el_turno`,
`test_un_reintento_de_langsmith_que_falla_marca_los_datos_y_reintenta_en_segundos`,
`test_reintentar_langsmith_no_espera_a_una_carga_en_curso`,
`test_el_aviso_del_reintento_se_marca_para_anunciarlo_tras_la_redireccion`.

### Edge Cases

- **Sin monitoreo**: LangSmith queda «Apagado» con el motivo y no hay red.
- **Sin clave de DeepSeek o con otro proveedor**: «Apagado»; no se consulta el saldo.
- **Sin el perfil `whatsapp`**: la URL por defecto del MCP no resuelve y su bloque dice «No se
  pudo conectar.»; los conteos de la base siguen.
- **LangSmith limita la frecuencia**: `POST /runs/stats` acepta como mucho una petición cada
  ~8 s (medido el 2026-11-26: con 6,4 s de espacio responde 429, con 8,5 s acepta; el 429 llega
  como página HTML sin `Retry-After`; `GET /sessions` no cuenta). Por eso:
  - el id del proyecto (`GET /sessions`) se guarda una hora y, si su renovación falla, se usa
    el anterior;
  - cada ventana (24 h y 7 días) tiene su propia caché de 10 min y nunca salen dos consultas a
    menos de 9 s; con la caché fría la página trae las 24 h y deja los 7 días «Pendiente» hasta
    su turno (la tarjeta se vuelve a armar a los ~9 s), en vez de chocar con un 429;
  - dentro de una carga solo se espera si cabe en el plazo total de la tarjeta (3 s): hasta 2
    reintentos ante un 429, esperando lo que diga `Retry-After` o, si no viene, el turno de 9 s.
    El 429 de LangSmith no trae `Retry-After` y 9 s no caben en 3 s, así que **en producción no
    hay reintentos dentro de la carga**: la ventana queda pendiente y se reintenta a los 30 s;
    los reintentos dentro de la carga solo ocurren con un `Retry-After` corto;
  - el plazo de 3 s acota cada petición entera: conectar (como mucho 1 s) y leer se reparten
    lo que queda (un `float` en httpx vale por fase y dejaría durar el doble);
  - una ventana que falla (429 u otro error) se reintenta a los 30 s, o cuando diga
    `Retry-After` (como mucho 10 min), conserva su dato anterior marcado «Desactualizada» y la
    otra ventana sigue disponible. Vale también si el dato anterior aún estaba vigente (un
    «Reintentar» que choca con un 429): el fallo no se pierde hasta que la ventana se consulte
    bien;
  - si fallan las dos y no hay dato anterior, «No disponible» con el motivo. La tarjeta no se
    guarda 30 s exactos sino hasta la próxima consulta posible de alguna ventana: como las dos
    van a 9 s una de otra, su vigencia alterna entre ~9 y ~21 s, y LangSmith recibe dos
    consultas cada ~30 s (una por ventana), no una por visita;
  - si el plazo se va antes de pedir una ventana (p. ej. un `GET /sessions` lento con la caché
    fría), la ventana dice «La consulta a LangSmith se alargó y esta ventana se pide en la
    próxima carga.» (no que espera turno ni que LangSmith limitó) y se pide en la visita
    siguiente;
  - «Reintentar» no espera a una carga de LangSmith en curso: deja la marca y la carga
    siguiente la aplica.
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
| LangSmith | trazas raíz, errores y tasa, latencia p50/p99, tokens de entrada y salida, última traza, en 24 h y 7 días | `GET /sessions?name=` (caché 1 h) y un `POST /runs/stats` por ventana, a 9 s o más uno de otro (filtro por `agent_environment` si está configurado), plazo 4 s por petición y 3 s por tarjeta | caché 10 min por ventana; ventana pendiente o fallida: a su turno (~9 s) o a los 30 s |
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
- **FR-008**: «Reintentar» es `POST /metricas/reintentar` con `fuente` y el token CSRF de la
  app (sin token: 403). Solo acepta `qdrant`, `langsmith`, `whatsapp` y, con
  `operator_authorized`, `deepseek_balance` (otra fuente: 400; el saldo sin operador: 403). No
  llama a la fuente: la da por caducada (conservando el dato anterior por si vuelve a fallar) y
  la carga siguiente la consulta. El enfriamiento es de 15 s desde la última consulta real de
  esa fuente, global para todas las visitas; dentro de él responde 303 con «Espera N s para
  reintentar» y no toca la caché. En LangSmith, el reintento no se salta el turno de 9 s.

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
  LangSmith, Qdrant y la base reales). La tarjeta de LangSmith en frío, con la clave real:
  0,93 s (proyecto + 24 h, 7 días pendiente); a los 10 s, 0,62 s y completa; entre medias, sin
  red (0,01 s). Con el plazo repartido entre conectar y leer (medido de nuevo el 2026-11-26):
  0,88 s en frío y 0,51 s al completarse.
- **SC-006**: Una recarga o un «Reintentar» nunca lanzan dos `POST /runs/stats` a menos de 9 s
  ni martillan una fuente: como mucho una consulta manual por fuente cada 15 s.
