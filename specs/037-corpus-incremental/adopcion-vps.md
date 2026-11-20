# Adopción y poda en el VPS (T056)

> Estado: **ejecutada**, según confirmación del owner. Este registro no incluye
> una captura del `/health` posterior a la ejecución.

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

## Después

El owner confirmó que la adopción y la poda se ejecutaron. No se dispone de los
conteos finales ni de la salida de `/health` inmediatamente posterior para
compararlos con el estado anterior. [T075](tasks.md#phase-7-adopción-documentación-y-pulido)
da seguimiento a esa evidencia histórica, sin repetir la poda.
