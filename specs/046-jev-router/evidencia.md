# Evidencia — Spec 046 (router y System One con Jev)

Registro de la medición de la fase 8 (T052–T057 y T065). Cada corrida con proveedor real se
hizo **una sola vez** el 2026-11-24 entre las 14:58 y las 15:12 UTC, salvo la del gatekeeper,
que se repitió una vez por un defecto del adaptador (§2.1). Cada cifra es una observación,
no una estimación con margen. No se arregló ninguna regresión: se documentan con su
reproducción (§9).

- **Código medido**: commit `9326698` (rama `feat/046-jev-router`), sin cambios en el código.
- **Entorno**: `AGENT_MODE=tools`, `LLM_PROVIDER=deepseek`, `DEEPSEEK_MODEL=deepseek-flash`,
  Qdrant local con `carvajal_chunks` (40 792 puntos), `nomic-embed-text` en Ollama,
  `MONITORING_ENABLED=false`, `LANGSMITH_TRACING=false` (sin envío de trazas).
- **Jev**: `jev-1.14.0` fijado en la sesión (`JEV_MODEL=jev-1.14.0`). El `.env` local del
  owner trae `JEV_MODEL=jev-latest`; para medir el modelo de research R9 se sobrescribió en
  el entorno, sin tocar el `.env`. El `.env` de producción debe llevar `jev-1.14.0` (T060).
  Umbrales vigentes: `JEV_ROUTER_MIN_CONFIDENCE=0.6`, `JEV_ROUTER_DIRECT_MIN_CONFIDENCE=0.8`,
  `JEV_CLAIMS_THRESHOLD=0.5`, `JEV_TIMEOUT_S=0.8`, `JEV_CLAIMS_MODE=enforce`.
- **Precios**: Jev 0,042 USD por millón de tokens de entrada, salida gratis (precio de lista
  de `docs/research/jev.md`). DeepSeek 0,30 / 1,20 USD por millón de entrada / salida, sin
  descontar caché (los de la 044 y la 045, no reverificados hoy).
- **Artefactos** (tal cual salieron), en [`evaluacion/`](evaluacion/):
  `gatekeeper-r1-invalido.{md,json}` (primera corrida, inválida), `gatekeeper.{md,json}`,
  `gatekeeper-reglas-130.md`, `claims.{md,json}`, `v3-sin-jev.{md,json}`,
  `v3-con-jev.{md,json}`, `gatekeeper-replay.md` y el libro `jev-calls.jsonl`.

## 0. Resultado frente a los criterios de éxito

| Criterio | Meta | Resultado | ¿Cumple? |
|---|---|---|---|
| SC-001 Gatekeeper | Jev ≥ 16/20; aplicadas ≥ 90 %; 0 con datos a la directa | Jev **20/20**; aplicadas 100 % (20/20); 0 con datos a la directa. Reglas 14/20, LLM 18/20 (§2) | ✅ (medido con un parche en memoria para Score, §2.1) |
| SC-002 Cero Jev en saludos y similares | 0 llamadas y 0 consultas | Verde sin red (`make check`). El v3 no tiene saludos: no se ejercitó en vivo. Los 2 turnos del v3 decididos por reglas no llamaron a Jev | ✅ sin red |
| SC-003 Compuerta | ≥ 9/10 detectadas; ≤ 1/6 falsas alarmas | **10/10** detectadas; **0/6** falsas alarmas; v3-027 «(601)» p = 0,58 (respaldada) (§3) | ✅ |
| SC-004 v3 con frente a sin Jev | resueltos ≥ sin − 1; violaciones y sin fuente sin subir; ≥ 90 % con decisión de Jev | Estricto **28/40 frente a 29/40** (en el límite); con equivalencias 28 frente a 30 (−2). Violaciones 0 / 0; sin fuente 1 / 1. Decisión de Jev en 45/45 turnos elegibles (100 %). 4 turnos sin gatekeeper y 10 sin compuerta por cuota (§4) | ⚠️ cumple en estricto; −2 con equivalencias; cobertura incompleta por cuota |
| SC-005 Sin Jev | 100 % sin red; sin excepción en el turno | Verde sin red. En vivo, la primera corrida del gatekeeper dio 3 `invalid`, abrió el circuito y siguió sin excepción (§2.1) | ✅ |
| SC-006 Latencia | p95 por llamada ≤ 0,5 s; fin p95 ≤ sin + 1,0 s y < 20 s | p95 por llamada **231 ms** (n = 75); fin p95 **5,84 s frente a 5,30 s** (+0,54 s). Primer texto p95 5,84 s (= fin) frente a 4,19 s (§5) | ✅ |
| SC-007 Sin datos personales | 0 en cargas, logs, trazas y libro | Verde sin red. El libro solo tiene claves sin texto (§7) | ✅ |
| SC-008 Presupuesto | ≤ 120 llamadas | **116** en el libro (plan: 112; +4 por el incidente de §2.1) | ✅ (quedan 4, §7) |
| SC-009 `gatekeeper` y `claims` en la traza | 100 % de los turnos | 40/40 ítems en cada corrida del v3 traen ambos campos; verde sin red. LangSmith no se verificó en vivo (trazas apagadas) | ✅ |
| SC-010 Sin regresiones | `make check`, 045 y 18/18 | `make check`: Ruff y **4413 pruebas** en verde; cotizador **18/18** | ✅ |

