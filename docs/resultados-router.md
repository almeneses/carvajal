# Resultados de la comparación de enrutadores (spec 036, T041)

Comparación del enrutador por reglas (`decide_route`), el enrutador LLM (`LLMRouter`) y el
adaptador de modelo de decisión (`DecisionRouter`, para Jev u otro modelo «System One») sobre
el conjunto versionado `data/eval/routing_questions.json` (110 preguntas: 49 previas a la 036
y 61 comerciales). Los tres cumplen el mismo contrato (`RouteDecision`), así que el grafo los
usa sin cambios (FR-013).

## Cómo se reproduce

```bash
uv run carvajal eval-router                                  # reglas
LLM_PROVIDER=deepseek uv run carvajal eval-router --router rules --router llm
DECISION_MODEL=paquete.modulo:fabrica uv run carvajal eval-router --router decision
uv run carvajal eval-router --router rules --router llm --output docs/eval/router.md
```

Métricas:

- **Ruta** e **intención**: aciertos sobre las 110 preguntas.
- **Datos**: aciertos por clave esperada en `RouteDecision.extra` (uso, temperatura,
  capacidad, cantidad, país, ciudad…).
- **Violaciones de material**: opciones que la secuencia comercial real recomendaría con un
  material de `constraints.forbidden_materials`. Debe ser 0 con cualquier enrutador, porque la
  regla la aplica `check_material` y no el enrutador; si no lo es, el enrutador extrajo mal la
  temperatura o el uso.
- **Salidas inválidas**: respuestas del modelo que no cumplen el contrato; el turno cae al
  enrutador de reglas y la razón empieza por «salida inválida del enrutador».
- **Latencia** de la decisión y **llamadas al modelo**, base del costo: el consumo de tokens
  de cada llamada queda en los logs del proveedor (`llm.usage`).

## Resultados

### Corrida 1 — 2026-11-16, DeepSeek (`deepseek-chat`)

| Enrutador | Preguntas | Ruta | Intención | Datos | Violaciones de material | Salidas inválidas | Latencia media (ms) | Latencia p95 (ms) | Llamadas al modelo |
|---|---|---|---|---|---|---|---|---|---|
| rules | 110 | 100.0% | 100.0% | 100.0% | 0 | 0 | 0.4 | 0.7 | 0 |
| llm | 110 | 99.1% | 98.2% | 95.0% | 3 | 4 | 1316.7 | 1637.7 | 110 |
| decision | — | pendiente | | | | | | | |

Costo del LLM: unos 735 tokens de entrada y 100 de salida por decisión (logs `llm.completed`),
es decir, unos 81 000 de entrada y 11 000 de salida para las 110 preguntas.

Fallos del LLM (8) y su causa:

| Pregunta | Fallo | Causa |
|---|---|---|
| `c-09` sopa para calentar en microondas | `microwave` perdido; EPS recomendado | **Error del adaptador** |
| `c-10` envases para salsas aptos para microondas | `microwave` perdido; 2 referencias no aptas | **Error del adaptador** |
| `c-03`, `c-19`, `c-21` (papel, compostables, sin icopor) | `prefers_renewable` perdido | **Error del adaptador** |
| `n-05` «Mi nombre es Laura» | `greeting` en vez de `memory_write` | Error del LLM: el prompt no lista «mi nombre es» como preferencia |
| `c-12` «contenedores para almuerzos» | pide aclaración en vez de recomendar | Etiqueta discutible: «almuerzo» no dice si es caliente |
| `c-14` «bandejas para frutas» | temperatura `ambiente` en vez de `fria` | Etiqueta discutible: la fruta entera puede ir a temperatura ambiente |

**Error del adaptador.** El esquema que recibía el LLM mostraba los booleanos entre comillas
(`"microwave": "true | null"`) y el modelo respondió el texto `"true"`, que la validación
descartaba. Las 3 violaciones de material vienen de ahí: sin «microondas», la secuencia
comercial no excluyó el EPS. Corregido en esta misma PR: el esquema muestra `true | null` sin
comillas y la validación acepta el texto `"true"`. Las 4 salidas inválidas no aparecían en la
lista porque el respaldo a reglas acertaba; desde esta corrección se listan también.

### Corrida 2 — 2026-11-16, adaptador corregido y mismo prompt

| Enrutador | Preguntas | Ruta | Intención | Datos | Violaciones de material | Salidas inválidas | Latencia media (ms) | Latencia p95 (ms) | Llamadas al modelo |
|---|---|---|---|---|---|---|---|---|---|
| rules | 110 | 100.0% | 100.0% | 100.0% | 0 | 0 | 0.4 | 0.6 | 0 |
| llm | 110 | 98.2% | 97.3% | 95.6% | 0 | 3 | 1342.1 | 1742.3 | 110 |

