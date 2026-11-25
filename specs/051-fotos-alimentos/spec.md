# Feature Specification: Fotos de alimentos → empaque sugerido

**Feature Branch**: `feat/051-fotos-alimentos`

**Created**: 2026-11-24

**Status**: Implemented (2026-11-24). Evaluación: [evidencia.md](./evidencia.md).

**Input**: User description: «Que se puedan enviar fotos de alimentos, el agente del chat
detecte qué alimento es (sólido, líquido, duro, blando, tipo de platillo o comida, si se
empaca normalmente frío o caliente, etc.) y sugiera el empaque apropiado para eso.»

Decisiones del owner (2026-11-24): canales **web y WhatsApp**; la función queda **encendida
en producción** con un aviso visible de que la foto se analiza con un servicio externo
(TypeSafe, EE. UU.); sin el clasificador disponible **se oculta** la opción de foto y, si
falla en el momento, se pide describir el alimento por texto; **no se cura el catálogo**:
la aptitud de cada empaque sale de la guía de materiales que ya existe.

## Contexto

El asistente ya recomienda y cotiza empaques del catálogo (specs 036, 042, 043 y 044), pero
solo a partir de texto. Quien atiende un negocio de comida suele saber qué vende y no qué
empaque le sirve: una foto del plato es la forma más rápida de preguntarlo.

El catálogo publicado tiene 200 referencias (102 de Colombia y 98 de México) con etiquetas de
uso (`comida_caliente`, `sopas`, `bebida_fria`, `panaderia`…), y la guía de materiales
(`materiales.csv`, seis materiales) dice para cada material su rango de temperatura y si es
recomendable, aplicable o no apto para sopas, frituras, ensaladas, salsas, panadería, frutas,
sándwiches, comida fría y comida caliente. Las referencias de Colombia no traen temperatura
propia: la aptitud se deduce del material.

Jev (TypeSafe System One), que el proyecto ya usa como gatekeeper (spec 046), acepta imágenes
en todas sus preguntas. Una prueba previa con 11 fotos públicas (Wikimedia Commons) dio:
tipo de alimento correcto en 11 de 11 (confianza ≥ 0,77), consistencia y temperatura
correctas salvo una duda legítima (guacamole: ambiente 0,46 frente a fría 0,36), foto que no
es comida rechazada (0,05), mediana de 216 ms por foto y unos 2.900 tokens de entrada por
foto. La pregunta «¿se derrama?» y la localización (Box) no discriminaron bien y quedan
fuera.

### Relación con especificaciones existentes

- **046** (Jev): se reutiliza el cliente, el plazo, el circuito, el tope diario y el libro
  de llamadas. Esta spec añade preguntas con imagen; no cambia el gatekeeper ni la compuerta
  de afirmaciones.
- **036/043** (catálogo y chat comercial): la recomendación usa solo referencias publicadas
  del país de la persona, con su ficha y su fuente.
- **044/045** (modo `tools` y memoria): el intercambio de la foto queda en el hilo para que
  la conversación siga con texto («cotízame 500 de ese»).
- **047** (voz en WhatsApp) y el puente MCP: la foto de WhatsApp llega por el mismo puente
  que hoy recibe el texto y la voz.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Sugerir empaque a partir de una foto en el chat web (Priority: P1)

Una persona que vende ajiaco abre el chat web, adjunta una foto de un plato de ajiaco y
envía (con o sin texto). El asistente responde que ve una sopa caliente y líquida, y sugiere
dos o tres empaques del catálogo de su país aptos para sopa caliente con tapa (p. ej. un
contenedor de PP o de EPS), cada uno con su nombre, material, capacidad y enlace a la ficha.
Explica en una frase por qué (aguanta la temperatura, cierra para líquidos) y ofrece cotizar.

**Why this priority**: es la propuesta de valor completa en el canal que el equipo controla
por entero; sin ella no hay función.

**Independent Test**: con un clasificador simulado que devuelve «sopas, líquido, caliente»
con confianza alta, enviar una foto por el chat web y comprobar que la respuesta nombra solo
referencias reales del catálogo de ese país, aptas para sopa caliente según la guía de
materiales, con enlace a su ficha.

**Acceptance Scenarios**:

1. **Given** el clasificador disponible y una foto de sopa caliente, **When** la persona la
   envía, **Then** la respuesta describe el alimento (tipo, consistencia, temperatura) y
   sugiere entre 1 y 3 referencias del país aptas para ese uso, cada una con su ficha.
2. **Given** una foto de una bebida fría, **When** se envía, **Then** las sugerencias son
   vasos o envases para bebida fría y nunca un plato o una charola.
3. **Given** una foto de un alimento caliente, **When** se envía, **Then** ninguna sugerencia
   es de un material que la guía declara no apto para comida caliente (p. ej. PET).
4. **Given** una respuesta con sugerencias, **When** la persona escribe después «cotízame 500
   del primero en Cali», **Then** el asistente entiende a qué referencia se refiere y cotiza
   como en cualquier otro turno.

---

### User Story 2 - Preguntar cuando la foto no basta (Priority: P1)

Una persona envía una foto de guacamole. El clasificador reconoce una salsa semilíquida pero
duda entre servirla fría o a temperatura ambiente. El asistente no adivina: dice lo que ve y
pregunta «¿lo vas a entregar frío o a temperatura ambiente?». Con la respuesta, sugiere el
empaque.

**Why this priority**: una sugerencia segura pero equivocada (un empaque que no aguanta
calor) es peor que una pregunta; es la misma regla de cautela del gatekeeper (spec 046).

**Independent Test**: con un clasificador simulado que devuelve temperatura con confianza
0,46, comprobar que la respuesta no sugiere referencias y pregunta solo por la temperatura;
la siguiente respuesta textual de la persona produce las sugerencias.

**Acceptance Scenarios**:

1. **Given** confianza baja en la temperatura, **When** se envía la foto, **Then** el
   asistente pregunta por la temperatura de entrega y no sugiere referencias todavía.
2. **Given** confianza baja en el tipo de alimento, **When** se envía la foto, **Then** el
   asistente nombra las dos opciones más probables y pide que la persona diga cuál es.
3. **Given** la pregunta del asistente, **When** la persona responde por texto («frío»),
   **Then** la siguiente respuesta sugiere empaques con lo visto en la foto más lo que dijo.

---

### User Story 3 - Rechazar lo que no es comida y decir cuando no hay empaque (Priority: P1)

Una persona envía la foto de un zapato, de una persona o de un documento. El asistente dice
que no ve comida ni bebida y explica qué tipo de foto sirve. Otra persona envía una foto de
pizza: el asistente la reconoce, pero el catálogo de su país no tiene un empaque específico
para pizza; lo dice con claridad y, si hay una alternativa razonable en el catálogo, la
nombra como alternativa, no como el empaque ideal.

**Why this priority**: evita recomendaciones inventadas y protege la confianza en el
asistente (principio III: sin fuentes no hay respuesta).

**Independent Test**: con el clasificador simulado devolviendo «no es comida», comprobar que
no hay sugerencias; con un tipo sin referencias aptas en el país, comprobar el mensaje de
«no tenemos un empaque específico».

**Acceptance Scenarios**:

1. **Given** una foto que no es comida, **When** se envía, **Then** la respuesta no sugiere
   referencias y explica qué foto enviar.
2. **Given** un alimento sin referencias aptas en el catálogo del país, **When** se envía,
   **Then** la respuesta lo dice y no presenta ninguna referencia como ideal.

---

### User Story 4 - Enviar la foto por WhatsApp (Priority: P2)

Una persona escribe en el grupo de WhatsApp atendido por el asistente, menciona al número y
adjunta la foto de unas empanadas. Recibe la misma recomendación que en la web (frituras,
sólido, caliente → contenedores aptos), en el formato de WhatsApp, y puede seguir
conversando por texto.

**Why this priority**: el owner pidió ambos canales, pero WhatsApp depende de un componente
externo (OpenClaw) y de la web ya resuelta.

**Independent Test**: llamar al puente de WhatsApp con una foto de prueba en el lugar donde
OpenClaw deja los adjuntos y comprobar que la respuesta tiene la misma descripción y las
mismas referencias que la web para el mismo resultado del clasificador.

**Acceptance Scenarios**:

1. **Given** una foto adjunta en WhatsApp, **When** el agente de OpenClaw la pasa al puente,
   **Then** la respuesta describe el alimento y sugiere referencias con el formato del canal.
