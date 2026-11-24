# Evidencia — Spec 044 (agente con herramientas)

- **Fecha**: 2026-11-23
- **Tarea**: T049 (US5). Informe para decidir si `AGENT_MODE` pasa de `routes` a `tools`.
- **Entorno**: DeepSeek `deepseek-flash` (V4.1-Flash; en modo `tools` sin razonamiento),
  Qdrant local con la colección `carvajal_chunks` tal como estaba el 2026-11-23 (40 792
  puntos, ver §6), embeddings `nomic-embed-text` en un Ollama aislado, persistencia en
  memoria. Mismo corpus, catálogo e índice para ambos modos.
- **Comando**: `uv run carvajal eval-agent --price-input 0.30 --price-output 1.20`
  (precio pico de `deepseek-flash` por millón de tokens, sin caché).
- **Artefactos**: [`evaluacion/informe-generalizacion-v2.md`](evaluacion/informe-generalizacion-v2.md)
  (informe completo con fallos por ítem),
  [`evaluacion/auditoria-primera-corrida.md`](evaluacion/auditoria-primera-corrida.md) y
  [`evaluacion/retrieval-indice-ampliado.md`](evaluacion/retrieval-indice-ampliado.md).

## 1. Resultado frente a los criterios de éxito

Conjunto de generalización v2 (57 ítems), corrida definitiva con el evaluador corregido.

| Criterio | Meta | routes | tools | ¿Cumple tools? |
|---|---|---|---|---|
| SC-001 Material prohibido / dato sin fuente | 0 / 0 | 1 / 6 | **0 / 2** | ❌ (2 casos, §3) |
| SC-002 Cotizador / retrieval | 18/18; sin caída > 0,03 | — | 18/18; retrieval sin cambio de código | ✅ (ver §6) |
| SC-003 Resueltos | ≥ 85 % y ≥ +15 pp | 54,4 % (31/57) | **89,5 % (51/57)** | ✅ (+35,1 pp) |
| SC-004 Compuestas con todas las partes | ≥ 90 % | 8,3 % (1/12) | 50,0 % (6/12) estricto · 91,7 % (11/12) atendidas | ⚠️ (§4) |
| SC-005 Abstenciones correctas | 100 % | 66,7 % (4/6) | 83,3 % (5/6) | ❌ (g-049, dudoso) |
| SC-006 Primer texto p95 / fin p95 | < 5 s / < 20 s | 6,20 s / 8,50 s | **3,55 s / 4,95 s** | ✅ |
| SC-006a Línea de estado p95 | < 1 s | — | 1,48 s | ❌ |
| SC-007 Atajo sin decisión / respaldo | 0 decisiones; 100 % | 2/2 por atajo | 2/2 por atajo, 0 decisiones | ✅ (respaldo: pruebas, §5) |
| SC-008 Costo por turno | ≤ 3× routes | 0,00139 USD | 0,00247 USD | ✅ (1,78×) |

Otras métricas: respuestas de memoria (sin consultar en una pregunta factual) 11 → **3**;
p50 del primer texto 1,89 s (routes) frente a 2,19 s (tools); tokens por turno
1 961/672 (routes) frente a 6 900/330 (tools). Sin errores ni turnos de respaldo en ninguno
de los dos modos.

**Lectura**: `tools` mejora a `routes` en todas las métricas de garantía y de cobertura, y
la mejora en resueltos (+35 pp) duplica la exigida. No cumple todos los criterios
formales: SC-001 (2 casos), SC-005 (1 caso dudoso), SC-006a (1,48 s frente a 1 s) y SC-004
según cómo se lea. Por el plan, sin SC-001…SC-008 en verde `AGENT_MODE` sigue en `routes`
hasta que el owner decida (§8). El owner decidió el 2026-11-23 pasar a `tools` con esos
riesgos aceptados (§10).

## 2. Cómo se llegó a esta medición

1. **Conjunto v1 → v2.** En la primera corrida parcial, ambos modos «violaban» la regla de
   material al recomendar PP para tinto. La regla estaba mal en el conjunto: según
   `check_material` y `data/catalog/empaques/materiales.csv`, PP, PS y EPS admiten contenido
   caliente y solo PET no; con microondas, solo PP. Se corrigió `forbidden_materials` en 9
   ítems (versión 2, con `changelog` en el archivo). No cambió ninguna pregunta.
