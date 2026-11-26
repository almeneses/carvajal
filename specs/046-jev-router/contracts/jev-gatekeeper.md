# Contrato — Gatekeeper y compuerta de afirmaciones con Jev (spec 046)

Define qué se pregunta a Jev, qué se le envía, cómo se interpreta la respuesta, qué pasa
cuando falla y qué queda visible. Las formas de los valores están en
[data-model.md](../data-model.md); las decisiones, en [research.md](../research.md). Los
textos de las preguntas son prompts: viven en `prompts.py` y cualquier cambio exige repetir
su medición (principio III).

## 1. Flujo del turno (modo `tools`)

```text
recall ─► shortcut (gatekeeper) ─┬─ scope=direct ──► direct (sin herramientas) ─┐
                                 └─ scope=rag|structured|all ─► agent ⇄ tools ───┤
                                                                                  ▼
                       _TurnStream: compuerta determinista ─► compuerta de afirmaciones ─► emitir
```

Gatekeeper (`decide_gate`, nodo `shortcut`):

```text
1. Reglas de conversación (rule_decision): greeting | farewell | memory_forget
   └─► scope=direct, source=reglas                                   (sin Jev, sin consultas)
   (el nodo shortcut aplica el olvido; si falla, rule=memory_unavailable)
2. decide_route → memory_write             ─► scope=all,        source=reglas   (sin Jev)
   (también declares_memory: «recuérdalo», «soy Ana», «trabajo en…», «suelo comprar en…»)
3. _commercial_follow_up(question, ctx)    ─► scope=structured, source=reglas   (sin Jev)
4. JEV_ROUTER_MODE≠off y judge.available() ─► Choice (una llamada, plazo JEV_TIMEOUT_S)
     ├─ JudgeUnavailable(status)           ─► scope=all, source=llm, reason=sin_jev:<status>
     ├─ shadow                             ─► scope=all, source=jev, reason=sombra
     ├─ desacuerdo fuerte con las reglas   ─► scope=all, cautious, reason=desacuerdo_reglas
     ├─ reglas ven commercial_mixed        ─► scope=all, reason=mixta_reglas
     ├─ confidence < umbral (o < umbral de directa para respuesta_directa)
     │                                     ─► scope=all, cautious, reason=confianza_baja[_directa]
     ├─ rag y commercial_signal(pregunta)  ─► scope=all, reason=producto_reglas (research R16)
     └─ si no                              ─► scope=map(clase), reason=aplicada
5. Sin Jev                                 ─► scope=all, source=llm, reason=sin_jev:off
```

Con `source=llm`, al cerrar el turno se deriva `llm_class` del `route_group` de las consultas
ejecutadas (solo `rag` → `rag`; solo `structured` → `herramienta_estructurada`; ambas →
`mixta`; solo `sin_consulta` o ninguna → `respuesta_directa`), sin costo ni red. Sin Jev no
hay cautela (decisión del owner: sin Jev, «como hoy»).

## 2. Petición del gatekeeper (Choice)

`POST {JEV_BASE_URL}/systemone`, `Authorization: Bearer <TYPESAFE_API_KEY>`,
`Content-Type: application/json`.

```json
{
  "model": "jev-1.14.0",
  "state": {
    "pregunta": "<outgoing_question(pregunta), ≤ 1 000 caracteres>",
    "contexto": "<outgoing_question(pregunta anterior), ≤ 160 caracteres; la clave se omite si no aplica>"
  },
  "questions": {
    "ruta": {
      "type": "choice",
      "instructions": "JEV_ROUTE_INSTRUCTIONS",
      "criteria": {
        "herramienta_estructurada": "JEV_ROUTE_CRITERIA[herramienta_estructurada]",
        "rag": "JEV_ROUTE_CRITERIA[rag]",
        "mixta": "JEV_ROUTE_CRITERIA[mixta]",
        "respuesta_directa": "JEV_ROUTE_CRITERIA[respuesta_directa]"
      }
    }
  }
}
```

Textos iniciales (`prompts.py`; se miden con `gatekeeper_046.json` antes de adoptarlos):

