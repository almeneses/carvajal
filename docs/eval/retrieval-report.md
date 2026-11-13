# Evaluación de retrieval — Qdrant

> Generado: 2026-11-13T21:20:27.858902+00:00 · colección `carvajal_chunks` · k=10 · puntos=125

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

## Top-k por consulta

### q01 · ¿Qué es Carvajal y a qué sectores se dedica?

1. `quienes_somos` · score 0.7702 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Hemos estado presente en las generaciones de muchas familias Impulsado el crecimiento de la industria de alimentos a través de nuestros empaques Bioform, la educación de América Latina con la entrañable marca Norma, l
2. `home` · score 0.7369 · [fuente](https://www.carvajal.com/)
   > ## INMUEBLES ## a la medida para múltiples espacios productivos y de vivienda. Carvajal Propiedades e Inversiones ## NUESTRO ## CAMINO HACIA LA SOSTENIBILIDAD Todos somos parte de la solución, si te interesa el desarroll
3. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7248 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > El cierre de la planta de Yumbo Ha sido interpretado por algunos sectores como una señal de debilidad, pero en realidad responde a una estrategia de concentración y eficiencia, trasladando la producción a la planta de Gu
4. `historia` · score 0.7236 · [fuente](https://www.carvajal.com/historia/)
   > Venta de Ofixpress y Comolsa Lanzamiento de Earth Pact. Una nueva línea de papeles elaborados 100% a base de bagazo de caña y libre de químicos blanqueadores. El sector Empaques inaugura la primera etapa de la planta de 
5. `empaques` · score 0.7166 · [fuente](https://www.carvajal.com/empaques/)
   > Nuestros empaques están pensados para el cuidado de medio ambiente, novedoso en su usabilidad y diseño, Carvajal Empaques hace uso del residuo agroindustrial de la caña, el bagazo de caña y resinas posconsumo para el des
6. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7159 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > # Carvajal S.A.: Futuro consolidado ante desafíos globales Publicado por Redacción | 16 Abr 2025 | Portada 2 Las recientes asambleas de Accionistas de Carvajal S.A., celebradas el 31 de marzo y el 11 de abril de 2025, ma
7. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7137 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - Reducción de colaboradores, por cierre de campañas y proyectos en tecnología, call center, etc. Sectores y Subordinadas Carvajal S.A. es la matriz de la Organización Carvajal, un grupo empresarial diversificado con pre
8. `home` · score 0.7125 · [fuente](https://www.carvajal.com/)
   > ## Hacer las cosas bien es crear valor ## para cada generación ## Contribuimos a mejorar el mundo con nuestros ## EMPAQUES ## para alimentos.​ Carvajal Empaques ## Proponemos alternativas sostenibles para la producción y
9. `historia` · score 0.7121 · [fuente](https://www.carvajal.com/historia/)
   > Inicia operaciones la División Muebles, hoy Carvajal Espacios ## 1959 Antes de consultar por internet nuestra ubicación y el destino al cual deseamos llegar, el directorio telefónico era nuestra única oportunidad de busc
10. `empaques` · score 0.7114 · [fuente](https://www.carvajal.com/empaques/)
   > Carvajal Empaques ## Contribuimos a mejorar el mundo con nuestros ## EMPAQUES ## para alimentos.​ ## Sostenibilidad e innovación en cada empaque: Nuestro enfoque por más de 40 años ha sido escuchar a las personas y sus n

### q02 · ¿En qué año y ciudad fue fundada Carvajal?

1. `home` · score 0.7521 · [fuente](https://www.carvajal.com/)
   > ## INMUEBLES ## a la medida para múltiples espacios productivos y de vivienda. Carvajal Propiedades e Inversiones ## NUESTRO ## CAMINO HACIA LA SOSTENIBILIDAD Todos somos parte de la solución, si te interesa el desarroll
2. `home` · score 0.7221 · [fuente](https://www.carvajal.com/)
   > ### Carvajal Beca Jóvenes con buen empaque ## Mayra Cintura ### Carvajal Empaques Colaboradora de Carvajal Empaques – Tocancipá ## Ulder Fabio Valencia ### Fundación Propal Beneficiario de Fundacion propal – Campaña El B
3. `historia` · score 0.7135 · [fuente](https://www.carvajal.com/historia/)
   > ## 2014 Ampliación de la planta de Carvajal Empaques en Tocancipá para la manufactura de empaques plásticos. Su ubicación estratégica y capacidad de producción la consolida como una de las plantas más modernas en Latinoa
4. `historia` · score 0.7109 · [fuente](https://www.carvajal.com/historia/)
   > ## 2023 Carvajal Pulpa y Papel desarrolla una nueva cartulina bajo las marcas Earth pact natural Grease proof K12 y Propal Grease proof K12 con barrera a la humedad y la grasa sin laminación plástica enfocada en el segme
5. `historia` · score 0.7089 · [fuente](https://www.carvajal.com/historia/)
   > ## 2021 Declaramos nuestro propósito, fruto de una convicción colectiva y construido gracias a la participa de más de 1.500 personas entre colaboradores, clientes y proveedores. El propósito hace parte del legado que dej
6. `historia` · score 0.7087 · [fuente](https://www.carvajal.com/historia/)
   > ## 1974 Publicar inicia operaciones en Chile y Nicaragua ## 1975 Se crea la división de empaques flexibles, en su momento Flexa ## 1976 Carvajal se convierte en sociedad anónima. Se funda Musicar S. A. en asocio con Cara
7. `quienes_somos` · score 0.7054 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Hemos estado presente en las generaciones de muchas familias Impulsado el crecimiento de la industria de alimentos a través de nuestros empaques Bioform, la educación de América Latina con la entrañable marca Norma, l
8. `historia` · score 0.7009 · [fuente](https://www.carvajal.com/historia/)
   > ## 1994 Carvajal adquiere a Pamolsa en el Perú. Nace Carvajal Financiera, Compañía de Financiamiento Comercial. El Grupo Editorial Norma adquiere la Editorial Kapelusz de Argentina. Se inicia la distribución de muebles e
9. `historia` · score 0.6996 · [fuente](https://www.carvajal.com/historia/)
   > Inicia operaciones la División Muebles, hoy Carvajal Espacios ## 1959 Antes de consultar por internet nuestra ubicación y el destino al cual deseamos llegar, el directorio telefónico era nuestra única oportunidad de busc
10. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.6983 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - Muebles y Soluciones Empresariales: Mepal, enfocada en mobiliario y gestión de espacios empresariales. Organización Carvajal: Innovación, internacionalización y sostenibilidad. Su estrategia se centra en fortalecer la 

### q03 · ¿En cuántos países tiene presencia Carvajal?

1. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.7223 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Adicional, la compañía reportó que no habrá distribución de utilidades y eligió nuevos miembros en su junta directiva. El Grupo Carvajal celebra 120 años de operaciones. Foto: suministrado por la empresa. | Foto: El país
2. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7206 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - Reducción de colaboradores, por cierre de campañas y proyectos en tecnología, call center, etc. Sectores y Subordinadas Carvajal S.A. es la matriz de la Organización Carvajal, un grupo empresarial diversificado con pre
3. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.7201 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Economía # Carvajal y sus empresas reportan resultados Los ingresos alcanzaron $3,9 billones, una utilidad bruta de $1,07 billones Siga a EL PAÍS en Google Discover y no se pierda las últimas noticias El negocio de empaq
4. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7161 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > El cierre de la planta de Yumbo Ha sido interpretado por algunos sectores como una señal de debilidad, pero en realidad responde a una estrategia de concentración y eficiencia, trasladando la producción a la planta de Gu
5. `quienes_somos` · score 0.7157 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Hemos estado presente en las generaciones de muchas familias Impulsado el crecimiento de la industria de alimentos a través de nuestros empaques Bioform, la educación de América Latina con la entrañable marca Norma, l
6. `empaques` · score 0.7153 · [fuente](https://www.carvajal.com/empaques/)
   > Nuestros empaques están pensados para el cuidado de medio ambiente, novedoso en su usabilidad y diseño, Carvajal Empaques hace uso del residuo agroindustrial de la caña, el bagazo de caña y resinas posconsumo para el des
7. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7151 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > # Carvajal S.A.: Futuro consolidado ante desafíos globales Publicado por Redacción | 16 Abr 2025 | Portada 2 Las recientes asambleas de Accionistas de Carvajal S.A., celebradas el 31 de marzo y el 11 de abril de 2025, ma
8. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.7138 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Federación de Cafeteros y Consejo Gremial, preocupados por impacto de la Ley de Rescate Fiscal Jóvenes aprenden a ahorrar creando y usando videojuegos; esta es la experiencia surgida desde Cali Carvajal S.A. informó sobr
9. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7137 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - 11 de abril de 2025: La Asamblea Extraordinaria decidió el cierre indefinido de la planta de producción de papel y cartulinas en Yumbo, Valle del Cauca, tras más de 60 años de operación. Esta medida responde a la impos
10. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7123 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - marzo 2026 - febrero 2026 - enero 2026 - diciembre 2025 - noviembre 2025 - octubre 2025 - septiembre 2025 - agosto 2025 - julio 2025 - junio 2025 - mayo 2025 - abril 2025 - marzo 2025 - febrero 2025 - enero 2025 - dici

### q04 · ¿Cuáles son los cuatro sectores en los que opera Carvajal?

1. `quienes_somos` · score 0.7325 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Hemos estado presente en las generaciones de muchas familias Impulsado el crecimiento de la industria de alimentos a través de nuestros empaques Bioform, la educación de América Latina con la entrañable marca Norma, l
2. `espacios` · score 0.6895 · [fuente](https://www.carvajal.com/espacios/)
   > ## Creamos ## ESPACIOS ## de bienestar integral que impulsan la productividad y fomentan la colaboración al interior de las organizaciones. ## Creamos espacios pensando en ti Con más de 65 años de trayectoria atendemos l
3. `espacios` · score 0.686 · [fuente](https://www.carvajal.com/espacios/)
   > ## Sillas de Oficina Las sillas son el elemento esencial de un espacio porque más que ergonomía, las sillas son innovación,calidad y bienestar. Encuentra Sillas Ergonómicas, Sillas Gerenciales, Sillas Interlocutoras, Sil
4. `gobierno_corporativo` · score 0.6859 · [fuente](https://www.carvajal.com/gobierno-corporativo/)
   > ## Catalina Giraldo Valencia Vicepresidente de Asuntos Corporativos Es abogada de la Universidad EAFIT, con especialización en Derecho Comercial de la Pontificia Universidad Javeriana y especialización en Derecho Informá
5. `gobierno_corporativo` · score 0.684 · [fuente](https://www.carvajal.com/gobierno-corporativo/)
   > Ingeniero Civil de la Escuela de Ingeniería de Antioquia con formación de Alto Gobierno de la Universidad de los Andes y programas de especialización en Management y Supply Chain Strategies de las universidades de Kellog
6. `gobierno_corporativo` · score 0.6822 · [fuente](https://www.carvajal.com/gobierno-corporativo/)
   > ## German Castellanos Valero Vicepresidente de Experiencia de Personas. ## Camilo Neira Mendieta Vicepresidente Digital y Analítica Economista, MBA en Dirección de Empresas Tecnológicas y MS en Innovación. Con 15 años de
7. `gobierno_corporativo` · score 0.6817 · [fuente](https://www.carvajal.com/gobierno-corporativo/)
   > La composición actual de la junta directiva es la siguiente: *Miembros independientes ## Juan Guillermo Castañeda* Ingeniero Industrial de la Pontificia Universidad Javeriana, con un Máster en Ciencias en Administración 
8. `historia` · score 0.6807 · [fuente](https://www.carvajal.com/historia/)
   > Venta de Ofixpress y Comolsa Lanzamiento de Earth Pact. Una nueva línea de papeles elaborados 100% a base de bagazo de caña y libre de químicos blanqueadores. El sector Empaques inaugura la primera etapa de la planta de 
9. `gobierno_corporativo` · score 0.676 · [fuente](https://www.carvajal.com/gobierno-corporativo/)
   > La composición de los comités de la Junta Directiva de Carvajal S.A. es la siguiente Comité de Auditoría y Riesgos: 1. Juan Guillermo Castañeda (Presidente) 2. Santiago Delgado 3. Ramiro Guerrero Comité de Nombramientos,
10. `quienes_somos` · score 0.6731 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Juntos ## HACIENDO LAS ## COSAS BIEN ## por más de un siglo. Hemos aportado al desarrollo de múltiples mercados gracias al trabajo de equipos altamente capacitados y comprometidos. ¿Qué nos ha movilizado? El interés p

### q05 · ¿Cuál es el propósito superior de Carvajal?

1. `proposito_superior` · score 0.794 · [fuente](https://www.carvajal.com/proposito-superior/)
   > ## Nuestro ## PROPÓSITO SUPERIOR Nuestro propósito superior es fruto de una convicción colectiva que hoy, se transforma en nuestro propósito el cual fue construido con la participación de más de 1.500 personas entre cola
2. `home` · score 0.7448 · [fuente](https://www.carvajal.com/)
   > ### Carvajal Beca Jóvenes con buen empaque ## Mayra Cintura ### Carvajal Empaques Colaboradora de Carvajal Empaques – Tocancipá ## Ulder Fabio Valencia ### Fundación Propal Beneficiario de Fundacion propal – Campaña El B
3. `home` · score 0.7422 · [fuente](https://www.carvajal.com/)
   > ## INMUEBLES ## a la medida para múltiples espacios productivos y de vivienda. Carvajal Propiedades e Inversiones ## NUESTRO ## CAMINO HACIA LA SOSTENIBILIDAD Todos somos parte de la solución, si te interesa el desarroll
4. `home` · score 0.7373 · [fuente](https://www.carvajal.com/)
   > ## Hacer las cosas bien es crear valor ## para cada generación ## Contribuimos a mejorar el mundo con nuestros ## EMPAQUES ## para alimentos.​ Carvajal Empaques ## Proponemos alternativas sostenibles para la producción y
5. `historia` · score 0.722 · [fuente](https://www.carvajal.com/historia/)
   > ## 2023 Carvajal Pulpa y Papel desarrolla una nueva cartulina bajo las marcas Earth pact natural Grease proof K12 y Propal Grease proof K12 con barrera a la humedad y la grasa sin laminación plástica enfocada en el segme
6. `historia` · score 0.7178 · [fuente](https://www.carvajal.com/historia/)
   > ## 2021 Declaramos nuestro propósito, fruto de una convicción colectiva y construido gracias a la participa de más de 1.500 personas entre colaboradores, clientes y proveedores. El propósito hace parte del legado que dej
7. `historia` · score 0.7068 · [fuente](https://www.carvajal.com/historia/)
   > Inicia operaciones la División Muebles, hoy Carvajal Espacios ## 1959 Antes de consultar por internet nuestra ubicación y el destino al cual deseamos llegar, el directorio telefónico era nuestra única oportunidad de busc
8. `historia` · score 0.7044 · [fuente](https://www.carvajal.com/historia/)
   > Venta de Ofixpress y Comolsa Lanzamiento de Earth Pact. Una nueva línea de papeles elaborados 100% a base de bagazo de caña y libre de químicos blanqueadores. El sector Empaques inaugura la primera etapa de la planta de 
9. `historia` · score 0.7036 · [fuente](https://www.carvajal.com/historia/)
   > Unificación de nombres y marcas a. Carvajal Educación (Norma, Kiut, Jean Book, colores, marcadores, entre otros) b. Carvajal Tecnología y Servicios (assendared, Cen, Servinte) Crea sistemas de intercambio de información,
10. `historia` · score 0.7024 · [fuente](https://www.carvajal.com/historia/)
   > ## 2014 Ampliación de la planta de Carvajal Empaques en Tocancipá para la manufactura de empaques plásticos. Su ubicación estratégica y capacidad de producción la consolida como una de las plantas más modernas en Latinoa

### q06 · ¿Qué empresas conforman el sector de Papel y Empaques?

1. `quienes_somos` · score 0.7237 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Hemos estado presente en las generaciones de muchas familias Impulsado el crecimiento de la industria de alimentos a través de nuestros empaques Bioform, la educación de América Latina con la entrañable marca Norma, l
2. `empaques` · score 0.7125 · [fuente](https://www.carvajal.com/empaques/)
   > Nuestro portafolio ofrece soluciones de empaques para consumir en sitio, exhibir o para llevar, así como la oportunidad de adaptar el diseño de los productos acorde a las necesidades del cliente y la personalización del 
3. `empaques` · score 0.6904 · [fuente](https://www.carvajal.com/empaques/)
   > ## HOGAR Extendemos nuestras capacidades para poder atender la necesidad de los consumidores por empaques atractivos listos para los eventos más importantes con la calidad y garantía que las familias requieren. ## Agroin
4. `pulpa_y_papel` · score 0.6895 · [fuente](https://www.carvajal.com/pulpa-y-papel/)
   > Después del segundo párrafo poner: Descubre nuestro compromiso el desarrollo sostenible, en nuestro informe de sostenibilidad. ## Nuestra ## PRESENCIA: ## NUESTROS MERCADOS: - Consumo - Artes gráficas - Otros convertidor
5. `empaques` · score 0.6848 · [fuente](https://www.carvajal.com/empaques/)
   > Nuestros empaques están pensados para el cuidado de medio ambiente, novedoso en su usabilidad y diseño, Carvajal Empaques hace uso del residuo agroindustrial de la caña, el bagazo de caña y resinas posconsumo para el des
6. `quienes_somos` · score 0.6813 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Juntos ## HACIENDO LAS ## COSAS BIEN ## por más de un siglo. Hemos aportado al desarrollo de múltiples mercados gracias al trabajo de equipos altamente capacitados y comprometidos. ¿Qué nos ha movilizado? El interés p
7. `empaques` · score 0.6797 · [fuente](https://www.carvajal.com/empaques/)
   > Carvajal Empaques ## Contribuimos a mejorar el mundo con nuestros ## EMPAQUES ## para alimentos.​ ## Sostenibilidad e innovación en cada empaque: Nuestro enfoque por más de 40 años ha sido escuchar a las personas y sus n
8. `espacios` · score 0.6771 · [fuente](https://www.carvajal.com/espacios/)
   > ## Sillas de Oficina Las sillas son el elemento esencial de un espacio porque más que ergonomía, las sillas son innovación,calidad y bienestar. Encuentra Sillas Ergonómicas, Sillas Gerenciales, Sillas Interlocutoras, Sil
9. `espacios` · score 0.6762 · [fuente](https://www.carvajal.com/espacios/)
   > Al ser una empresa BIC mantenemos un compromiso constante con el desarrollo sostenible. ## Nuestra ## PRESENCIA: ## NUESTROS MERCADOS: - Puestos de trabajo - Zona individual - Zona social - Zona de colaboración - Almacen
10. `pulpa_y_papel` · score 0.6686 · [fuente](https://www.carvajal.com/pulpa-y-papel/)
   > Carvajal Pulpa y Papel ## Proponemos alternativas sostenibles para la producción y consumo de ## Papel ## Utilizando el bagazo de la caña como materia prima desarrollamos papeles y cartulinas para impresión, escritura y 

### q07 · ¿Qué es Bioform y a qué industria está dirigido?

1. `empaques` · score 0.6555 · [fuente](https://www.carvajal.com/empaques/)
   > Nuestro portafolio ofrece soluciones de empaques para consumir en sitio, exhibir o para llevar, así como la oportunidad de adaptar el diseño de los productos acorde a las necesidades del cliente y la personalización del 
2. `quienes_somos` · score 0.6539 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Hemos estado presente en las generaciones de muchas familias Impulsado el crecimiento de la industria de alimentos a través de nuestros empaques Bioform, la educación de América Latina con la entrañable marca Norma, l
3. `empaques` · score 0.6449 · [fuente](https://www.carvajal.com/empaques/)
   > Carvajal Empaques ## Contribuimos a mejorar el mundo con nuestros ## EMPAQUES ## para alimentos.​ ## Sostenibilidad e innovación en cada empaque: Nuestro enfoque por más de 40 años ha sido escuchar a las personas y sus n
4. `quienes_somos` · score 0.6434 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Juntos ## HACIENDO LAS ## COSAS BIEN ## por más de un siglo. Hemos aportado al desarrollo de múltiples mercados gracias al trabajo de equipos altamente capacitados y comprometidos. ¿Qué nos ha movilizado? El interés p
5. `compromiso_etico` · score 0.6387 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > ## Medio ambiente La Organización propiciará, a través de su gestión y de sus procesos industriales, la protección del medio ambiente, procurando implementar mejores prácticas ambientales, alineadas con los estándares in
6. `gobierno_corporativo` · score 0.6282 · [fuente](https://www.carvajal.com/gobierno-corporativo/)
   > ## German Castellanos Valero Vicepresidente de Experiencia de Personas. ## Camilo Neira Mendieta Vicepresidente Digital y Analítica Economista, MBA en Dirección de Empresas Tecnológicas y MS en Innovación. Con 15 años de
7. `pulpa_y_papel` · score 0.6216 · [fuente](https://www.carvajal.com/pulpa-y-papel/)
   > Después del segundo párrafo poner: Descubre nuestro compromiso el desarrollo sostenible, en nuestro informe de sostenibilidad. ## Nuestra ## PRESENCIA: ## NUESTROS MERCADOS: - Consumo - Artes gráficas - Otros convertidor
8. `gobierno_corporativo` · score 0.6174 · [fuente](https://www.carvajal.com/gobierno-corporativo/)
   > ## Gobierno corporativo ## jUNTA ## directiva De conformidad con el Artículo 29 de los estatutos de la sociedad la Junta Directiva está compuesta por 9 miembros principales en los siguientes términos: Nueve (9) miembros 
9. `empaques` · score 0.615 · [fuente](https://www.carvajal.com/empaques/)
   > ## HOGAR Extendemos nuestras capacidades para poder atender la necesidad de los consumidores por empaques atractivos listos para los eventos más importantes con la calidad y garantía que las familias requieren. ## Agroin
10. `espacios` · score 0.6149 · [fuente](https://www.carvajal.com/espacios/)
   > Al ser una empresa BIC mantenemos un compromiso constante con el desarrollo sostenible. ## Nuestra ## PRESENCIA: ## NUESTROS MERCADOS: - Puestos de trabajo - Zona individual - Zona social - Zona de colaboración - Almacen

### q08 · ¿Qué marca de educación tiene Carvajal y a quién se dirige?

1. `educacion` · score 0.7959 · [fuente](https://www.carvajal.com/educacion/)
   > ## Maletería Nos inspiramos en el diseño y la funcionalidad, creando productos con compartimientos pensados para cada usuario. Ofrecemos una amplia gama de cartucheras, loncheras y morrales que permiten distribuir y orga
2. `educacion` · score 0.7878 · [fuente](https://www.carvajal.com/educacion/)
   > Carvajal Educación. ## Facilitamos el aprendizaje y la ## educación ## en todas las etapas de la vida. ## Con marcas emblemáticas ## Nuestra ## Presencia: ## Panamá ## Puerto Rico ## NUESTROS MERCADOS: - Cuadernos - Arte
3. `home` · score 0.6963 · [fuente](https://www.carvajal.com/)
   > ## Hacer las cosas bien es crear valor ## para cada generación ## Contribuimos a mejorar el mundo con nuestros ## EMPAQUES ## para alimentos.​ Carvajal Empaques ## Proponemos alternativas sostenibles para la producción y
4. `historia` · score 0.6946 · [fuente](https://www.carvajal.com/historia/)
   > ## 1994 Carvajal adquiere a Pamolsa en el Perú. Nace Carvajal Financiera, Compañía de Financiamiento Comercial. El Grupo Editorial Norma adquiere la Editorial Kapelusz de Argentina. Se inicia la distribución de muebles e
5. `historia` · score 0.686 · [fuente](https://www.carvajal.com/historia/)
   > Unificación de nombres y marcas a. Carvajal Educación (Norma, Kiut, Jean Book, colores, marcadores, entre otros) b. Carvajal Tecnología y Servicios (assendared, Cen, Servinte) Crea sistemas de intercambio de información,
6. `quienes_somos` · score 0.6829 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Hemos estado presente en las generaciones de muchas familias Impulsado el crecimiento de la industria de alimentos a través de nuestros empaques Bioform, la educación de América Latina con la entrañable marca Norma, l
7. `historia` · score 0.6829 · [fuente](https://www.carvajal.com/historia/)
   > ## 1990 El Grupo Editorial Norma inicia la distribución de sus productos en Chile y adquiere la editorial Parramón Ediciones S. A., de España. Publicar S. A. inicia operaciones en Panamá. ## 1991 El Grupo Editorial Norma
8. `historia` · score 0.6794 · [fuente](https://www.carvajal.com/historia/)
   > ## 2014 Ampliación de la planta de Carvajal Empaques en Tocancipá para la manufactura de empaques plásticos. Su ubicación estratégica y capacidad de producción la consolida como una de las plantas más modernas en Latinoa
9. `home` · score 0.6784 · [fuente](https://www.carvajal.com/)
   > ### Carvajal Beca Jóvenes con buen empaque ## Mayra Cintura ### Carvajal Empaques Colaboradora de Carvajal Empaques – Tocancipá ## Ulder Fabio Valencia ### Fundación Propal Beneficiario de Fundacion propal – Campaña El B
10. `home` · score 0.6748 · [fuente](https://www.carvajal.com/)
   > ## INMUEBLES ## a la medida para múltiples espacios productivos y de vivienda. Carvajal Propiedades e Inversiones ## NUESTRO ## CAMINO HACIA LA SOSTENIBILIDAD Todos somos parte de la solución, si te interesa el desarroll

### q09 · ¿Qué soluciones ofrece Carvajal Tecnología y Servicios?

1. `tecnologia` · score 0.8314 · [fuente](https://www.carvajal.com/tecnologia/)
   > Carvajal Tecnología y Servicios ## Entendemos la ## TECNOLOGÍA ## y la transformamos en soluciones digitales para ti. ## Somos una compañía que crea sistemas de intercambio de información para lograr conectar a los ciuda
2. `tecnologia` · score 0.7626 · [fuente](https://www.carvajal.com/tecnologia/)
   > ## Nuestra ## Presencia: ## NUESTROS MERCADOS: - E-Business - PliP ## E-Business Las plataformas digitales CEN Transaccional, CEN Analíticos y CEN Financiero son líderes en los procesos EDI (Electronic Document Interchan
3. `comunicacion` · score 0.7587 · [fuente](https://www.carvajal.com/comunicacion/)
   > Carvajal Soluciones de Comunicación ## Impulsamos tecnologías renovables para la solución de servicios de ## Comunicación ## impresa y digital. ## Somos una empresa ecológica Carvajal Soluciones de Comunicación es una em
4. `home` · score 0.7413 · [fuente](https://www.carvajal.com/)
   > ## Hacer las cosas bien es crear valor ## para cada generación ## Contribuimos a mejorar el mundo con nuestros ## EMPAQUES ## para alimentos.​ Carvajal Empaques ## Proponemos alternativas sostenibles para la producción y
5. `quienes_somos` · score 0.7309 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Hemos estado presente en las generaciones de muchas familias Impulsado el crecimiento de la industria de alimentos a través de nuestros empaques Bioform, la educación de América Latina con la entrañable marca Norma, l
6. `comunicacion` · score 0.7299 · [fuente](https://www.carvajal.com/comunicacion/)
   > ## Servicio de impresión administrada (MPS) Ayudamos a nuestros clientes a optimizar sus procesos de impresión a través de equipos con tecnología de última generación, soluciones de seguridad y herramientas de monitoreo 
7. `comunicacion` · score 0.7261 · [fuente](https://www.carvajal.com/comunicacion/)
   > ## Nuestra ## PRESENCIA: ## NUESTROS MERCADOS: - Impresión digital transaccional - Impresión digital editorial - Servicio de impresión administrada (MPS) ## Impresión digital transaccional Prestamos servicios para compañ
8. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7224 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - Reducción de colaboradores, por cierre de campañas y proyectos en tecnología, call center, etc. Sectores y Subordinadas Carvajal S.A. es la matriz de la Organización Carvajal, un grupo empresarial diversificado con pre
9. `espacios` · score 0.7039 · [fuente](https://www.carvajal.com/espacios/)
   > ## Sillas de Oficina Las sillas son el elemento esencial de un espacio porque más que ergonomía, las sillas son innovación,calidad y bienestar. Encuentra Sillas Ergonómicas, Sillas Gerenciales, Sillas Interlocutoras, Sil
10. `empaques` · score 0.6994 · [fuente](https://www.carvajal.com/empaques/)
   > Nuestros empaques están pensados para el cuidado de medio ambiente, novedoso en su usabilidad y diseño, Carvajal Empaques hace uso del residuo agroindustrial de la caña, el bagazo de caña y resinas posconsumo para el des

### q10 · ¿Qué es Mepal y a qué sector pertenece?

1. `quienes_somos` · score 0.708 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Hemos estado presente en las generaciones de muchas familias Impulsado el crecimiento de la industria de alimentos a través de nuestros empaques Bioform, la educación de América Latina con la entrañable marca Norma, l
2. `espacios` · score 0.693 · [fuente](https://www.carvajal.com/espacios/)
   > ## Sillas de Oficina Las sillas son el elemento esencial de un espacio porque más que ergonomía, las sillas son innovación,calidad y bienestar. Encuentra Sillas Ergonómicas, Sillas Gerenciales, Sillas Interlocutoras, Sil
3. `espacios` · score 0.6866 · [fuente](https://www.carvajal.com/espacios/)
   > Al ser una empresa BIC mantenemos un compromiso constante con el desarrollo sostenible. ## Nuestra ## PRESENCIA: ## NUESTROS MERCADOS: - Puestos de trabajo - Zona individual - Zona social - Zona de colaboración - Almacen
4. `espacios` · score 0.6827 · [fuente](https://www.carvajal.com/espacios/)
   > ## Creamos ## ESPACIOS ## de bienestar integral que impulsan la productividad y fomentan la colaboración al interior de las organizaciones. ## Creamos espacios pensando en ti Con más de 65 años de trayectoria atendemos l
5. `comunicacion` · score 0.6675 · [fuente](https://www.carvajal.com/comunicacion/)
   > ## Nuestra ## PRESENCIA: ## NUESTROS MERCADOS: - Impresión digital transaccional - Impresión digital editorial - Servicio de impresión administrada (MPS) ## Impresión digital transaccional Prestamos servicios para compañ
6. `quienes_somos` · score 0.666 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Juntos ## HACIENDO LAS ## COSAS BIEN ## por más de un siglo. Hemos aportado al desarrollo de múltiples mercados gracias al trabajo de equipos altamente capacitados y comprometidos. ¿Qué nos ha movilizado? El interés p
7. `pulpa_y_papel` · score 0.6639 · [fuente](https://www.carvajal.com/pulpa-y-papel/)
   > Después del segundo párrafo poner: Descubre nuestro compromiso el desarrollo sostenible, en nuestro informe de sostenibilidad. ## Nuestra ## PRESENCIA: ## NUESTROS MERCADOS: - Consumo - Artes gráficas - Otros convertidor
8. `inicio` · score 0.6619 · [fuente](https://www.carvajal.com/inversionistas/inicio/)
   > ## Tipos de ## acciones y derechos ## Capital y ## tipos de acciones ## Cifras ## Relevantes Somos aliados de más de empresas de diversos sectores en América Latina. En los últimos 10 años nuestro monto de inversión soci
9. `empaques` · score 0.6543 · [fuente](https://www.carvajal.com/empaques/)
   > ## HOGAR Extendemos nuestras capacidades para poder atender la necesidad de los consumidores por empaques atractivos listos para los eventos más importantes con la calidad y garantía que las familias requieren. ## Agroin
10. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.6539 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Economía # Carvajal y sus empresas reportan resultados Los ingresos alcanzaron $3,9 billones, una utilidad bruta de $1,07 billones Siga a EL PAÍS en Google Discover y no se pierda las últimas noticias El negocio de empaq

### q11 · ¿Qué hace Carvajal Espacios?

1. `espacios` · score 0.7958 · [fuente](https://www.carvajal.com/espacios/)
   > ## Creamos ## ESPACIOS ## de bienestar integral que impulsan la productividad y fomentan la colaboración al interior de las organizaciones. ## Creamos espacios pensando en ti Con más de 65 años de trayectoria atendemos l
2. `home` · score 0.7654 · [fuente](https://www.carvajal.com/)
   > ## Hacer las cosas bien es crear valor ## para cada generación ## Contribuimos a mejorar el mundo con nuestros ## EMPAQUES ## para alimentos.​ Carvajal Empaques ## Proponemos alternativas sostenibles para la producción y
3. `home` · score 0.7631 · [fuente](https://www.carvajal.com/)
   > ## INMUEBLES ## a la medida para múltiples espacios productivos y de vivienda. Carvajal Propiedades e Inversiones ## NUESTRO ## CAMINO HACIA LA SOSTENIBILIDAD Todos somos parte de la solución, si te interesa el desarroll
4. `home` · score 0.7503 · [fuente](https://www.carvajal.com/)
   > ### Carvajal Beca Jóvenes con buen empaque ## Mayra Cintura ### Carvajal Empaques Colaboradora de Carvajal Empaques – Tocancipá ## Ulder Fabio Valencia ### Fundación Propal Beneficiario de Fundacion propal – Campaña El B
5. `espacios` · score 0.7483 · [fuente](https://www.carvajal.com/espacios/)
   > La socialización hace parte de la naturaleza humana, por eso la facilitación del intercambio casual de ideas e información en los espacios de trabajo es fundamental. ## Zona de colaboración Extracto:Las zonas de colabora
6. `espacios` · score 0.7469 · [fuente](https://www.carvajal.com/espacios/)
   > ## Sillas de Oficina Las sillas son el elemento esencial de un espacio porque más que ergonomía, las sillas son innovación,calidad y bienestar. Encuentra Sillas Ergonómicas, Sillas Gerenciales, Sillas Interlocutoras, Sil
7. `espacios` · score 0.7375 · [fuente](https://www.carvajal.com/espacios/)
   > Al ser una empresa BIC mantenemos un compromiso constante con el desarrollo sostenible. ## Nuestra ## PRESENCIA: ## NUESTROS MERCADOS: - Puestos de trabajo - Zona individual - Zona social - Zona de colaboración - Almacen
8. `quienes_somos` · score 0.7202 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Hemos estado presente en las generaciones de muchas familias Impulsado el crecimiento de la industria de alimentos a través de nuestros empaques Bioform, la educación de América Latina con la entrañable marca Norma, l
9. `historia` · score 0.7197 · [fuente](https://www.carvajal.com/historia/)
   > ## 2014 Ampliación de la planta de Carvajal Empaques en Tocancipá para la manufactura de empaques plásticos. Su ubicación estratégica y capacidad de producción la consolida como una de las plantas más modernas en Latinoa
10. `empaques` · score 0.7088 · [fuente](https://www.carvajal.com/empaques/)
   > Nuestros empaques están pensados para el cuidado de medio ambiente, novedoso en su usabilidad y diseño, Carvajal Empaques hace uso del residuo agroindustrial de la caña, el bagazo de caña y resinas posconsumo para el des

### q12 · ¿Qué es LiquidezYa?

1. `quienes_somos` · score 0.6037 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Hemos estado presente en las generaciones de muchas familias Impulsado el crecimiento de la industria de alimentos a través de nuestros empaques Bioform, la educación de América Latina con la entrañable marca Norma, l
2. `tecnologia` · score 0.5906 · [fuente](https://www.carvajal.com/tecnologia/)
   > ## Nuestra ## Presencia: ## NUESTROS MERCADOS: - E-Business - PliP ## E-Business Las plataformas digitales CEN Transaccional, CEN Analíticos y CEN Financiero son líderes en los procesos EDI (Electronic Document Interchan
3. `empaques` · score 0.5769 · [fuente](https://www.carvajal.com/empaques/)
   > ## HOGAR Extendemos nuestras capacidades para poder atender la necesidad de los consumidores por empaques atractivos listos para los eventos más importantes con la calidad y garantía que las familias requieren. ## Agroin
4. `empaques` · score 0.5731 · [fuente](https://www.carvajal.com/empaques/)
   > Nuestro portafolio ofrece soluciones de empaques para consumir en sitio, exhibir o para llevar, así como la oportunidad de adaptar el diseño de los productos acorde a las necesidades del cliente y la personalización del 
5. `empaques` · score 0.5728 · [fuente](https://www.carvajal.com/empaques/)
   > Nuestros empaques están pensados para el cuidado de medio ambiente, novedoso en su usabilidad y diseño, Carvajal Empaques hace uso del residuo agroindustrial de la caña, el bagazo de caña y resinas posconsumo para el des
6. `quienes_somos` · score 0.572 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Juntos ## HACIENDO LAS ## COSAS BIEN ## por más de un siglo. Hemos aportado al desarrollo de múltiples mercados gracias al trabajo de equipos altamente capacitados y comprometidos. ¿Qué nos ha movilizado? El interés p
7. `comunicacion` · score 0.5704 · [fuente](https://www.carvajal.com/comunicacion/)
   > ## Servicio de impresión administrada (MPS) Ayudamos a nuestros clientes a optimizar sus procesos de impresión a través de equipos con tecnología de última generación, soluciones de seguridad y herramientas de monitoreo 
8. `comunicacion` · score 0.5692 · [fuente](https://www.carvajal.com/comunicacion/)
   > ## Nuestra ## PRESENCIA: ## NUESTROS MERCADOS: - Impresión digital transaccional - Impresión digital editorial - Servicio de impresión administrada (MPS) ## Impresión digital transaccional Prestamos servicios para compañ
9. `tecnologia` · score 0.5627 · [fuente](https://www.carvajal.com/tecnologia/)
   > Carvajal Tecnología y Servicios ## Entendemos la ## TECNOLOGÍA ## y la transformamos en soluciones digitales para ti. ## Somos una compañía que crea sistemas de intercambio de información para lograr conectar a los ciuda
10. `empaques` · score 0.5615 · [fuente](https://www.carvajal.com/empaques/)
   > Carvajal Empaques ## Contribuimos a mejorar el mundo con nuestros ## EMPAQUES ## para alimentos.​ ## Sostenibilidad e innovación en cada empaque: Nuestro enfoque por más de 40 años ha sido escuchar a las personas y sus n

### q13 · ¿En qué consiste el negocio de Propiedades e Inversiones?

1. `propiedades_e_inversiones` · score 0.7336 · [fuente](https://www.carvajal.com/propiedades-e-inversiones/)
   > ## Generamos experiencias confortables, con la administración de ## inmuebles ## a la medida para múltiples espacios productivos y de vivienda. ## Transformamos algunos de nuestros inmuebles que prestan servicios inmobil
2. `propiedades_e_inversiones` · score 0.7207 · [fuente](https://www.carvajal.com/propiedades-e-inversiones/)
   > ## Nuestra ## PRESENCIA: ## Activos en renta: - Santa Mónica - Candelaria - Yumbo - Ciudad Guabinas - Zonamerica ## Santa Mónica Proyecto: Oficinas Santa Mónica Ubicación: Calle 29 norte #6a – 40 en Cali, Colombia Renta 
3. `inicio` · score 0.7036 · [fuente](https://www.carvajal.com/inversionistas/inicio/)
   > ## Tipos de ## acciones y derechos ## Capital y ## tipos de acciones ## Cifras ## Relevantes Somos aliados de más de empresas de diversos sectores en América Latina. En los últimos 10 años nuestro monto de inversión soci
4. `inicio` · score 0.7028 · [fuente](https://www.carvajal.com/inversionistas/inicio/)
   > Únete a Hacer las cosas bien ## Información ## relevante ## Otros eventos. Carvajal S.A. informa sobre modificación al prospecto de información de la emisión de bonos ordinarios en el Segundo Mercado No. 202312 de diciem
5. `inicio` · score 0.7001 · [fuente](https://www.carvajal.com/inversionistas/inicio/)
   > Carvajal S.A. informa sobre convocatoria a la primera Asamblea General de Tenedores de Bonos en el segundo mercado (ver documento adjunto). ## Calificaciones de riesgo. S&P National Ratings S. A. SCV divulga el documento
6. `compromiso_etico` · score 0.6737 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > y se da cierre formal al reporte, del cual recibirás respuesta con el número de caso. ## descripciones de las relaciones con grupos de interés externos ## Clientes y Usuarios La satisfacción de las necesidades de nuestro
7. `relacionamiento_con_grupos_de_interes` · score 0.6647 · [fuente](https://www.carvajal.com/compromiso-etico/relacionamiento-con-grupos-de-interes/)
   > ## Compromiso ## ÉTICO ## Descripciones de las relaciones con grupos de interés externos Clientes y usuarios Accionistas e inversionistas Proveedores y acreedores Los proveedores y acreedores son nuestros socios en la sa
8. `comunicacion` · score 0.6558 · [fuente](https://www.carvajal.com/comunicacion/)
   > ## Servicio de impresión administrada (MPS) Ayudamos a nuestros clientes a optimizar sus procesos de impresión a través de equipos con tecnología de última generación, soluciones de seguridad y herramientas de monitoreo 
9. `comunicacion` · score 0.648 · [fuente](https://www.carvajal.com/comunicacion/)
   > ## Nuestra ## PRESENCIA: ## NUESTROS MERCADOS: - Impresión digital transaccional - Impresión digital editorial - Servicio de impresión administrada (MPS) ## Impresión digital transaccional Prestamos servicios para compañ
10. `resultados_empresariales_2024_de_carvajal_4099828` · score 0.6421 · [fuente](https://www.larepublica.co/empresas/resultados-empresariales-2024-de-carvajal-4099828)
   > De otro lado, se destacó la inversión en el proceso de transformación tecnológica, los planes de mejora de la competitividad y la implementación de eficiencias en costos y gastos , que si bien requirieron de la ejecución

### q14 · ¿Cómo está conformado el gobierno corporativo de Carvajal?

1. `gobierno_corporativo` · score 0.7911 · [fuente](https://www.carvajal.com/gobierno-corporativo/)
   > ## Gobierno corporativo ## jUNTA ## directiva De conformidad con el Artículo 29 de los estatutos de la sociedad la Junta Directiva está compuesta por 9 miembros principales en los siguientes términos: Nueve (9) miembros 
2. `gobierno_corporativo` · score 0.7896 · [fuente](https://www.carvajal.com/gobierno-corporativo/)
   > Ingeniero Civil de la Escuela de Ingeniería de Antioquia con formación de Alto Gobierno de la Universidad de los Andes y programas de especialización en Management y Supply Chain Strategies de las universidades de Kellog
3. `gobierno_corporativo` · score 0.7798 · [fuente](https://www.carvajal.com/gobierno-corporativo/)
   > La composición de los comités de la Junta Directiva de Carvajal S.A. es la siguiente Comité de Auditoría y Riesgos: 1. Juan Guillermo Castañeda (Presidente) 2. Santiago Delgado 3. Ramiro Guerrero Comité de Nombramientos,
4. `gobierno_corporativo` · score 0.7776 · [fuente](https://www.carvajal.com/gobierno-corporativo/)
   > ## German Castellanos Valero Vicepresidente de Experiencia de Personas. ## Camilo Neira Mendieta Vicepresidente Digital y Analítica Economista, MBA en Dirección de Empresas Tecnológicas y MS en Innovación. Con 15 años de
5. `gobierno_corporativo` · score 0.7745 · [fuente](https://www.carvajal.com/gobierno-corporativo/)
   > ## Catalina Giraldo Valencia Vicepresidente de Asuntos Corporativos Es abogada de la Universidad EAFIT, con especialización en Derecho Comercial de la Pontificia Universidad Javeriana y especialización en Derecho Informá
6. `gobierno_corporativo` · score 0.7722 · [fuente](https://www.carvajal.com/gobierno-corporativo/)
   > La composición actual de la junta directiva es la siguiente: *Miembros independientes ## Juan Guillermo Castañeda* Ingeniero Industrial de la Pontificia Universidad Javeriana, con un Máster en Ciencias en Administración 
7. `gobierno_corporativo` · score 0.7627 · [fuente](https://www.carvajal.com/gobierno-corporativo/)
   > ## Santiago Delgado Castro* Licenciado en Administración de Empresas y Sistemas de Información Gerencial de la Universidad de Nebraska. Cuenta con un master de The University of Chicago Booth School of Business. Se desem
8. `gobierno_corporativo` · score 0.7577 · [fuente](https://www.carvajal.com/gobierno-corporativo/)
   > ## Juan Pablo Molina Vicepresidente Financiero y de Estrategia Ingeniero Industrial con MBA de la Universidad Icesi y Maestría en Economía de la Universidad de Girona con más de 25 años en posiciones de alta dirección en
9. `gobierno_corporativo` · score 0.7566 · [fuente](https://www.carvajal.com/gobierno-corporativo/)
   > ## Ramiro Guerrero Carvajal* Economista y PhD en economía del King’s College London, con experiencia en investigación, consultoría, formulación de políticas, salud, educación y periodismo. Ha sido viceministro de Protecc
10. `gobierno_corporativo` · score 0.7564 · [fuente](https://www.carvajal.com/gobierno-corporativo/)
   > ## Andrés Crump Gómez* Abogado y Máster en Finanzas de la Universidad de los Andes y en Derecho (LLM) de la Universidad de Columbia. Ha sido vicepresidente senior de Brookfield Asset Management, socio de Baker Mackenzie 

### q15 · ¿Qué compromisos éticos declara la empresa?

1. `compromiso_etico` · score 0.7884 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > Para reportar un caso en la Línea Ética puede hacerlo aqui ## ¿Cómo opera la Línea ética Carvajal? ## Asi opera nuestra Línea ética ## Contácte con la línea Ética Via web o telefónica ## Tu reporte será recibido por la e
2. `compromiso_etico` · score 0.7864 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > y se da cierre formal al reporte, del cual recibirás respuesta con el número de caso. ## descripciones de las relaciones con grupos de interés externos ## Clientes y Usuarios La satisfacción de las necesidades de nuestro
3. `compromiso_etico` · score 0.7815 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > Para reportar un caso en la Línea Ética puede hacerlo aquí . Clientes y usuarios Con el medio ambiente Con los accionistas e inversionistas Con proveedores y acreedores con el estado con las autoridades Con la competenci
4. `compromiso_etico` · score 0.7811 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > ## Compromiso Ético La Organización Carvajal asume la responsabilidad de ser parte integrante de la sociedad en cualquier país donde se encuentre, por lo cual sin excepción tiene como premisa que su actuar estará regido 
5. `compromiso_etico` · score 0.779 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > Conoce cómo esta fundamentando Nuestro Actuar Ético aquí. ## pautas de nuestro actuar ético ## 1 Cumplimiento de la ley ## 2 Conflicto de intereses ## 3 Relaciones con grupos de interés ## 4 Manejo de la información ## 5
6. `compromiso_etico` · score 0.7778 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > ## Respeto Reconocer, aceptar y comprender a todas las personas que interactúan con la Organización. ## orientación al cliente Conocer las necesidades de los consumidores, usuarios y clientes para satisfacerlas adecuadam
7. `compromiso_etico` · score 0.7752 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > ## 08. Garantizar la privacidad de los datos personales de todos los grupos de interes con quienes nos relacionamos. ## 09. Rechazar y combatir cualquier comportamiento antietico o corrupto. ## 10. Generar y mantener can
8. `compromiso_etico` · score 0.7683 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > ## Medio ambiente La Organización propiciará, a través de su gestión y de sus procesos industriales, la protección del medio ambiente, procurando implementar mejores prácticas ambientales, alineadas con los estándares in
9. `compromiso_etico` · score 0.7676 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > ## PROTECCIÓN Y CUIDADO DE LA VIDA Velar por el cuidado integral de la propia vida y la de quienes están alrededor en cualquier momento y lugar. ## Principios y Políticas Uno de los aspectos en los cuales se fundamenta l
10. `compromiso_etico` · score 0.7629 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > ## Proveedores y Acreedores Los proveedores y acreedores son nuestros socios en la satisfacción de las necesidades de los clientes. Nuestras decisiones de compra han de basarse en criterios de confiabilidad, eficiencia, 

### q16 · ¿Qué iniciativas de sostenibilidad adelanta Carvajal?

1. `home` · score 0.7821 · [fuente](https://www.carvajal.com/)
   > ## INMUEBLES ## a la medida para múltiples espacios productivos y de vivienda. Carvajal Propiedades e Inversiones ## NUESTRO ## CAMINO HACIA LA SOSTENIBILIDAD Todos somos parte de la solución, si te interesa el desarroll
2. `home` · score 0.7775 · [fuente](https://www.carvajal.com/)
   > ## Hacer las cosas bien es crear valor ## para cada generación ## Contribuimos a mejorar el mundo con nuestros ## EMPAQUES ## para alimentos.​ Carvajal Empaques ## Proponemos alternativas sostenibles para la producción y
3. `empaques` · score 0.7721 · [fuente](https://www.carvajal.com/empaques/)
   > Carvajal Empaques ## Contribuimos a mejorar el mundo con nuestros ## EMPAQUES ## para alimentos.​ ## Sostenibilidad e innovación en cada empaque: Nuestro enfoque por más de 40 años ha sido escuchar a las personas y sus n
4. `home` · score 0.7375 · [fuente](https://www.carvajal.com/)
   > ### Carvajal Beca Jóvenes con buen empaque ## Mayra Cintura ### Carvajal Empaques Colaboradora de Carvajal Empaques – Tocancipá ## Ulder Fabio Valencia ### Fundación Propal Beneficiario de Fundacion propal – Campaña El B
5. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7361 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - Muebles y Soluciones Empresariales: Mepal, enfocada en mobiliario y gestión de espacios empresariales. Organización Carvajal: Innovación, internacionalización y sostenibilidad. Su estrategia se centra en fortalecer la 
6. `comunicacion` · score 0.7318 · [fuente](https://www.carvajal.com/comunicacion/)
   > Carvajal Soluciones de Comunicación ## Impulsamos tecnologías renovables para la solución de servicios de ## Comunicación ## impresa y digital. ## Somos una empresa ecológica Carvajal Soluciones de Comunicación es una em
7. `historia` · score 0.731 · [fuente](https://www.carvajal.com/historia/)
   > ## 2021 Declaramos nuestro propósito, fruto de una convicción colectiva y construido gracias a la participa de más de 1.500 personas entre colaboradores, clientes y proveedores. El propósito hace parte del legado que dej
8. `historia` · score 0.7248 · [fuente](https://www.carvajal.com/historia/)
   > Se retoma el eslogan “Carvajal hace las cosas bien”, haciendo énfasis en los valores que han caracterizado la Organización y se define los elementos integrados de la cultura Carvajal. ## 2018 Carvajal Empaques inauguro G
9. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7246 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > El cierre de la planta de Yumbo Ha sido interpretado por algunos sectores como una señal de debilidad, pero en realidad responde a una estrategia de concentración y eficiencia, trasladando la producción a la planta de Gu
10. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7245 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - Reducción de colaboradores, por cierre de campañas y proyectos en tecnología, call center, etc. Sectores y Subordinadas Carvajal S.A. es la matriz de la Organización Carvajal, un grupo empresarial diversificado con pre

### q17 · ¿Qué noticias recientes publica Carvajal en su sitio web?

1. `noticias` · score 0.7483 · [fuente](https://www.carvajal.com/noticias/)
   > ## UNIDOS PARA RECUPERAR Y REVITALIZAR A FICO ## julio, 2 jueves, 2026 ## Pedro Felipe Carvajal: “Continuaremos con nuestra apuesta decidida por Colombia” ## mayo, 28 miércoles, 2025 ## Carvajal Empaques, reconocida como
2. `noticias` · score 0.746 · [fuente](https://www.carvajal.com/noticias/)
   > ## Cali Recicla con tu Reciclador: fortaleciendo la economía circular y la inclusión social ## diciembre, 2 lunes, 2024 ## Fundación Propal en su Programa de Primera Infancia con énfasis en Nutrición clausura con éxito s
3. `inicio` · score 0.7106 · [fuente](https://www.carvajal.com/inversionistas/inicio/)
   > Únete a Hacer las cosas bien ## Información ## relevante ## Otros eventos. Carvajal S.A. informa sobre modificación al prospecto de información de la emisión de bonos ordinarios en el Segundo Mercado No. 202312 de diciem
4. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.7098 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Adicional, la compañía reportó que no habrá distribución de utilidades y eligió nuevos miembros en su junta directiva. El Grupo Carvajal celebra 120 años de operaciones. Foto: suministrado por la empresa. | Foto: El país
5. `comunicacion` · score 0.7002 · [fuente](https://www.carvajal.com/comunicacion/)
   > ## Nuestra ## PRESENCIA: ## NUESTROS MERCADOS: - Impresión digital transaccional - Impresión digital editorial - Servicio de impresión administrada (MPS) ## Impresión digital transaccional Prestamos servicios para compañ
6. `tecnologia` · score 0.6947 · [fuente](https://www.carvajal.com/tecnologia/)
   > Carvajal Tecnología y Servicios ## Entendemos la ## TECNOLOGÍA ## y la transformamos en soluciones digitales para ti. ## Somos una compañía que crea sistemas de intercambio de información para lograr conectar a los ciuda
7. `home` · score 0.6927 · [fuente](https://www.carvajal.com/)
   > ## Hacer las cosas bien es crear valor ## para cada generación ## Contribuimos a mejorar el mundo con nuestros ## EMPAQUES ## para alimentos.​ Carvajal Empaques ## Proponemos alternativas sostenibles para la producción y
8. `comunicacion` · score 0.6913 · [fuente](https://www.carvajal.com/comunicacion/)
   > ## Servicio de impresión administrada (MPS) Ayudamos a nuestros clientes a optimizar sus procesos de impresión a través de equipos con tecnología de última generación, soluciones de seguridad y herramientas de monitoreo 
9. `comunicacion` · score 0.6909 · [fuente](https://www.carvajal.com/comunicacion/)
   > Carvajal Soluciones de Comunicación ## Impulsamos tecnologías renovables para la solución de servicios de ## Comunicación ## impresa y digital. ## Somos una empresa ecológica Carvajal Soluciones de Comunicación es una em
10. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.6904 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Economía # Carvajal y sus empresas reportan resultados Los ingresos alcanzaron $3,9 billones, una utilidad bruta de $1,07 billones Siga a EL PAÍS en Google Discover y no se pierda las últimas noticias El negocio de empaq

### q18 · ¿Cuál es la historia de Carvajal desde su fundación?

1. `historia` · score 0.8324 · [fuente](https://www.carvajal.com/historia/)
   > ## 1974 Publicar inicia operaciones en Chile y Nicaragua ## 1975 Se crea la división de empaques flexibles, en su momento Flexa ## 1976 Carvajal se convierte en sociedad anónima. Se funda Musicar S. A. en asocio con Cara
2. `historia` · score 0.8306 · [fuente](https://www.carvajal.com/historia/)
   > ## 2021 Declaramos nuestro propósito, fruto de una convicción colectiva y construido gracias a la participa de más de 1.500 personas entre colaboradores, clientes y proveedores. El propósito hace parte del legado que dej
3. `historia` · score 0.8188 · [fuente](https://www.carvajal.com/historia/)
   > ## 2023 Carvajal Pulpa y Papel desarrolla una nueva cartulina bajo las marcas Earth pact natural Grease proof K12 y Propal Grease proof K12 con barrera a la humedad y la grasa sin laminación plástica enfocada en el segme
4. `historia` · score 0.8165 · [fuente](https://www.carvajal.com/historia/)
   > ## 1904 ## Nuestra ## Historia El sábado 29 de octubre de 1904, nace el sueño de la Familia Carvajal con la impresión del semanario El Día. Todos participaban: Manuel Carvajal Valencia, su esposa Micaela Borrero, los hij
5. `historia` · score 0.8141 · [fuente](https://www.carvajal.com/historia/)
   > Inicia operaciones la División Muebles, hoy Carvajal Espacios ## 1959 Antes de consultar por internet nuestra ubicación y el destino al cual deseamos llegar, el directorio telefónico era nuestra única oportunidad de busc
6. `historia` · score 0.8085 · [fuente](https://www.carvajal.com/historia/)
   > Unificación de nombres y marcas a. Carvajal Educación (Norma, Kiut, Jean Book, colores, marcadores, entre otros) b. Carvajal Tecnología y Servicios (assendared, Cen, Servinte) Crea sistemas de intercambio de información,
7. `historia` · score 0.8052 · [fuente](https://www.carvajal.com/historia/)
   > ## 2004 Carvajal adquiere una proporción mayoritaria de acciones de Productora de Papeles S. A. (Propal S. A) y primer centenario de Carvajal. ## 2007 Adquisición Termoformados Modernos en El Salvador ## 2008 Ricardo Obr
8. `historia` · score 0.805 · [fuente](https://www.carvajal.com/historia/)
   > ## 2022 a. Como compañía familiar, Carvajal recibe el primer reconocimiento empresarial en Colombia y a nivel latinoamericano de IMD Global Family Business Award. El premio IMD ha sido el reconocimiento para vivir nuestr
9. `historia` · score 0.8041 · [fuente](https://www.carvajal.com/historia/)
   > ## 1994 Carvajal adquiere a Pamolsa en el Perú. Nace Carvajal Financiera, Compañía de Financiamiento Comercial. El Grupo Editorial Norma adquiere la Editorial Kapelusz de Argentina. Se inicia la distribución de muebles e
10. `historia` · score 0.8029 · [fuente](https://www.carvajal.com/historia/)
   > ## 1912 La llegada de una maquina rayadora de papel desde Alemania hizo posible que los cuadernos tuvieran línea corriente, algo provechoso para las primeras letras en escuelas y colegios. De igual manera para los libros

### q19 · ¿Qué información ofrece el portal de inversionistas?

1. `inicio` · score 0.75 · [fuente](https://www.carvajal.com/inversionistas/inicio/)
   > Únete a Hacer las cosas bien ## Información ## relevante ## Otros eventos. Carvajal S.A. informa sobre modificación al prospecto de información de la emisión de bonos ordinarios en el Segundo Mercado No. 202312 de diciem
2. `inicio` · score 0.7382 · [fuente](https://www.carvajal.com/inversionistas/inicio/)
   > ## Tipos de ## acciones y derechos ## Capital y ## tipos de acciones ## Cifras ## Relevantes Somos aliados de más de empresas de diversos sectores en América Latina. En los últimos 10 años nuestro monto de inversión soci
3. `inicio` · score 0.704 · [fuente](https://www.carvajal.com/inversionistas/inicio/)
   > Carvajal S.A. informa sobre convocatoria a la primera Asamblea General de Tenedores de Bonos en el segundo mercado (ver documento adjunto). ## Calificaciones de riesgo. S&P National Ratings S. A. SCV divulga el documento
4. `comunicacion` · score 0.6446 · [fuente](https://www.carvajal.com/comunicacion/)
   > ## Servicio de impresión administrada (MPS) Ayudamos a nuestros clientes a optimizar sus procesos de impresión a través de equipos con tecnología de última generación, soluciones de seguridad y herramientas de monitoreo 
5. `comunicacion` · score 0.6181 · [fuente](https://www.carvajal.com/comunicacion/)
   > ## Nuestra ## PRESENCIA: ## NUESTROS MERCADOS: - Impresión digital transaccional - Impresión digital editorial - Servicio de impresión administrada (MPS) ## Impresión digital transaccional Prestamos servicios para compañ
6. `propiedades_e_inversiones` · score 0.615 · [fuente](https://www.carvajal.com/propiedades-e-inversiones/)
   > ## Generamos experiencias confortables, con la administración de ## inmuebles ## a la medida para múltiples espacios productivos y de vivienda. ## Transformamos algunos de nuestros inmuebles que prestan servicios inmobil
7. `propiedades_e_inversiones` · score 0.6149 · [fuente](https://www.carvajal.com/propiedades-e-inversiones/)
   > ## Nuestra ## PRESENCIA: ## Activos en renta: - Santa Mónica - Candelaria - Yumbo - Ciudad Guabinas - Zonamerica ## Santa Mónica Proyecto: Oficinas Santa Mónica Ubicación: Calle 29 norte #6a – 40 en Cali, Colombia Renta 
8. `espacios` · score 0.6015 · [fuente](https://www.carvajal.com/espacios/)
   > ## Sillas de Oficina Las sillas son el elemento esencial de un espacio porque más que ergonomía, las sillas son innovación,calidad y bienestar. Encuentra Sillas Ergonómicas, Sillas Gerenciales, Sillas Interlocutoras, Sil
9. `compromiso_etico` · score 0.5969 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > y se da cierre formal al reporte, del cual recibirás respuesta con el número de caso. ## descripciones de las relaciones con grupos de interés externos ## Clientes y Usuarios La satisfacción de las necesidades de nuestro
10. `tecnologia` · score 0.5924 · [fuente](https://www.carvajal.com/tecnologia/)
   > Carvajal Tecnología y Servicios ## Entendemos la ## TECNOLOGÍA ## y la transformamos en soluciones digitales para ti. ## Somos una compañía que crea sistemas de intercambio de información para lograr conectar a los ciuda

### q20 · ¿Qué fundaciones están vinculadas a Carvajal?

1. `home` · score 0.7945 · [fuente](https://www.carvajal.com/)
   > ## INMUEBLES ## a la medida para múltiples espacios productivos y de vivienda. Carvajal Propiedades e Inversiones ## NUESTRO ## CAMINO HACIA LA SOSTENIBILIDAD Todos somos parte de la solución, si te interesa el desarroll
2. `home` · score 0.7722 · [fuente](https://www.carvajal.com/)
   > ### Carvajal Beca Jóvenes con buen empaque ## Mayra Cintura ### Carvajal Empaques Colaboradora de Carvajal Empaques – Tocancipá ## Ulder Fabio Valencia ### Fundación Propal Beneficiario de Fundacion propal – Campaña El B
3. `historia` · score 0.7364 · [fuente](https://www.carvajal.com/historia/)
   > ## 1974 Publicar inicia operaciones en Chile y Nicaragua ## 1975 Se crea la división de empaques flexibles, en su momento Flexa ## 1976 Carvajal se convierte en sociedad anónima. Se funda Musicar S. A. en asocio con Cara
4. `home` · score 0.7351 · [fuente](https://www.carvajal.com/)
   > ## Hacer las cosas bien es crear valor ## para cada generación ## Contribuimos a mejorar el mundo con nuestros ## EMPAQUES ## para alimentos.​ Carvajal Empaques ## Proponemos alternativas sostenibles para la producción y
5. `historia` · score 0.7307 · [fuente](https://www.carvajal.com/historia/)
   > ## 2014 Ampliación de la planta de Carvajal Empaques en Tocancipá para la manufactura de empaques plásticos. Su ubicación estratégica y capacidad de producción la consolida como una de las plantas más modernas en Latinoa
6. `historia` · score 0.7295 · [fuente](https://www.carvajal.com/historia/)
   > ## 2023 Carvajal Pulpa y Papel desarrolla una nueva cartulina bajo las marcas Earth pact natural Grease proof K12 y Propal Grease proof K12 con barrera a la humedad y la grasa sin laminación plástica enfocada en el segme
7. `historia` · score 0.7261 · [fuente](https://www.carvajal.com/historia/)
   > ## 2021 Declaramos nuestro propósito, fruto de una convicción colectiva y construido gracias a la participa de más de 1.500 personas entre colaboradores, clientes y proveedores. El propósito hace parte del legado que dej
8. `historia` · score 0.7202 · [fuente](https://www.carvajal.com/historia/)
   > a nivel global, fundamentada en una renovada arquitectura de marca que agrupa las empresas de la Organización. h. Se establece la promesa de valor y tres atributos de marca: Compromiso con los resultados, cercanía a los 
9. `quienes_somos` · score 0.7196 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Hemos estado presente en las generaciones de muchas familias Impulsado el crecimiento de la industria de alimentos a través de nuestros empaques Bioform, la educación de América Latina con la entrañable marca Norma, l
10. `historia` · score 0.719 · [fuente](https://www.carvajal.com/historia/)
   > ## 1994 Carvajal adquiere a Pamolsa en el Perú. Nace Carvajal Financiera, Compañía de Financiamiento Comercial. El Grupo Editorial Norma adquiere la Editorial Kapelusz de Argentina. Se inicia la distribución de muebles e

### q21 · ¿Cómo puedo contactar a Carvajal o solicitar información?

1. `comunicacion` · score 0.7655 · [fuente](https://www.carvajal.com/comunicacion/)
   > Carvajal Soluciones de Comunicación ## Impulsamos tecnologías renovables para la solución de servicios de ## Comunicación ## impresa y digital. ## Somos una empresa ecológica Carvajal Soluciones de Comunicación es una em
2. `comunicacion` · score 0.7365 · [fuente](https://www.carvajal.com/comunicacion/)
   > ## Servicio de impresión administrada (MPS) Ayudamos a nuestros clientes a optimizar sus procesos de impresión a través de equipos con tecnología de última generación, soluciones de seguridad y herramientas de monitoreo 
3. `comunicacion` · score 0.7359 · [fuente](https://www.carvajal.com/comunicacion/)
   > ## Nuestra ## PRESENCIA: ## NUESTROS MERCADOS: - Impresión digital transaccional - Impresión digital editorial - Servicio de impresión administrada (MPS) ## Impresión digital transaccional Prestamos servicios para compañ
4. `home` · score 0.7111 · [fuente](https://www.carvajal.com/)
   > ## Hacer las cosas bien es crear valor ## para cada generación ## Contribuimos a mejorar el mundo con nuestros ## EMPAQUES ## para alimentos.​ Carvajal Empaques ## Proponemos alternativas sostenibles para la producción y
5. `noticias` · score 0.7007 · [fuente](https://www.carvajal.com/noticias/)
   > ## UNIDOS PARA RECUPERAR Y REVITALIZAR A FICO ## julio, 2 jueves, 2026 ## Pedro Felipe Carvajal: “Continuaremos con nuestra apuesta decidida por Colombia” ## mayo, 28 miércoles, 2025 ## Carvajal Empaques, reconocida como
6. `noticias` · score 0.7 · [fuente](https://www.carvajal.com/noticias/)
   > ## Cali Recicla con tu Reciclador: fortaleciendo la economía circular y la inclusión social ## diciembre, 2 lunes, 2024 ## Fundación Propal en su Programa de Primera Infancia con énfasis en Nutrición clausura con éxito s
7. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.6944 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - Reducción de colaboradores, por cierre de campañas y proyectos en tecnología, call center, etc. Sectores y Subordinadas Carvajal S.A. es la matriz de la Organización Carvajal, un grupo empresarial diversificado con pre
8. `inicio` · score 0.6923 · [fuente](https://www.carvajal.com/inversionistas/inicio/)
   > Únete a Hacer las cosas bien ## Información ## relevante ## Otros eventos. Carvajal S.A. informa sobre modificación al prospecto de información de la emisión de bonos ordinarios en el Segundo Mercado No. 202312 de diciem
9. `home` · score 0.6921 · [fuente](https://www.carvajal.com/)
   > ## INMUEBLES ## a la medida para múltiples espacios productivos y de vivienda. Carvajal Propiedades e Inversiones ## NUESTRO ## CAMINO HACIA LA SOSTENIBILIDAD Todos somos parte de la solución, si te interesa el desarroll
10. `historia` · score 0.691 · [fuente](https://www.carvajal.com/historia/)
   > ## 1999 Alberto José Carvajal Lourido es nombrado presidente de Organización Carvajal. Venta de Carvajal Financiera al Banco Intercontinental. Venta de Chimolsa. ## 2000 Venta de DIRECTV a Hughes Electronics Corporation.

### q22 · ¿Qué es la línea ética de Carvajal?

1. `compromiso_etico` · score 0.8171 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > Conoce cómo esta fundamentando Nuestro Actuar Ético aquí. ## pautas de nuestro actuar ético ## 1 Cumplimiento de la ley ## 2 Conflicto de intereses ## 3 Relaciones con grupos de interés ## 4 Manejo de la información ## 5
2. `compromiso_etico` · score 0.8031 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > ## Compromiso Ético La Organización Carvajal asume la responsabilidad de ser parte integrante de la sociedad en cualquier país donde se encuentre, por lo cual sin excepción tiene como premisa que su actuar estará regido 
3. `compromiso_etico` · score 0.7954 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > ## PROTECCIÓN Y CUIDADO DE LA VIDA Velar por el cuidado integral de la propia vida y la de quienes están alrededor en cualquier momento y lugar. ## Principios y Políticas Uno de los aspectos en los cuales se fundamenta l
4. `compromiso_etico` · score 0.7948 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > Para reportar un caso en la Línea Ética puede hacerlo aquí . Clientes y usuarios Con el medio ambiente Con los accionistas e inversionistas Con proveedores y acreedores con el estado con las autoridades Con la competenci
5. `compromiso_etico` · score 0.7832 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > Para reportar un caso en la Línea Ética puede hacerlo aqui ## ¿Cómo opera la Línea ética Carvajal? ## Asi opera nuestra Línea ética ## Contácte con la línea Ética Via web o telefónica ## Tu reporte será recibido por la e
6. `compromiso_etico` · score 0.7824 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > ## Compromiso ## ÉTICO ## Política de Derechos Humanos La Organización Carvajal comprometida en la construcción de relaciones sostenibles con los diferentes grupos de interés y basado en su adhesión y cumplimiento al Pac
7. `compromiso_etico` · score 0.7703 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > ## 03. Rechazar el trabajo infantil y el trabajo forzoso u obligatorio. ## 04. Respetar la libertad de asociación y negociación colectiva. ## 05. Garantizar que todos los colaboradores cuentan con un regimen laboral que 
8. `compromiso_etico` · score 0.7692 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > ## 06. Ofrecer y garantizar un empleo digno y justo basado en instalaciones confortables y seguras, propiciar espacios de bienestar y recreación para el colaborador y su familia, estableciendo un modelo de desarrollo que
9. `compromiso_etico` · score 0.7585 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > ## Proveedores y Acreedores Los proveedores y acreedores son nuestros socios en la satisfacción de las necesidades de los clientes. Nuestras decisiones de compra han de basarse en criterios de confiabilidad, eficiencia, 
10. `compromiso_etico` · score 0.7552 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > ## Estado La Organización respeta las instituciones y autoridades reconocidas en los países donde tiene actividades y aplicará correctamente sus leyes y normas. ## Autoridades Es deber de la Organización prestar oportuna

### q23 · ¿Qué productos o servicios ofrece Carvajal en comunicación?

1. `comunicacion` · score 0.8134 · [fuente](https://www.carvajal.com/comunicacion/)
   > Carvajal Soluciones de Comunicación ## Impulsamos tecnologías renovables para la solución de servicios de ## Comunicación ## impresa y digital. ## Somos una empresa ecológica Carvajal Soluciones de Comunicación es una em
2. `comunicacion` · score 0.7952 · [fuente](https://www.carvajal.com/comunicacion/)
   > ## Servicio de impresión administrada (MPS) Ayudamos a nuestros clientes a optimizar sus procesos de impresión a través de equipos con tecnología de última generación, soluciones de seguridad y herramientas de monitoreo 
3. `comunicacion` · score 0.7902 · [fuente](https://www.carvajal.com/comunicacion/)
   > ## Nuestra ## PRESENCIA: ## NUESTROS MERCADOS: - Impresión digital transaccional - Impresión digital editorial - Servicio de impresión administrada (MPS) ## Impresión digital transaccional Prestamos servicios para compañ
4. `tecnologia` · score 0.7682 · [fuente](https://www.carvajal.com/tecnologia/)
   > Carvajal Tecnología y Servicios ## Entendemos la ## TECNOLOGÍA ## y la transformamos en soluciones digitales para ti. ## Somos una compañía que crea sistemas de intercambio de información para lograr conectar a los ciuda
5. `home` · score 0.7524 · [fuente](https://www.carvajal.com/)
   > ## Hacer las cosas bien es crear valor ## para cada generación ## Contribuimos a mejorar el mundo con nuestros ## EMPAQUES ## para alimentos.​ Carvajal Empaques ## Proponemos alternativas sostenibles para la producción y
6. `compromiso_etico` · score 0.729 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > ## Respeto Reconocer, aceptar y comprender a todas las personas que interactúan con la Organización. ## orientación al cliente Conocer las necesidades de los consumidores, usuarios y clientes para satisfacerlas adecuadam
7. `quienes_somos` · score 0.7274 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Hemos estado presente en las generaciones de muchas familias Impulsado el crecimiento de la industria de alimentos a través de nuestros empaques Bioform, la educación de América Latina con la entrañable marca Norma, l
8. `empaques` · score 0.726 · [fuente](https://www.carvajal.com/empaques/)
   > Nuestro portafolio ofrece soluciones de empaques para consumir en sitio, exhibir o para llevar, así como la oportunidad de adaptar el diseño de los productos acorde a las necesidades del cliente y la personalización del 
9. `relacionamiento_con_grupos_de_interes` · score 0.7198 · [fuente](https://www.carvajal.com/compromiso-etico/relacionamiento-con-grupos-de-interes/)
   > ## Compromiso ## ÉTICO ## Descripciones de las relaciones con grupos de interés externos Clientes y usuarios Accionistas e inversionistas Proveedores y acreedores Los proveedores y acreedores son nuestros socios en la sa
10. `compromiso_etico` · score 0.7195 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > Para reportar un caso en la Línea Ética puede hacerlo aquí . Clientes y usuarios Con el medio ambiente Con los accionistas e inversionistas Con proveedores y acreedores con el estado con las autoridades Con la competenci

### q24 · ¿Qué presencia tiene Carvajal en Colombia?

1. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7443 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - 11 de abril de 2025: La Asamblea Extraordinaria decidió el cierre indefinido de la planta de producción de papel y cartulinas en Yumbo, Valle del Cauca, tras más de 60 años de operación. Esta medida responde a la impos
2. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7426 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - Reducción de colaboradores, por cierre de campañas y proyectos en tecnología, call center, etc. Sectores y Subordinadas Carvajal S.A. es la matriz de la Organización Carvajal, un grupo empresarial diversificado con pre
3. `quienes_somos` · score 0.7312 · [fuente](https://www.carvajal.com/quienes-somos/)
   > ## Hemos estado presente en las generaciones de muchas familias Impulsado el crecimiento de la industria de alimentos a través de nuestros empaques Bioform, la educación de América Latina con la entrañable marca Norma, l
4. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7308 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > El cierre de la planta de Yumbo Ha sido interpretado por algunos sectores como una señal de debilidad, pero en realidad responde a una estrategia de concentración y eficiencia, trasladando la producción a la planta de Gu
5. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7289 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - Muebles y Soluciones Empresariales: Mepal, enfocada en mobiliario y gestión de espacios empresariales. Organización Carvajal: Innovación, internacionalización y sostenibilidad. Su estrategia se centra en fortalecer la 
6. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7285 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > # Carvajal S.A.: Futuro consolidado ante desafíos globales Publicado por Redacción | 16 Abr 2025 | Portada 2 Las recientes asambleas de Accionistas de Carvajal S.A., celebradas el 31 de marzo y el 11 de abril de 2025, ma
7. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.7275 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Economía # Carvajal y sus empresas reportan resultados Los ingresos alcanzaron $3,9 billones, una utilidad bruta de $1,07 billones Siga a EL PAÍS en Google Discover y no se pierda las últimas noticias El negocio de empaq
8. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7229 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - marzo 2026 - febrero 2026 - enero 2026 - diciembre 2025 - noviembre 2025 - octubre 2025 - septiembre 2025 - agosto 2025 - julio 2025 - junio 2025 - mayo 2025 - abril 2025 - marzo 2025 - febrero 2025 - enero 2025 - dici
9. `historia` · score 0.7193 · [fuente](https://www.carvajal.com/historia/)
   > ## 1997 Mepal inaugura plantas de muebles en Venezuela y Argentina. Visipak instala su segunda planta de producción de láminas plásticas y termoformados Carvajal, en asocio con el grupo empresarial Bavaria, la Organizaci
10. `historia` · score 0.7179 · [fuente](https://www.carvajal.com/historia/)
   > Inicia operaciones la División Muebles, hoy Carvajal Espacios ## 1959 Antes de consultar por internet nuestra ubicación y el destino al cual deseamos llegar, el directorio telefónico era nuestra única oportunidad de busc

### q25 · ¿Cuáles son los valores o principios que guían a Carvajal?

1. `home` · score 0.7631 · [fuente](https://www.carvajal.com/)
   > ## Hacer las cosas bien es crear valor ## para cada generación ## Contribuimos a mejorar el mundo con nuestros ## EMPAQUES ## para alimentos.​ Carvajal Empaques ## Proponemos alternativas sostenibles para la producción y
2. `home` · score 0.7357 · [fuente](https://www.carvajal.com/)
   > ## INMUEBLES ## a la medida para múltiples espacios productivos y de vivienda. Carvajal Propiedades e Inversiones ## NUESTRO ## CAMINO HACIA LA SOSTENIBILIDAD Todos somos parte de la solución, si te interesa el desarroll
3. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7279 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - Reducción de colaboradores, por cierre de campañas y proyectos en tecnología, call center, etc. Sectores y Subordinadas Carvajal S.A. es la matriz de la Organización Carvajal, un grupo empresarial diversificado con pre
4. `home` · score 0.7212 · [fuente](https://www.carvajal.com/)
   > ### Carvajal Beca Jóvenes con buen empaque ## Mayra Cintura ### Carvajal Empaques Colaboradora de Carvajal Empaques – Tocancipá ## Ulder Fabio Valencia ### Fundación Propal Beneficiario de Fundacion propal – Campaña El B
5. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7186 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - Muebles y Soluciones Empresariales: Mepal, enfocada en mobiliario y gestión de espacios empresariales. Organización Carvajal: Innovación, internacionalización y sostenibilidad. Su estrategia se centra en fortalecer la 
6. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.718 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > El cierre de la planta de Yumbo Ha sido interpretado por algunos sectores como una señal de debilidad, pero en realidad responde a una estrategia de concentración y eficiencia, trasladando la producción a la planta de Gu
7. `compromiso_etico` · score 0.718 · [fuente](https://www.carvajal.com/compromiso-etico/)
   > Para reportar un caso en la Línea Ética puede hacerlo aquí . Clientes y usuarios Con el medio ambiente Con los accionistas e inversionistas Con proveedores y acreedores con el estado con las autoridades Con la competenci
8. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7178 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - marzo 2026 - febrero 2026 - enero 2026 - diciembre 2025 - noviembre 2025 - octubre 2025 - septiembre 2025 - agosto 2025 - julio 2025 - junio 2025 - mayo 2025 - abril 2025 - marzo 2025 - febrero 2025 - enero 2025 - dici
9. `historia` · score 0.7162 · [fuente](https://www.carvajal.com/historia/)
   > Unificación de nombres y marcas a. Carvajal Educación (Norma, Kiut, Jean Book, colores, marcadores, entre otros) b. Carvajal Tecnología y Servicios (assendared, Cen, Servinte) Crea sistemas de intercambio de información,
10. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7162 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > # Carvajal S.A.: Futuro consolidado ante desafíos globales Publicado por Redacción | 16 Abr 2025 | Portada 2 Las recientes asambleas de Accionistas de Carvajal S.A., celebradas el 31 de marzo y el 11 de abril de 2025, ma

### q26 · ¿Cuáles fueron los ingresos consolidados de Carvajal en 2024?

1. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.8226 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - marzo 2026 - febrero 2026 - enero 2026 - diciembre 2025 - noviembre 2025 - octubre 2025 - septiembre 2025 - agosto 2025 - julio 2025 - junio 2025 - mayo 2025 - abril 2025 - marzo 2025 - febrero 2025 - enero 2025 - dici
2. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.8186 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > # Carvajal S.A.: Futuro consolidado ante desafíos globales Publicado por Redacción | 16 Abr 2025 | Portada 2 Las recientes asambleas de Accionistas de Carvajal S.A., celebradas el 31 de marzo y el 11 de abril de 2025, ma
3. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.8157 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > El cierre de la planta de Yumbo Ha sido interpretado por algunos sectores como una señal de debilidad, pero en realidad responde a una estrategia de concentración y eficiencia, trasladando la producción a la planta de Gu
4. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.8102 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - Muebles y Soluciones Empresariales: Mepal, enfocada en mobiliario y gestión de espacios empresariales. Organización Carvajal: Innovación, internacionalización y sostenibilidad. Su estrategia se centra en fortalecer la 
5. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.8045 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - Reducción de colaboradores, por cierre de campañas y proyectos en tecnología, call center, etc. Sectores y Subordinadas Carvajal S.A. es la matriz de la Organización Carvajal, un grupo empresarial diversificado con pre
6. `resultados_empresariales_2024_de_carvajal_4099828` · score 0.7914 · [fuente](https://www.larepublica.co/empresas/resultados-empresariales-2024-de-carvajal-4099828)
   > De otro lado, se destacó la inversión en el proceso de transformación tecnológica, los planes de mejora de la competitividad y la implementación de eficiencias en costos y gastos , que si bien requirieron de la ejecución
7. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.7904 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Pedro Carvajal es presidente de la Organización Carvajal desde el año 2020. Foto suministrada por la empresa. | Foto: El país De otro lado, destacaron la inversión en el proceso de transformación tecnológica, los planes 
8. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7881 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - 11 de abril de 2025: La Asamblea Extraordinaria decidió el cierre indefinido de la planta de producción de papel y cartulinas en Yumbo, Valle del Cauca, tras más de 60 años de operación. Esta medida responde a la impos
9. `resultados_empresariales_2024_de_carvajal_4099828` · score 0.78 · [fuente](https://www.larepublica.co/empresas/resultados-empresariales-2024-de-carvajal-4099828)
   > ### Conozca los beneficios exclusivos para nuestros suscriptores ## MÁS DE EMPRESAS ## Mercado Libre registró más de 3,3 millones de búsquedas asociadas con Halloween Al desagregar el flujo de búsquedas por categoría, Me
10. `resultados_empresariales_2024_de_carvajal_4099828` · score 0.779 · [fuente](https://www.larepublica.co/empresas/resultados-empresariales-2024-de-carvajal-4099828)
   > ## Carvajal presentó resultados financieros y reportó ingresos por $3,9 billones en 2024 Carvajal presentó resultados financieros y reportó ingresos por $3,9 billones en 2024 - Agregue a sus temas de interés - Carvajal -

### q27 · ¿Cuál fue el EBITDA consolidado de Carvajal a septiembre de 2025 y qué margen reportó?

1. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7956 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > # Carvajal S.A.: Futuro consolidado ante desafíos globales Publicado por Redacción | 16 Abr 2025 | Portada 2 Las recientes asambleas de Accionistas de Carvajal S.A., celebradas el 31 de marzo y el 11 de abril de 2025, ma
2. `carvajal_reporto_ingresos_por_2_58_billones_a_septiembre_de_2025_4269762` · score 0.7939 · [fuente](https://www.larepublica.co/empresas/carvajal-reporto-ingresos-por-2-58-billones-a-septiembre-de-2025-4269762)
   > ## Carvajal cerró planta de Propal en Yumbo por aumento de la competencia con China El Ebitda consolidado ascendió a $351.631 millones, mejorando el margen Ebitda de 12% a 14% en el último año. ## Carvajal presentó resul
3. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7901 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - marzo 2026 - febrero 2026 - enero 2026 - diciembre 2025 - noviembre 2025 - octubre 2025 - septiembre 2025 - agosto 2025 - julio 2025 - junio 2025 - mayo 2025 - abril 2025 - marzo 2025 - febrero 2025 - enero 2025 - dici
4. `carvajal_reporto_ingresos_por_2_58_billones_a_septiembre_de_2025_4269762` · score 0.7888 · [fuente](https://www.larepublica.co/empresas/carvajal-reporto-ingresos-por-2-58-billones-a-septiembre-de-2025-4269762)
   > ## La Organización Carvajal reportó ingresos por $2,58 billones a septiembre de 2025 Pedro Felipe Carvajal, presidente de Carvajal. - Agregue a sus temas de interés - Carvajal - Propal - Resultados financieros El sector 
5. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.7819 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Federación de Cafeteros y Consejo Gremial, preocupados por impacto de la Ley de Rescate Fiscal Jóvenes aprenden a ahorrar creando y usando videojuegos; esta es la experiencia surgida desde Cali Carvajal S.A. informó sobr
6. `carvajal_reporto_ingresos_por_2_58_billones_a_septiembre_de_2025_4269762` · score 0.7814 · [fuente](https://www.larepublica.co/empresas/carvajal-reporto-ingresos-por-2-58-billones-a-septiembre-de-2025-4269762)
   > A nivel operativo, la compañía informó que, si bien el trimestre presenta un resultado positivo, aún persisten desafíos. En el sector de empaques, la organización ha enfrentado un incremento en los precios de materias pr
7. `carvajal_reporto_ingresos_por_2_8_billones_con_corte_al_tercer_trimestre_3999096` · score 0.7803 · [fuente](https://www.larepublica.co/empresas/carvajal-reporto-ingresos-por-2-8-billones-con-corte-al-tercer-trimestre-3999096)
   > Los pasivos de Carvajal ascienden a $953.564 millones, que representó un endeudamiento total de 46% de los activos. Con corte a septiembre de este año, la compañía asegura que no tuvo cambios materiales en su situación d
8. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.7802 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Economía # Carvajal y sus empresas reportan resultados Los ingresos alcanzaron $3,9 billones, una utilidad bruta de $1,07 billones Siga a EL PAÍS en Google Discover y no se pierda las últimas noticias El negocio de empaq
9. `carvajal_reporto_ingresos_por_2_8_billones_con_corte_al_tercer_trimestre_3999096` · score 0.7785 · [fuente](https://www.larepublica.co/empresas/carvajal-reporto-ingresos-por-2-8-billones-con-corte-al-tercer-trimestre-3999096)
   > ## Carvajal reportó ingresos consolidados por $2,8 billones con corte al tercer trimestre Resultados de Carvajal para el tercer trimestre. - Agregue a sus temas de interés - Carvajal - Empresas - Resultados empresariales
10. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7783 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > El cierre de la planta de Yumbo Ha sido interpretado por algunos sectores como una señal de debilidad, pero en realidad responde a una estrategia de concentración y eficiencia, trasladando la producción a la planta de Gu

### q28 · ¿Qué utilidad o pérdida neta reportó Carvajal en 2024 y a qué filial se atribuye?

1. `resultados_empresariales_2024_de_carvajal_4099828` · score 0.7914 · [fuente](https://www.larepublica.co/empresas/resultados-empresariales-2024-de-carvajal-4099828)
   > Además, durante la Asamblea General de Accionistas de Carvajal se informó que aprobaron los estados financieros separados y consolidados con corte al 31 de diciembre de 2024, del informe anual 2024 y en general, las cuen
2. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.7898 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Federación de Cafeteros y Consejo Gremial, preocupados por impacto de la Ley de Rescate Fiscal Jóvenes aprenden a ahorrar creando y usando videojuegos; esta es la experiencia surgida desde Cali Carvajal S.A. informó sobr
3. `resultados_empresariales_2024_de_carvajal_4099828` · score 0.7897 · [fuente](https://www.larepublica.co/empresas/resultados-empresariales-2024-de-carvajal-4099828)
   > ## Carvajal presentó resultados financieros y reportó ingresos por $3,9 billones en 2024 Carvajal presentó resultados financieros y reportó ingresos por $3,9 billones en 2024 - Agregue a sus temas de interés - Carvajal -
4. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7792 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - marzo 2026 - febrero 2026 - enero 2026 - diciembre 2025 - noviembre 2025 - octubre 2025 - septiembre 2025 - agosto 2025 - julio 2025 - junio 2025 - mayo 2025 - abril 2025 - marzo 2025 - febrero 2025 - enero 2025 - dici
5. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7787 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > # Carvajal S.A.: Futuro consolidado ante desafíos globales Publicado por Redacción | 16 Abr 2025 | Portada 2 Las recientes asambleas de Accionistas de Carvajal S.A., celebradas el 31 de marzo y el 11 de abril de 2025, ma
6. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.7705 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Economía # Carvajal y sus empresas reportan resultados Los ingresos alcanzaron $3,9 billones, una utilidad bruta de $1,07 billones Siga a EL PAÍS en Google Discover y no se pierda las últimas noticias El negocio de empaq
7. `resultados_empresariales_2024_de_carvajal_4099828` · score 0.7678 · [fuente](https://www.larepublica.co/empresas/resultados-empresariales-2024-de-carvajal-4099828)
   > ### Conozca los beneficios exclusivos para nuestros suscriptores ## MÁS DE EMPRESAS ## Mercado Libre registró más de 3,3 millones de búsquedas asociadas con Halloween Al desagregar el flujo de búsquedas por categoría, Me
8. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.7665 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Pedro Carvajal es presidente de la Organización Carvajal desde el año 2020. Foto suministrada por la empresa. | Foto: El país De otro lado, destacaron la inversión en el proceso de transformación tecnológica, los planes 
9. `carvajal_reporto_ingresos_por_1_9_billones_a_junio_un_decrecimiento_de_14_anual_3928992` · score 0.765 · [fuente](https://www.larepublica.co/empresas/carvajal-reporto-ingresos-por-1-9-billones-a-junio-un-decrecimiento-de-14-anual-3928992)
   > ## Carvajal reportó ingresos por $1,9 billones a junio, un decrecimiento de 14% anual Resultados primer semestre de 2024 Grupo Carvajal - Agregue a sus temas de interés - Carvajal - Resultados financieros - Industria - R
10. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.764 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Adicional, la compañía reportó que no habrá distribución de utilidades y eligió nuevos miembros en su junta directiva. El Grupo Carvajal celebra 120 años de operaciones. Foto: suministrado por la empresa. | Foto: El país

### q29 · ¿Cuáles son los activos, pasivos y patrimonio consolidados de Carvajal al cierre de 2024?

1. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.8202 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > El cierre de la planta de Yumbo Ha sido interpretado por algunos sectores como una señal de debilidad, pero en realidad responde a una estrategia de concentración y eficiencia, trasladando la producción a la planta de Gu
2. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.8164 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - marzo 2026 - febrero 2026 - enero 2026 - diciembre 2025 - noviembre 2025 - octubre 2025 - septiembre 2025 - agosto 2025 - julio 2025 - junio 2025 - mayo 2025 - abril 2025 - marzo 2025 - febrero 2025 - enero 2025 - dici
3. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.8146 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > # Carvajal S.A.: Futuro consolidado ante desafíos globales Publicado por Redacción | 16 Abr 2025 | Portada 2 Las recientes asambleas de Accionistas de Carvajal S.A., celebradas el 31 de marzo y el 11 de abril de 2025, ma
4. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.8059 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - Muebles y Soluciones Empresariales: Mepal, enfocada en mobiliario y gestión de espacios empresariales. Organización Carvajal: Innovación, internacionalización y sostenibilidad. Su estrategia se centra en fortalecer la 
5. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.7881 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - 11 de abril de 2025: La Asamblea Extraordinaria decidió el cierre indefinido de la planta de producción de papel y cartulinas en Yumbo, Valle del Cauca, tras más de 60 años de operación. Esta medida responde a la impos
6. `resultados_empresariales_2024_de_carvajal_4099828` · score 0.7867 · [fuente](https://www.larepublica.co/empresas/resultados-empresariales-2024-de-carvajal-4099828)
   > ### Conozca los beneficios exclusivos para nuestros suscriptores ## MÁS DE EMPRESAS ## Mercado Libre registró más de 3,3 millones de búsquedas asociadas con Halloween Al desagregar el flujo de búsquedas por categoría, Me
7. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.783 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > - Reducción de colaboradores, por cierre de campañas y proyectos en tecnología, call center, etc. Sectores y Subordinadas Carvajal S.A. es la matriz de la Organización Carvajal, un grupo empresarial diversificado con pre
8. `noticias` · score 0.7814 · [fuente](https://www.carvajal.com/noticias/)
   > ## Cali Recicla con tu Reciclador: fortaleciendo la economía circular y la inclusión social ## diciembre, 2 lunes, 2024 ## Fundación Propal en su Programa de Primera Infancia con énfasis en Nutrición clausura con éxito s
9. `noticias` · score 0.7814 · [fuente](https://www.carvajal.com/noticias/)
   > ## UNIDOS PARA RECUPERAR Y REVITALIZAR A FICO ## julio, 2 jueves, 2026 ## Pedro Felipe Carvajal: “Continuaremos con nuestra apuesta decidida por Colombia” ## mayo, 28 miércoles, 2025 ## Carvajal Empaques, reconocida como
10. `historia` · score 0.7787 · [fuente](https://www.carvajal.com/historia/)
   > ## 2021 Declaramos nuestro propósito, fruto de una convicción colectiva y construido gracias a la participa de más de 1.500 personas entre colaboradores, clientes y proveedores. El propósito hace parte del legado que dej

### q30 · ¿Cuántos empleados tiene Carvajal y qué fuente lo reporta?

1. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.758 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > “De cara a 2025, mantenemos nuestro compromiso con la creación de valor sostenible, enfocando esfuerzos en la ejecución de la estrategia, garantizando operaciones eficientes y manteniendo costos competitivos para protege
2. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.7472 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Federación de Cafeteros y Consejo Gremial, preocupados por impacto de la Ley de Rescate Fiscal Jóvenes aprenden a ahorrar creando y usando videojuegos; esta es la experiencia surgida desde Cali Carvajal S.A. informó sobr
3. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.7464 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Economía # Carvajal y sus empresas reportan resultados Los ingresos alcanzaron $3,9 billones, una utilidad bruta de $1,07 billones Siga a EL PAÍS en Google Discover y no se pierda las últimas noticias El negocio de empaq
4. `resultados_empresariales_2024_de_carvajal_4099828` · score 0.7457 · [fuente](https://www.larepublica.co/empresas/resultados-empresariales-2024-de-carvajal-4099828)
   > ### MÁS DE LA REPÚBLICA ## Crecimiento de EE.UU. repuntó en tercer trimestre, mientras inflación sigue alta ## Gremios piden modernizar el transporte de carga para mejorar la competitividad ## Los clientes de Banco Conta
5. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.7391 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Adicional, la compañía reportó que no habrá distribución de utilidades y eligió nuevos miembros en su junta directiva. El Grupo Carvajal celebra 120 años de operaciones. Foto: suministrado por la empresa. | Foto: El país
6. `carvajal_reporto_ingresos_por_2_8_billones_con_corte_al_tercer_trimestre_3999096` · score 0.7305 · [fuente](https://www.larepublica.co/empresas/carvajal-reporto-ingresos-por-2-8-billones-con-corte-al-tercer-trimestre-3999096)
   > Los pasivos de Carvajal ascienden a $953.564 millones, que representó un endeudamiento total de 46% de los activos. Con corte a septiembre de este año, la compañía asegura que no tuvo cambios materiales en su situación d
7. `carvajal_reporto_ingresos_por_2_58_billones_a_septiembre_de_2025_4269762` · score 0.729 · [fuente](https://www.larepublica.co/empresas/carvajal-reporto-ingresos-por-2-58-billones-a-septiembre-de-2025-4269762)
   > ## Carvajal cerró planta de Propal en Yumbo por aumento de la competencia con China El Ebitda consolidado ascendió a $351.631 millones, mejorando el margen Ebitda de 12% a 14% en el último año. ## Carvajal presentó resul
8. `carvajal_reporto_ingresos_por_2_8_billones_con_corte_al_tercer_trimestre_3999096` · score 0.7269 · [fuente](https://www.larepublica.co/empresas/carvajal-reporto-ingresos-por-2-8-billones-con-corte-al-tercer-trimestre-3999096)
   > ## Carvajal reportó ingresos consolidados por $2,8 billones con corte al tercer trimestre Resultados de Carvajal para el tercer trimestre. - Agregue a sus temas de interés - Carvajal - Empresas - Resultados empresariales
9. `carvajal_reporto_ingresos_por_2_58_billones_a_septiembre_de_2025_4269762` · score 0.7257 · [fuente](https://www.larepublica.co/empresas/carvajal-reporto-ingresos-por-2-58-billones-a-septiembre-de-2025-4269762)
   > ## La Organización Carvajal reportó ingresos por $2,58 billones a septiembre de 2025 Pedro Felipe Carvajal, presidente de Carvajal. - Agregue a sus temas de interés - Carvajal - Propal - Resultados financieros El sector 
10. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.7244 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Cali ## Así concluyó el Encuentro Regional del Plan Nacional de Desarrollo 2026-2030 realizado en Cali ## Cali tendrá una oficina bancaria móvil: dónde estará y qué trámites se podrán realizar ## Banco de la República ma

### q31 · ¿Cuál es el número de teléfono de la sede de Carvajal en Tokio?

1. `comunicacion` · score 0.6855 · [fuente](https://www.carvajal.com/comunicacion/)
   > Carvajal Soluciones de Comunicación ## Impulsamos tecnologías renovables para la solución de servicios de ## Comunicación ## impresa y digital. ## Somos una empresa ecológica Carvajal Soluciones de Comunicación es una em
2. `carvajal_y_sus_empresas_reportan_resultados_0239html` · score 0.6851 · [fuente](https://www.elpais.com.co/economia/carvajal-y-sus-empresas-reportan-resultados-0239.html)
   > Economía # Carvajal y sus empresas reportan resultados Los ingresos alcanzaron $3,9 billones, una utilidad bruta de $1,07 billones Siga a EL PAÍS en Google Discover y no se pierda las últimas noticias El negocio de empaq
3. `tecnologia` · score 0.6808 · [fuente](https://www.carvajal.com/tecnologia/)
   > Carvajal Tecnología y Servicios ## Entendemos la ## TECNOLOGÍA ## y la transformamos en soluciones digitales para ti. ## Somos una compañía que crea sistemas de intercambio de información para lograr conectar a los ciuda
4. `historia` · score 0.6788 · [fuente](https://www.carvajal.com/historia/)
   > ## 1999 Alberto José Carvajal Lourido es nombrado presidente de Organización Carvajal. Venta de Carvajal Financiera al Banco Intercontinental. Venta de Chimolsa. ## 2000 Venta de DIRECTV a Hughes Electronics Corporation.
5. `carvajal_reporto_ingresos_por_2_58_billones_a_septiembre_de_2025_4269762` · score 0.676 · [fuente](https://www.larepublica.co/empresas/carvajal-reporto-ingresos-por-2-58-billones-a-septiembre-de-2025-4269762)
   > ### Conozca los beneficios exclusivos para nuestros suscriptores ## MÁS DE EMPRESAS ## Satena obtiene certificación de EE.UU. y participará en negocio de reparación aeronáutica La aerolínea estatal podrá diversificarse y
6. `carvajal_reporto_ingresos_por_2_58_billones_a_septiembre_de_2025_4269762` · score 0.674 · [fuente](https://www.larepublica.co/empresas/carvajal-reporto-ingresos-por-2-58-billones-a-septiembre-de-2025-4269762)
   > ## EE.UU. propone elecciones presidenciales en Venezuela en 2027, dice opositora Machado ## Utilidades del Emisor a septiembre fueron de $5,97 billones, registran caída de 45% ## Martín Pescador abre su primera tienda in
7. `historia` · score 0.6711 · [fuente](https://www.carvajal.com/historia/)
   > Inicia operaciones la División Muebles, hoy Carvajal Espacios ## 1959 Antes de consultar por internet nuestra ubicación y el destino al cual deseamos llegar, el directorio telefónico era nuestra única oportunidad de busc
8. `comunicacion` · score 0.6703 · [fuente](https://www.carvajal.com/comunicacion/)
   > ## Nuestra ## PRESENCIA: ## NUESTROS MERCADOS: - Impresión digital transaccional - Impresión digital editorial - Servicio de impresión administrada (MPS) ## Impresión digital transaccional Prestamos servicios para compañ
9. `carvajal_s_a_futuro_consolidado_ante_desafios_globales` · score 0.6687 · [fuente](https://caliescribe.com/2025/04/16/carvajal-s-a-futuro-consolidado-ante-desafios-globales)
   > El cierre de la planta de Yumbo Ha sido interpretado por algunos sectores como una señal de debilidad, pero en realidad responde a una estrategia de concentración y eficiencia, trasladando la producción a la planta de Gu
10. `carvajal_reporto_ingresos_por_2_58_billones_a_septiembre_de_2025_4269762` · score 0.6685 · [fuente](https://www.larepublica.co/empresas/carvajal-reporto-ingresos-por-2-58-billones-a-septiembre-de-2025-4269762)
   > ## Carvajal cerró planta de Propal en Yumbo por aumento de la competencia con China El Ebitda consolidado ascendió a $351.631 millones, mejorando el margen Ebitda de 12% a 14% en el último año. ## Carvajal presentó resul