La corrección de los booleanos funcionó: **0 violaciones de material** (microondas ya se
respeta) y `c-09`, `c-10` y `c-19` pasan. Como ahora se listan las salidas inválidas,
aparecen fallos que en la corrida 1 quedaban ocultos detrás del respaldo a reglas. Por eso la
ruta baja de 99,1 % a 98,2 %: no es que el LLM empeorara, es que la métrica dejó de esconderlos.

Fallos del LLM (10) y su causa:

| Pregunta | Fallo | Causa |
|---|---|---|
| `r-q29`, `n-06`, `n-09` (activos, EBITDA, utilidad neta) | salida inválida: indicador no reconocido | **Vacío del adaptador**: el prompt no listaba los nombres del registro y el LLM respondió «EBITDA consolidado» |
| `c-13` «charolas para ensalada de 500 ml» | 16,9 oz en vez de 16,91 | **Error de la métrica**: exigía igualdad exacta; la búsqueda tolera ±0,25 oz |
| `n-05` «Mi nombre es Laura» | `greeting` en vez de `memory_write` | Error del LLM |
| `c-03` «vasos de papel…», `c-21` «…sin icopor…» | no marca la preferencia renovable | Error del LLM: no deduce la preferencia del material |
| `c-15` «contenedores para papas fritas» | pide aclaración de temperatura | Error del LLM: no deduce caliente de «papas fritas» |
| `c-12` «contenedores para almuerzos» | pide aclaración | Etiqueta discutible |
| `c-14` «bandejas para frutas» | temperatura `ambiente` | Etiqueta discutible |

Cambios después de la corrida 2, en esta misma PR: el prompt lista los indicadores válidos
(`activos, ebitda, empleados, ingresos_consolidados, utilidad_bruta, utilidad_neta`) y la
evaluación compara la capacidad con la tolerancia de la búsqueda. No se cambiaron las
etiquetas del conjunto ni se añadieron reglas al prompt para los errores del LLM, para no
ajustar el enrutador al examen.

### Corrida 3 — opcional

Mide el efecto de esos dos cambios. Si se corre, lo esperado son 0 salidas inválidas y unos 6
fallos: los 4 del LLM y las 2 etiquetas discutibles.

```bash
uv run carvajal eval-router --router rules --router llm --output docs/eval/router.md
```

### Corrida 4 — 2026-11-18, grupo «colombia» de la spec 039

La spec 039 añade 20 preguntas de Colombia (grupo `colombia`): 16 comerciales con la
referencia esperada y su total en COP, o la ausencia de precio declarada, y 4 aclaraciones.
Solo se corrió el enrutador por reglas, que no necesita LLM:

| Enrutador | Preguntas | Ruta | Intención | Datos | Violaciones de material | Salidas inválidas | Latencia media (ms) | Latencia p95 (ms) | Llamadas al modelo |
|---|---|---|---|---|---|---|---|---|---|
| rules | 130 | 100.0% | 100.0% | 100.0% | 0 | 0 | 1.6 | 5.0 | 0 |

Al redactar el grupo aparecieron dos huecos del router frente al catálogo nuevo: «cazuelas» y
«bowls» no se reconocían como contenedores y esas preguntas iban a la ruta documental. Se
añadieron como sinónimos de la familia `contenedor`; las 110 preguntas anteriores conservan
su ruta. Los totales en COP se verifican en `tests/test_sc_039.py` (SC-004). La latencia sube
respecto de las corridas 1 y 2 porque esta corrida se hizo en otro entorno.

### Lectura

- **Reglas.** El 100 % no mide generalización: el conjunto se escribió junto con las reglas.
  Lo que sí demuestra es la no regresión (las 49 preguntas previas conservan la ruta del router
  de `dev`) y que ninguna recomendación viola la regla de material. Es el piso de latencia y
  costo (sin llamadas al modelo).
- **LLM.** Con el adaptador corregido, no viola la regla de material y sus errores propios son
  de deducción: no infiere caliente de «papas fritas», ni la preferencia renovable de «papel»
  o «sin icopor», ni que «mi nombre es» se guarda en memoria. Las reglas aciertan esos casos
  porque se escribieron para ellos; con frases nuevas la ventaja puede invertirse. Cuesta unos 1,3 s y ~835 tokens por turno
  frente a menos de 1 ms de las reglas. La regla de material nunca depende del enrutador: la
  aplica `check_material`, pero un dato mal extraído (microondas, temperatura) sí cambia qué se
  valida, y por eso se mide.