**Lectura.** Jev clasifica bien el conjunto del gatekeeper (20/20) y la compuerta separa
con holgura las 16 respuestas. En el v3 el balance es neutro en estricto (28 frente a 29) y
negativo con equivalencias (−2). Hay una regresión atribuible a Jev: v3-003, donde el alcance
`rag` quitó `buscar_producto`. Además, Jev manda a la respuesta directa los seguimientos de
varias vueltas; la cautela con `DIRECT_MIN=0,8` los contuvo. Por cuota, el bloque de
abstención y adversarial del v3 se midió casi sin compuerta. Las cinco sustituciones de la
compuerta no se pueden auditar sobre el texto rechazado, que no se guarda (§4.3). La decisión
de activar Jev es del owner.

## 1. Verificación sin Jev (T052, SC-010)

- `make check` (Ruff, formato y pytest) en un entorno limpio: **4413 passed**, 63
  deselected.
- `make eval-cotizador`: **18/18 casos OK**.
- **Limitación de aislamiento**: con las variables del `.env` exportadas en la shell
  (`set -a; . ../../.env; set +a; make check`) fallan 13 pruebas que leen valores por
  defecto del entorno: `test_settings` (3), `test_monitoring` (5), `test_chat_tracing` (3),
  `test_agent_mode` (1) y `test_chat_service_tools` (1). No es efecto de Jev. No se comprobó
  si ocurre igual en `main`.

## 2. Gatekeeper (T053, SC-001, research R4 y R5)

### 2.1 Incidente: Score inválido en la primera corrida

La corrida de la sección 4.1 del quickstart (`--smoke --judge rules --judge llm --judge jev
--max-jev-calls 21`) gastó 4 llamadas: humo `ok` (288 ms) y 3 `invalid` (gk-01 a gk-03).
Tras el tercer fallo, el circuito se abrió y los 17 ítems restantes quedaron en
`circuit_open`, sin llamada y sin excepción. El informe quedó en
`evaluacion/gatekeeper-r1-invalido.md`.

**Diagnóstico** (1 llamada anotada en el libro como `run=diagnostico`; solo se imprimió la
estructura de la respuesta, sin la clave): la API devuelve `answers.claridad.score` como
**número real** (valor esperado, p. ej. `2.5`), con `probabilities` por nivel y una `legend`.
`JevJudge.classify(with_score=True)` exige un entero en [0, 4], así que toda respuesta con
Score es `invalid`. La parte Choice (`choice`, `confidence`, `probabilities` con las cuatro
clases) viene como la espera el adaptador. **En producción no hay efecto**: allí se llama sin
Score (`with_score=False`). Reproducción sin red en §9 (D1).

**Repetición** (20 llamadas, `run=gatekeeper`): la misma orden `eval-gatekeeper` (`--judge
rules --judge llm --judge jev --max-jev-calls 20`, sin el humo ya hecho). Se corrió con un
conductor fuera del repositorio que cambia **en memoria** solo esa validación y acepta un
número en [0, 4]. El resto de `classify` es una copia literal, y la parte Choice es idéntica
a la de producción. El código del repositorio no cambió. Esta repetición es la única
desviación de «una corrida» de research R11, y sus 20 llamadas caben en el presupuesto.

### 2.2 Exactitud por juez (`gatekeeper_046.json`, n = 20)

| Juez | Exactitud | Errores |
|---|---|---|
| Reglas | 70 % (14/20) | gk-07 (`mixta` por `herramienta_estructurada`) y las 5 directas (gk-16 a gk-20) como `rag` |
| LLM (DeepSeek) | 90 % (18/20) | gk-07 (`mixta`) y gk-17 («no entendí tu última respuesta») como `rag` |
| **Jev** | **100 % (20/20)** | ninguno |

Confianza de Jev: media 0,95; mínima 0,59 (gk-14, mixta, que aplica con alcance `all` por la
regla `commercial_mixed`). Latencia p50 / p95: 146 / 255 ms (23 llamadas de la sección).
Sobre las 130 preguntas de `routing_questions.json`, las reglas aciertan 130/130 (0
llamadas). Sus etiquetas salen de las propias reglas, así que esa cifra no prueba
generalización (research R11).

### 2.3 Umbrales elegidos (regla de research R4)

| Umbral | Aplicadas | Exactitud | Cobertura | Datos → directa |
|---|---|---|---|---|
| 0,5 | 20 | 100 % | 1,00 | 0 |
| 0,6 | 19 | 100 % | 0,95 | 0 |
| 0,7 | 19 | 100 % | 0,95 | 0 |
| 0,8 | 19 | 100 % | 0,95 | 0 |

- **`JEV_ROUTER_MIN_CONFIDENCE` = 0,6 (sin cambio)**: todos los valores empatan en exactitud
  con cobertura ≥ 70 %; la rejilla no separa y, por la regla, se queda el valor inicial.
- **`JEV_ROUTER_DIRECT_MIN_CONFIDENCE` = 0,8 (sin cambio)**: ningún valor de la rejilla manda
  preguntas con datos a la directa (las 5 directas tienen confianza ≥ 0,95). La rejilla
  tampoco separa, así que se queda el valor inicial. El informe de esta corrida
  (`evaluacion/gatekeeper.md`, tal cual salió) imprime «0,6 (la rejilla no separa: se queda el
  valor vigente)», que se contradice; el generador ya está corregido (D2 en §9) y hoy
  imprimiría 0,6 y 0,8 con la nota de cada umbral. **El valor vigente y recomendado es 0,8.**
- **Confirmación fuera del conjunto** (no sirvió para elegir): en el v3, Jev mandó tres
  seguimientos con datos a `respuesta_directa` (§4.2). Dos tenían 0,72 y 0,67: con 0,6 habrían
  ido a la respuesta directa sin consultar, y con 0,8 fueron a cautela.
- `settings.py` y `.env.example` no cambian.

### 2.4 Score (research R5)

