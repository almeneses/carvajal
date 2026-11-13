# Evaluación de retrieval — Qdrant

> Generado: 2026-11-13T22:02:50.034783+00:00 · colección `carvajal_chunks` · k=10 · puntos=125

Etiquetas manuales a nivel de documento. La relevancia se marca sobre `data/gold`; no depende del LLM.

## Resumen

| Métrica | Valor |
|---|---:|
| Consultas etiquetadas | 30 |
| Recall@10 | 0.8444 |
| nDCG@10 | 0.8002 |
| Consultas sin evidencia | 1 |

## Detalle por consulta

| ID | Consulta | Recall@k | nDCG@k | Top-3 recuperado |
|---|---|---:|---:|---|
| q01 | ¿Qué es Carvajal y a qué sectores se dedica? | 1.000 | 1.000 | quienes_somos, home, carvajal_s_a_futuro_consolidado_ante_desafios_globales |
| q02 | ¿En qué año y ciudad fue fundada Carvajal? | 1.000 | 0.693 | home, home, historia |
| q03 | ¿En cuántos países tiene presencia Carvajal? | 0.667 | 0.531 | carvajal_y_sus_empresas_reportan_resultados_0239html, carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_y_sus_empresas_reportan_resultados_0239html |
| q04 | ¿Cuáles son los cuatro sectores en los que opera Carvajal? | 0.500 | 0.613 | quienes_somos, espacios, espacios |
| q05 | ¿Cuál es el propósito superior de Carvajal? | 1.000 | 1.000 | proposito_superior, home, home |
| q06 | ¿Qué empresas conforman el sector de Papel y Empaques? | 1.000 | 0.693 | quienes_somos, empaques, empaques |
| q07 | ¿Qué es Bioform y a qué industria está dirigido? | 1.000 | 1.000 | empaques, quienes_somos, empaques |
| q08 | ¿Qué marca de educación tiene Carvajal y a quién se dirige? | 1.000 | 1.000 | educacion, educacion, home |
| q09 | ¿Qué soluciones ofrece Carvajal Tecnología y Servicios? | 1.000 | 1.000 | tecnologia, tecnologia, comunicacion |
| q10 | ¿Qué es Mepal y a qué sector pertenece? | 1.000 | 0.933 | quienes_somos, espacios, espacios |
| q11 | ¿Qué hace Carvajal Espacios? | 1.000 | 1.000 | espacios, home, home |
| q12 | ¿Qué es LiquidezYa? | 0.500 | 0.613 | quienes_somos, tecnologia, empaques |
| q13 | ¿En qué consiste el negocio de Propiedades e Inversiones? | 1.000 | 1.000 | propiedades_e_inversiones, propiedades_e_inversiones, inicio |
| q14 | ¿Cómo está conformado el gobierno corporativo de Carvajal? | 1.000 | 1.000 | gobierno_corporativo, gobierno_corporativo, gobierno_corporativo |
| q15 | ¿Qué compromisos éticos declara la empresa? | 0.500 | 0.613 | compromiso_etico, compromiso_etico, compromiso_etico |
| q16 | ¿Qué iniciativas de sostenibilidad adelanta Carvajal? | 1.000 | 1.000 | home, home, empaques |
| q17 | ¿Qué noticias recientes publica Carvajal en su sitio web? | 1.000 | 1.000 | noticias, noticias, inicio |
| q18 | ¿Cuál es la historia de Carvajal desde su fundación? | 1.000 | 1.000 | historia, historia, historia |
| q19 | ¿Qué información ofrece el portal de inversionistas? | 1.000 | 1.000 | inicio, inicio, inicio |
| q20 | ¿Qué fundaciones están vinculadas a Carvajal? | 1.000 | 0.920 | home, home, historia |
| q21 | ¿Cómo puedo contactar a Carvajal o solicitar información? | 0.500 | 0.387 | comunicacion, comunicacion, comunicacion |
| q22 | ¿Qué es la línea ética de Carvajal? | 1.000 | 1.000 | compromiso_etico, compromiso_etico, compromiso_etico |
| q23 | ¿Qué productos o servicios ofrece Carvajal en comunicación? | 1.000 | 1.000 | comunicacion, comunicacion, comunicacion |
| q24 | ¿Qué presencia tiene Carvajal en Colombia? | 0.667 | 0.765 | carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_s_a_futuro_consolidado_ante_desafios_globales, quienes_somos |
| q25 | ¿Cuáles son los valores o principios que guían a Carvajal? | 0.000 | 0.000 | home, home, carvajal_s_a_futuro_consolidado_ante_desafios_globales |
| q26 | ¿Cuáles fueron los ingresos consolidados de Carvajal en 2024? | 1.000 | 0.920 | carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_s_a_futuro_consolidado_ante_desafios_globales |
| q27 | ¿Cuál fue el EBITDA consolidado de Carvajal a septiembre de 2025 y qué margen reportó? | 1.000 | 0.631 | carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_reporto_ingresos_por_2_58_billones_a_septiembre_de_2025_4269762, carvajal_s_a_futuro_consolidado_ante_desafios_globales |
| q28 | ¿Qué utilidad o pérdida neta reportó Carvajal en 2024 y a qué filial se atribuye? | 1.000 | 0.693 | resultados_empresariales_2024_de_carvajal_4099828, carvajal_y_sus_empresas_reportan_resultados_0239html, resultados_empresariales_2024_de_carvajal_4099828 |
| q29 | ¿Cuáles son los activos, pasivos y patrimonio consolidados de Carvajal al cierre de 2024? | 1.000 | 1.000 | carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_s_a_futuro_consolidado_ante_desafios_globales |
| q30 | ¿Cuántos empleados tiene Carvajal y qué fuente lo reporta? | 0.000 | 0.000 | carvajal_y_sus_empresas_reportan_resultados_0239html, carvajal_y_sus_empresas_reportan_resultados_0239html, carvajal_y_sus_empresas_reportan_resultados_0239html |
| q31 | ¿Cuál es el número de teléfono de la sede de Carvajal en Tokio? | abstención: sí | — | comunicacion, carvajal_y_sus_empresas_reportan_resultados_0239html, tecnologia |

