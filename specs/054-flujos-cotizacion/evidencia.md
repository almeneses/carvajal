# Evidencia — Spec 054, flujos de cotización

**Fecha**: 2026-11-25 · **Rama**: `feat/054-flujos-cotizacion`

## 1. Qué se midió y cómo

| Criterio | Prueba | Resultado |
|---|---|---|
| SC-001: 18 guiones (8 flujos, texto y voz) sin LLM | `tests/test_flujos_cotizacion.py::test_guion_de_cotizacion` sobre `data/eval/flujos_cotizacion_054.json` | 18/18 |
| SC-002: ningún registro sin «sí» | `test_ninguna_cotizacion_se_registra_sin_el_si`, servicio (`tests/test_flujos_servicio.py`, `tests/test_chat_service_tools.py`), WhatsApp (`tests/test_mcp_*.py`) y webhook (`tests/test_webhooks.py`) | 0 registros sin «sí» |
| SC-003: plantillas cortas | `test_las_plantillas_son_cortas_y_cierran_con_una_pregunta` (≤ 14 líneas, voz ≤ 45 palabras, sin Markdown ni emojis en voz) | Cumple |
| SC-004: suite completa | `make check` (Ruff, formato, `ty` y pytest sin integración) | Verde (ver §4) |

Las pruebas nuevas son 78: guiones, reglas de respuestas cortas, números dichos con palabras,
borrador, plantillas, recordatorio fuera del flujo, Jev (decide, poca confianza, caído y no
consultado) y seis pruebas del servicio real sobre SQLite. Además se agregaron 4 pruebas del
canal de WhatsApp en `tests/test_mcp_voz.py`: plantilla y voz, PDF adjunto al registrar, «sí,
mándame el PDF» y «PDF» sin nada pendiente.

## 2. Pruebas que cambiaron de expectativa

Cambian a propósito porque una cotización ya no queda como «propuesta del turno anterior», sino
como línea del borrador:

- `tests/test_chat_service_tools.py`: el turno comercial agrega una línea («✅ Agregué…»,
  `flow.awaiting == "algo_mas"`) y «No, confírmala» registra. Dos productos en un turno dan
  **un** folio con dos líneas (antes eran dos folios), y dos cantidades del mismo SKU dan un
  comparativo. La fórmula del folio pasa de `confirmacion:{mensaje}` a
  `borrador:{revisión}:{actualización}`.
- `tests/test_foto_cotizacion.py`: la tarjeta de la propuesta se reemplaza por los botones del
  flujo (`data-flujo`); el segundo «sí» responde «ya quedó registrada» con el folio en negrita.
- `tests/test_mcp_whatsapp.py` y `tests/test_webhooks.py`: el carrito («🛒 *Tu cotización · 2
  productos*», «*Total parcial: $11.253*») reemplaza a las dos propuestas 🧾, y el cierre da un
  solo folio.
- `tests/golden/tool_schemas_044.json`: `cotizar` gana el campo `cantidades`, y es el único
  cambio en el esquema.
- `tests/test_settings.py`: `agent_max_quotes` pasa de 3 a 4.
- `tests/test_quotation_request.py`: sale la prueba de los alias privados de la 044, que se
  eliminaron.
- `tests/test_quotation_memory.py`: salen las pruebas de `extract`, `merge` y
  `forget_requested`, que se eliminaron porque solo las usaban las pruebas. Se agrega una de
  `normalize`.

## 3. Lo que no se hizo y por qué

- **`quotation.quote()`** (la entrada de texto del motor) y **`agent/commercial.quote()`**
  se conservan. El primero alimenta el respaldo `routes` cuando DeepSeek no está y
  `scripts/evaluar_cotizador.py`. El segundo sostiene los criterios de éxito de la spec 036
  (`tests/test_sc_036.py`). Ambos pasan por el borrador común, así que no hay un segundo
  flujo.
- **`quote_remarks`** se conserva como red de seguridad en WhatsApp: si el modelo repite una
  cifra pese a la regla del prompt, no aparece dos veces. `whatsapp_quote` y `spoken_quote`
  siguen sirviendo a los turnos registrados antes de la 054 y a la voz web
  (`apps/api/voice.py`).