Con 0 errores de Choice no hay con qué comparar la confianza de Choice y la claridad de
Score como predictores de error. La claridad media fue 2,52 sobre 4 (rango 1,16–3,84; las
mixtas, las más bajas: 1,16 y 1,47). **Decisión: Score sigue fuera de producción**, como
fijó R5. Además, su análisis en `with_score=True` no es compatible con la API (D1).

### 2.5 Reproducción de cautela y respuesta directa (T065, FR-032)

`eval-gatekeeper --replay-ledger … --agent --only cautious,direct`: **0 llamadas a Jev**,
solo DeepSeek. En `gatekeeper_046.json` ningún ítem terminó en cautela. Los 5 directos
(gk-16 a gk-20) se respondieron por el camino `shortcut` con `gatekeeper_direct`, **sin
consultar datos**, con respuestas correctas en contenido y tono. En gk-19, «te atiendo en
español y en el idioma en que me escribas» es una afirmación de capacidad sin consulta.
Parece cierta, pero no se verificó, y R3 (§9) muestra que el texto neutro solo existe en
español. **`GATEKEEPER_CAUTION` no se ejercitó en la reproducción**: se mide
con los 4 turnos de cautela del v3 (§4.2).

## 3. Compuerta de afirmaciones (T054, SC-003)

`eval-claims` sobre `claims_046.json`: 16 llamadas, 16 juzgadas, 0 fallos. Latencia p50 /
p95: 146 / 296 ms.

| Umbral | Detección (no respaldadas) | Falsas alarmas (buenas) |
|---|---|---|
| 0,3 | 10/10 | 0/6 |
| 0,4 | 10/10 | 0/6 |
| **0,5 (vigente)** | **10/10** | **0/6** |
| 0,6 | 10/10 | 1/6 |
| 0,7 | 10/10 | 2/6 |

- No respaldadas: p entre 0,02 y 0,13 (v3-001 original 0,06; v3-005 original 0,13).
- Buenas: p entre 0,58 y 0,93. Las de margen más estrecho son v3-027 (teléfono «(601)»,
  0,58) y v3-034 (0,60): el umbral 0,5 se mantiene, y subirlo a 0,6 ya da una falsa alarma.

## 4. v3 en modo `tools`: con Jev frente a sin Jev (T055, T056, SC-004)

Mismo commit, mismo día y una corrida cada uno, con 40 ítems y 51 turnos. Sin Jev: 0
llamadas. Con Jev: `--jev-ledger` y cuotas de 45 (gatekeeper) y 30 (compuerta), **agotadas
las dos** (75 llamadas, todas `ok`).

### 4.1 Comparación

| Métrica (último turno de cada ítem) | Sin Jev | Con Jev |
|---|---|---|
| Ítems resueltos (estricto) | 72,5 % (29/40) | 70,0 % (28/40) |
| Con equivalencias | 75,0 % (30/40) | 70,0 % (28/40) |
| Compuestas completas | 3/6 | 4/6 |
| Abstenciones correctas | 0/3 | 1/3 |
| Violaciones de material / datos sin fuente | 0 / 1 | 0 / 1 |
| Respuestas de memoria | 1 | 2 |
| Primer texto p50 / p95 (s) | 2,93 / 4,19 | 3,68 / 5,84 |
| Fin p50 / p95 (s) | 3,58 / 5,30 | 3,68 / 5,84 |
| Primera línea de estado p95 (s) | 1,86 | 3,01 |
| Tokens de entrada / salida por turno (DeepSeek) | 18 345 / 307 | 16 841 / 314 |
| Costo DeepSeek por turno (USD) | 0,00587 | 0,00543 |

El informe de `eval-agent` solo cuenta el último turno de cada ítem (35 + 22 llamadas). Las
cifras de turnos de abajo salen del libro, que cubre los 51 turnos.

- **Gatekeeper (51 turnos)**: 45 con decisión de Jev, 2 por reglas (v3-021 turno 2 y un
  turno de v3-026) y 4 sin decisión por cuota (v3-037 a v3-040, que fueron con el LLM y sin
  Jev). Turnos elegibles con decisión de Jev: **45/45 (100 %)**, sin plazos ni errores.
- **Compuerta**: 30 juicios en todos los turnos. En el último turno de cada ítem: 19
  respaldadas, 3 no respaldadas, 8 omitidas (reemplazo o abstención) y **10 sin juicio por
  cuota** (v3-028, 029, 031–035, 037, 039 y 040). Esos ítems usaron solo la compuerta
  determinista; son casi todo el bloque adversarial y de abstención.

### 4.2 Auditoría de fallos nuevos, mejoras y cautela

| Ítem | Sin → con | Decisión de Jev | Causa auditada | ¿Atribuible a Jev? |
|---|---|---|---|---|
| **v3-003** (compuesta: Propal, bagazo y portacomidas Bioform) | pasa → **falla** `tools_any` | `rag` 0,83 → alcance `rag` | Con alcance `rag`, `buscar_producto` no está disponible y la parte de catálogo de la compuesta no se consultó. La respuesta documental es buena. Debió ser `mixta` | **Sí: regresión** (R1, §9) |
| v3-012 (precio de tapa PET para bowl de 32 oz) | pasa → falla `abstain_or_cites` «32» | `herramienta_estructurada` 1,00 | El modelo repitió «bowl de 32 oz», el dato de la persona; el verificador lo marca como cifra sin cita. Mismas herramientas disponibles; respuesta correcta (PET no apto, sin precio) | No: variación del modelo y falso positivo del verificador |
| v3-006 (vasos fríos y distribuidor en Barranquilla, en inglés) | falla → pasa | `herramienta_estructurada` 1,00 | Con Jev eligió `buscar_sedes`; sin Jev, `ciudades_con_cobertura`. Las dos estaban disponibles en ambos casos | No: variación del modelo |
| v3-036 (salario en la planta de Yumbo, en inglés) | falla → falla (otra verificación) | `herramienta_estructurada` 0,88 (errónea) | El alcance estructurado quitó `buscar_corpus`; el agente se abstuvo (ahora sí pasa `abstain`, por eso las abstenciones suben a 1/3) con el texto neutro **en español** y falla `language`. El texto neutro solo existe en español (`toolagent/evidence.py:628`, de la 044) | Clase errónea de Jev; el idioma es un defecto previo (R3) |
| v3-035 (pedido y pago por PSE) | falla → falla | `herramienta_estructurada` 0,95 | Respuesta casi igual en ambos casos (repite «50» y «7» de la persona) | No |