- `JEV_ROUTE_INSTRUCTIONS`: «Decide cómo debe atender el asistente de Carvajal el mensaje
  `pregunta`. Si hay `contexto`, es la pregunta anterior de la misma persona. El asistente
  puede consultar herramientas estructuradas (catálogo de empaques, precios, sedes, cifras
  financieras) o buscar en los documentos de Carvajal.»
- `herramienta_estructurada`: «Pide datos del catálogo de empaques (productos, materiales,
  presentaciones, fichas o comparaciones), precios o cotizaciones, dónde comprar, sedes o
  distribuidores, una cifra financiera o de empleados de un período, una fecha o un día
  hábil, una cotización guardada, o lo que el asistente recuerda de la persona.»
- `rag`: «Pregunta por Carvajal como organización: historia, fundadores, empresas y marcas
  del grupo, sectores, sostenibilidad, gobierno corporativo, noticias, informes o preguntas
  frecuentes, sin pedir precios, productos para comprar ni la cifra de un período.»
- `mixta`: «Pide a la vez algo de herramienta_estructurada y algo de rag en el mismo
  mensaje.»
- `respuesta_directa`: «No pide ningún dato: saludo, agradecimiento, despedida, charla,
  preguntas sobre el propio asistente o cómo usarlo, o un comentario que no necesita
  consultar nada.»

Solo en la medición (`with_score=True`), la misma petición añade:

```json
"claridad": {
  "type": "score",
  "instructions": "¿Qué tan clara es la intención de `pregunta` para decidir cómo atenderla?",
  "criteria": [
    "No se entiende qué pide",
    "Admite varias lecturas muy distintas",
    "Se entiende, pero falta un dato clave",
    "Clara",
    "Totalmente clara y sin ambigüedad"
  ]
}
```

Según `openapi.json`, el nivel de Score es la posición en `criteria` empezando en cero: con
estos cinco criterios, `score` ∈ [0, 4]. La API devuelve el **valor esperado** del nivel, un
número real (p. ej. `2.5`), no un entero (D1 de `evidencia.md`): el adaptador acepta un real
en [0, 4]. Se guardan `clarity` (ese valor) y `clarity_confidence` (el `confidence` de la
respuesta Score).

### Respuesta esperada

```json
{
  "model": "jev-1.14.0",
  "answers": {
    "ruta": {
      "type": "choice",
      "choice": "rag",
      "confidence": 0.83,
      "probabilities": {
        "herramienta_estructurada": 0.09, "rag": 0.83, "mixta": 0.05, "respuesta_directa": 0.03
      }
    }
  },
  "usage": {"input_tokens": 412, "output_tokens": 9}
}
```

Validación (si falla → `JudgeUnavailable("invalid")`): HTTP 200; `answers.ruta.type ==
"choice"`; `choice` es una de las cuatro claves; `probabilities` trae las cuatro con valores
en [0, 1]; `confidence` en [0, 1] (si falta, se usa la mayor probabilidad y se registra);
`usage` con enteros. `model` puede diferir del pedido: se registra el devuelto.

## 3. Petición de la compuerta (Noul)

```json
{
  "model": "jev-1.14.0",
  "state": {
    "evidencia": [
      {"consulta": "dicho_por_la_persona", "texto": "cantidad: 500; familia: vaso; capacidad: 9 oz; ciudad: Cali"},
      {"consulta": "buscar_producto", "texto": "<backing_data compacto, recortado a su cuota>"},
      {"consulta": "buscar_corpus", "texto": "[3] <fragmento citado>"}
    ],
    "respuesta": "<outgoing_answer(respuesta), ≤ 8 000 caracteres>"
  },
  "questions": {
    "respaldo": {
      "type": "noul",
      "instructions": "JEV_CLAIMS_INSTRUCTIONS",
      "criteria": {"true": "JEV_CLAIMS_CRITERIA[true]", "false": "JEV_CLAIMS_CRITERIA[false]"}
    }
  }
}
```

La pregunta no viaja (decisión del owner: a la compuerta, la respuesta y la evidencia
mínima). Lo que la persona dijo y puede aparecer en la respuesta va como el extracto
`dicho_por_la_persona`: solo los valores estructurados de la extracción de las reglas
(cantidad, capacidad o medida, familia, uso, temperatura, ciudad, indicador, período),
pasados por `redact`; si no hay ninguno, el extracto no se envía.