- **Duplicados de `_plain`**: no se unificaron. Las copias no son equivalentes (unas quitan
  la puntuación y otras no) y unificarlas cambiaría el router y el motor fuera de esta spec.
  Los dos detectores de «sí» también siguen separados: `builtin._confirms` acepta la ciudad
  propuesta y `followup.is_confirmation` confirma la cotización.
- **El cambio de producto** se detecta en el servicio, antes del agente, y no con una señal
  de `_fill_from_context`. Así se pregunta antes de que el agente descarte nada.
## 4. Evaluación del agente (con permiso del owner, 2026-11-25)

Evaluaciones locales:

- Enrutador por reglas: 130 preguntas, ruta, intención y datos al 100 %, 0 violaciones de
  material. Igual que la línea base.
- Cotizador (`make eval-cotizador`): 18/18 casos OK.
- Retrieval: no se corrió porque el cambio no toca el índice ni la recuperación.

`eval-agent --mode tools` sobre `generalization_questions.json` (57 ítems, DeepSeek), en esta
rama y en `main` (e12033a) en un worktree, con el mismo índice, embedder y catálogo, y Jev
apagado:

| Métrica | `main` | Rama 054 | Δ |
|---|---|---|---|
| Ítems resueltos | 82,5 % (47/57) | 86,0 % (49/57) | +3,5 pp |
| Resueltos con equivalencias | 86,0 % | 87,7 % | +1,7 pp |
| Compuestas con todas las partes | 50,0 % (6/12) | 41,7 % (5/12) | −1 ítem |
| Abstenciones correctas | 33,3 % (2/6) | 66,7 % (4/6) | +2 ítems |
| Violaciones de material / datos sin fuente | 0 / 1 | 0 / 2 | +1 (ver abajo) |
| Primer texto p95 / fin p95 (s) | 4,26 / 5,23 | 4,58 / 5,40 | +0,3 / +0,2 |
| Tokens de entrada por turno | 18.866 | 20.094 | +6,5 % (prompt y lista de alternativas) |
| Turnos por respaldo / con error | 0 / 0 | 0 / 0 | = |

Los fallos de la rama que no estaban en la corrida de `main` se corrieron otra vez (dos
veces en la rama y tres en `main`, solo esos ítems):

- **g-008** (EBITDA y Carvajal Tecnología): falla también en `main` en las tres corridas.
  Usa `empresas_del_grupo` en lugar de `buscar_corpus`, y «2,58 billones» sale del título
  de la fuente de terceros, no del extracto. No tiene que ver con la cotización.
- **g-041** (plazo exacto de 500 vasos a Pereira): falla en `main` en dos de tres corridas;
  es inestable desde antes.
- **g-002** («sin fuente ['310', '451']»): es el teléfono del directorio «(310) 451 4938»
  reformateado con paréntesis. El criterio acordado con el owner es que reformatear un
  teléfono no es inventar, así que es un falso positivo del verificador.
- **g-031**: pasó en las dos repeticiones; fue variación del modelo.

Conclusión: no hay regresión atribuible a la 054 en los criterios bloqueantes. Violaciones
de material: 0. Los «datos sin fuente» de la rama son falsos positivos del verificador (un
teléfono y un título), y la línea base ya tenía uno con la misma causa. SC-001 ya estaba
fuera de meta como riesgo aceptado en la 044. Este conjunto no mide las plantillas del flujo:
`eval-agent` corre el agente sin el servicio del chat. Las plantillas las cubren los guiones
de §1. Limitación de origen del conjunto (FR-016): el borrador lo redactó el asistente de
desarrollo con conocimiento previo del enrutador y del cotizador, y el owner lo congeló sin
cambios.

## 5. Resultado de `make check`

Ruff (lint y formato), `ty` y pytest (`-m "not integration and not e2e and not smoke"`) en
verde el 2026-11-25. Las pruebas de PostgreSQL con testcontainers corren donde hay Docker.
