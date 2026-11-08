# Decisiones de arquitectura (ADR)

Registro de las decisiones de arquitectura del proyecto. Cada archivo documenta
**una** decisión: su contexto, las opciones evaluadas, la elegida y sus
consecuencias.

## Por qué existen

- Evitan re-discutir lo ya decidido: si alguien propone otra vez Kafka, el ADR
  responde con números.
- Explican el *por qué* de lo que dice [`CONSTITUTION.md`](../../CONSTITUTION.md),
  que solo describe el *qué*.
- Sirven de contexto para personas y agentes que llegan nuevos al repositorio.

## Reglas

1. Un ADR no se edita una vez aceptado. Si la decisión cambia, se escribe otro
   que lo **reemplaza** y se actualiza su estado.
2. Se numera en orden: `0001`, `0002`… sin huecos.
3. Estados posibles: `propuesto`, `aceptado`, `rechazado`, `reemplazado por NNNN`.
4. Si ocupa más de una página, son dos decisiones: divídelo.

## Índice

| ADR | Título | Estado | Fecha |
|-----|--------|--------|-------|
| [0001](0001-stack-app-chat.md) | Stack y arquitectura de la app de chat | propuesto | 2026-11-08 |

## Plantilla

```markdown
# NNNN — Título corto y en presente

- **Estado**: propuesto | aceptado | rechazado | reemplazado por NNNN
- **Fecha**: AAAA-MM-DD
- **Reemplaza**: — | NNNN

## Contexto
El problema y las restricciones reales, con números cuando existan.

## Opciones
Las 2–3 alternativas evaluadas y su costo.

## Decisión
Una sola, en presente.

## Consecuencias
Lo bueno y lo que la decisión ata.

## Disparadores para revisar
Los umbrales concretos que justificarían cambiarla.
```