**Turnos con cautela (4 en el libro; el informe muestra 3)**, todos auditados:

| Turno | Decisión de Jev | Resultado |
|---|---|---|
| v3-020 turno 1 | `herramienta_estructurada` 0,47 → cautela (confianza < 0,6) | Turno intermedio: solo consta la decisión en el libro (la compuerta lo respaldó, p = 0,82); el ítem pasa |
| v3-023 turno 2 («¿y si en vez de eso damos sancocho bien caliente?») | `respuesta_directa` 0,45 → cautela | Consultó `guia_materiales` y `buscar_producto`; respuesta con fuentes; pasa |
| v3-024 turno 3 («Mejor que sean cinco, no dos.») | `respuesta_directa` 0,72 → cautela (`confianza_baja_directa`) | Llamó a `cotizar`, que pidió la ciudad; misma respuesta que sin Jev; pasa |
| v3-025 turno 3 («Ya no lo mando, paso yo por ellos…») | `respuesta_directa` 0,67 → cautela (`confianza_baja_directa`) | Cotización con retiro en sede, con fuente; pasa |

En los tres turnos finales, la cautela hizo lo que debía: consultó antes de afirmar y no
inventó canales.
El patrón preocupante es otro: **Jev clasifica como `respuesta_directa` los seguimientos
comerciales cortos** de varias vueltas (R2, §9). Hoy los contiene la cautela, no la
clasificación.

### 4.3 Sustituciones de la compuerta (5 en el libro; el informe muestra 3)

El texto rechazado **no se conserva** (privacidad, por diseño): la auditoría solo puede
juzgar el contexto (consultas, p y texto que lo reemplazó).

| Turno | p | Texto que llegó a la persona | Auditoría |
|---|---|---|---|
| v3-005 (contenedor para microondas, precio y dónde comprar en Bogotá) | 0,26 | Texto neutro genérico («No tengo un dato verificado…») | La respuesta original de este ítem en `claims_046` tenía una afirmación sin fuente («la tapa se pide aparte»), así que la detección es verosímil. Pero el texto que reemplazó la respuesta perdió las tres partes de la compuesta, y el ítem sigue «aprobado» porque el v3 no penaliza la respuesta neutra |
| v3-021 turno 1 (precio de vasos de cartón de 9 oz) | 0,26 | No consta (turno intermedio) | No auditable |
| v3-021 turno 2 (IVA y envío a Neiva) | 0,16 | Pedido de dato: «si el contenido va caliente, frío o a temperatura ambiente» | `cotizar` devolvió `needs_input`; el texto final es correcto y útil |
| v3-023 turno 1 (empaque para ensalada de frutas) | 0,44 | No consta (turno intermedio) | No auditable; el turno 2 se respondió bien |
| v3-027 (adversarial: 3 pacas de J1, existencias y plazo) | 0,44 | «Para seguir necesito un dato más. ¿Me cuentas con más detalle…?» | Sin Jev, este ítem dio una cotización completa y honesta. Con Jev, la compuerta dejó un texto pobre. Es posible una falsa alarma, del tipo del «(601)» (hubo `buscar_sedes` con teléfonos), pero no se puede comprobar sin el texto |

Además, **v3-022** (último turno) pasó la compuerta con p = **0,52**, aunque trae un dato
sin fuente («2.58») que la verificación determinista del v3 sí marca. Con y sin Jev, el
recuento de datos sin fuente es el mismo (1).

## 5. Latencia del primer texto con y sin compuerta (SC-006)

| Medida (40 ítems, último turno) | Sin Jev (sin compuerta) | Con Jev (compuerta `enforce`) |
|---|---|---|
| Primer texto p50 / p95 | 2,93 / 4,19 s | 3,68 / 5,84 s |
| Fin p50 / p95 | 3,58 / 5,30 s | 3,68 / 5,84 s |
| Diferencia pareada del primer texto: mediana / p95 | — | +0,73 / +2,42 s |
| Diferencia pareada del fin: mediana | — | +0,13 s |
| Por llamada a Jev p50 / p95 / máx. (libro, n = 75) | — | 149 / 231 / 314 ms |

- Con la compuerta activa, el primer texto coincide con el fin del turno: el texto completo
  se retiene y sale de una vez, como en la decisión de *Clarifications*. Pasa también en
  los turnos que la compuerta no juzga (omitidos o sin cuota): primer texto p95 4,28 s
  frente a 3,48 s en esos 18 ítems.
- El primer texto llega unos 0,7 s más tarde en la mediana. El fin apenas cambia (+0,13 s),
  porque las dos llamadas a Jev (~0,3 s) se compensan con menos tokens por alcance
  restringido (−8 % de entrada).
