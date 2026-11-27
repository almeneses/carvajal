# Análisis de las transcripciones del ensayo

Las alertas automáticas de `informe.md` solo ven respuestas genéricas; los problemas reales
(un carrito inventado, una fuente de otro producto, una tapa que no sirve) se encuentran
**leyendo** las conversaciones. Con ~68 archivos conviene repartirlos.

## Grupos (web y WhatsApp del mismo guion juntos)

1. Cotización: `cambiar_cantidad`, `cambio_de_producto`, `cantidades_raras`, `caro_y_oferta`, `comparativo`.
2. Cotización: `ciudad_sin_distribuidor`, `referencia_ambigua_y_ciudad`, `producto_inexistente`, `varios_productos`, `ver_carrito`, `tendero_directo`.
3. Elección y voz: `opciones_por_voz`, `opciones_y_alternativa`, `retomar`, `si_ante_la_pregunta`, `sin_temperatura`, `solo_mirar`, `microondas`.
4. Fuera de flujo y seguridad: `fuera_de_alcance`, `fuera_de_flujo`, `inyeccion`, `insulto`, `ingles`, `errores_de_tipeo`.
5. Corpus: `financiero`, `historia`, `sedes`, `saludo_y_ayuda` y la salida de `ensayo-completo`.
6. Pedido: los cinco `pedido_*` de `exploracion-pedido/` y la salida de `ensayo-pedido`.

## Lector (uno por grupo)

Contexto que necesita: el asistente tiene un flujo determinista (rutas `cotizacion`, `pedido`,
`eleccion`…) y un agente con herramientas (ruta `agent`); Jev sustituye las respuestas no
respaldadas por un genérico; cada turno de la web trae una línea JSON con `ruta`, `espera`,
`tarjetas` y `compuerta`; los compradores `Ensayo …@example.com` son ficticios. Pasa la lista
de hallazgos **conocidos** del informe anterior para que los marque «conocido».

Pide, por guion, un veredicto (`bien` / `aceptable` / `mal`) y hallazgos con: título,
gravedad, tipo (`bug`, `calidad_respuesta`, `ux`, `seguridad`, `rendimiento`,
`prueba_desactualizada`, `positivo`), canal, guion, archivo, turno, **cita literal** y lo
esperado. Busca: respuestas incoherentes, sustituciones genéricas que dejan sin respuesta,
`espera` equivocada, datos inventados (SKU, precios, sedes), fuentes que no cuadran,
diferencias injustificadas entre canales, Markdown roto en WhatsApp, temas ajenos atendidos,
**tarjetas del carrito escritas por el modelo** y notas internas que llegan al cliente.

## Verificador (dos, la mitad de los hallazgos cada uno)

Escéptico: relee la transcripción y trata de **refutar** cada hallazgo (¿la cita existe? ¿la
respuesta era razonable?). Si es real, busca la causa en el código (solo lectura:
`packages/core/src/carvajal/`, `apps/api/`, `apps/mcp/`, `tests/e2e/guiones.py`) con
`archivo:línea` y propone un arreglo concreto. Ante la duda, `real=false`.

## Después

- Ordena por gravedad final y agrupa por causa (varios hallazgos suelen ser uno).
- **Revisa tú** los de gravedad alta abriendo la transcripción: en el ensayo del 2026-11-27
  así se confirmó que una tarjeta del carrito la había escrito el modelo (el texto no existía
  en `render.py`) y que el flujo no había agregado nada (logs sin `cotizacion.linea_agregada`).
- Para saber si un arreglo funcionó, repite solo los guiones afectados:
  `set -a && . ./.env.ensayo && set +a && uv run pytest tests/e2e/test_exploracion.py -m exploracion -k "<guion> or <guion>" -q -s -p no:cacheprovider`,
  dos veces (con `ENSAYO_COTIZAR=todas` si el guion necesita folio).
