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
