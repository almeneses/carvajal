# Jev (TypeSafe): prueba previa y decisión

> Investigación previa de la spec 046 (FR-028). Resume la prueba del **2026-11-24** con el
> modelo `jev-1.14.0`, hecha para la spec 045 con un presupuesto de créditos acotado a
> propósito: las muestras son pequeñas y así se informan. La decisión vigente y su
> medición no viven aquí sino en [`specs/046-jev-router/`](../../specs/046-jev-router/)
> (spec, plan, investigación R1–R15, contrato y, al terminar la medición, `evidencia.md`).
> Ni este documento ni los archivos de la prueba contienen la clave de API ni datos
> personales.

## 1. Resumen

| Uso | Resultado de la prueba | Decisión vigente (spec 046) |
|---|---|---|
| Compuerta de afirmaciones (Noul) | Con las etiquetas corregidas, umbral 0,5: detección 12/13, falsas alarmas 0/7, p95 284 ms | **Adoptada**: después de la compuerta determinista, `JEV_CLAIMS_MODE=enforce` por defecto cuando Jev está encendido, umbral 0,5 (enmienda E) |
| Gatekeeper del modo `tools` (Choice) | No se probó en esta ronda | **Adoptado**: reglas → Jev → LLM, con cautela por confianza baja (enmienda D); se mide en la fase 8 de la spec 046 |
| Reranker del retrieval | Mejora la media pero empeora 3 de 8 consultas (nDCG@10) | **No adoptado** |
| Abstención por «max p < 0,5» del rerank | Separa los grupos, pero con una sola consulta negativa | No adoptada; vale una prueba aparte |

Jev está **apagado por defecto** (`JEV_ENABLED=false`) aunque haya clave. Sin Jev, el
asistente funciona como en la 045: reglas + LLM y la compuerta determinista.

## 2. Qué es Jev y su API

- **Producto**: modelo propietario de TypeSafe que responde «preguntas» estructuradas sobre
  un estado (texto, JSON o imágenes) y devuelve probabilidades, no texto libre. Los alias
  `jev-latest` y `jev-preview` apuntaban ese día a `jev-1.14.0`; el proyecto **fija**
  `jev-1.14.0` y revalida los umbrales si cambia.
- **Endpoint**: `POST https://api.typesafe.ai/v1/systemone`; `GET /v1/models` lista los
  modelos (lo usa el humo de la medición). `Authorization: Bearer <clave>` y JSON.
- **Petición**: `{model, state, questions: {nombre: {type, instructions, criteria?}}}`.
  Tipos: `noul` (sí/no, una probabilidad), `choice` (1–255 opciones, con `confidence` y
  `probabilities`), `score` (niveles) y `box` (imágenes, no aplica). Las preguntas apuntan a
  partes del estado con backticks.
- **Respuesta**: `{model, answers: {nombre: {...}}, usage: {input_tokens, output_tokens}}`.
  Noul no trae confianza y la API redondea p a dos decimales.
- **Límites**: 64k tokens por petición y 32k por pregunta; cuerpo de 32 MiB; tasa por
  organización, no publicada. No hay endpoint de lote ni de saldo de créditos.
- **Errores**: 400, 401, 402, 413, 415, 422, 429 y 529.
- **Advertencias de la documentación**: el contenido ajeno a la pregunta baja la precisión
  y hay sesgo posicional hacia la primera opción (por eso la respuesta directa va última en
  la pregunta Choice del gatekeeper).
- **Precio de lista**: USD 0,042 por millón de tokens de entrada; la salida es gratis.

## 3. Diseño de la prueba

Presupuesto: **30 llamadas** (1 de humo, 20 del juez, 9 del reranker), todas 200 al primer
intento. 110.563 tokens de entrada y 3.681 de salida: ≈ USD 0,0046 a precio de lista.

