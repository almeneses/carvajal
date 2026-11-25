# Research: Fotos de alimentos → empaque sugerido (spec 051)

## R1. Clasificador: una petición de Jev con imagen

- **Decision**: una sola petición `POST /v1/systemone` por foto con `jev-1.14.0`, la foto en
  `assets` (base64 en JSON, formato `jpeg`) referenciada desde `state.foto`, la cabecera
  `TypeSafe-Danger-Zone-Images: true` y cinco preguntas: `es_comida` (Noul), `tipo`
  (Choice, 11 opciones), `consistencia` (Choice: sólido, semilíquido, líquido),
  `temperatura` (Choice: caliente, fría, ambiente) y `porcion` (Score, 4 niveles).
- **Rationale**: la prueba previa (11 fotos de Wikimedia Commons, 2026-11-24) acertó el tipo
  en 11/11, separó la foto sin comida (0,05) y midió mediana 216 ms y ~2.900 tokens de
  entrada por foto (~US$0,00013). Las preguntas se evalúan en paralelo sobre el mismo estado;
  añadirlas apenas cambia la latencia (documentación de TypeSafe).
- **Alternatives considered**: modelo con visión de OpenAI (clave ya en el VPS; texto libre
  que hay que validar y más caro); modelo local en Ollama (sin GPU: 30-90 s estimados);
  «¿se derrama?» (Noul) y Box: medidos y descartados (los sólidos caen en 0,6-0,77; Box
  enmarcó mal la bandeja paisa). El derrame se deduce de la consistencia.
- **Base64 frente a multipart**: base64 pesa un tercio más, pero la foto preparada pesa
  < 400 KB y reutiliza el cuerpo JSON del cliente actual; multipart queda como optimización.

## R2. Activación independiente del gatekeeper

- **Decision**: la foto tiene su propia bandera `FOOD_PHOTO_ENABLED` (por defecto `true`) y
  solo exige `TYPESAFE_API_KEY`; no depende de `JEV_ENABLED`. Usa un `JevClient` propio
  (tipo de llamada `food`) con su plazo (`FOOD_PHOTO_TIMEOUT_S`, 4 s), su tope diario
  (`FOOD_PHOTO_MAX_CALLS_PER_DAY`, 500), su circuito y el mismo libro `JEV_LEDGER`.
- **Rationale**: el owner pidió la foto encendida en producción (2026-11-24); encender
  `JEV_ENABLED` activaría además el gatekeeper y la compuerta de afirmaciones de la 046, cuya
  activación sigue pendiente de su decisión. Un cliente propio evita que el circuito o el
  tope de un uso apague el otro, y el plazo de 0,8 s del router no sirve para imágenes.
- **Alternatives considered**: reutilizar `build_judge` (exige `jev_active`); subir
  `JEV_TIMEOUT_S` (afectaría al router).

## R3. Preparación de la foto

- **Decision**: Pillow (ya en el lock como dependencia transitiva; se declara directa).
  `Image.open` con `MAX_IMAGE_PIXELS` acotado (40 Mpx, defensa contra bombas de
  descompresión), formato JPEG o PNG, sin animación; `ImageOps.exif_transpose`; RGB;
  `thumbnail(1568)`; lado menor ≥ 280 después de escalar; re-codificación JPEG calidad 85
  **sin** `exif` ni `icc_profile`: el archivo resultante no lleva metadatos.
- **Rationale**: límites de TypeSafe (lado 280-1568 px, ≤ 2.458.624 px, sin orientación EXIF
  aplicada) y FR-002/FR-003. Re-codificar es la forma segura de quitar todo metadato.
- **Límite de subida**: 10 MB (FR-001), comprobado por `Content-Length` antes de leer el
  cuerpo y otra vez al leer.

## R4. Umbrales y preguntas

- **Decision**: comida si `es_comida ≥ 0,5` y `tipo ≠ no_es_comida`; tipo seguro si su
  `confidence ≥ FOOD_PHOTO_MIN_CONFIDENCE` (0,6); temperatura igual. Orden de decisión: no
  es comida → tipo dudoso (nombra las dos más probables) → temperatura dudosa (pregunta) →
  recomendación. El texto opcional de la persona fija la temperatura por palabras clave
  («frío», «caliente», «ambiente», «congelado», «refrigerado») y el país («México»,
  «Colombia»); no fija el tipo.
- **Rationale**: 0,6 es el mismo umbral del gatekeeper (046). En la prueba, todas las
  respuestas correctas superaron 0,77 y la única duda real (guacamole) quedó en 0,46.
- **La respuesta a la pregunta** («frío») la atiende el agente normal en el turno siguiente:
  el texto guardado de la foto describe el alimento y la duda, y el agente llama a
  `buscar_producto` con lo que la persona aclaró.

## R5. Tabla tipo → requisitos

