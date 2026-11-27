# MEMORY.md — Memoria de largo plazo del agente «ventas»

Hechos y decisiones durables. El detalle del día va en la bitácora
(`memory/AAAA-MM-DD.md`). La memoria de cada cliente vive en el backend, no aquí.

## Cómo funciona

- Flujo: WhatsApp → OpenClaw (agente `ventas`) → servidor MCP propio
  (`consultar_carvajal`) → backend de Carvajal (agente LangGraph con DeepSeek, RAG
  sobre Qdrant con citas, Postgres) → respuesta.
- El backend guarda por número la memoria de corto plazo (la conversación) y la de
  largo plazo (preferencias y cotizaciones). `nueva=true` empieza una conversación
  nueva.
- Las respuestas citan la fuente: la web de Carvajal o el catálogo.
- Las cotizaciones traen folio y un PDF. La línea `ADJUNTAR_PDF: <url>` se manda como
  documento adjunto al mismo chat.
- Tras el folio, la herramienta puede ofrecer el pedido en Odoo, pedir nombre y correo
  y mostrarlos con «¿Lo confirmo?». Con el «sí» del cliente, Odoo crea el pedido
  confirmado y envía la confirmación por correo (cuando tenga servidor de correo).

## Decisiones

- 2026-11-23 — `ventas` es un relé: la respuesta al cliente es exactamente el texto de
  consultar_carvajal. Así las cifras y las fuentes salen de un solo lugar, el backend,
  y se pueden verificar.
- 2026-11-24 — Las notas de voz entran transcritas con OpenAI `gpt-4o-mini-transcribe`.
  Se llama con `voz=true` y se responde con un resumen corto dentro de
  `[[tts:text]]`; el texto completo y los enlaces van aparte.
- 2026-11-24 — La voz de las respuestas es OpenAI `gpt-4o-mini-tts`, voz «echo», con la
  instrucción «Habla en español de Colombia, cálido y claro, ritmo pausado.». Solo se
  responde con voz si el cliente escribió con voz (`tts.auto: inbound`). Se probó un
  acento paisa y se descartó el 2026-11-25.
- 2026-11-24 — Las cifras dictadas se normalizan al formato colombiano (1,800 → 1.800)
  antes de consultar.
- 2026-11-24 — La cotización y su PDF van en un mismo mensaje, y los temas ajenos a
  Carvajal reciben una negativa fija.
- 2026-11-26 — Pedidos en Odoo (spec 056): tras el folio, consultar_carvajal ofrece el
  pedido, pide nombre y correo y pregunta «¿Lo confirmo?». `ventas` pasa esas respuestas
  tal cual, también el «sí, confírmalo» (en el backend las leen reglas, sin Jev ni el
  LLM; van a Odoo y el historial las guarda sin el nombre ni el correo), no pide otros
  datos personales, no confirma por el cliente, no da un pedido por hecho sin la
  confirmación de la herramienta y deja el pago y la entrega al asesor.