- **Juez de respaldo**: una pregunta Noul por respuesta («¿todas las afirmaciones de hecho
  de `respuesta` están respaldadas por `evidencia` o repiten lo que dijo la persona?»).
  Muestra: 10 ítems de un solo turno del conjunto v3 (001, 002, 005, 008, 010, 018, 027,
  028, 032 y 034), cada uno con la respuesta original de la corrida v3-r4 y una perturbada
  con una frase inventada: n = 20. La evidencia se reconstruyó reejecutando las mismas
  herramientas con los mismos argumentos (`run_tool`, sin LLM ni red).
- **Reranker**: 20 candidatos por consulta en una petición con 20 preguntas Noul; 9
  consultas elegidas donde el rerank podía ayudar (sesgo a favor declarado).

## 4. Resultados

### 4.1 Juez de respaldo

| Umbral | Falsas alarmas (buenas) | Perturbadas detectadas | Originales sin fuente detectadas | Exactitud |
|---|---|---|---|---|
| **0,5** | **0/7** | **10/10** | **2/3** | **19/20** |
| 0,6 | 0/7 | 10/10 | 2/3 | 19/20 |
| 0,7 | 1/7 | 10/10 | 2/3 | 18/20 |
| 0,8 | 1/7 | 10/10 | 3/3 | 19/20 |

Con las etiquetas corregidas: v3-001 («la de 16 oz es la más usada para sopas») y v3-005
(«la tapa se pide aparte») son afirmaciones que ninguna consulta devolvió, y Jev las marcó
(p = 0,22 y 0,17). **El owner confirmó** que una valoración presentada como hecho sin fuente
cuenta como no respaldada. Con las etiquetas originales de la tarea, las falsas alarmas eran
2/9.

- **Margen a 0,5**: perturbadas entre 0,04 y 0,22; originales buenas entre 0,62 y 0,97.
- **Por qué no 0,8**: tres originales buenas quedan exactamente en 0,80 y la API redondea a
  dos decimales.
- **Caso v3-027**: la respuesta reescribe el teléfono de una sede con el indicativo «(601)»
  y obtiene p = 0,72. **Decisión del owner**: reformatear un dato del directorio no cuenta
  como inventado. Para que Jev lo vea, la compuerta de la 046 conserva los datos del
  directorio en la respuesta que viaja (research R8).
- **Latencia**: p50 267 ms, p95 284 ms, máximo 286 ms (n = 20); estados de ~0,9k a 3,8k
  tokens (≈ 2.303 por llamada).

### 4.2 Reranker

| Métrica (8 consultas) | Top-10 del pool | Rerank Jev |
|---|---|---|
| Recall@10 | 0,6250 | 0,6875 (+0,0625) |
| nDCG@10 | 0,3816 | 0,4806 (+0,0990) |

nDCG mejora en 4 de 8, empeora en 3 (q26, q07, q09) y empata en 1. En las consultas amplias
16–20 de los 20 pasajes tienen p ≥ 0,5 y el orden deja de discriminar. El cuello de botella
del retrieval es la generación de candidatos (8 de 30 consultas con Recall@20 = 0), no el
orden. Latencia p50 331 ms, p95 349 ms.

## 5. Costos

| Uso | Tokens de entrada por turno | USD por 1.000 turnos |
|---|---|---|
| Compuerta (1 llamada por respuesta) | ≈ 2.303 (~0,9k–3,8k) | ≈ 0,10 |
| Gatekeeper (1 llamada Choice por turno no decidido por reglas) | no medido en esta prueba; la pregunta redactada y los criterios son cortos | se mide en la spec 046 |
| Reranker (lote de 20 pasajes) | ≈ 7.103 | ≈ 0,30 |

No medidos: respuestas con `buscar_corpus` (evidencia más larga), rerank por pasaje y el
saldo de créditos (la API no lo informa). La medición de la spec 046 tiene un presupuesto
de **≤ 120 llamadas** con libro de llamadas sin texto (research R11).

