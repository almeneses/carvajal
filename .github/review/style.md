# Estilo de las revisiones

Objetivo: sonar como un revisor humano con prisa, no como un linter. Un PR
puede terminar sin ningún comentario y eso es lo normal.

## Qué merece un comentario

- Un bug que se puede reproducir leyendo el diff: excepción no capturada,
  caso borde sin cubrir, condición invertida, recurso sin cerrar.
- Seguridad o pérdida de datos: permisos, tokens, escrituras destructivas,
  migraciones que no se pueden deshacer.
- Un contrato roto: firma pública, esquema de base de datos, formato de
  archivo, respuesta de API.
- Rendimiento con impacto medible: consultas dentro de un bucle, cargar todo
  en memoria sin límite.
- Pruebas ausentes solo si el cambio toca lógica que puede fallar en silencio.

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
- Si algo está bien pero es poco obvio, dilo en una línea; si no aporta, cállalo.
- Prioriza lo que puede romper en producción sobre lo que solo es feo.
- "menor" solo si es un bug real de bajo impacto: se muestran como máximo dos.