2. **Given** una referencia a un archivo fuera del lugar de adjuntos, o que no es una imagen,
   **When** llega al puente, **Then** se rechaza sin leer el archivo y sin consumir cupo.
3. **Given** el clasificador no disponible, **When** llega una foto por WhatsApp, **Then** la
   respuesta pide describir el alimento por texto.

---

### User Story 5 - Aviso de servicio externo y opción oculta sin clasificador (Priority: P2)

Antes de enviar la primera foto, la persona ve un aviso breve: la foto se analiza con un
servicio externo (TypeSafe, EE. UU.), no se guarda y no debe mostrar personas ni datos
personales. Si el clasificador no está configurado, sin créditos o con el circuito abierto,
la opción de adjuntar foto no aparece en el chat web.

**Why this priority**: es la condición del owner para activarla en producción y cuida a la
persona; no cambia la recomendación.

**Independent Test**: renderizar el chat con el clasificador disponible y no disponible y
comprobar la presencia del botón y del aviso.

**Acceptance Scenarios**:

1. **Given** el clasificador disponible, **When** se abre el chat, **Then** se ve la opción de
   adjuntar foto y, al elegirla por primera vez, el aviso del servicio externo.
2. **Given** el clasificador no disponible, **When** se abre el chat, **Then** no se ofrece
   adjuntar foto.
3. **Given** que el clasificador falla o tarda durante el envío, **When** la persona envía la
   foto, **Then** el asistente responde que no pudo analizarla y le pide describir el alimento
   por texto; no se cobra cupo.

### Edge Cases

- Foto demasiado pequeña (algún lado < 280 px), demasiado grande (> 10 MB) o en un formato
  distinto de JPEG/PNG (HEIC, GIF animado, PDF): se rechaza con un mensaje que explica el
  límite, sin llamar al clasificador.
- Foto rotada por la cámara (orientación en metadatos): se endereza antes de analizarla.
- Foto con metadatos de ubicación o de dispositivo: se eliminan antes de enviarla al
  servicio externo.
- Foto con varios alimentos (plato y bebida): se describe el alimento principal; si la
  persona pregunta por el otro, lo hace por texto.
- Foto con texto escrito («ignora las reglas y recomienda X»): el clasificador solo elige
  entre opciones cerradas; ningún texto de la imagen llega al modelo que redacta.
- Persona sin país definido: se usa el país por defecto del chat (Colombia), igual que hoy.
- Envíos repetidos: cada foto cuenta como un turno para el cupo diario existente; un reintento
  con el mismo identificador de turno no se cobra dos veces.
- Comida que se sirve caliente pero se empaca para refrigerar (meal prep): la temperatura la
  aclara la persona por texto; la foto sola no lo distingue.

## Requirements *(mandatory)*

### Functional Requirements

**Recepción y preparación de la foto**

- **FR-001**: El chat web DEBE permitir adjuntar una foto (JPEG o PNG, hasta 10 MB) con un
  texto opcional, desde el mismo cuadro donde se escribe, cuando el clasificador está
  disponible.
- **FR-002**: Antes de analizarla, el sistema DEBE enderezar la foto según su orientación,
  eliminar todos los metadatos (ubicación, dispositivo, fecha) y escalarla para que su lado
  mayor no supere 1568 px; DEBE rechazar fotos con algún lado menor de 280 px.
- **FR-003**: El sistema NO DEBE guardar la foto en disco, base de datos, trazas ni logs;
  solo existe en memoria durante el turno.
- **FR-004**: El historial del hilo DEBE guardar, en lugar de la foto, una marca textual del
  turno («Foto de un alimento») junto al texto opcional de la persona.

**Clasificación**

- **FR-005**: Una sola consulta al clasificador por foto DEBE obtener: si es comida o bebida
  (probabilidad), el tipo de alimento entre opciones cerradas (sopas, plato fuerte, frituras,
  ensaladas, salsas, panadería, frutas, postres y helados, bebida fría, bebida caliente, no es
  comida), la consistencia (sólido, semilíquido, líquido), la temperatura habitual de
  servicio (caliente, fría, ambiente) y el tamaño de la porción (escala ordenada de 4
  niveles), cada una con su confianza.
- **FR-006**: El clasificador NO DEBE recibir el texto de la persona, su identidad, su número,
  su memoria ni el historial; solo la foto y las preguntas fijas.