Desde el 2026-11-26 (evidencia §10.3: los pedidos del dato que repetían lo dicho dos turnos
antes eran la clase más clara de falsos positivos):

- `dicho_por_la_persona` suma, tras `| en mensajes anteriores: `, los valores estructurados
  de los mensajes anteriores de la ventana del turno (semilla posterior a «olvidar todo» y
  `user_quotes` del resumen) que el turno no repite. Dos valores distintos del mismo campo
  viajan los dos, separados por « / ». Nunca el texto de esos mensajes.
- `borrador_de_cotizacion` (si el hilo tiene borrador de la spec 054 no cancelado): por
  línea, el producto, las unidades, la ciudad y el subtotal que calculó el motor; con una
  lista de referencias por elegir, sus nombres y la cantidad y la ciudad de la solicitud.
  Sin folio, vendedor ni contactos; cada línea pasa por `redact`.

```json
{"consulta": "dicho_por_la_persona", "texto": "cantidad: 300; familia: tapa | en mensajes anteriores: cantidad: 500; familia: vaso; capacidad: 9 oz; ciudad: Bogotá"}
{"consulta": "borrador_de_cotizacion", "texto": "1. Plato espumado pando 12 cm Wau · 500 und · Bogotá · subtotal 43700 COP"}
```

Textos iniciales (los de la prueba del 2026-11-24, 19/20 con etiquetas corregidas, con la
referencia a `pregunta` cambiada a `dicho_por_la_persona` y las reglas del owner y de la
revisión marcadas con ⁺; se miden con `claims_046.json`):

- `JEV_CLAIMS_INSTRUCTIONS`: «¿Todas las afirmaciones de hecho de `respuesta` están
  respaldadas por `evidencia` (resultados de las consultas del asistente y lo que dijo la
  persona, en `dicho_por_la_persona`)?»
- `true`: «Cada dato de la respuesta (productos, SKU, materiales, capacidades, unidades por
  caja, precios, direcciones, teléfonos, plazos de entrega, certificaciones, prácticas de la
  empresa, cifras financieras) aparece en `evidencia` (incluido `dicho_por_la_persona`). Decir
  que un dato no está publicado, rechazar algo de la pregunta, hacer preguntas a la persona u
  ofrecer ayuda no son afirmaciones que necesiten respaldo. ⁺Un mismo dato con otro formato
  (un teléfono con el indicativo actualizado o con otros separadores, una cifra con otro
  separador de miles, una unidad equivalente) cuenta como respaldado. Lo que la respuesta
  atribuye a lo que la persona dijo antes no se evalúa. ⁺Los marcadores `[dato de la
  persona]`, `[dato omitido]` y `[folio]` reemplazan datos de la persona y no se evalúan.»
- `false`: «Al menos una afirmación de hecho de la respuesta no aparece en `evidencia`, o la
  contradice (por ejemplo un precio, un plazo, una certificación, un
  teléfono o una práctica de la empresa que ninguna consulta devolvió). ⁺Una valoración o
  recomendación presentada como hecho («la más usada», «la preferida», «la mejor») que la
  evidencia no dice cuenta como no respaldada.»

Respuesta esperada: `{"answers": {"respaldo": {"type": "noul", "noul": 0.22}}, "model": …,
"usage": …}`. Validación: `noul` en [0, 1]. Veredicto: `unsupported` si `noul <
JEV_CLAIMS_THRESHOLD` (0,3 desde el 2026-11-26; antes 0,5, ver `evidencia.md` §10.7);
`supported` si no.

### Cuándo se juzga