## Top-3 por consulta

- **q01** · ¿Qué es Carvajal y a qué sectores se dedica?
  - top-3: `quienes_somos, home, carvajal_s_a_futuro_consolidado_ante_desafios_globales`
  - scores: `0.7702, 0.7369, 0.7248`
- **q02** · ¿En qué año y ciudad fue fundada Carvajal?
  - top-3: `home, home, historia`
  - scores: `0.7521, 0.7221, 0.7135`
- **q03** · ¿En cuántos países tiene presencia Carvajal?
  - top-3: `carvajal_y_sus_empresas_reportan_resultados_0239html, carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_y_sus_empresas_reportan_resultados_0239html`
  - scores: `0.7223, 0.7206, 0.7201`
- **q04** · ¿Cuáles son los cuatro sectores en los que opera Carvajal?
  - top-3: `quienes_somos, espacios, espacios`
  - scores: `0.7325, 0.6895, 0.686`
- **q05** · ¿Cuál es el propósito superior de Carvajal?
  - top-3: `proposito_superior, home, home`
  - scores: `0.794, 0.7448, 0.7422`
- **q06** · ¿Qué empresas conforman el sector de Papel y Empaques?
  - top-3: `quienes_somos, empaques, empaques`
  - scores: `0.7237, 0.7125, 0.6904`
- **q07** · ¿Qué es Bioform y a qué industria está dirigido?
  - top-3: `empaques, quienes_somos, empaques`
  - scores: `0.6555, 0.6539, 0.6449`
- **q08** · ¿Qué marca de educación tiene Carvajal y a quién se dirige?
  - top-3: `educacion, educacion, home`
  - scores: `0.7959, 0.7878, 0.6963`
- **q09** · ¿Qué soluciones ofrece Carvajal Tecnología y Servicios?
  - top-3: `tecnologia, tecnologia, comunicacion`
  - scores: `0.8314, 0.7626, 0.7587`
- **q10** · ¿Qué es Mepal y a qué sector pertenece?
  - top-3: `quienes_somos, espacios, espacios`
  - scores: `0.708, 0.693, 0.6866`
- **q11** · ¿Qué hace Carvajal Espacios?
  - top-3: `espacios, home, home`
  - scores: `0.7958, 0.7654, 0.7631`
- **q12** · ¿Qué es LiquidezYa?
  - top-3: `quienes_somos, tecnologia, empaques`
  - scores: `0.6037, 0.5906, 0.5769`
- **q13** · ¿En qué consiste el negocio de Propiedades e Inversiones?
  - top-3: `propiedades_e_inversiones, propiedades_e_inversiones, inicio`
  - scores: `0.7336, 0.7207, 0.7036`