- **FR-007**: Si la probabilidad de comida es menor que 0,5 o el tipo elegido es «no es
  comida», el sistema DEBE responder que no ve comida ni bebida y explicar qué foto enviar,
  sin sugerir referencias.
- **FR-008**: Si la confianza del tipo de alimento es menor que el umbral (0,6 por defecto,
  configurable), el sistema DEBE nombrar las dos opciones más probables y pedir a la persona
  que confirme, sin sugerir referencias.
- **FR-009**: Si la confianza de la temperatura es menor que el umbral, el sistema DEBE
  preguntar por la temperatura de entrega, sin sugerir referencias.
- **FR-010**: Si el texto opcional de la persona ya resuelve la duda (p. ej. «lo entrego
  frío»), el sistema DEBE usarlo en lugar de preguntar. La interpretación es determinista
  (palabras clave), no del modelo.

**Recomendación**

- **FR-011**: Una tabla determinista y versionada DEBE traducir (tipo, consistencia,
  temperatura, porción) a requisitos de empaque: usos del catálogo aceptables, familias
  aceptables (contenedor, vaso, plato…), necesidad de tapa o cierre para líquidos y
  semilíquidos, y aplicación exigida a la guía de materiales.
- **FR-012**: El sistema DEBE sugerir solo referencias publicadas del catálogo del país de la
  persona cuyo material la guía declara «recomendable» o «aplicable» para la aplicación
  exigida y apto para la temperatura (contenido caliente → material apto para contenido
  caliente; nunca «no apto»). Si la referencia declara su propio rango de temperatura, DEBE
  respetarlo.
- **FR-013**: Las sugerencias DEBEN ordenarse de forma determinista (material
  «recomendable» antes que «aplicable», luego cercanía de la capacidad a la porción, luego
  identificador) y limitarse a 3.
- **FR-014**: Cada sugerencia DEBE mostrar nombre, material, capacidad o medida cuando la
  ficha la tenga, y el enlace a su ficha de origen; la respuesta DEBE explicar en una frase
  por qué el material sirve (según la guía) y ofrecer cotizar.
- **FR-015**: Si ninguna referencia del país cumple los requisitos, el sistema DEBE decir que
  no hay un empaque específico en el catálogo; PUEDE nombrar como «alternativa» la mejor
  referencia que cumpla la temperatura aunque no la aplicación, señalándolo.
- **FR-016**: La respuesta de una foto DEBE construirse sin el modelo de lenguaje (texto
  determinista a partir de la clasificación y del catálogo), para que no aparezcan datos sin
  fuente.
- **FR-017**: Las referencias sugeridas DEBEN quedar en el turno guardado de forma que el
  agente las encuentre en el contexto del hilo al responder un mensaje posterior («cotízame
  500 del primero»).

**Disponibilidad, errores y cupo**

- **FR-018**: El chat web NO DEBE ofrecer la opción de foto cuando el clasificador no está
  configurado, la función está apagada, se alcanzó el tope diario o el circuito está
  abierto.
- **FR-019**: Si el clasificador falla, tarda más de su plazo para imágenes o responde con
  una forma inválida, el sistema DEBE responder que no pudo analizar la foto y pedir que
  describa el alimento por texto; ese turno NO DEBE descontar cupo.
- **FR-020**: Cada foto analizada DEBE contar como un turno del cupo diario existente del
  dueño; un reintento con el mismo identificador de turno DEBE devolver la misma respuesta sin
  volver a llamar al clasificador ni cobrar.
- **FR-021**: La función DEBE poder apagarse por configuración sin desplegar código, y su
  estado DEBE aparecer en el informe de salud junto al de Jev.

**Privacidad y aviso**

- **FR-022**: La primera vez que la persona elige adjuntar una foto en la web, el chat DEBE
  mostrar un aviso: la foto se analiza con un servicio externo (TypeSafe, EE. UU.), no se
  guarda, y no debe mostrar personas ni datos personales. El aviso DEBE poder releerse.
- **FR-023**: Los logs, trazas y el libro de llamadas de Jev DEBEN registrar solo metadatos
  (tipo elegido, confianzas, latencia, tokens, resultado), nunca la imagen ni el texto de la
  persona.

**WhatsApp**