| Caso | ¿Se llama? | `claims.status` |
|---|---|---|
| Compuerta `off`, juez no disponible al empezar el turno | no | `off` |
| Atajo por reglas (`source=reglas`, `scope=direct`) | no | `skipped` (`atajo`) |
| Abstención (camino `abstain` o rechazo determinista) | no | `skipped` (`abstencion`) |
| Reemplazo neutro (petición de dato, sedes sin precio) | no | `skipped` (`reemplazo`) |
| Turno solo de memoria | no | `skipped` (`memoria`) |
| Texto formado solo por preguntas | no | `skipped` (`solo_preguntas`) |
| Respuesta de más de 8 000 caracteres | no | `skipped` (`demasiado_larga`) |
| Respaldo determinista del servicio (`fallback`) | no | `skipped` (`reemplazo`) |
| Respuesta directa decidida por Jev (`source=jev`, `scope=direct`) | no | `skipped` (`directa`) |
| No queda plazo del turno para la llamada (≤ `JEV_TIMEOUT_S` + 0,2 s) | no | `unavailable` (`sin_plazo`) |
| Cuota de la compuerta agotada en una corrida de evaluación | no | `unavailable` (`cuota`) |
| Texto del modelo en `evidence` o `no_query` que pasó la compuerta determinista | sí | `supported` / `unsupported` / `unavailable` |

La respuesta directa (por reglas o decidida por Jev) no se juzga: no consulta nada, su
instrucción prohíbe cifras, fechas y nombres de Carvajal y ya pasa el filtro determinista de
`no_query`. Con evidencia vacía, Jev marcaría como no respaldadas frases propias del
asistente («soy un asistente virtual de Carvajal») y la persona recibiría `NO_DATA_ANSWER`.

### Sustitución

`unsupported` → el texto del modelo no se emite. Se emite `_replacement()` si devuelve texto
(conserva fuentes); si no, y el turno solo consultó búsquedas deterministas (`buscar_producto`,
`buscar_sedes`; desde el 2026-11-26), `presented_data()`: las opciones aptas (nombre, SKU,
material, capacidad y cita, como las tarjetas) y las sedes del directorio, sin redacción del
modelo; si no, `_abstain()` (abstención ajustada a lo consultado, sin fuentes). Con una
cotización de `cotizar` el reemplazo sigue siendo la abstención, que el servicio cambia por la
plantilla de opciones o de la línea agregada (spec 054). `claims.substituted = true`, `ToolAgentFinal.abstained` según el caso. Se persiste
y se devuelve por la API y por WhatsApp el texto sustituto.

### Retención

Con `claims.status` distinto de `off` al empezar el turno, en el camino `evidence` el paso
final del modelo se acumula entero (como `no_query`) y, la primera vez que se retiene texto,
el streaming emite una vez:

```text
event: status
data: {"text": "Reviso que lo que te digo tenga fuente…", "step": <siguiente>}
```

Con `off`, la retención es la de la 044 (48 caracteres). La abstención anticipada del
camino `abstain` no cambia.

Plazo del turno: la llamada a Noul se hace dentro del plazo de la 044
(`agent_turn_timeout_s`). Solo se llama si queda más de `JEV_TIMEOUT_S` + 0,2 s; si no,
`claims.status="unavailable"` (`sin_plazo`) y se emite lo que pasó la compuerta
determinista. Con texto final ya retenido, el streaming marca `begin_text()` antes de la
llamada para que el plazo no descarte la respuesta redactada.

## 4. Errores y respaldo

| Situación | `JevStatus` | Efecto en el gatekeeper | Efecto en la compuerta | Circuito |
|---|---|---|---|---|
| Sin clave o `JEV_ENABLED=false` | `off` | reglas + LLM | texto de la compuerta determinista | — |
| Tope del día alcanzado | `cap` | ídem | ídem | — (log `jev.cap_reached` una vez) |
| Circuito abierto | `circuit_open` | ídem | ídem | — |
| Más de `JEV_TIMEOUT_S` | `timeout` | ídem | ídem | +1 fallo |
| Red, 429, 5xx, 529 | `error` | ídem | ídem | +1 fallo |
| 400, 413, 415, 422 | `error` | ídem | ídem | +1 fallo; log con la categoría |
| 401, 402, 403 | `auth` | ídem | ídem | abre 15 min; log `jev.auth_or_credits` una vez |
| 200 con forma inválida | `invalid` | ídem | ídem | +1 fallo |

Tres fallos seguidos abren el circuito 60 s. Ningún fallo lanza hacia el turno ni se
muestra a la persona. Sin reintentos dentro del turno.

## 5. Privacidad

**Se envía**:

