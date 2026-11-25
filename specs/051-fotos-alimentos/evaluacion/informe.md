# Evaluación de fotos de alimentos (spec 051)

Fotos: 22 (19 de comida, 3 de control, 0 con error). Modelo: `jev-1.14.0`.

| Métrica | Resultado | Meta |
|---|---|---|
| Tipo correcto (comida) | 19/19 | ≥ 85 % |
| Temperatura aceptada (comida) | 19/19 | informativa |
| Controles rechazados o preguntados | 3/3 | 100 % |
| Sugerencias no aptas | 0 | 0 |
| Latencia p50 / p95 | 432 / 484 ms | < 5 s |
| Tokens de entrada por foto (media) | 3810 | informativa |

| Foto | Tipo (conf.) | Temperatura (conf.) | Estado | Sugerencias | OK |
|---|---|---|---|---|---|
| ajiaco | sopas (0.9) | caliente (0.98) | ok | CO-CAZ-16, CO-CTER-16, CO-CTT-16 | ✓ |
| sancocho | sopas (1.0) | caliente (0.97) | ok | CO-CAZ-16, CO-CTER-16, CO-CTT-16 | ✓ |
| bandeja_paisa | plato_fuerte (0.99) | caliente (0.98) | ok | CO-CAZ-16, CO-CTER-16, CO-CRIG-16 | ✓ |
| arroz_con_pollo | plato_fuerte (0.97) | caliente (0.98) | ok | CO-CAZ-16, CO-CTER-16, CO-CRIG-16 | ✓ |
| empanadas | frituras (0.91) | caliente (0.96) | ok | CO-CAZ-16, CO-CTER-16, CO-CRIG-16 | ✓ |
| papas_fritas | plato_fuerte (0.58) | caliente (1.0) | ask_kind | — | ✓ |
| ensalada | ensaladas (1.0) | fria (0.92) | ok | CO-PLISO, CO-PRIG-G, CO-PRIG-M | ✓ |
| ensalada_frutas | frutas (1.0) | fria (0.61) | ok | CO-PLISO, CO-PRIG-G, CO-PRIG-M | ✓ |
| guacamole | salsas (0.99) | ambiente (0.46) | ask_temperature | — | ✓ |
| aji | salsas (0.69) | ambiente (0.48) | ask_temperature | — | ✓ |
| pan_bono | panaderia (0.94) | ambiente (0.82) | ok | CO-CRIG-06, CO-CRIG-08, CO-CRIG-12 | ✓ |
| pan | panaderia (1.0) | ambiente (1.0) | ok | CO-CRIG-32, CO-CRIG-24, CO-CRIG-16 | ✓ |
| helado | postres_helados (1.0) | fria (1.0) | ok | CO-VTER-08, CO-VTER-06, CO-VTER-10 | ✓ |
| torta_chocolate | postres_helados (0.89) | fria (0.92) | ok | CO-BESP-1, CO-BESP-171, CO-BESP-251 ¹ | ✓ |
| limonada | bebida_fria (1.0) | fria (1.0) | ok | CO-VTER-08, 7704918100199, CO-VGOLD-09 | ✓ |
| jugo_naranja | bebida_fria (1.0) | fria (0.79) | ok | CO-VTER-08, 7704918100199, CO-VGOLD-09 | ✓ |
| salpicon | bebida_fria (0.98) | fria (0.91) | ok | CO-VGOLD-12, CO-VTER-10, CO-VRM-12 | ✓ |
| tinto | bebida_caliente (1.0) | caliente (1.0) | ok | CO-VTER-08, 7704918100199, CO-VRM-10 | ✓ |
| chocolate_caliente | bebida_caliente (0.92) | caliente (0.73) | ok | CO-VRM-12, CO-VTER-10, CO-VRG-13 | ✓ |
| control_zapato | no_es_comida (0.77) | ambiente (0.14) | not_food | — | ✓ |
| control_carro | no_es_comida (1.0) | ambiente (0.8) | not_food | — | ✓ |
| control_laptop | no_es_comida (1.0) | ambiente (0.92) | not_food | — | ✓ |

¹ Recalculado sin llamar a Jev, con el perfil guardado de esta corrida, tras corregir la
tabla: en la corrida la torta (postre sólido) recibió vasos térmicos (CO-VTER-08/06/10);
ahora un postre sólido va en contenedor, charola o plato (`_SOLID_DESSERT_FAMILIES`).
