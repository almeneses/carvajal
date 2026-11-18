# Adopción y poda en el VPS (T056)

> Estado: **bloqueada**. La adopción está implementada y probada, pero ejecutarla
> en producción exige desplegar antes una release con la spec 037 y este goal no
> toca `main`.

## Antes (medido el 2026-11-18)

Producción en `144.217.80.136` (`/opt/carvajal`, `compose.prod.yml`):

| Dato | Valor |
|---|---|
| Imagen | `ghcr.io/almeneses/carvajal-api:sha-7938986…` (release spec-036.1) |
| `GET /health` | `{"status":"ok","documents":1226,"chunks":41428,"index":{"status":"ok","collection":"carvajal_chunks","points":41295,"dimensions":768}}` |
| `carvajal sync --help` | no conoce `--adopt` (código anterior a la 037) |

## Por qué no se ejecutó

El procedimiento de T056 es `carvajal sync --adopt` y después
`carvajal sync --prune`, pero la imagen desplegada no incluye la 037: el comando
no existe. Publicar la imagen nueva requiere fusionar `dev` en `main` y esperar
el flujo de release y despliegue, y este goal tiene prohibido tocar `main`.

## Después (pendiente)

Cuando la release con la 037 esté desplegada:

1. `carvajal sync --adopt` y guardar el conteo de adoptadas, archivadas y sin
   cambios.
2. `carvajal sync --layer all --verify` para ver las diferencias sin escribir.
3. `carvajal sync --layer all --prune` y registrar el nuevo `/health`.
4. Comparar documentos, fragmentos y puntos con la tabla de arriba.
