# Evidencia: spec 051 (fotos de alimentos → empaque sugerido)

## Prueba previa (2026-11-24, antes de la spec)

11 fotos de Wikimedia Commons, una petición de Jev (`jev-1.14.0`) por foto con siete
preguntas: tipo correcto 11/11 (confianza ≥ 0,77), foto sin comida rechazada (0,05),
mediana 216 ms, ~2.900 tokens de entrada por foto (~0,00013 USD). «¿Se derrama?» (Noul)
dejó los sólidos entre 0,6 y 0,77 y Box enmarcó mal la bandeja paisa: ambas preguntas
quedaron fuera del diseño (research R1).

## Evaluación (FR-027, SC-001 a SC-004)

`uv run python scripts/evaluar_fotos.py`, 22 fotos de
[`evaluacion/fotos.json`](evaluacion/fotos.json) (19 de comida y 3 de control), 22 llamadas
anotadas en [`evaluacion/jev-calls.jsonl`](evaluacion/jev-calls.jsonl) (sin texto ni
imagen). Informe: [`evaluacion/informe.md`](evaluacion/informe.md).

| Criterio | Resultado | Meta |
|---|---|---|
| SC-001 tipo correcto (comida) | 19/19 | ≥ 85 % |
| SC-001 controles rechazados o preguntados | 3/3 | 100 % |
| SC-002 sugerencias de material no apto | 0 | 0 |
| SC-003 referencias reales del país con ficha | 100 % (prueba `test_ninguna_sugerencia_usa_un_material_no_apto`, 180 combinaciones) | 100 % |
| SC-004 latencia de Jev p50 / p95 | 432 / 484 ms | < 5 s de extremo a extremo |

- Preguntas en lugar de sugerencias, todas legítimas: guacamole y ají (temperatura 0,46 y
  0,48 entre ambiente y fría) y papas fritas con huevo (plato fuerte 0,58 frente a
  frituras).
- Corrección tras la corrida, sin volver a llamar a Jev: la torta de chocolate (postre
  sólido) recibió vasos térmicos; ahora un postre sólido va en contenedor, charola o plato.
- Coste: 83.824 tokens de entrada en total (~0,0035 USD).

## Pendiente de verificar en producción

- WhatsApp de extremo a extremo con OpenClaw (montaje de adjuntos, `chmod 711` e instrucción
  del agente): depende del despliegue y de la configuración del VPS
  ([guía](../../docs/whatsapp-openclaw.md#fotos-de-alimentos-spec-051)).
- SC-007 con el modelo real («foto → cotízame N del primero»): el turno guardado lleva la
  ref. de cada sugerencia (prueba `test_la_foto_produce_un_turno_guardado_con_las_referencias`);
  la cotización posterior la decide el agente con DeepSeek.