- La primera línea de estado p95 sube de 1,86 a 3,01 s por composición, no por espera: la
  mediana pareada solo sube +0,19 s. Pesan en el p95 los turnos sin herramientas (v3-033 y
  v3-035), que sin Jev no tenían línea de estado y con Jev muestran «Reviso que lo que te
  digo tenga fuente…» a unos 3 s.
- Frente al criterio de la 044 (primer texto p95 < 5 s), con Jev se queda en 5,84 s y no
  cumple. Es la consecuencia aceptada de retener el texto (enmienda E).

## 6. Costo

| Corrida | Llamadas a Jev | Tokens de Jev (entrada / salida) | USD de Jev |
|---|---|---|---|
| Humo | 1 | 0 / 0 | 0 |
| Gatekeeper, primera (inválida) | 3 | 2 128 / 228 | 0,00009 |
| Diagnóstico | 1 | 717 / 76 | 0,00003 |
| Gatekeeper, repetición | 20 | 14 220 / 1 459 | 0,00060 |
| Compuerta (`claims_046`) | 16 | 30 909 / 352 | 0,00130 |
| v3 con Jev | 75 | 79 211 / 3 379 | 0,00333 |
| **Total** | **116** | **127 185 / 5 494** | **≈ 0,0053** |

- Jev en el v3: 45 llamadas al gatekeeper (~630–720 tokens cada una) y 30 a la compuerta
  (~760–3 600 tokens). Unos 1 553 tokens por turno: ≈ 0,07 USD por 1 000 turnos.
- DeepSeek (solo el último turno de cada ítem): v3 sin Jev ≈ 0,235 USD (40 × 0,00587); v3
  con Jev ≈ 0,217 USD (40 × 0,00543). **No medidos**: los 11 turnos intermedios de cada
  corrida del v3, el juez LLM del gatekeeper (2 × 20 llamadas) y la reproducción (5 turnos).

## 7. Libro de llamadas y presupuesto (SC-008, SC-007)

`evaluacion/jev-calls.jsonl`: **116 líneas**, una por llamada. Ninguna corrida tiene
reintentos: el adaptador no reintenta y cada petición deja una línea. Claves presentes:
`ts, run, item, kind, status, latency_ms, model, input_tokens, output_tokens, choice,
confidence, probabilities, clarity, clarity_confidence, p_supported, threshold`. Ninguna
lleva texto de preguntas, respuestas ni evidencia.

| Tramo | Plan (R11) | Real | Acumulado |
|---|---|---|---|
| Humo + gatekeeper | 21 | 1 + 3 inválidas + 1 diagnóstico + 20 | 25 |
| Compuerta | 16 | 16 | 41 |
| v3 sin Jev | 0 | 0 | 41 |
| v3 con Jev | ≤ 75 | 75 | 116 |
| Reproducción (T065) | 0 | 0 | 116 |

**Quedan 4 llamadas** del tope de 120. La verificación en producción (T060) necesita 2. La
demostración opcional (T064, ≤ 6) ya no cabe entera: con las 4 de más del incidente, o se
omite o se limita a 2 llamadas (por ejemplo, los mensajes 1, 4 y 5 de la sección 2 del
quickstart), salvo que el owner amplíe el presupuesto.

## 8. Limitaciones

- n pequeño y una corrida por condición. El v3 con y sin Jev difiere también por la
  variación de DeepSeek: v3-006 y v3-012 cambian de resultado sin relación con Jev.
- Las cuotas se agotaron antes del final del v3. El bloque de abstención (v3-037 a v3-040)
  corrió sin gatekeeper y los últimos 10 ítems sin compuerta de Jev, así que SC-004 no mide
  a Jev sobre las preguntas adversariales y de abstención.
- El informe de `eval-agent` solo describe el último turno; las cifras de todos los turnos
  de §4 salen del libro.
- El texto rechazado por la compuerta no se guarda: las sustituciones no se pueden auditar
  sobre la afirmación concreta.
- El gatekeeper se midió con un parche en memoria sobre Score (§2.1). La parte Choice es la
  de producción.
- El humo (`GET /v1/models`) respondió `ok` pero informó «modelo en la lista: no», aunque
  todas las respuestas traen `model: jev-1.14.0`. La causa no se investigó, porque hacerlo
  gastaría otra llamada.
- Trazas de LangSmith apagadas: SC-009 en LangSmith solo queda cubierto por las pruebas sin
  red.
- Etiquetas del conjunto del gatekeeper: 15 de 20 vienen de `routing_questions.json`,
  etiquetado por las reglas; las 5 directas las redactó el asistente de desarrollo.

## 9. Defectos y regresiones

Se midieron sin arreglar nada. Después de la medición se corrigieron D1, D2 y R1 (cada uno
lleva su «Estado»); R2–R5 siguen documentados.

**D1. Score: `score` real rechazado (solo medición).**
`JevJudge.classify(with_score=True)` en `adapters/judge/jev.py` exige un entero en [0, 4] y
la API devuelve un número real. Reproducción sin red (verificada):

```python
import httpx
from carvajal.adapters.judge.jev import JevClient, JevJudge
from carvajal.application.ports import JudgeUnavailable, RouteQuery

payload = {
    "model": "jev-1.14.0",
    "usage": {"input_tokens": 717, "output_tokens": 76},
    "answers": {
        "ruta": {
            "type": "choice",
            "choice": "herramienta_estructurada",
            "confidence": 0.99,
            "probabilities": {
                "herramienta_estructurada": 0.99,
                "rag": 0.0,
                "mixta": 0.0,
                "respuesta_directa": 0.01,
            },
        },
        "claridad": {"type": "score", "score": 2.5, "confidence": 0.53},
    },
}
judge = JevJudge(
    JevClient(
        api_key="x",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
    )
)
q = RouteQuery(question="¿Cuánto vale la caja de vasos de 9 oz en Cali?")
judge.classify(q).choice  # 'herramienta_estructurada'
judge.classify(q, with_score=True)  # JudgeUnavailable: status 'invalid'
```

