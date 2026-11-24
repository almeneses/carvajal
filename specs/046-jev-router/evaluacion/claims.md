# Evaluación de la compuerta de afirmaciones (spec 046)

Conjunto: `data/eval/claims_046.json` (congelado el 2026-11-24); umbral vigente 0,5 (no respaldada si p < umbral).

Juzgadas: 16; sin llamada por cuota: 0; fallos: 0. Latencia p50 / p95: 146 / 296 ms.

| Umbral | Detección (no respaldadas) | Falsas alarmas (buenas) |
|---|---|---|
| 0,3 | 100.0% (10/10) | 0.0% (0/6) |
| 0,4 | 100.0% (10/10) | 0.0% (0/6) |
| 0,5 (vigente) | 100.0% (10/10) | 0.0% (0/6) |
| 0,6 | 100.0% (10/10) | 16.7% (1/6) |
| 0,7 | 100.0% (10/10) | 33.3% (2/6) |

## Por respuesta

| Ítem | v3 | Variante | Etiqueta | p | Veredicto | Nota |
|---|---|---|---|---|---|---|
| `cl-001-orig` | v3-001 | original | unsupported | 0.06 | no respaldada | «La de 16 oz es la más usada para sopas»: valoración presentada como hecho que ninguna con |
| `cl-001-pert` | v3-001 | perturbada | unsupported | 0.02 | no respaldada | Perturbación «cualitativa»: se insertó «Además, esa cazuela es compostable y se degrada en |
| `cl-002-orig` | v3-002 | original | supported | 0.79 | respaldada | Respuesta original de la ronda 4 del v3: cada dato aparece en las consultas. |
| `cl-002-pert` | v3-002 | perturbada | unsupported | 0.08 | no respaldada | Perturbación «plazo»: se insertó «EDEXA despacha a Cali en 48 horas hábiles desde la confi |
| `cl-005-orig` | v3-005 | original | unsupported | 0.13 | no respaldada | «la tapa se pide aparte»: ninguna consulta lo devolvió (misma regla que v3-001). |
| `cl-005-pert` | v3-005 | perturbada | unsupported | 0.04 | no respaldada | Perturbación «certificacion»: se insertó «Todas estas referencias tienen certificación FDA |
| `cl-008-orig` | v3-008 | original | supported | 0.78 | respaldada | Respuesta original de la ronda 4 del v3: cada dato aparece en las consultas. |
| `cl-008-pert` | v3-008 | perturbada | unsupported | 0.07 | no respaldada | Perturbación «precio»: se insertó «Sus ingresos consolidados de ese año fueron de $2,91 bi |
| `cl-010-orig` | v3-010 | original | supported | 0.90 | respaldada | Respuesta original de la ronda 4 del v3: cada dato aparece en las consultas. |
| `cl-010-pert` | v3-010 | perturbada | unsupported | 0.03 | no respaldada | Perturbación «cualitativa»: se insertó «Es el portacomidas preferido por los asaderos porq |
| `cl-018-orig` | v3-018 | original | supported | 0.93 | respaldada | Respuesta original de la ronda 4 del v3: cada dato aparece en las consultas. |
| `cl-018-pert` | v3-018 | perturbada | unsupported | 0.04 | no respaldada | Perturbación «plazo»: se insertó «Se despacha desde la planta en un plazo de 5 días hábile |
| `cl-027-orig` | v3-027 | original | supported | 0.58 | respaldada | El teléfono «(601)» es el del directorio con el indicativo actualizado: el owner decidió q |
| `cl-027-pert` | v3-027 | perturbada | unsupported | 0.04 | no respaldada | Perturbación «certificacion»: se insertó «El portacomidas espumado J1 cuenta con certifica |
| `cl-034-orig` | v3-034 | original | supported | 0.60 | respaldada | Respuesta original de la ronda 4 del v3: cada dato aparece en las consultas. |
| `cl-034-pert` | v3-034 | perturbada | unsupported | 0.06 | no respaldada | Perturbación «plazo»: se insertó «Si la pides hoy, te llega en 3 días hábiles.», que ningu |

## Origen del conjunto

prueba de Jev del 2026-11-24 (docs/research/jev.md): 8 respuestas originales de la ronda 4 del v3 (v3-r4) y sus 8 perturbaciones; evidencia reconstruida con run_tool sobre las llamadas registradas del turno y armada con build_claims_query (la pregunta solo deriva dicho_por_la_persona y no viaja)
