# Alcance del asistente Q&A

> Qué debe poder responder el asistente cuando un cliente contacta a Carvajal por
> primera vez, qué cubre el corpus y qué queda explícitamente fuera.

## 1. Objetivo

Responder preguntas de **primer contacto** —quién es la empresa, qué vende, dónde opera,
cómo le ha ido y cómo se contacta— usando **solo** el corpus capturado de fuentes
oficiales y de prensa, citando la fuente de cada dato.

## 2. Tipos de pregunta y cobertura

| # | Categoría | Ejemplos de pregunta | Cobertura | Documentos que la sustentan |
|---|---|---|---|---|
| 1 | Empresa | ¿Qué es Carvajal? ¿Cuál es su propósito? ¿Quién la preside? | ✅ Completa | `quienes_somos`, `home`, `proposito_superior`, `gobierno_corporativo` |
| 2 | Sectores y empresas del grupo | ¿Qué empresas la conforman? ¿Qué es Propal? ¿Qué hace Mepal? | ✅ Completa | `quienes_somos`, `empaques`, `educacion`, `tecnologia`, `espacios`, `pulpa-y-papel` |
| 3 | Productos y marcas | ¿Qué cuadernos vende? ¿Qué empaques produce? | ✅ Completa | `empaques`, `educacion`, `tecnologia`, `espacios`, `comunicacion` |
| 4 | Historia | ¿Cuándo y dónde nació? ¿Cuántos años lleva? | ✅ Completa | `historia`, `noticias` |
| 5 | Sostenibilidad y ética | ¿Qué hace en sostenibilidad? ¿Cómo denuncio un acto antiético? | ✅ Completa | `sostenibilidad`, `compromiso_etico`, `relacionamiento-con-grupos-de-interes`, `politica-de-innovacion` |
| 6 | Inversión y cifras | ¿Cuánto facturó en 2024? ¿Cuál fue el EBITDA? | ✅ Completa, con fuente de terceros | `resultados_empresariales_2024…`, `carvajal_reporto_ingresos_por_2_58…`, `carvajal_s_a_futuro_consolidado…` |
| 7 | Presencia geográfica | ¿En qué países opera? | ✅ Completa, con discrepancia declarada | `quienes_somos` (10 países), `carvajal_s_a_futuro_consolidado…` (17) |
| 8 | **Contacto, sedes y horarios** | ¿Cuál es el teléfono? ¿Dónde queda la sede? ¿En qué horario atienden? | ❌ **No disponible** | — (ver §3) |
| 9 | **Canales de venta y soporte** | ¿Cómo compro? ¿Cómo hago una garantía? | ❌ Fuera de alcance | — |

## 3. Hallazgo: el sitio no publica datos de contacto ni sedes

Antes de declarar el alcance se intentó capturar esas páginas. La evidencia:

| Comprobación | Resultado |
|---|---|
| `GET /contacto/` | **404** |
| `GET /contactenos/` | **404** |
| `GET /sedes/` y `/oficinas/` | **404** |
| Barrido de los 26 sub-sitemaps (~600 URLs) | **ninguna** URL de contacto, sedes u oficinas |
| `/inversionistas/servicio-al-inversionista/contactenos/` | 200, pero el texto extraído son **14 caracteres**: el contenido es un formulario dinámico |
| HTML de la portada | **0** correos, **0** teléfonos, **0** direcciones |
| `/trabaja-con-nosotros/` y `/nuestras-empresas/` | 200, pero **0 caracteres** extraíbles (render dinámico) |

**Decisión:** no se inventan datos ni se rellenan con fuentes no verificables. El asistente
debe decir que la información no está en el corpus y remitir a los canales oficiales del
sitio. El único canal de contacto que **sí** está documentado es la **Línea Ética**
(`compromiso_etico`), confidencial y 24/7.

La FAQ generada (`docs/faq.md`) declara esta ausencia en la sección *«Temas sin información
en el corpus»*, y es la respuesta correcta según el criterio de no-alucinación.

## 4. Fuera de alcance

- Precios, disponibilidad de producto, tiempos de entrega y soporte postventa.
- Procesos internos, información de empleados o datos personales.
- Contenido tras autenticación y documentos PDF (estados financieros completos).
- Opiniones o comparaciones con competidores.

## 5. Composición del corpus (24 documentos)

| Origen | Documentos | Aporte |
|---|---|---|
| `carvajal.com` (oficial) | 18 | Empresa, sectores, productos, historia, ética, sostenibilidad, innovación, pulpa y papel |
| `larepublica.co`, `elpais.com.co`, `caliescribe.com` | 6 | Ingresos, EBITDA, márgenes, utilidad, activos, empleo |

Cada documento registra en su *front matter*: `url`, `title`, `section`, `domain`,
`source_type`, `captured_at`, `hash` y `chars`.

## 6. Cómo se verificó

```bash
uv run carvajal capture     # 24/24 URLs, 0 descartadas, 92 682 caracteres
uv run carvajal status      # 18 oficiales / 6 terceros
uv run carvajal faq         # la FAQ declara los temas ausentes
```

La matriz de la §2 se contrastó documento por documento contra `data/gold/` y el índice
`data/gold/index.jsonl`.
