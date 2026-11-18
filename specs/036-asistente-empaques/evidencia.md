# Evidencia de los criterios de éxito — spec 036 (T044)

Estado al 2026-11-18, spec cerrada como as-built (incluye #135, #139–#143 y #145).

| Criterio | Estado | Evidencia |
|---|---|---|
| **SC-001** 0 % de recomendaciones que violan una regla de material | ✅ Cumple | `tests/test_routing_eval_set.py::test_ninguna_recomendacion_viola_la_regla_de_material` ejecuta la secuencia comercial real sobre las preguntas con `forbidden_materials`; `eval-router`: 0 violaciones con reglas y con el LLM (corrida 2). Reglas de material en `tests/test_commercial_tools.py`. |
| **SC-002** 100 % de cajas y totales correctos | ✅ Cumple | `tests/test_sc_036.py::test_sc002_*`: las 52 referencias del catálogo real con 6 cantidades (1 a 123 457), redondeo hacia arriba, excedente y total = precio × cajas en `Decimal`. Escenarios de US2 en `tests/test_commercial_quote.py`. |
| **SC-003** 100 % de precios con moneda, nota de impuestos y fecha | ✅ Cumple | `tests/test_sc_036.py::test_sc003_*`: cada referencia con precio da moneda, «con/sin impuestos», fecha de captura y aviso; sin precio, `NoPriceError`. Las tarjetas muestran total, aviso y fecha (`tests/test_productos.py`). |
| **SC-004** ≥ 90 % de precisión de ruta con el enrutador por defecto | ✅ Cumple | `carvajal eval-router`: 100 % de ruta sobre 110 preguntas con `rules` ([`docs/resultados-router.md`](../../docs/resultados-router.md)). El conjunto se escribió con las reglas: no mide generalización. |
| **SC-005** Sin regresión en router, grafo y recuperación | ✅ Cumple (recuperación indirecta) | `tests/test_agent_router.py` y `tests/test_agent_graph.py` sin cambios y en verde; las 49 preguntas previas a la 036 conservan la ruta del router de `dev` (`test_las_rutas_existentes_no_cambian`). Las 31 consultas de recuperación mantienen ruta, intención y consulta expandida, así que el retrieval no cambia; para confirmarlo con Qdrant: `make index-eval` (Recall@10 esperado 0.8000). |
| **SC-006** Informe comparativo de los tres enrutadores | ➡️ Transferido a la 041 | `rules` y `llm` medidos con las mismas métricas (dos corridas con DeepSeek). `decision` está implementado y probado con un modelo falso; la elección del paquete, la configuración de `DECISION_MODEL` y la corrida comparativa pasan a [`specs/041-enrutador-jev`](../041-enrutador-jev/spec.md), que hereda este criterio como FR-013 y SC-005. La 036 cierra sin exigir un modelo de decisión, coherente con su alcance: el enrutador por reglas es el valor por defecto. |

## Pendientes que afectan la evidencia

- **T009**: traspasada a la spec 039 (catálogo de Colombia con precios). La 036 cierra sin
  referencias de Colombia, como estaba previsto, y en Colombia el asistente remite a los
  distribuidores hasta que la 039 se implemente.
- **T043**: corrida por API el 2026-11-18 con `make check` en verde (1124 pruebas) y las tres
  conversaciones registradas en [`docs/demo-036.md`](../../docs/demo-036.md). Falta la
  revisión visual de las tarjetas en el navegador.
- **SC-006**: transferido a la spec 041 (Jev cloud). La 036 cierra con `rules` como enrutador
  por defecto y sin exigir un modelo de decisión.
- **Generalización del enrutador**: la spec 041 incluye el grupo ciego de al menos 30
  preguntas y la comparación de los tres enrutadores en igualdad de condiciones.