| Tipo | Uso (guía y fichas) | Familias | Temperatura por defecto | Cierre |
|---|---|---|---|---|
| sopas | `sopas` | contenedor, envase, vaso | caliente | sí |
| plato_fuerte | `comida_caliente` / `comida_fria` | contenedor, charola, plato | caliente | no |
| frituras | `frituras` | contenedor, charola, plato | caliente | no |
| ensaladas | `ensaladas` | contenedor, plato, charola | fría | no |
| salsas | `salsas` | envase, contenedor | ambiente | sí |
| panaderia | `panaderia` | contenedor, charola, plato | ambiente | no |
| frutas | `frutas` | contenedor, vaso, plato | fría | no |
| postres_helados | `comida_fria` | vaso, contenedor | fría | no |
| bebida_fria | `bebida_fria` | vaso | fría | sí |
| bebida_caliente | `bebida_caliente` | vaso | caliente | sí |

- La temperatura de la foto (si es segura) reemplaza la de la tabla; en `plato_fuerte` elige
  el uso (`comida_caliente` o `comida_fria`). Líquido o semilíquido implica cierre aunque la
  tabla diga que no.
- La aptitud la decide `check_material` (la misma regla de la 036/044): contenido caliente
  exige material con `hot_content`, la guía no puede marcar «no apto» para el uso, y los
  vasos declarados solo para la temperatura contraria se descartan.
- **Orden**: nivel de la guía para el uso («recomendable» > «aplicable» > sin dato), la ficha
  declara el uso, cercanía de la capacidad a la porción (comida: 4/8/16/32 oz; bebida:
  4/8/12/16 oz), SKU. Se eligen hasta 3 dando prioridad a materiales distintos.
- **Sin aptas**: «no tenemos un empaque específico»; como alternativa, la primera referencia
  de las mismas familias que cumple la temperatura aunque la guía no declare el uso, rotulada
  como alternativa.

## R6. Respuesta determinista y turno guardado

- **Decision**: el servidor redacta la respuesta sin LLM (como el respaldo de la 044) y la
  guarda como un turno normal: pregunta «📷 Foto de un alimento» (más el texto opcional),
  respuesta con la descripción, las sugerencias (nombre, ref., material, capacidad y ficha)
  y la oferta de cotizar; el resultado comercial va en `sources` con el mismo formato de
  tarjetas que `buscar_producto`, así la web las pinta y al recargar se reconstruyen.
- **Rationale**: principio III (sin fuentes no hay respuesta): ningún dato sale del modelo.
  El agente ve el turno en el historial (`seed_turns`) y con la referencia escrita puede
  cotizar «500 del primero».
- **Monitoreo**: el span del turno lleva solo metadatos (`path: "foto"`, tipo, confianzas,
  estado de la recomendación, número de sugerencias); `span.add_outputs` recibe la respuesta
  como en cualquier turno (sin imagen).

## R7. Cupo, idempotencia y fallos

- **Decision**: `ChatService.photo_stream(owner, note, *, analyze, charge, client_turn_id,
  thread_id)`: primero busca el turno por `client_turn_id` (reemite sin clasificar ni cobrar),
  después llama a `analyze()` (clasificador) y solo si clasificó llama a `charge()` (cupo);
  después recomienda, guarda y emite. Un fallo del clasificador emite `error` con el mensaje
  «no pude analizar la foto, descríbela por texto» y no guarda ni cobra.
- **Rationale**: FR-019 y FR-020 con una sola implementación para la web y WhatsApp.

## R8. WhatsApp: adjunto de OpenClaw

- **Decision**: OpenClaw muestra el adjunto al agente como
  `[media attached: media://inbound/<archivo>.jpg (image/jpeg)]` (verificado en
  `openclaw 2026.11.7`, `media-note`). `consultar_carvajal` gana el parámetro opcional `foto`
  (esa referencia o solo el nombre del archivo). El puente acepta solo nombres
  `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.(jpe?g|png)$`, los abre con `O_NOFOLLOW` dentro de
  `FOOD_PHOTO_MEDIA_DIR` (montado en solo lectura desde `~/.openclaw/media/inbound`) y lee como
  máximo 10 MB. Sin carpeta configurada, la foto por WhatsApp responde que no está disponible.
- **Rationale**: el agente de OpenClaw no tiene herramientas de archivos (están negadas por
  seguridad) y DeepSeek no ve imágenes; el puente sí puede leer el adjunto si comparten
  máquina, que es la configuración actual del VPS.
- **Riesgo aceptado**: un participante podría escribir el nombre de un adjunto ajeno; solo
  obtendría la clasificación (no la imagen) y los nombres son UUID no adivinables.
- **OpenClaw**: `tools.media.image.enabled: false`, para que OpenClaw no mande la foto a otro
  proveedor de visión; la guía lo documenta.

## R9. Interfaz web

- **Decision**: botón «Foto» junto al cuadro de texto (`<input type="file"
  accept="image/jpeg,image/png">`, oculto hasta que el JS lo activa), una ficha con el nombre
  del archivo y un aviso fijo mientras haya foto adjunta («La foto se analiza con TypeSafe
  (EE. UU.), no se guarda; evita personas y datos personales»). Sin vista previa: evita
  ampliar la CSP a `blob:`. El texto es opcional con foto. El envío va a `POST /chat/foto`
  (multipart, `X-CSRF-Token` en cabecera) y la respuesta es el mismo SSE de `/chat/stream`,
  así `chat-stream.js` reutiliza el pintado, las tarjetas y el `done`.
- **Rationale**: FR-001, FR-018 y FR-022 con el mínimo de código nuevo en el cliente.
