# Despliegue en producción (OVH VPS)

Flujo automático desde `main`:

```
merge aprobado a main
        │
        ▼
CI (Calidad + Integración + Base de datos)
        │  verde
        ▼
GitHub Actions construye la imagen y la publica en GHCR con tag sha-<commit>
        │
        ▼
SSH al VPS → migraciones → docker compose up → /live y /health
        │
        └── si falla: rollback al tag anterior
```

El VPS no construye la imagen ni hace `git pull`: solo descarga un tag inmutable
de GHCR y aplica el compose versionado. Así el despliegue es reproducible y el
rollback es cambiar de tag.

## 1. Preparar el VPS (una vez)

Se asume Ubuntu 24.04, acceso `root` por SSH y un dominio disponible. Con
DeepSeek como LLM, 4 GB de RAM bastan para app, Postgres, Qdrant, Caddy y Ollama
calculando solo embeddings. Si vas a usar Ollama también como LLM, sube a 8 GB o
más.

```bash
# En el VPS, como root
adduser deploy
usermod -aG sudo deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy/
```

```bash
# En tu máquina
ssh-copy-id deploy@IP_DEL_VPS
ssh deploy@IP_DEL_VPS
```

Instala Docker, el plugin de Compose y el cortafuegos:

```bash
sudo apt update && sudo apt upgrade -y
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker deploy
sudo apt install -y ufw fail2ban unattended-upgrades
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw enable
```

Cierra la sesión y vuelve a entrar para que el grupo `docker` aplique.

### DNS

Crea un registro `A` (y `AAAA` si el VPS tiene IPv6) para el dominio, por
ejemplo `app.tudominio.com → IP_DEL_VPS`. Caddy necesita que el nombre resuelva
al VPS para emitir el certificado de Let's Encrypt.

### Directorio y secretos

```bash
sudo mkdir -p /srv/carvajal
sudo chown deploy:deploy /srv/carvajal
cd /srv/carvajal
```

Copia `.env.example` desde tu máquina y edítalo en el servidor:

```bash
scp .env.example deploy@IP_DEL_VPS:/srv/carvajal/.env
ssh deploy@IP_DEL_VPS
chmod 600 /srv/carvajal/.env
```

Valores mínimos que debes cambiar en `/srv/carvajal/.env`:

```dotenv
DOMAIN=app.tudominio.com
SESSION_SECRET=<openssl rand -hex 32>
POSTGRES_PASSWORD=<openssl rand -hex 24>
DEEPSEEK_API_KEY=<tu clave>
LLM_PROVIDER=deepseek
FRONTEND_ORIGINS=
```

`POSTGRES_USER` y `POSTGRES_DB` pueden quedarse en `carvajal`; la contraseña no.
No hace falta definir `OLLAMA_BASE_URL`: `compose.prod.yml` apunta al servicio
`ollama` de la red interna.

### Acceso a GHCR

Si el paquete `ghcr.io/almeneses/carvajal-api` es privado, inicia sesión una vez
en el VPS con un token clásico que tenga `read:packages`:

```bash
echo "$GHCR_TOKEN" | docker login ghcr.io -u almeneses --password-stdin
```

Si haces público el paquete en GitHub → Packages, puedes omitir este paso.

### Secretos de GitHub Actions

En **Settings → Secrets and variables → Actions** del repositorio:

| Secreto | Valor |
|---|---|
| `VPS_HOST` | IP o nombre del VPS |
| `VPS_USER` | `deploy` |
| `VPS_SSH_KEY` | contenido de la clave privada dedicada |
| `VPS_HOST_KEY` | línea de `ssh-keyscan` con la clave pública del VPS |
| `VPS_PORT` | opcional; por defecto `22` |

Fija la clave del host para que Actions no confíe en la primera conexión
(`VPS_HOST_KEY` debe empezar por el mismo valor de `VPS_HOST`):

```bash
ssh-keyscan -t ed25519 IP_DEL_VPS
# Copia la línea completa (IP ssh-ed25519 AAAA...) en el secreto VPS_HOST_KEY.
```

Si usas un puerto distinto de 22, el primer campo debe ser `[host]:puerto`.
Genera una clave solo para el despliegue y autorízala en el VPS:

```bash
ssh-keygen -t ed25519 -C "github-actions@carvajal" -f deploy_key -N ""
ssh-copy-id -i deploy_key.pub deploy@IP_DEL_VPS
# Pega deploy_key en el secreto VPS_SSH_KEY y borra el archivo local.
```

## 2. Primer despliegue

El workflow vive en `.github/workflows/deploy.yml` y se dispara cuando CI
termina en `main`, o a mano desde **Actions → Despliegue → Run workflow**
(selecciona `main`). Este segundo camino es el recomendado para el arranque:

1. Completa la sección 1 y deja `/srv/carvajal/.env` listo.
2. Ejecuta el workflow desde `main`.
3. Comprueba `https://DOMAIN/live` y `https://DOMAIN/health`.
4. Descarga el modelo de embeddings e indexa el corpus (siguiente apartado).

El primer `/health` puede devolver `503` con `"status":"missing"` porque Qdrant
aún no tiene la colección. El script lo trata como aviso, no como fallo: el
proceso está vivo y la base responde. La app queda degradada hasta indexar.

### Corpus e índice

El corpus no viaja en la imagen. Cópialo al VPS y construye el índice una vez
(o cuando cambie `data/gold`):