## 6. Riesgos y cómo los atiende la spec 046

| Riesgo | Mitigación vigente |
|---|---|
| **Privacidad**: cada turno con Jev envía datos a un tercero (TypeSafe, EE. UU.) | Minimización (FR-017–FR-020): al gatekeeper solo la pregunta redactada (filtro de la 045 y enmascarado de nombre, empresa y notas) y, si aplica, una línea de contexto; a la compuerta, la respuesta sin datos de la persona, los extractos mínimos de evidencia y los valores estructurados que la persona dio, nunca la pregunta, la identidad, la memoria, el resumen ni el historial. Logs, traza, LangSmith, Sentry y el libro sin texto. |
| **Retención del proveedor**: el DPA no fija un plazo en la documentación; ZDR solo para enterprise; DPA, MCA y política de privacidad no se leyeron completos | **Pendiente (T063)**: revisar el DPA y su plazo antes de activar Jev en producción; si no se completa, la PR pide al owner aceptar el riesgo de forma explícita. |
| **Dependencia de un proveedor**: tasa no publicada, sin endpoint de saldo, alias que cambian | Modelo fijado, plazo total de 0,8 s, sin reintentos, tope diario por proceso, circuito (3 fallos → 60 s; 401/402/403 → 15 min) y respaldo al camino sin Jev (FR-006, FR-015, FR-023). |
| **Costo y créditos** | `JEV_ENABLED=false` por defecto; ningún comando local llama a Jev sin opt-in (`--jev-ledger`, `--ledger`); `JEV_LEDGER` anota cada llamada; cuotas por corrida en la medición. |
| **Latencia percibida**: un juez que bloquea necesita la respuesta completa | Con la compuerta activa se retiene el texto completo con una línea de estado (enmienda E); Noul solo si queda plazo del turno; `JEV_CLAIMS_MODE=shadow|off` para volver atrás sin desplegar. |
| **Falsas alarmas**: una alarma reemplaza una respuesta buena por la neutra | Umbral 0,5 (estable frente al redondeo); auditoría de cada sustitución en la evidencia de la 046. |

## 7. Limitaciones de la prueba

- Muestras pequeñas (n = 20 en el juez, 8 + 1 en el reranker): intervalos de confianza muy
  amplios.
- Perturbaciones evidentes (una frase inventada); no se probaron cambios sutiles (un dígito,
  un SKU). La compuerta determinista de cifras sigue siendo la defensa para precios,
  teléfonos y SKU.
- Evidencia reconstruida (el payload como objeto, no el texto exacto del `ToolMessage`) con
  el catálogo del 2026-11-18.
- Sin repeticiones (varianza entre llamadas), sin límites de tasa reales y con latencias de
  una sola corrida desde la máquina de desarrollo.

## 8. Trazabilidad

Los archivos de la prueba **no están en el repositorio**: quedaron en el directorio temporal
de la sesión de desarrollo (`scratchpad/jev/`), que puede no conservarse:

- Cliente: `jev_client.py`, `smoke.py`, `calls.jsonl` (1 de humo y 9 del rerank).
- Juez: `respaldo/judge.py`, `replay.py`, `evidence.json`, `results.jsonl`, `metrics.json`
  (etiquetas de la tarea), `metrics_ajustadas.json` (etiquetas corregidas) y `calls.jsonl`
  (20 líneas).
- Reranker: `rerank/retrieve.py`, `candidates.json`, `rerank.py`, `run.log`,
  `rerank_results.json`.
- Informe consolidado: `informe-jev.md`, del que sale este resumen.

Las cifras de este documento se recalcularon desde esos archivos al consolidar el informe.
Lo que el repositorio sí conserva de esa prueba son los conjuntos congelados de la spec 046:
`data/eval/claims_046.json` (8 originales y 8 perturbadas, evidencia reconstruida con
`run_tool` y armada como en producción) y `data/eval/gatekeeper_046.json`.