- **q14** · ¿Cómo está conformado el gobierno corporativo de Carvajal?
  - top-3: `gobierno_corporativo, gobierno_corporativo, gobierno_corporativo`
  - scores: `0.7911, 0.7896, 0.7798`
- **q15** · ¿Qué compromisos éticos declara la empresa?
  - top-3: `compromiso_etico, compromiso_etico, compromiso_etico`
  - scores: `0.7884, 0.7864, 0.7815`
- **q16** · ¿Qué iniciativas de sostenibilidad adelanta Carvajal?
  - top-3: `home, home, empaques`
  - scores: `0.7821, 0.7775, 0.7721`
- **q17** · ¿Qué noticias recientes publica Carvajal en su sitio web?
  - top-3: `noticias, noticias, inicio`
  - scores: `0.7483, 0.746, 0.7106`
- **q18** · ¿Cuál es la historia de Carvajal desde su fundación?
  - top-3: `historia, historia, historia`
  - scores: `0.8324, 0.8306, 0.8188`
- **q19** · ¿Qué información ofrece el portal de inversionistas?
  - top-3: `inicio, inicio, inicio`
  - scores: `0.75, 0.7382, 0.704`
- **q20** · ¿Qué fundaciones están vinculadas a Carvajal?
  - top-3: `home, home, historia`
  - scores: `0.7945, 0.7722, 0.7364`
- **q21** · ¿Cómo puedo contactar a Carvajal o solicitar información?
  - top-3: `comunicacion, comunicacion, comunicacion`
  - scores: `0.7655, 0.7365, 0.7359`
- **q22** · ¿Qué es la línea ética de Carvajal?
  - top-3: `compromiso_etico, compromiso_etico, compromiso_etico`
  - scores: `0.8171, 0.8031, 0.7954`
- **q23** · ¿Qué productos o servicios ofrece Carvajal en comunicación?
  - top-3: `comunicacion, comunicacion, comunicacion`
  - scores: `0.8134, 0.7952, 0.7902`
- **q24** · ¿Qué presencia tiene Carvajal en Colombia?
  - top-3: `carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_s_a_futuro_consolidado_ante_desafios_globales, quienes_somos`
  - scores: `0.7443, 0.7426, 0.7312`
- **q25** · ¿Cuáles son los valores o principios que guían a Carvajal?
  - top-3: `home, home, carvajal_s_a_futuro_consolidado_ante_desafios_globales`
  - scores: `0.7631, 0.7357, 0.7279`
- **q26** · ¿Cuáles fueron los ingresos consolidados de Carvajal en 2024?
  - top-3: `carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_s_a_futuro_consolidado_ante_desafios_globales`
  - scores: `0.8226, 0.8186, 0.8157`
- **q27** · ¿Cuál fue el EBITDA consolidado de Carvajal a septiembre de 2025 y qué margen reportó?
  - top-3: `carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_reporto_ingresos_por_2_58_billones_a_septiembre_de_2025_4269762, carvajal_s_a_futuro_consolidado_ante_desafios_globales`
  - scores: `0.7956, 0.7939, 0.7901`
- **q28** · ¿Qué utilidad o pérdida neta reportó Carvajal en 2024 y a qué filial se atribuye?
  - top-3: `resultados_empresariales_2024_de_carvajal_4099828, carvajal_y_sus_empresas_reportan_resultados_0239html, resultados_empresariales_2024_de_carvajal_4099828`
  - scores: `0.7914, 0.7898, 0.7897`
- **q29** · ¿Cuáles son los activos, pasivos y patrimonio consolidados de Carvajal al cierre de 2024?
  - top-3: `carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_s_a_futuro_consolidado_ante_desafios_globales`
  - scores: `0.8202, 0.8164, 0.8146`
- **q30** · ¿Cuántos empleados tiene Carvajal y qué fuente lo reporta?
  - top-3: `carvajal_y_sus_empresas_reportan_resultados_0239html, carvajal_y_sus_empresas_reportan_resultados_0239html, carvajal_y_sus_empresas_reportan_resultados_0239html`
  - scores: `0.758, 0.7472, 0.7464`
- **q31** · ¿Cuál es el número de teléfono de la sede de Carvajal en Tokio?
  - top-3: `comunicacion, carvajal_y_sus_empresas_reportan_resultados_0239html, tecnologia`
  - scores: `0.6855, 0.6851, 0.6808`

El JSON completo con extractos y los 10 resultados por consulta se regenera con el mismo comando y no se versiona para no inflar el PR.
