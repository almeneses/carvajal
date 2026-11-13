# Estilo de las revisiones

Reporta defectos P0/P1 que el PR introduce y que el autor puede corregir.

## Qué merece un comentario

- Una regresión de impacto material con un escenario respaldado por el diff.
- Seguridad o pérdida de datos: permisos, tokens, escrituras destructivas,
  migraciones que no se pueden deshacer.
- Un contrato roto: firma pública, esquema de base de datos, formato de
  archivo, respuesta de API.
- Rendimiento con impacto medible: consultas dentro de un bucle, cargar todo
  en memoria sin límite.

## Qué no se comenta

- Estilo, formato, nombres, orden de imports: los cubre Ruff.
- Redacción de documentación, comentarios o mensajes de error.
- Refactors, patrones que faltan, "esto quedaría más limpio".
- Riesgos hipotéticos que hoy no ocurren.
- El mismo problema repetido en varias líneas del mismo archivo.

## Forma

- Español neutro y directo. Frases cortas. Sin emojis ni superlativos.
- Máximo dos frases por hallazgo: qué se rompe y cómo arreglarlo.
- Nombra el término técnico correcto y explícalo en lenguaje cotidiano la
  primera vez que aparece: "timeout (dejar de esperar tras X segundos)".
- No supongas que quien lee el comentario conoce el dominio del proyecto.
