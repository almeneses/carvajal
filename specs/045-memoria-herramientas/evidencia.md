# Evidencia — Spec 045 (memoria del agente y 20 herramientas)

Registro de las mediciones de la spec. Las corridas con proveedor real (T094–T097) se
hacen una sola vez cada una (research R13) y todavía **no se han hecho**: sus secciones
están pendientes.

## 1. Compuerta de tokens antes de redactar (T085, SC-009, research R14)

Estimación sin proveedor, con los textos que ve el modelo en el primer paso: el
`AGENT_SYSTEM` y los esquemas que envía `bind_tools` (`convert_to_openai_tool`, JSON
compacto). La línea base es la del último commit antes de registrar herramientas nuevas
(`b0bcf89`, seis herramientas).

| Pieza | Base 044 (caracteres) | 045 (caracteres) |
|---|---:|---:|
| Esquemas de las seis de la 044 | 7 631 | 8 250 (+619 por las cinco frases «No la uses para…») |
| Esquemas de las 14 nuevas | — | 9 806 |
| `AGENT_SYSTEM` | 6 636 | 8 809 (área de memoria y bloque por áreas) |
| **Prefijo del paso** | **14 267** | **26 865 (1,88×)** |

Las 14 descripciones nuevas miden entre 208 y 297 caracteres (tope 300); lo que más pesa
en sus esquemas es la estructura JSON de los campos opcionales (`anyOf` con `null`,
`default`, límites), no el texto. Antes de redactar las descripciones finales se acortaron
las descripciones de campo más largas (`ficha_producto`, `comparar_referencias`,
`consultar_cotizacion`, `empresas_del_grupo` y `preguntas_frecuentes`), unos 230
caracteres; no alcanza para volver a 1,5×.

**Tokens por turno.** Un turno gasta `c · P + M`: `c` pasos del modelo (cada uno reenvía
el prefijo `P`) y `M` tokens de historial, pregunta y resultados, que la 045 no hace
crecer salvo el bloque del resumen (≤ 1 500 caracteres) y el de preferencias. Con la
línea base de 6 900 tokens por turno (044, `evidencia.md` §1) y una razón de 3,6 a 4,5
caracteres por token, la estimación queda entre **1,55× y 1,85×** (unos 10 700 a 12 800
tokens por turno). El cociente por turno no puede superar el del prefijo (1,88×) mientras
el número de pasos por turno no crezca, porque `M` no crece en la misma proporción.

**Decisión (regla de T085).** 1,5× no es alcanzable sin quitar herramientas o recortar las
reglas que ya miden los conjuntos de la 044, así que el umbral de SC-009 se ajusta a
**1,9× la línea base (13 110 tokens por turno)**, con la justificación registrada en
*Clarifications* de la spec. La caché de prefijo de DeepSeek reduce el costo de este
crecimiento porque el orden de las herramientas y el sistema son estables; el costo se
informa aparte, como pide SC-009. La medición real es la corrida única del v3 (T095).

## 2. Verificación sin red (T092)

2026-11-24, sobre el código de la US5 (20 herramientas registradas): `make check` en
verde. Ruff sin hallazgos, formato al día (504 archivos), `ty` sin diagnósticos y
**3 728 pruebas** en verde (62 deseleccionadas: integración, e2e y smoke). La sección 1 del
quickstart también pasa por partes: registro, memoria y resumen (241), las once
herramientas nuevas (395) y la FAQ existente sin cambios (36).

## 3. Cotizador (T093)

2026-11-24: `make eval-cotizador` da **18/18 casos OK**, igual que antes del reloj único
(T018) y de las herramientas nuevas.

## 4. Conjunto de herramientas (T094)

Pendiente. Limitaciones conocidas del ejecutor de evaluación, que se declaran con el
resultado:

- No tiene `SummaryWorker`: el resumen solo se rellena de forma determinista.
- No tiene base de cotizaciones: `consultar_cotizacion` devuelve `error` declarado
  (ítem `h045-12`).
- `preguntas_frecuentes` solo tiene corpus y LLM con el runtime real y una base
  configurada; sin ellos devuelve `error` declarado (ítem `h045-16`). El LLM de la FAQ no
  pasa por el medidor de uso del informe.
- El informe da el crudo estricto y el crudo con las equivalencias congeladas de T090.

## 5. Generalización v3, simulación y resumidor (T095–T097)

Pendientes.