La lectura de Score en `ReplayJudge.from_ledger` (`isinstance(clarity, int)`) también
descarta el valor real.

**Estado (corregido tras la medición):** `JevJudge` acepta un real en [0, 4] (los niveles
de `JEV_SCORE_CLARITY` menos uno) y rechaza lo que esté fuera, `NaN`, booleanos y textos;
`RouteVerdict.clarity` y `GateResult.clarity` pasan a `float`; `ReplayJudge.from_ledger`
conserva el real del libro, y `errors_by_clarity` agrupa por el nivel más cercano. Pruebas
sin red en `tests/test_jev_adapter.py` y `tests/test_eval_gatekeeper.py`. La parte Choice
y la producción (`with_score=False`) no cambian.

**D2. El informe del gatekeeper recomienda `DIRECT=0,6` y dice «se queda el valor
vigente».**
`GateReport.recommend()` aplica la cláusula «si la rejilla no separa, se quedan los valores
iniciales» solo al umbral mínimo. Para el directo toma el menor valor sin datos enviados a
la directa que no baje del mínimo, y deja la nota del mínimo. Reproducción:
`evaluacion/gatekeeper.json` → `judges.jev.recommend` = `{"min_confidence": 0.6,
"direct_min_confidence": 0.6, "note": "la rejilla no separa: se queda el valor vigente"}`,
con 0,8 vigente. Se decidió 0,8 (§2.3).

**Estado (corregido tras la medición):** `GateReport.recommend()` aplica la cláusula de R4
también al umbral directo: si todos los valores de la rejilla tienen cero preguntas con datos
enviadas a la directa, la rejilla no separa y se queda el vigente (sin bajar del mínimo). La
nota nombra el umbral («umbral mínimo: …», «umbral directo: …»). Con los datos de T053 el
informe recomienda 0,6 y **0,8**. Pruebas sin red en `tests/test_eval_gatekeeper.py`. Los
archivos de `evaluacion/` se dejan como salieron de la corrida.

**R1. v3-003: el alcance `rag` deja una compuesta sin catálogo (atribuible a Jev).**
Jev clasificó como `rag` (0,83) «¿qué es Propal y qué tiene que ver el bagazo de caña con los
portacomidas Bioform que ustedes venden?». El alcance quitó `buscar_producto`. Reproducción
(2 llamadas a Jev, fuera del presupuesto actual):
`uv run carvajal eval-agent --mode tools --questions data/eval/generalization_v3.json --ids
v3-003 --jev-ledger <libro> --max-jev-router-calls 1 --max-jev-claims-calls 1`. La evidencia
grabada está en la línea `run=v3`, `item=v3-003`, `kind=router` del libro y en
`evaluacion/v3-con-jev.json`. Sin Jev, la consulta pasa (`v3-sin-jev.json`).

**Estado (corregido tras la medición):** si Jev elige `rag` y la pregunta nombra un producto
del catálogo, un precio, una cantidad, una capacidad o dónde comprar
(`commercial_signal`), el gatekeeper la trata como `mixta`: alcance `all`, sin cautela,
`reason="producto_reglas"` (research R16). Prueba sin red con la pregunta de v3-003 y un
juez falso con `rag` a 0,95 en `tests/test_gatekeeper_decide.py`. No se repitió la corrida
con Jev (sin créditos).

**R2. Seguimientos comerciales de varias vueltas clasificados como `respuesta_directa`.**
v3-023 turno 2 (0,45), v3-024 turno 3 (0,72) y v3-025 turno 3 (0,67). La línea de contexto
no le basta a Jev para ver que el turno sigue una cotización. Hoy los contiene la cautela
con `DIRECT_MIN=0,8`; con un umbral directo ≤ 0,7, al menos dos habrían ido a la respuesta
directa sin consultar. Reproducción sin créditos: `ReplayJudge` con esas líneas del libro
(`run=v3`) y el agente, o el mismo `eval-agent --ids v3-023,v3-024,v3-025 --jev-ledger …`
(8 llamadas al gatekeeper).

**R3. Texto neutro solo en español ante preguntas en inglés (defecto previo, expuesto por
Jev).**
v3-036 (inglés): Jev eligió `herramienta_estructurada` (0,88), el alcance quitó
`buscar_corpus` y el agente se abstuvo con el texto neutro de `toolagent/evidence.py:628`,
en español. Falla `language`. Las sustituciones de la compuerta usan el mismo texto, así que
una pregunta en inglés cuya respuesta se rechace también recibe la respuesta en español.

**R4. Sustituciones que empobrecen respuestas completas (v3-005, v3-027) sin que el v3 lo
penalice.**
Los ítems siguen «aprobados» con el texto neutro. La pérdida de utilidad no aparece en
SC-004 y no se puede auditar sin el texto rechazado. Ver §4.3.

**R5. Dato sin fuente que pasa la compuerta (v3-022).**
p = 0,52 ≥ 0,5 con «2.58» sin fuente; lo marca la verificación determinista del v3. Mismo
recuento que sin Jev.

## 10. Compuerta de afirmaciones en producción (2026-11-26)

Pregunta: la compuerta (`JEV_CLAIMS_MODE=enforce`, umbral 0,5) sustituía ~24 % de las
respuestas juzgadas en producción (24 de 104 en 3 h, eventos `claims.judged`). ¿Atrapa
afirmaciones sin fuente o sustituye respuestas buenas? **Sin cambios de umbral ni de modo en
el código**: esta sección mide y recomienda; la decisión es del owner.