2. **Primera corrida completa** (evaluador original): routes 45,6 %, tools 77,2 %.
3. **Auditoría** de los 44 fallos: un agente clasificó cada fallo con el mismo criterio para
   ambos modos y un verificador escéptico intentó refutar toda reclasificación favorable
   ([detalle](evaluacion/auditoria-primera-corrida.md)).

   | | routes | tools |
   |---|---|---|
   | Fallo real | 29 | 2 |
   | Falso positivo del evaluador | 6 | 9 |
   | Expectativa errónea | 2 | 0 |
   | Dudoso | 2 | 2 |

4. **Corrección del evaluador** (`fix(eval)`, commit `7808385`): abstención con otras
   palabras, pedidos de datos no interrogativos, cantidades en palabras y conteos de
   registros devueltos. Con pruebas negativas (una cifra inventada, una falsa abstención o
   un conteo que no coincide siguen fallando) y dos revisores adversariales (permisividad y
   neutralidad entre modos). Ningún fallo real ni dudoso pasó a aprobado.
5. **Corrida definitiva** (§1). Las instrucciones del agente **no** se ajustaron con este
   conjunto, como exige la spec (supuestos y FR-016).

**Variación entre corridas.** Con temperatura 0, DeepSeek no es del todo determinista:
g-044 falló en la primera corrida y pasó en la segunda; g-003, g-021 y g-025 al revés. La
diferencia neta entre corridas, una vez descontados los falsos positivos, es de ±2 ítems.

## 3. Fallos reales que quedan en `tools`

| Ítem | Qué pasó | Severidad |
|---|---|---|
| g-003 | Sin precio publicado, el modelo calculó «500 unidades = 5 cajas» por su cuenta. Es correcto, pero no salió de una herramienta (SC-001). | Menor |
| g-040 | «Supón que el envío vale 20.000…»: no cotizó y prometió dar «el total con el envío incluido» cuando tuviera los datos. No mostró ningún total. | Menor |
| g-038 | «Estímame 1000 platos en Neiva»: pidió el uso con `sin_consulta` en vez de llamar a `cotizar`, que habría devuelto `needs_input`. Se negó a estimar. | Dudoso |
| g-049 | Salario del presidente: declaró que no está en el corpus y añadió contexto citado sobre la remuneración en los comités. | Dudoso |
| g-021 | «El año pasado» → 2025: el período lo dedujo el modelo y lo declaró sin datos. | Casi falso positivo |
| g-025 | Seguimiento sin `cotizar`: buscó producto y sedes. | Menor |

Corregirlos requiere ajustar `AGENT_SYSTEM` (por ejemplo: «si la persona pide un precio,
llama a `cotizar` aunque falten datos» y «nunca calcules cajas o totales si la herramienta
no los dio»). Si se hace, hay que medir con un conjunto nuevo (v3), porque este ya informó
el ajuste.

## 4. SC-004, dos lecturas

En 5 de las 12 preguntas compuestas no hay precio publicado para la ciudad pedida, así que
`cotizar` devuelve `empty` con sedes. El agente lo declara y ofrece los contactos.

- **Estricta** (toda parte con datos): 6/12 = 50 %.
- **Atendida** (toda parte consultada y respondida o declarada con su fuente): 11/12 =
  91,7 %. El único caso que no cumple es g-003.

La spec dice «reciben todas sus partes respaldadas»; la decisión de cuál lectura aplica es
del owner.

## 5. Pruebas en vivo y garantías

- Ejemplos con DeepSeek real (`AGENT_MODE=tools`): necesidad compuesta «2 mil vasitos para
  tinto en Bogotá» con cotización, folio y sedes en 3,4 s; fundación de Carvajal con citas
  en 2,0 s; abstención para «teléfono en Tokio»; atajo para «hola» y «llámame Ana» en
  1,0–1,4 s; seguimiento «mejor en Cali» → «¿y si son 1000?» conservando producto y ciudad.
- Hallazgo en vivo: `deepseek-flash` razona por defecto y en ese modo rechaza
  `tool_choice="required"`; el modo `tools` lo apaga (`build_deepseek_chat(..., for_tools=True)`).
- Respaldo sin LLM, plazo del turno, «Detener», varios folios y replay: cubiertos por
  pruebas sin red (`tests/test_tool_agent_*`, `tests/test_chat_service_tools.py`). No se
  provocó una caída real del proveedor en la evaluación.