- **Modelo de decisión.** Transferido a la spec 041: se eligió Jev cloud y allí se mide y se
  decide si se enciende. El adaptador lo carga con `DECISION_MODEL=paquete.modulo:fabrica`;
  sin él, `eval-router` termina con un error controlado y el agente sigue con reglas.

Para comparar la generalización hace falta un grupo de unas 30 preguntas redactadas por alguien
que no haya visto las reglas (grupo `ciego` en `routing_questions.json`); la spec 041 lo
incluye como requisito.

## Registrar la corrida como experimento (spec 038)

La corrida se puede guardar en LangSmith además del informe local. Es opcional:
sin la bandera el comando se comporta igual que antes y no toca la red.

```bash
export LANGSMITH_API_KEY=...          # la misma llave del monitoreo del chat
uv run carvajal eval-router --router rules --router llm --langsmith
uv run python scripts/eval_retrieval.py --langsmith
```

Qué se registra:

- un dataset `carvajal-<nombre-del-archivo>-<8 hex del SHA-256 del archivo>` con las preguntas de
  `data/eval/routing_questions.json` y, como salida de referencia, la verdad esperada
  (ruta e intención). Se crea la primera vez, se reutiliza después y solo completa los
  ejemplos que falten, así que un fallo a medias no deja el dataset vacío;
- un experimento `routing-<enrutador>-<fecha>` con **una fila por pregunta**, enlazada a su
  ejemplo del dataset por `reference_example_id`, con la predicción (ruta, intención,
  validez, latencia). Las filas se crean ya cerradas para que el servicio calcule la
  duración;
- los agregados del informe local (precisión de ruta, intención y datos, violaciones de
  material, salidas inválidas, latencia media y p95, llamadas al modelo) como metadatos
  del experimento, no como una fila suelta;
- metadatos de la corrida: `commit` (resuelto contra la raíz de este repositorio), fecha,
  enrutador, modelo y ruta del conjunto.

La evaluación de recuperación hace lo mismo sobre `carvajal-retrieval_questions-<huella>`
con Recall@10 y nDCG@10 por consulta.

**Comparar dos corridas**: en LangSmith, abre el dataset y elige *Compare* con los dos
experimentos. Las filas quedan alineadas por pregunta, así que la columna de diferencias
muestra exactamente qué preguntas cambiaron de ruta o de intención entre una corrida y
otra. En local, `git diff docs/resultados-router.md` sigue mostrando la tabla agregada.

**Si el servicio falla**, el comando imprime «Experimento no registrado: …» en `stderr`
(con el tipo de error, nunca el mensaje del servicio) y termina con código 0: el informe
local y la tabla se generan igual. Sin `LANGSMITH_API_KEY` ocurre lo mismo, con un aviso
explícito.

## Corrida 3 — 2026-11-19 (registrada como experimento)

Repetición de las dos corridas anteriores con el registro en LangSmith activo:

```bash
uv run carvajal eval-router --router rules --langsmith
uv run carvajal eval-router --router llm --langsmith
```

| Enrutador | Preguntas | Ruta | Intención | Datos | Violaciones | Inválidas | Latencia media | Latencia p95 | Llamadas |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| rules | 110 | 100,0 % | 100,0 % | 100,0 % | 0 | 0 | 0,29 ms | 0,48 ms | 0 |
| llm | 110 | 98,2 % | 97,3 % | 96,9 % | 0 | 0 | 3.082,8 ms | 5.139,9 ms | 110 |

Las dos corridas quedaron como experimentos (`routing-rules-2026-11-19T09:53:53Z`
y `routing-llm-2026-11-19T10:03:07Z`) sobre el dataset
`carvajal-routing_questions` con 110 ejemplos; comparadas pregunta a pregunta,
2 de 110 cambiaron de ruta. Desde entonces el nombre del dataset incorpora la
huella del archivo, así que una corrida nueva con las mismas preguntas creará
`carvajal-routing_questions-<8 hex>` y no reutilizará etiquetas viejas. La evidencia completa está en
[`docs/evidence/m2/monitoring.md`](evidence/m2/monitoring.md).

Las cifras difieren de la corrida 2 porque el adaptador del enrutador por LLM
cambió desde el 2026-11-16 y ahora el experimento mide la latencia real de cada
pregunta, incluido el reintento cuando el modelo no devuelve JSON válido.
