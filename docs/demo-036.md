# Prueba de punta a punta del asistente de empaques (spec 036, T043)

> Entrada: <http://localhost:8001> (`make web`). API: <http://localhost:8000> (`make api`).
> Prepara el entorno como en [`demo-m2.md`](demo-m2.md) y deja a la vista los logs de la API.
> Con `AGENT_ROUTER=rules` (por defecto) la ruta no depende del LLM; el LLM solo redacta.

Cada conversación se hace en un **hilo nuevo**. Un seguimiento hereda la solicitud anterior si
es corto (hasta 8 palabras) y no menciona a Carvajal. En la traza del chat se ve la ruta y la
herramienta; en los logs, la línea `agent.route_decided` con los datos extraídos (FR-012).

## Conversación 1 — Recomendación, cantidad y precio en México (US1 + US2)

| Turno | Pregunta | Qué debe pasar |
|---|---|---|
| 1 | `Necesito 5.000 vasos de 12 oz para café en México` | `route=commercial`, `tool=catalogo_empaques`. Tarjetas de vasos de **papel** (ningún PET ni PS), cada una con SKU, 1000 unidades por caja, **5 cajas**, total en **MXN** «en total», aviso de precio de referencia y «Ver ficha · capturada el 2026-10-23». La respuesta no promete existencias. |
| 2 | `¿y si son 4.500?` | Hereda vaso, 12 oz, café y México: 5 cajas con 500 unidades de sobra por referencia. |

Log esperado del turno 1: `extracted={'use': 'bebida_caliente', 'temperature': 'caliente',
'capacity_oz': 12.0, 'family': 'vaso', 'quantity': 5000, 'country': 'MX'}`.

## Conversación 2 — Aclaración y regla de material (US1 escenarios 2 y 3)

| Turno | Pregunta | Qué debe pasar |
|---|---|---|
| 1 | `busco contenedores para mi restaurante` | `route=direct`, `intent=commercial_clarify`: pregunta si el contenido es caliente, frío o ambiente. Sin tarjetas. |
| 2 | `para sopa caliente, aptos para microondas` | `route=commercial`, hereda «contenedor». Con el catálogo actual **ningún** contenedor es apto para microondas (EPS, pulpa y papel se descartan con su motivo): el asistente lo dice y no ofrece una alternativa que incumpla la regla. |
| 3 | `en México` | Mismo resultado; pide la cantidad solo si hay opciones. |

## Conversación 3 — Colombia, distribuidores y caso a la medida (US3 + US4)

| Turno | Pregunta | Qué debe pasar |
|---|---|---|
| 1 | `vasos para jugo en Medellín` | `route=commercial`. Sin precios ni estimaciones. Mientras el catálogo de Colombia (T009) no esté cargado, la nota lo dice y lista los **distribuidores de Medellín** con dirección y teléfono. |
| 2 | `¿y en Tuluá?` | No hay distribuidores publicados: lista las ciudades que sí tienen. |
| 3 | `quiero vasos personalizados con mi logo` | `intent=commercial_custom`: no recomienda referencias; da los canales oficiales (tienda de México y directorio de Colombia). |
| 4 | `¿Qué vasos fabrica Carvajal Empaques?` | `route=rag`: pregunta institucional, responde con fuentes del corpus. |

## Registro de la corrida

| Conversación | Fecha | Resultado | Observaciones |
|---|---|---|---|
| 1 | | | |
| 2 | | | |
| 3 | | | |