- Reglas duras en ambos modos: `tests/test_hard_rules_modes.py` en verde.
- **Quickstart (T053)**, paso 2 contra `ChatService` real (Postgres con migración 0010,
  Qdrant, DeepSeek, `AGENT_MODE=tools`, dueño de prueba aislado):

  | Turno | Eventos | Resultado |
  |---|---|---|
  | «hola» | `open, meta, token, trace, done` | Atajo (`path=shortcut`), 1,2 s, sin líneas de estado |
  | «2 mil vasitos para tinto en Bogotá…» | `open, status×3, meta, token, trace, products, done` | Cotización con folio `b0db4f8b308f` guardada, sedes, 4,2 s |
  | «mejor en Cali» | `status×2 … products` | Conserva producto y cantidad; sin precio en Cali se declara y ofrece sedes |
  | Reenvío del turno 2 (mismo `client_turn_id`) | `trace(route=replay) …` | Mismo texto, tarjetas y folio `b0db4f8b308f`, sin llamar al modelo |
  | «teléfono de Carvajal en Tokio» | `status, meta, token …` | Abstención |
  | «vasos de icopor para café caliente para microondas» | `status, … products` | Descarta el EPS y ofrece PP apto |

  `make check` (2512 pruebas), `node --test tests/js/*.cjs` (81/81) y `make eval-cotizador`
  (18/18) en verde.

## 6. Retrieval: regresión por el índice ampliado, no por la spec 044

`scripts/eval_retrieval.py` sobre el índice actual da **Recall@10 = 0,578 y nDCG@10 =
0,491**, frente a 0,800 / 0,779 en la línea base del 2026-11-17
([detalle](evaluacion/retrieval-indice-ampliado.md)).

- El código de recuperación no cambió en la spec 044 (`application/ask.py`,
  `application/retrieval.py`, `adapters/vector/`, `indexing/` y el script son idénticos a
  `main`).
- La colección `carvajal_chunks` pasó de **125 a 40 792 puntos** (217 documentos, 174 de
  ellos `wp_content_uploads_*`: informes y estados financieros en PDF), por la ampliación
  del corpus de otro frente de trabajo.
- Las etiquetas de `retrieval_questions.json` se hicieron sobre el corpus anterior; los PDF
  desplazan del top-10 a las páginas esperadas.

Afecta por igual a ambos modos del agente y a producción. Recomendación: re-etiquetar el
conjunto de retrieval o ajustar la prioridad de capas o secciones para el corpus ampliado,
en una spec aparte.

## 7. Hallazgos en el modo `routes` (producción actual)

| Ítem | Hallazgo | Severidad |
|---|---|---|
| g-003 | La tarjeta de producto incluye un contenedor de EPS para sopa de microondas; el texto lo descarta, pero la tarjeta lo muestra como opción. | Grave |
| g-024, g-037 | El LLM calcula paquetes, sobrantes y subtotales («6 paquetes: 22.506 COP») que el motor no entregó, porque el regex no leyó la cantidad. | Grave |
| 21 ítems | No consulta lo necesario: el enrutador elige una sola ruta, o una equivocada, para preguntas compuestas o coloquiales. | Menor |

En modo `tools` estos tres hallazgos graves no aparecen: las cifras de cotización salen
del motor y las tarjetas solo traen opciones validadas.

## 8. Decisiones para el owner

1. **¿`AGENT_MODE` pasa a `tools`?** Formalmente faltan SC-001 (2 casos menores), SC-005
   (1 caso dudoso), SC-006a (1,48 s) y SC-004 en su lectura estricta. A la vez, `tools`
   supera a `routes` en todas las garantías y `routes` tiene hallazgos graves en producción
   (§7). **Resuelta el 2026-11-23: sí, con riesgos aceptados → §10.**
2. **¿Se acepta el conjunto v2?** (§2.1). **Resuelta el 2026-11-23: sí.** El owner acepta
   la corrección de `forbidden_materials` en los 9 ítems, derivada de `check_material` y
   `materiales.csv`, sin cambios en las preguntas. La v2 es la versión vigente del conjunto.
3. **¿Se ajusta `AGENT_SYSTEM` para los fallos de §3?** Implica escribir un conjunto v3 para
   medir sin contaminación. **Abierta.**
4. **¿Se abren specs aparte** para los hallazgos de `routes` (§7) y para la regresión de
   retrieval (§6)? **Abierta.**

## 9. Limitación de origen del conjunto (FR-016)

Borrador del asistente de desarrollo, revisado y congelado por el owner sin cambios el
2026-11-23. Quien redactó el borrador ya había leído `agent/router.py`, el primer ítem de
`routing_questions.json` y había probado frases contra el enrutador y el cotizador durante
el plan; no se consultó `routing_questions.json` para escribir los ítems. La versión 2
corrige solo expectativas de material contra `check_material` y la aceptó el owner el
2026-11-23 (§8, decisión 2).

