# Evaluación de retrieval — Qdrant

> Generado: 2026-11-13T22:43:02.142296+00:00 · colección `carvajal_chunks` · k=10 · puntos=125

Etiquetas manuales a nivel de documento. La relevancia se marca sobre `data/gold`; no depende del LLM.

## Resumen

| Métrica | Valor |
|---|---:|
| Consultas etiquetadas | 30 |
| Recall@10 | 0.8000 |
| nDCG@10 | 0.7791 |
| Consultas sin evidencia | 1 |

## Detalle por consulta

| ID | Consulta | Recall@k | nDCG@k | Top-3 recuperado |
|---|---|---:|---:|---|
| q01 | ¿Qué es Carvajal y a qué sectores se dedica? | 0.500 | 0.613 | quienes_somos, carvajal_s_a_futuro_consolidado_ante_desafios_globales, historia |
| q02 | ¿En qué año y ciudad fue fundada Carvajal? | 1.000 | 1.000 | historia, historia, quienes_somos |
| q03 | ¿En cuántos países tiene presencia Carvajal? | 0.667 | 0.765 | carvajal_s_a_futuro_consolidado_ante_desafios_globales, quienes_somos, carvajal_s_a_futuro_consolidado_ante_desafios_globales |
| q04 | ¿Cuáles son los cuatro sectores en los que opera Carvajal? | 0.500 | 0.387 | gobierno_corporativo, quienes_somos, quienes_somos |
| q05 | ¿Cuál es el propósito superior de Carvajal? | 1.000 | 1.000 | proposito_superior, home, home |
| q06 | ¿Qué empresas conforman el sector de Papel y Empaques? | 1.000 | 0.605 | quienes_somos, quienes_somos, pulpa_y_papel |
| q07 | ¿Qué es Bioform y a qué industria está dirigido? | 1.000 | 1.000 | empaques, quienes_somos, empaques |
| q08 | ¿Qué marca de educación tiene Carvajal y a quién se dirige? | 1.000 | 1.000 | educacion, educacion, historia |
| q09 | ¿Qué soluciones ofrece Carvajal Tecnología y Servicios? | 1.000 | 1.000 | tecnologia, tecnologia, comunicacion |
| q10 | ¿Qué es Mepal y a qué sector pertenece? | 0.667 | 0.765 | quienes_somos, espacios, espacios |
| q11 | ¿Qué hace Carvajal Espacios? | 1.000 | 1.000 | espacios, espacios, historia |
| q12 | ¿Qué es LiquidezYa? | 0.500 | 0.613 | quienes_somos, quienes_somos, carvajal_reporto_ingresos_por_2_8_billones_con_corte_al_tercer_trimestre_3999096 |
| q13 | ¿En qué consiste el negocio de Propiedades e Inversiones? | 1.000 | 1.000 | propiedades_e_inversiones, home, home |
| q14 | ¿Cómo está conformado el gobierno corporativo de Carvajal? | 1.000 | 1.000 | gobierno_corporativo, gobierno_corporativo, gobierno_corporativo |
| q15 | ¿Qué compromisos éticos declara la empresa? | 0.500 | 0.613 | compromiso_etico, compromiso_etico, compromiso_etico |
| q16 | ¿Qué iniciativas de sostenibilidad adelanta Carvajal? | 1.000 | 0.631 | noticias, home, home |
| q17 | ¿Qué noticias recientes publica Carvajal en su sitio web? | 1.000 | 1.000 | noticias, noticias, carvajal_s_a_futuro_consolidado_ante_desafios_globales |
| q18 | ¿Cuál es la historia de Carvajal desde su fundación? | 1.000 | 1.000 | historia, historia, historia |
| q19 | ¿Qué información ofrece el portal de inversionistas? | 1.000 | 1.000 | inicio, inicio, inicio |
| q20 | ¿Qué fundaciones están vinculadas a Carvajal? | 1.000 | 0.693 | historia, historia, historia |
| q21 | ¿Cómo puedo contactar a Carvajal o solicitar información? | 0.000 | 0.000 | comunicacion, historia, comunicacion |
| q22 | ¿Qué es la línea ética de Carvajal? | 1.000 | 1.000 | compromiso_etico, compromiso_etico, compromiso_etico |
| q23 | ¿Qué productos o servicios ofrece Carvajal en comunicación? | 1.000 | 1.000 | comunicacion, home, comunicacion |
| q24 | ¿Qué presencia tiene Carvajal en Colombia? | 0.667 | 0.765 | carvajal_s_a_futuro_consolidado_ante_desafios_globales, quienes_somos, carvajal_s_a_futuro_consolidado_ante_desafios_globales |
| q25 | ¿Cuáles son los valores o principios que guían a Carvajal? | 0.000 | 0.000 | compromiso_etico, compromiso_etico, compromiso_etico |
| q26 | ¿Cuáles fueron los ingresos consolidados de Carvajal en 2024? | 1.000 | 0.920 | carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_s_a_futuro_consolidado_ante_desafios_globales, resultados_empresariales_2024_de_carvajal_4099828 |
| q27 | ¿Cuál fue el EBITDA consolidado de Carvajal a septiembre de 2025 y qué margen reportó? | 1.000 | 1.000 | carvajal_reporto_ingresos_por_2_58_billones_a_septiembre_de_2025_4269762, carvajal_reporto_ingresos_por_2_58_billones_a_septiembre_de_2025_4269762, carvajal_reporto_ingresos_por_2_58_billones_a_septiembre_de_2025_4269762 |
| q28 | ¿Qué utilidad o pérdida neta reportó Carvajal en 2024 y a qué filial se atribuye? | 1.000 | 1.000 | carvajal_y_sus_empresas_reportan_resultados_0239html, carvajal_y_sus_empresas_reportan_resultados_0239html, carvajal_y_sus_empresas_reportan_resultados_0239html |
| q29 | ¿Cuáles son los activos, pasivos y patrimonio consolidados de Carvajal al cierre de 2024? | 1.000 | 1.000 | carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_s_a_futuro_consolidado_ante_desafios_globales |
| q30 | ¿Cuántos empleados tiene Carvajal y qué fuente lo reporta? | 0.000 | 0.000 | carvajal_y_sus_empresas_reportan_resultados_0239html, carvajal_y_sus_empresas_reportan_resultados_0239html, carvajal_y_sus_empresas_reportan_resultados_0239html |
| q31 | ¿Cuál es el número de teléfono de la sede de Carvajal en Tokio? | abstención: sí | — | historia, compromiso_etico, comunicacion |

El detalle por consulta incluye el top-3 y su score; el JSON completo con los 10 resultados y extractos se regenera con el mismo comando y no se versiona para no inflar el PR.
