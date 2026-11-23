# Evaluación de retrieval — Qdrant

> Generado: 2026-11-23T23:20:43.398512+00:00 · colección `carvajal_chunks` · k=10 · puntos=40792

Etiquetas manuales a nivel de documento. La relevancia se marca sobre `data/gold`; no depende del LLM.

## Resumen

| Métrica | Valor |
|---|---:|
| Consultas etiquetadas | 30 |
| Recall@10 | 0.5778 |
| nDCG@10 | 0.4905 |
| Consultas sin evidencia | 1 |

## Detalle por consulta

| ID | Consulta | Recall@k | nDCG@k | Top-3 recuperado |
|---|---|---:|---:|---|
| q01 | ¿Qué es Carvajal y a qué sectores se dedica? | 0.500 | 0.307 | wp_content_uploads_2025_04_2024_informe_anual_carvajal_b16_c15_digital_pdf, noticias_el_sector_privado_se_dio_cuenta_que_era_un_actor_relevante_en_el_cuidado_ambienta_f329d12058, quienes_somos |
| q02 | ¿En qué año y ciudad fue fundada Carvajal? | 0.500 | 0.204 | noticias_buenaventura_territorio_focalizado_por_fundacion_carvajal, noticias_buenaventura_territorio_focalizado_por_fundacion_carvajal, noticias_cali_se_escribe_con_c_de_carvajal |
| q03 | ¿En cuántos países tiene presencia Carvajal? | 0.667 | 0.498 | noticias_la_huella_mas_importante_que_hemos_dejado_es_nuestro_trabajo_en_la_reduccion_de_l_61e398ba8c, noticias_la_huella_mas_importante_que_hemos_dejado_es_nuestro_trabajo_en_la_reduccion_de_l_61e398ba8c, carvajal_s_a_futuro_consolidado_ante_desafios_globales |
| q04 | ¿Cuáles son los cuatro sectores en los que opera Carvajal? | 0.500 | 0.387 | noticias_el_sector_privado_se_dio_cuenta_que_era_un_actor_relevante_en_el_cuidado_ambienta_f329d12058, quienes_somos, inversionistas_perfil_corporativo_descripcion_del_negocio_acerca_de_nosotros |
| q05 | ¿Cuál es el propósito superior de Carvajal? | 1.000 | 1.000 | proposito_superior, wp_content_uploads_2022_06_infor_anual_2021_carvajal_1_pdf, wp_content_uploads_2022_12_202212_infor_anual_2021_b31_c16_final_web_2_mar_23_2022_pdf |
| q06 | ¿Qué empresas conforman el sector de Papel y Empaques? | 0.000 | 0.000 | wp_content_uploads_2024_03_informe_anual_2023_carvajal_sa_pdf, wp_content_uploads_2024_03_informe_anual_2023_carvajal_sa_pdf, wp_content_uploads_2022_06_informe_de_sostenibilidad_2021_1_pdf |
| q07 | ¿Qué es Bioform y a qué industria está dirigido? | 1.000 | 0.431 | wp_content_uploads_2021_11_prospectoinformacioncarvajal_pdf, wp_content_uploads_2022_01_prospecto_de_informacion_noviembre_2019_de_cas_pdf, noticias_del_plastico_al_carton_la_migracion_que_impulsa_la_eco_innovacion |
| q08 | ¿Qué marca de educación tiene Carvajal y a quién se dirige? | 1.000 | 1.000 | educacion, educacion, wp_content_uploads_2021_11_informe_anual_carvajal_2018_pdf |
| q09 | ¿Qué soluciones ofrece Carvajal Tecnología y Servicios? | 1.000 | 0.500 | wp_content_uploads_2021_11_carvajal_consolidado_junio_2017_pdf, wp_content_uploads_2026_11_as_7448_26_notas_a_los_ef_consolidados_septiembre_2026_pdf, tecnologia |
| q10 | ¿Qué es Mepal y a qué sector pertenece? | 0.667 | 0.437 | wp_content_uploads_2022_12_202212_infor_anual_2021_b31_c16_final_web_2_mar_23_2022_pdf, wp_content_uploads_2022_06_infor_anual_2021_carvajal_1_pdf, quienes_somos |
| q11 | ¿Qué hace Carvajal Espacios? | 1.000 | 1.000 | espacios, espacios, espacios |
| q12 | ¿Qué es LiquidezYa? | 0.000 | 0.000 | noticias_liquidezya_un_apoyo_en_factoring_para_las_pymes, canal_clientes_liquidezya_un_apoyo_en_factoring_para_las_pymes, wp_content_uploads_2024_03_informe_anual_2023_carvajal_sa_pdf |
| q13 | ¿En qué consiste el negocio de Propiedades e Inversiones? | 0.000 | 0.000 | wp_content_uploads_2021_11_prospectoinformacioncarvajal_pdf, wp_content_uploads_2022_01_prospecto_de_informacion_noviembre_2019_de_cas_pdf, wp_content_uploads_2021_11_prospectoinformacioncarvajal_pdf |
| q14 | ¿Cómo está conformado el gobierno corporativo de Carvajal? | 1.000 | 1.000 | gobierno_corporativo, wp_content_uploads_2021_11_informe_anual_carvajal_2018_pdf, wp_content_uploads_2021_11_informe_anual_carvajal_2018_pdf |
| q15 | ¿Qué compromisos éticos declara la empresa? | 0.500 | 0.613 | compromiso_etico, wp_content_uploads_2021_11_actuar_etico_2021_pdf, compromiso_etico |
| q16 | ¿Qué iniciativas de sostenibilidad adelanta Carvajal? | 0.000 | 0.000 | noticias_el_sector_privado_se_dio_cuenta_que_era_un_actor_relevante_en_el_cuidado_ambienta_f329d12058, wp_content_uploads_2025_04_informe_de_sostenibilidad_carvajal_pdf, noticias_ginebra_sostenible_transformando_entornos_se_transforman_personas |
| q17 | ¿Qué noticias recientes publica Carvajal en su sitio web? | 1.000 | 1.000 | noticias, noticias, wp_content_uploads_2022_01_terminosycondicionescarvajal_pdf |
| q18 | ¿Cuál es la historia de Carvajal desde su fundación? | 1.000 | 0.631 | noticias_la_huella_mas_importante_que_hemos_dejado_es_nuestro_trabajo_en_la_reduccion_de_l_61e398ba8c, historia, historia |
| q19 | ¿Qué información ofrece el portal de inversionistas? | 1.000 | 1.000 | inicio, inicio, inicio |
| q20 | ¿Qué fundaciones están vinculadas a Carvajal? | 0.500 | 0.264 | wp_content_uploads_2022_01_informe_social_y_ambiental_2020_carvajal_sa_pdf, noticias_buenaventura_territorio_focalizado_por_fundacion_carvajal, wp_content_uploads_2025_06_informe_carvajal_empaques_2024_pdf |
| q21 | ¿Cómo puedo contactar a Carvajal o solicitar información? | 0.000 | 0.000 | wp_content_uploads_2024_08_manual_de_procedimientos_carvajal_s_a_1_pdf, wp_content_uploads_2022_01_prospecto_de_informacion_noviembre_2019_de_cas_pdf, wp_content_uploads_2021_11_prospectoinformacioncarvajal_pdf |
| q22 | ¿Qué es la línea ética de Carvajal? | 1.000 | 1.000 | compromiso_etico, compromiso_etico, compromiso_etico |
| q23 | ¿Qué productos o servicios ofrece Carvajal en comunicación? | 1.000 | 1.000 | comunicacion, wp_content_uploads_2022_01_informe_social_y_ambiental_2020_carvajal_sa_pdf, wp_content_uploads_2022_01_terminosycondicionescarvajal_pdf |
| q24 | ¿Qué presencia tiene Carvajal en Colombia? | 0.000 | 0.000 | wp_content_uploads_2026_11_as_7448_26_notas_a_los_ef_consolidados_septiembre_2026_pdf, noticias_la_huella_mas_importante_que_hemos_dejado_es_nuestro_trabajo_en_la_reduccion_de_l_61e398ba8c, wp_content_uploads_2026_09_as_6214_26_e_f_carvajal_s_a_consolidados_al_30_de_junio_2026_pd_bd20be121c |
| q25 | ¿Cuáles son los valores o principios que guían a Carvajal? | 0.000 | 0.000 | wp_content_uploads_2025_06_informe_carvajal_empaques_2024_pdf, wp_content_uploads_2022_08_as_7061_22_carvajal_sa_eeff_completo_separado_carvajal_junio_20_e8e5de3344, wp_content_uploads_2022_11_as_8859_22_informe_separado_carvajal_s_a_sep_30_2022_pdf |
| q26 | ¿Cuáles fueron los ingresos consolidados de Carvajal en 2024? | 0.500 | 0.613 | carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_s_a_futuro_consolidado_ante_desafios_globales |
| q27 | ¿Cuál fue el EBITDA consolidado de Carvajal a septiembre de 2025 y qué margen reportó? | 0.000 | 0.000 | wp_content_uploads_2026_09_as_6214_26_e_f_carvajal_s_a_consolidados_al_30_de_junio_2026_pd_bd20be121c, wp_content_uploads_2021_11_consolidado_septiembre_2020_pdf, wp_content_uploads_2026_09_as_6214_26_e_f_carvajal_s_a_consolidados_al_30_de_junio_2026_pd_bd20be121c |
| q28 | ¿Qué utilidad o pérdida neta reportó Carvajal en 2024 y a qué filial se atribuye? | 1.000 | 0.832 | carvajal_y_sus_empresas_reportan_resultados_0239html, wp_content_uploads_2025_04_2024_informe_anual_carvajal_b16_c15_digital_pdf, wp_content_uploads_2025_04_20250331_reporte_financiero_info_relevante_aclaracion_pdf |
| q29 | ¿Cuáles son los activos, pasivos y patrimonio consolidados de Carvajal al cierre de 2024? | 1.000 | 1.000 | carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_s_a_futuro_consolidado_ante_desafios_globales, carvajal_s_a_futuro_consolidado_ante_desafios_globales |
| q30 | ¿Cuántos empleados tiene Carvajal y qué fuente lo reporta? | 0.000 | 0.000 | noticias_la_huella_mas_importante_que_hemos_dejado_es_nuestro_trabajo_en_la_reduccion_de_l_61e398ba8c, carvajal_y_sus_empresas_reportan_resultados_0239html, noticias_la_huella_mas_importante_que_hemos_dejado_es_nuestro_trabajo_en_la_reduccion_de_l_61e398ba8c |
| q31 | ¿Cuál es el número de teléfono de la sede de Carvajal en Tokio? | abstención: sí | — | wp_content_uploads_2025_05_as_4836_25_carvajal_sa_eeff_completo_separado_intermedio_marzo__ae2f746229, wp_content_uploads_2024_03_informe_anual_2023_carvajal_sa_pdf, wp_content_uploads_2024_05_estados_financieros_intermedios_separados_marzo_2024_pdf |

El detalle por consulta incluye el top-3 y su score; el JSON completo con los 10 resultados y extractos se regenera con el mismo comando y no se versiona para no inflar el PR.