## 10. Decisión del owner

- **Fecha**: 2026-11-23.
- **Decisión**: la 1 de §8. `AGENT_MODE` pasa de `routes` a `tools` como valor por
  defecto en `Settings`, `.env.example`, `compose.yml` y `compose.prod.yml`. `routes` queda
  como interruptor de emergencia (`AGENT_MODE=routes`, sin migraciones) y sigue atendiendo
  el atajo, el respaldo y todos los turnos cuando el modo `tools` no arranca.
- **Modelo**: la decisión vale para `deepseek-flash` sin razonamiento, el modelo con el que
  se midió este informe. `.env.example` y el runbook de producción lo fijan
  (`DEEPSEEK_MODEL=deepseek-flash`); `Settings` y los compose conservan `deepseek-chat` como
  valor por defecto. No se corrió `eval-agent` con ese nombre, pero sí se comprobó contra la
  API el 2026-11-23: una petición con `model: "deepseek-chat"` y `tool_choice: "required"`
  respondió con `"model": "deepseek-flash"`, sin contenido de razonamiento y con la llamada
  a la herramienta. Es decir, el mismo modelo evaluado, por su alias sin razonamiento. `agent.runtime_ready` registra `tools_model` para
  comprobar el modelo en uso.
- **Consecuencia para el proveedor**: el modo `tools` usa siempre DeepSeek, así que solo
  arranca con `LLM_PROVIDER=deepseek` y DeepSeek completo (clave, modelo y URL). Con
  `LLM_PROVIDER=ollama` (el valor por defecto de `Settings` y de los compose) o `claude`,
  el chat sigue en `routes` con ese proveedor aunque haya una clave de DeepSeek para el
  enrutador, `eval-agent` o la revisión automática: los turnos no salen del servidor sin
  decidirlo. Si no arranca, un aviso `agent.tools_mode_unavailable` (con `reason`, sin
  traza) lo registra y `/health` lo publica en el bloque `agent`, sin 503.
- **Motivo**: `tools` supera a `routes` en todas las garantías y en cobertura (+35,1 pp de
  resueltos) y elimina los tres hallazgos graves que `routes` tiene hoy en producción (§7).
- **Riesgos aceptados** como conocidos y menores:
  - SC-001: 2 casos (§3).
  - SC-005: 1 caso dudoso (g-049).
  - SC-006a: línea de estado p95 de 1,48 s frente a la meta de 1 s.
  - SC-004: 91,7 % en la lectura de partes atendidas y 50,0 % en la estricta (§4). El owner
    aceptó el riesgo **sin elegir lectura**: la decisión no depende de ella y la elección
    queda abierta.
- **Período de observación**: dos semanas **desde el día en que `agent.runtime_ready`
  muestra `"mode": "tools"` en producción**, no desde la fecha de la decisión. Previsto del
  2026-11-23 al 2026-12-07; si el despliegue se retrasa, el período se corre con él. Los
  criterios son los del plan (*Convivencia y retiro*):
  - cero violaciones de material o datos sin fuente en los turnos revisados (el tipo de
    g-003, cajas calculadas por el modelo, cuenta como dato sin fuente, igual que en
    SC-001);
  - tasa de respaldo < 2 % de los turnos del modo `tools`;
  - sin aumento de la tasa de «No útil» frente a las dos semanas previas en `routes`.

  Procedimiento (export de logs, muestra diaria de hasta 10 hilos leídos en Postgres,
  consultas de la tasa de respaldo y de «No útil»), activación y vuelta a `routes`:
  [`docs/despliegue-ovh.md`](../../docs/despliegue-ovh.md) (*Modo del agente*). Revisa el
  owner. Si se cumplen los tres criterios, se abre T055 (F5, retiro); si alguno falla, se
  vuelve a `routes` y el caso se registra aquí.

### Registro de la observación

- **Inicio real** (primer `agent.runtime_ready` con `"mode": "tools"` en producción):
  pendiente.
- **Cierre** (inicio + 14 días): pendiente.
- **Base de «No útil»** (dos semanas previas en `routes`, congelada el día del inicio):
  pendiente.

| Fecha | Hilos revisados | Violaciones de material | Datos sin fuente | Respaldo acumulado | Notas |
|---|---|---|---|---|---|