```bash
# En tu máquina
ssh deploy@IP_DEL_VPS "mkdir -p /srv/carvajal/data/gold"
rsync -av data/gold/ deploy@IP_DEL_VPS:/srv/carvajal/data/gold/
```

```bash
# En el VPS
cd /srv/carvajal
docker compose -f compose.prod.yml exec ollama ollama pull nomic-embed-text
docker compose -f compose.prod.yml run --rm \
  -v /srv/carvajal/data:/app/data:ro \
  app carvajal index-qdrant --recreate
curl -fsS https://app.tudominio.com/health
```

El índice queda en el volumen `qdrantdata` y sobrevive a reinicios y despliegues.
`deploy.sh` escribe `APP_IMAGE=ghcr.io/...:<tag>` en `.env`, así que los comandos
manuales de `docker compose` usan la misma imagen que quedó desplegada.

## 3. Flujo automático

`main` está protegida: los cambios llegan por el PR de release aprobado. Al
fusionarlo:

1. CI corre en el commit de merge.
2. Si CI termina en verde, `deploy.yml` construye la imagen con el SHA completo
   y la publica como `sha-<commit>` y `latest`.
3. Copia `compose.prod.yml`, `Caddyfile` y `deploy.sh` a `/srv/carvajal`.
4. `deploy.sh` descarga el tag SHA, aplica `alembic upgrade head`, recrea los
   contenedores y comprueba `/live` y `/health`.
5. Si algo falla, vuelve al tag guardado en `/srv/carvajal/.deployed_tag`.

El workflow se puede relanzar a mano desde `main`; no se dispara desde ramas de
feature ni desde `dev`. Usa el environment `production` de GitHub: si le añades
revisores obligatorios, el despliegue deja de ser automático y espera una
aprobación manual.

## 4. Rollback

Automático: si `/live` no responde o `/health` falla por algo distinto de un
índice ausente, `deploy.sh` restaura el tag anterior.

Manual:

```bash
cd /srv/carvajal
cat .deployed_tag                    # tag que está funcionando
IMAGE_REPO=ghcr.io/almeneses/carvajal-api bash deploy.sh <tag-anterior>
```

La imagen anterior se puede consultar en GitHub → Packages o con:

```bash
docker images ghcr.io/almeneses/carvajal-api
```

**Migraciones:** volver a una imagen anterior no revierte la base. Escribe
migraciones compatibles hacia atrás (*expand/contract*) o restaura un backup si
el cambio fue destructivo.

## 5. Respaldos

Postgres es la fuente de verdad; Qdrant se puede reconstruir desde `data/gold`.

```bash
sudo mkdir -p /srv/backups
sudo chown deploy:deploy /srv/backups
```

`/srv/carvajal/backup.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
cd /srv/carvajal
fecha="$(date +%F-%H%M)"
docker compose -f compose.prod.yml exec -T db \
  sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  | gzip > "/srv/backups/carvajal-${fecha}.sql.gz"
find /srv/backups -name 'carvajal-*.sql.gz' -mtime +14 -delete
```

```bash
chmod +x /srv/carvajal/backup.sh
crontab -e
# 0 3 * * * /srv/carvajal/backup.sh
```

Copia los respaldos fuera del VPS (restic, rclone o `scp` a otra máquina). Un
backup que solo vive en el mismo disco no protege de perder el VPS.

## 6. Operación

```bash
cd /srv/carvajal
docker compose -f compose.prod.yml ps
docker compose -f compose.prod.yml logs -f --tail=100 app caddy
curl -fsS https://app.tudominio.com/live
curl -fsS https://app.tudominio.com/health
```

- `app`, `db` y `qdrant` solo son accesibles dentro de la red de Compose; la
  app se publica en `127.0.0.1:8000` para los healthchecks del host.
- Caddy escucha en `80/443` y guarda certificados en el volumen `caddydata`.
- Los logs JSON salen por stdout; Sentry solo se activa con `SENTRY_DSN`.
- No uses `docker compose down -v`: borra los volúmenes de Postgres y Qdrant.

## 7. Seguridad

- `.env` con permisos `600` y fuera de Git.
- Clave SSH dedicada para Actions; desactiva el acceso por contraseña.
- Solo 22, 80 y 443 abiertos en `ufw` (más 443/UDP para HTTP/3).
- El token de GHCR del VPS debe ser de solo lectura (`read:packages`).
- Pertenecer al grupo `docker` equivale a privilegios de root en el host:
  limita quién entra como `deploy`.
- Mantén el sistema y Docker actualizados (`unattended-upgrades`).

## 8. Problemas frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `Permission denied (publickey)` | `VPS_SSH_KEY` no coincide o falta en `authorized_keys` | Revisa el secreto y `~deploy/.ssh/authorized_keys` |
| `denied` al hacer `docker compose pull` | El VPS no está autenticado en GHCR | `docker login ghcr.io` con `read:packages` |
| Caddy no emite el certificado | DNS no apunta al VPS o 80/443 cerrados | Verifica `dig`, `ufw` y los logs de Caddy |
| `/health` con `"status":"missing"` | Qdrant no tiene la colección | Ejecuta la indexación del apartado 2 |
| `model not found` al preguntar | Falta `nomic-embed-text` en Ollama | `docker compose exec ollama ollama pull nomic-embed-text` |
| El despliegue falla en migraciones | Migración incompatible o base caída | Revisa `logs db` y `logs app`; el tag anterior sigue activo |
| Disco lleno | Imágenes y capas antiguas | `docker system df` y `docker image prune -a` |