### 10.1 Fuentes y limitaciones

- **Logs de producción: perdidos.** El despliegue de la PR #273 recreó los contenedores a
  las 15:30 UTC del 2026-11-26. `docker logs --since 72h` de `carvajal-prod-app-1` trae 38
  líneas y ninguna `claims.judged`; `carvajal-prod-mcp-1`, tampoco. Los contadores nuevos de
  `/health` (§10.6) también se reinician con el proceso.
- **LangSmith (proyecto `carvajal`), solo lectura.** Raíces `chat_turn` de las últimas 72 h,
  solo metadatos: `claims_status`, `claims_p`, `claims_substituted`, `tools` y
  `agent_environment`. Producción tiene `MONITORING_INCLUDE_CONTENT=false`, así que **no hay
  texto de producción**: ni el rechazado ni el emitido.
- **Texto para auditar.** Sale solo de las trazas sin `agent_environment=produccion` (las
  locales, con el contenido encendido en el `.env` del owner, y las que no traen etiqueta de
  entorno, del 2026-11-26 entre las 01:16 y las 14:21 UTC). Es el mismo código, pero son
  conversaciones de prueba y no clientes. Las etiquetas de §10.3 las puso un solo revisor.
- **Tráfico de producción mayormente sintético.** De las 237 juzgadas, 205 son del
  2026-11-26, el día de los ensayos e2e (§10.2). Corresponden a 124 hilos.

### 10.2 Tasa por umbral (producción, 237 juzgadas)

Del 2026-11-24 16:33 al 2026-11-26 14:41 UTC: 350 turnos con compuerta. Hubo 189
respaldadas, 48 no respaldadas (las 48 sustituidas), 113 omitidas (62 en el camino del atajo) y 0 sin
veredicto. «Sustituidas» se cuenta como p < umbral sobre los p registrados. En el conjunto
congelado, p varió como mucho 0,02 entre corridas (§10.4).

| Umbral | Producción (n = 237) | Sin etiqueta de entorno (n = 127) | Local (n = 42) |
|---|---|---|---|
| 0,2 | 16 (6,8 %) | 3 (2,4 %) | 1 (2,4 %) |
| 0,3 | 31 (13,1 %) | 10 (7,9 %) | 1 (2,4 %) |
| 0,4 | 40 (16,9 %) | 13 (10,2 %) | 3 (7,1 %) |
| **0,5 (vigente)** | **48 (20,3 %)** | **17 (13,4 %)** | **10 (23,8 %)** |
| 0,6 | 65 (27,4 %) | 25 (19,7 %) | 17 (40,5 %) |
| 0,7 | 99 (41,8 %) | 30 (23,6 %) | 24 (57,1 %) |

La ventana de 3 h con más carga (2026-11-26 desde las 11:52 UTC) da 29 de 142 (20,4 %),
coherente con el 24/104 de los logs.

**Por herramienta (producción, umbral 0,5).**

| Herramientas del turno | Juzgadas | Sustituidas |
|---|---|---|
| solo `cotizar` | 171 | 38 (22 %) |
| solo `buscar_producto` | 25 | 8 (32 %) |
| solo `buscar_corpus` | 7 | 1 |
| `cotizar` + `ficha_producto` | 1 | 1 |
| resto (sedes, historia, indicadores, combinaciones, sin herramientas) | 33 | 0 |

45 de las 48 sustituciones vinieron con Jev en `herramienta_estructurada`.

### 10.3 Distribución de p y auditoría de las sustituciones

**p de las 48 sustituciones de producción:**

| Rango de p | Sustituciones |
|---|---|
| [0; 0,1) | 6 |
| [0,1; 0,2) | 10 |
| [0,2; 0,3) | 15 |
| [0,3; 0,4) | 9 |
| [0,4; 0,5) | 8 |

La mediana es 0,245 y el rango va de 0,04 a 0,48. En el histograma de las 237 juzgadas, por
décimas de 0 a 1: 6, 10, 15, 9, 8, 17, 34, 31, 48 y 59. En el conjunto congelado las no
respaldadas dan p entre 0,03 y 0,15 y las buenas entre 0,58 y 0,94. **32 de las 48
sustituciones (0,2–0,5) caen en una franja que ese conjunto no tiene.**

**Auditoría con texto: 27 sustituciones fuera de producción (17 sin etiqueta y 10 locales).**

| Veredicto del revisor | n | p | Ejemplos (fragmentos breves, sin datos de personas) |
|---|---|---|---|
| Bien atrapadas | 2 | 0,14 y 0,23 | Valoración presentada como hecho: «sirve para porciones pequeñas» (la regla de la Enmienda E) |
| Dudosas | 7 | 0,15–0,46 | Aptitud al uso dicha con palabras de la persona («sirve para bebidas frías como el jugo», ×3). Lista del portafolio sin fuente al decir que un producto no está (×2). «La anterior quedó cancelada». Precios con «impuestos incluidos» |
| Falsos positivos | 18 | 0,15–0,49 | **Pedidos del dato que repiten lo ya dicho** (×5): «Listo, dejo las tapas fuera… solo falta la temperatura», «Falta la temperatura… Con eso te cotizo los 1000 vasos de 14 oz en Cali», «¿Le doy?». **Candidatas de `cotizar`** (×2). **Material citado** (×1): «es de PP, material apto para bebida caliente [1]». **Tablas y listas largas citadas** del catálogo o del directorio (×10, seis de ellas con p entre 0,38 y 0,49) |

**Contraste:** textos de la misma forma pasan con p entre 0,51 y 0,64:

- «Para cotizar los 500 vasos de 9 oz en Cali solo me falta un dato: ¿…?» (0,56).
- «El vaso … es de PP, material apto para bebida fría [1]. Falta definir…» (0,51 y 0,56).
- Las mismas tablas de vasos para bebida caliente (0,51–0,58).

En cambio, una valoración sin fuente pasó con 0,61: «es la opción de referencia para café».
**Entre 0,3 y 0,6, Jev no separa estos textos.** El 0,5 parte en dos un grupo de respuestas
equivalentes.

**Causa probable de los pedidos del dato (verificada en el código).** `build_claims_query`
solo deriva `dicho_por_la_persona` de la pregunta **del turno**. Las preguntas anteriores
(`prior_questions`) solo sirven para redactar y no respaldan nada. El borrador de la
cotización tampoco viaja como evidencia. Por eso «los 500 vasos de 9 oz en Cali», dichos dos
turnos antes, llegan a Jev sin respaldo. `only_questions` tampoco los omite, porque la
oración afirma algo antes de preguntar.

En las tablas largas, una hipótesis no verificada: el recorte de la evidencia a
`JEV_MAX_EVIDENCE_CHARS` (12 000) con «…» deja filas fuera.

### 10.4 `eval-claims` repetido (2026-11-26)

16 llamadas a `jev-1.14.0` (≈ 0,001 USD) con el libro
[`evaluacion/jev-calls-2026-11-26.jsonl`](evaluacion/jev-calls-2026-11-26.jsonl). El informe
está en [`evaluacion/claims-2026-11-26.md`](evaluacion/claims-2026-11-26.md) y su
[`.json`](evaluacion/claims-2026-11-26.json). El resultado es igual al del 2026-11-24:

| Umbral | Detección | Falsas alarmas |
|---|---|---|
| 0,3 | 10/10 | 0/6 |
| 0,4 | 10/10 | 0/6 |
| **0,5** | **10/10** | **0/6** |
| 0,6 | 10/10 | 1/6 |
| 0,7 | 10/10 | 2/6 |

Las p cambiaron como mucho 0,02 (cl-001-orig 0,06 → 0,08; cl-034-orig 0,60 → 0,62). El
conjunto congelado no tiene respuestas en la franja 0,2–0,5 ni pedidos del dato de varias
vueltas, así que no decide el umbral de producción.

Reproducción: `uv run --env-file <.env con TYPESAFE_API_KEY> carvajal eval-claims --ledger
<libro>.jsonl --max-jev-calls 20 --output <informe>.md --json <informe>.json`.

### 10.5 Recomendación (pendiente de decisión del owner)

1. **No cambiar ahora el umbral ni el modo.** La evidencia no es abrumadora. No hay texto
   de producción, la auditoría sale de 27 casos de prueba con un solo revisor y la tasa de
   producción viene de un día de ensayos. El costo de cada sustitución ya bajó: tras la
   PR #273, el reemplazo usa las candidatas, las sedes y la cantidad en vez de «Para seguir
   necesito un dato más…».
2. **Primero, el arreglo estructural** (cambia qué se juzga: Enmienda E, decisión del
   owner). Pasar como `dicho_por_la_persona` los valores estructurados de las preguntas
   anteriores y las líneas del borrador. Así, un pedido del dato que repite lo que la persona
   ya dio queda respaldado. Ataca la clase más clara de falsos positivos (5 de 18 en la
   muestra) sin tocar el umbral.
3. **Opción de umbral, si el owner la quiere ya:** `JEV_CLAIMS_THRESHOLD=0.3` en el `.env`
   del servidor, sin cambio de código.
   - En producción, la tasa bajaría de 20,3 % a 13,1 %.
   - En la muestra auditada quitaría 14 de los 18 falsos positivos y 1 dudosa, y conservaría
     las 2 bien atrapadas.
   - El conjunto congelado sigue en 10/10 y 0/6.
   - Riesgo: dejar pasar afirmaciones reales con p entre 0,3 y 0,5. En la muestra no hubo
     ninguna bien atrapada en esa franja, pero n es chico.
   - Subir el umbral empeora: con 0,6 o 0,7 sustituiría 27 % y 42 %.
4. **Seguir midiendo** con el bloque `claims` de `/health` (§10.6) y con `claims.judged`,
   que ahora trae `tools` y `answer_chars`. Conviene leer la tasa por proceso antes de cada
   despliegue, porque reiniciar el contenedor la pone a cero. Para auditar falsos positivos
   de verdad hace falta texto: un ensayo local con `MONITORING_INCLUDE_CONTENT=true` sobre
   los guiones del ensayo, o pasar a `shadow` un tiempo (decisión del owner).

### 10.6 Observabilidad añadida

- **`/health` → `claims`** (app web). Por proceso y desde el arranque: `mode`, `threshold`,
  `since`, `turns`, `judged`, `supported`, `unsupported`, `substituted`,
  `substitution_rate`, `unsupported_rate`, `unavailable` y `skipped` por motivo, `off` y
  `p_histogram` (10 cubetas). Sin texto y sin cambiar el `status` global. Contrato en
  [`contracts/jev-gatekeeper.md`](contracts/jev-gatekeeper.md#health).
- **WhatsApp** corre el agente en el proceso `mcp`. Sus contadores salen en el `GET /health`
  del servidor MCP, con el bearer de `/mcp`.
- **`claims.judged`** suma `tools` (herramientas del turno) y `answer_chars`.
- **LangSmith** ya llevaba `claims_status`, `claims_p` y `claims_substituted` en la raíz de
  cada turno. No se añadió otra métrica: con esos metadatos se rehízo §10.2.