| Llamada | Campos | Transformación |
|---|---|---|
| Gatekeeper | `pregunta` | `outgoing_question`, ≤ 1 000 caracteres |
| Gatekeeper | `contexto` (opcional) | pregunta anterior de la persona, solo si la actual es elíptica o tiene ≤ 6 palabras; `outgoing_question`, ≤ 160 |
| Compuerta | `respuesta` | `outgoing_answer`, ≤ 8 000 caracteres |
| Compuerta | `evidencia` | datos de las consultas del turno que pueden respaldar (criterio de `_tool_backing`), sin `sin_consulta`, memoria ni errores; del corpus, solo fragmentos citados si hay citas; folios como `[folio]`; tope `JEV_MAX_EVIDENCE_CHARS` |
| Compuerta | `evidencia` (`dicho_por_la_persona`) | solo los valores estructurados que la persona dio en el turno y en los mensajes anteriores de la ventana (extracción de las reglas), pasados por `redact`; se omite si no hay ninguno |
| Compuerta | `evidencia` (`borrador_de_cotizacion`) | líneas del borrador (producto, unidades, ciudad, subtotal del motor) y referencias por elegir; sin folio ni contactos; `redact`; se omite sin borrador o si está cancelado |

`outgoing_question(text, memory)`: `redact(text)` (045) → enmascara con `[dato de la
persona]` el nombre y la empresa que `classify(text)` detecta (la empresa, solo con un
marcador fuerte —«trabajo en», «mi empresa es», «mi negocio se llama»— o con forma de nombre
propio; nunca una ciudad) y los valores de `nombre`, `empresa` y notas de la memoria →
recorta.

`outgoing_answer(answer, question, memory, payloads)`: **no** aplica `redact` completo
(borraría los teléfonos del directorio y Jev no podría verificar ni un teléfono inventado ni
uno reformateado). Sustituye literalmente solo los tramos que el filtro de la 045 encuentra
en la pregunta (`sensitive_spans(question)`), el nombre, la empresa y las notas de la
memoria, y los folios de las cotizaciones de la persona (`[folio]`) → recorta. Ejemplos:
«llama a la sede al (601) 555 1234» se conserva; «te escribo al 300 123 4567», si la persona
dio ese celular, queda «te escribo al [dato omitido]».

**Nunca se envía**: la pregunta completa a la compuerta, folios de cotizaciones,
`owner_key`, `thread_id`, `conversation_id`, número de WhatsApp,
cookies, cabeceras de la sesión, la memoria del dueño (como bloque), el resumen de la
conversación, el contexto comercial, respuestas de turnos anteriores, `seed_turns` (salvo la
línea de contexto y los valores estructurados de `dicho_por_la_persona`), el borrador como
bloque (solo `borrador_de_cotizacion`), IP ni `request_id`.

**Nunca se registra** (logs, traza SSE, LangSmith con cualquier `content`, Sentry, libro de
llamadas):
ningún campo de `state`. Solo `JudgeCall`, la decisión o el veredicto, las probabilidades o
la confianza y el umbral. Las excepciones del adaptador y de `agent/gatekeeper/` se
registran sin `exc_info`; Sentry va con `include_local_variables=False` o con un
`before_send` que elimina esos frames.

## 6. Visibilidad

### Evento `trace` del chat (campos nuevos)

```json
{
  "gatekeeper": {
    "gate_class": "rag",
    "scope": "rag",
    "source": "jev",
    "confidence": 0.83,
    "cautious": false,
    "rule": "",
    "reason": "aplicada",
    "jev_status": "ok",
    "llm_class": null
  },
  "claims": {"status": "supported", "p": 0.91, "substituted": false}
}
```

En `routes`: `{"gate_class": "<map_route>", "scope": "", "source": "reglas", …}` y
`claims: {"status": "off"}`. El evento sigue sin mostrarse sin `?debug=1`; con él, el panel
añade «Decisión: rag · jev 0,83» (o «saludo · reglas», «todas · llm», «cautela · jev 0,41»).

### LangSmith

Raíz del turno: `gatekeeper_route`, `gatekeeper_scope`, `gatekeeper_source`,
`gatekeeper_llm_class`,
`gatekeeper_confidence`, `gatekeeper_cautious`, `gatekeeper_rule`, `claims_status`,
`claims_p`. Un hijo `jev` (`run_type="tool"`) por llamada con `kind`, `status`, `latency_ms`,
`model`, `input_tokens`, `output_tokens` y `choice`/`confidence`/`probabilities` o
`p_supported`/`threshold`. Sin entradas ni salidas de texto, también con `content=full`.

