# ADR-0003 — Escritura del corpus solo para operadores

- **Estado:** aceptado
- **Fecha:** 2026-11-22
- **Sustituye:** la decisión de 2026-11-14 que hacía pública la carga desde
  Configuración (y la captura web pública de la spec 025).

## Contexto

La auditoría de `main` en `a21425c` confirmó que `POST /configuracion` y
`POST /configuracion/captura` solo exigían el token CSRF que el propio
visitante recibía al abrir la página. Cualquier visitante podía, por tanto,
escribir Bronze/Silver/Gold y sincronizar PostgreSQL y Qdrant, es decir,
alimentar el corpus que usa el chat.

El repositorio no tenía autenticación de usuarios en `main`: la identidad del
chat es anónima y el stack OIDC de la spec 029 no está implementado.

## Decisión

1. La escritura web exige un token compartido de despliegue
   (`OPERATOR_TOKEN`, con `CONFIG_TOKEN` como alias) y conserva el CSRF como
   control independiente. La autorización se evalúa antes de leer archivos o
   crear el job de captura.
2. No se introduce un sistema de cuentas. El token es un único secreto de
   operador; la sesión del navegador es una cookie HMAC de ocho horas. Cambiar
   el token invalida todas las sesiones.
3. La carga y la captura web solo escriben Gold. La publicación a PostgreSQL
   y Qdrant sigue siendo `carvajal sync`, después de revisar el lote; así se
   reutiliza la curaduría que ya existía para la CLI (`--solo-extraer`,
   `--solo-gold`, `sync --verify`).
4. Configuración y Captura salen de la navegación pública. La lectura del
   chat, FAQ, Cotizador, corpus, documentos y citas no cambia.
5. Sin `OPERATOR_TOKEN`, la escritura web queda cerrada y la operación se hace
   por la CLI interna, que exige acceso al servidor o al contenedor.

## Consecuencias

- Un visitante anónimo recibe `403` y no inicia ingesta, captura ni
  indexación.
- El operador puede usar la web con token o la CLI; la publicación es un paso
  explícito y revisable.
- Hay que custodiar `OPERATOR_TOKEN` y `SESSION_SECRET` en `.env` (`600`) y
  rotarlos si se sospecha exposición.
- Quedan fuera de esta decisión el acceso directo al volumen `appdata`, a
  PostgreSQL/Qdrant y los comandos CLI: su frontera es el acceso al servidor.
- El procedimiento operativo y la tabla de rutas están en
  [`docs/seguridad-operacion-corpus.md`](../seguridad-operacion-corpus.md).
