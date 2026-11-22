# Adopción y poda en el VPS (T056)

> Estado: **ejecutada** y evidencia recuperada el 2026-11-20.

## Antes del despliegue de la 037 (medido el 2026-11-18)

Producción en `144.217.80.136` (`/opt/carvajal`, `compose.prod.yml`):

| Dato | Valor |
|---|---|
| Imagen | `ghcr.io/almeneses/carvajal-api:sha-7938986…` (release spec-036.1) |
| `GET /health` | `{"status":"ok","documents":1226,"chunks":41428,"index":{"status":"ok","collection":"carvajal_chunks","points":41295,"dimensions":768}}` |
| `carvajal sync --help` | no conoce `--adopt` (código anterior a la 037) |

## Antes de T056, ya con la 037 desplegada

Tras la [release #165](https://github.com/almeneses/carvajal/pull/165), se registraron
2.193 documentos, 77.581 fragmentos y 77.604 puntos en `/health`, con la imagen
`sha-1976b1e…`.

## Después (captura del 2026-11-20)

El owner confirmó que la adopción y la poda se ejecutaron. La captura disponible
del estado posterior es:

| Dato | Valor |
|---|---|
| Imagen | `ghcr.io/almeneses/carvajal-api:sha-3c25e46…` (release spec 038; incluye el fix #166) |
| `GET /health` | `{"status":"ok","documents":2193,"chunks":77581,"index":{"status":"ok","collection":"carvajal_chunks","points":77588,"dimensions":768}}` |

Comparación con la línea base previa a T056 (2.193 documentos, 77.581 fragmentos,
77.604 puntos):

- Documentos y fragmentos: **sin cambios**.
- Puntos en Qdrant: **−16** (77.604 → 77.588), coherente con la poda y la
  reindexación pendiente.
- Qdrant reporta 2.192 documentos con puntos porque `sostenibilidad` no genera
  fragmentos (Markdown vacío); no es un documento perdido.

La verificación posterior detecta documentos legados cuyos payloads no tienen
`chunks_total`; no son huecos reales (por ejemplo, `historia` tiene 29 puntos y
la huella de su texto). No se repitió la poda para fabricar evidencia, como pide
[T075](tasks.md#phase-7-adopción-documentación-y-pulido).