### `/health`

Bloque `jev` (data-model). No llama a TypeSafe; no cambia el `status` global (Jev es
opcional para atender).

Bloque `claims` (añadido el 2026-11-26, `carvajal.agent.gatekeeper.stats`): contadores
de la compuerta de afirmaciones **de este proceso** desde que arrancó (se reinician con el
contenedor), sin texto ni red y sin cambiar el `status` global.

| Clave | Significado |
|---|---|
| `mode` | `enforce`, `shadow` u `off` (`off` si el bloque `agent` no espera `tools` —`AGENT_MODE`, proveedor o DeepSeek incompleto—, sin Jev encendido o sin juez en el runtime) |
| `threshold` | `JEV_CLAIMS_THRESHOLD` |
| `since` | arranque del proceso (UTC) |
| `turns` | turnos del modo `tools` contados (cada uno con un solo estado) |
| `judged`, `supported`, `unsupported` | respuestas con veredicto de Jev |
| `substituted` | no respaldadas sustituidas (`enforce`; en `shadow` queda en 0) |
| `substitution_rate`, `unsupported_rate` | sobre `judged`; `null` sin juzgadas |
| `unavailable` | sin veredicto por motivo (`timeout`, `sin_plazo`, …); incluye los turnos en que Jev no estaba disponible al empezar (`circuit_open`, `cap`): el veredicto del turno es `off` (data-model), pero aquí no se confunde con la compuerta apagada |
| `skipped` | no juzgadas por motivo (`atajo`, `directa`, `reemplazo`, `solo_preguntas`, …) |
| `off` | turnos con la compuerta apagada por configuración o sin juez |
| `p_histogram` | 10 cubetas de `p` de las juzgadas: `[0; 0,1)`, …, `[0,9; 1]` |

El servidor MCP de WhatsApp corre el agente en otro proceso: publica el mismo bloque en su
`GET /health`, con el bearer y la defensa contra DNS rebinding de `/mcp` (`curl -H "Authorization: Bearer $MCP_TOKEN"
127.0.0.1:8765/health`). El evento `claims.judged` añade `tools` (nombres de las
herramientas del turno) y `answer_chars` (largo del texto juzgado), sin texto.

## 7. Bloques nuevos del sistema del agente

Al final del `SystemMessage` del turno (después de `<resumen_conversacion>`), solo cuando
aplican, para no mover el prefijo de `AGENT_SYSTEM`:

```text
<alcance_del_turno>
En este turno puedes consultar: buscar_corpus, preguntas_frecuentes, historia_carvajal,
empresas_del_grupo, sin_consulta.
</alcance_del_turno>
```

```text
<cautela>
GATEKEEPER_CAUTION
</cautela>
```

`GATEKEEPER_CAUTION` (prompts.py, inicial): «No está claro qué pide la persona. Consulta
antes de afirmar cualquier dato. Si la pregunta admite varias lecturas, pide la aclaración
con sin_consulta (motivo pedir_dato) en una sola pregunta concreta. Si lo que pide no está en
tus consultas, dilo y ofrece lo que sí puedes buscar: productos, precios, dónde comprar o
datos de Carvajal. No inventes canales de contacto: usa solo los que devuelvan tus
consultas. ⁺Si el mensaje es solo conversación (saludo, agradecimiento), responde con
sin_consulta (motivo conversacion) sin buscar.»

`DIRECT_INSTRUCTIONS["gatekeeper_direct"]` (respuesta directa decidida por Jev): «La pregunta
no necesita datos de Carvajal. Responde en una o dos frases, sin cifras, fechas ni nombres de
Carvajal, y ofrece el siguiente paso. Si en realidad pide datos de Carvajal, dilo y pide que
la precise para buscarlos.»

Una llamada a una consulta fuera del alcance recibe:

```json
{"status": "error", "message": "Esa consulta no está disponible en este turno.", "error": "fuera_de_alcance"}
```

Gasta cupo, no se ejecuta y se registra `tool_agent.out_of_scope_call`.