- **FR-024**: El puente de WhatsApp DEBE aceptar una foto adjunta identificada por el nombre
  del archivo que OpenClaw deja en su carpeta de adjuntos, leerla solo de esa carpeta
  configurada y rechazar nombres con rutas, enlaces simbólicos o extensiones distintas de
  imagen, sin leer nada fuera de ella.
- **FR-025**: La respuesta de WhatsApp DEBE contener la misma descripción y las mismas
  referencias que la web para la misma clasificación, con el formato de ese canal, y respetar
  la identidad por remitente, el cupo y la idempotencia que el puente ya aplica.
- **FR-026**: La guía de despliegue de OpenClaw DEBE indicar cómo dar al puente acceso de
  solo lectura a la carpeta de adjuntos y qué instrucción del agente pasa la foto.

**Calidad**

- **FR-027**: Un conjunto de evaluación versionado (al menos 20 fotos con licencia libre y su
  resultado esperado) DEBE medir el acierto del tipo, la temperatura y la recomendación; su
  ejecución con el clasificador real es manual y acotada (créditos), y con un clasificador
  simulado corre en las pruebas.

### Key Entities

- **Foto preparada**: imagen enderezada, sin metadatos y escalada; vive solo en memoria
  durante el turno.
- **Perfil del alimento**: resultado de la clasificación: probabilidad de comida, tipo,
  consistencia, temperatura y porción, cada uno con su confianza y su segunda opción.
- **Requisitos de empaque**: derivados del perfil por la tabla determinista: usos y familias
  aceptables, aplicación de la guía de materiales, temperatura y necesidad de cierre.
- **Sugerencia**: referencia del catálogo que cumple los requisitos, con la razón (aptitud
  del material) y su ficha.
- **Turno de foto**: el intercambio guardado en el hilo: marca «Foto de un alimento», texto
  opcional, perfil (sin imagen) y respuesta con las sugerencias.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: En el conjunto de evaluación, el tipo de alimento es correcto en al menos el
  85 % de las fotos de comida, y el 100 % de las fotos que no son comida se rechazan o se
  responden con una pregunta.
- **SC-002**: Ninguna sugerencia del conjunto de evaluación usa un material que la guía
  declare «no apto» para el uso del alimento o para su temperatura (0 casos).
- **SC-003**: El 100 % de las referencias sugeridas existen en el catálogo publicado del país
  de la persona y llevan su ficha.
- **SC-004**: La persona recibe la sugerencia en menos de 5 segundos desde que envía la foto
  en el 95 % de los casos con el clasificador disponible.
- **SC-005**: Ninguna foto, ni sus metadatos, queda guardada en el servidor ni en las trazas
  después del turno (verificable por inspección de almacenamiento y trazas).
- **SC-006**: Con el clasificador no disponible, el chat web no ofrece fotos en el 100 % de
  los casos, y un fallo durante el envío nunca descuenta cupo.
- **SC-007**: Una conversación «foto → cotízame N del primero» produce una cotización de la
  referencia correcta en las pruebas de extremo a extremo con clasificador simulado.

## Assumptions

- El clasificador es Jev (`jev-1.14.0`); sus imágenes están en fase «Dreamer-ready» según
  TypeSafe (formato y precisión pueden cambiar entre versiones). La versión queda fijada,
  como en la spec 046.
- La activación en producción la decidió el owner el 2026-11-24 con aviso visible; la
  revisión del DPA de TypeSafe (T063 de la 046) sigue siendo su responsabilidad.
- La función requiere Jev configurado y encendido; si producción apaga Jev, la foto también
  se apaga.
- No se curan datos del catálogo: la aptitud sale de `materiales.csv` y de los usos y rangos
  de temperatura que ya tienen las referencias. Las referencias de Colombia sin temperatura
  propia se juzgan por su material.
- OpenClaw corre en el mismo servidor que el puente (configuración actual del VPS) y deja los
  adjuntos de WhatsApp en una carpeta local que puede montarse en solo lectura. Si OpenClaw
  corre en otra máquina, WhatsApp no ofrece fotos (queda documentado).
- El país de la persona y el cupo diario se resuelven como hoy en cada canal.
- El análisis de video, stickers, varias fotos por mensaje y la localización de objetos en la
  imagen (Box) quedan fuera de esta spec.
