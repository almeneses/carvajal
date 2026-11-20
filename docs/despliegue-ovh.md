# Despliegue demo en OVH (por IP, sin dominio)

Esta guía despliega Carvajal en el VPS `144.217.80.136` como **demo por IP**:

```text
Internet
   │  http://144.217.80.136:8080
   ▼
contenedor app ── red Docker interna ── db, qdrant, ollama
```

No hay dominio ni TLS. La app se publica directamente en el puerto `8080`
(configurable con `APP_PORT`). El servidor ya tiene nginx ocupando 80/443 con
otros sitios: **no se toca nginx ni se apaga**.

Flujo automático desde `main`:

```text
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

El VPS no construye la imagen ni hace `git pull`: descarga un tag inmutable de
GHCR y aplica el compose versionado.

> **Contexto del host:** es un servidor compartido con nginx (538 sitios),
> PostgreSQL 12, Odoo y certbot. No detengas nginx para poner Caddy: tumbarías
> todos esos sitios. Para la demo se usa el puerto 8080 directamente.

## 1. Bootstrap del VPS (root, una vez)

El usuario `deploy` ya existe y tiene tu clave SSH, pero no está en el grupo
`docker`, `/opt/carvajal` no existe y `/opt` es de root. Ejecuta como root:

```bash
usermod -aG docker deploy
mkdir -p /opt/carvajal/data/gold /opt/carvajal/backups
chown -R deploy:deploy /opt/carvajal
```

Cierra la sesión y vuelve a entrar como `deploy` para que aplique el grupo:

```bash
ssh -p 2422 deploy@144.217.80.136
id
# Debe aparecer el grupo docker
docker ps
```

Si `docker ps` sigue fallando, revisa que el socket sea accesible:

```bash
ls -l /var/run/docker.sock
```

### Compose v2 (sin root)

El servidor tiene Docker 20.10 y `docker-compose` v1.25, que no entiende el
`name:` del compose nuevo. Instala el plugin v2 en tu usuario:

```bash
mkdir -p ~/.docker/cli-plugins
curl -fsSL https://github.com/docker/compose/releases/download/v2.24.7/docker-compose-linux-x86_64 \
  -o ~/.docker/cli-plugins/docker-compose
chmod +x ~/.docker/cli-plugins/docker-compose
docker compose version
```

Debe imprimir `Docker Compose version v2.24.7` o similar. Si `docker compose`
sigue diciendo que no es un comando, revisa que el binario esté en
`~/.docker/cli-plugins/docker-compose` y que tenga permiso de ejecución.

## 2. Crear el `.env`

Copia la plantilla desde tu máquina:

```bash
# LOCAL
scp .env.example deploy@144.217.80.136:/opt/carvajal/.env
```

Genera los secretos en el VPS:

```bash
# VPS
cd /opt/carvajal
chmod 600 .env

openssl rand -hex 32   # SESSION_SECRET
openssl rand -hex 24   # POSTGRES_PASSWORD
nano .env
```

Valores mínimos:

```dotenv
APP_PORT=8080
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=tu_clave_de_deepseek

SESSION_SECRET=pega_aqui_el_hex_de_32
POSTGRES_USER=carvajal
POSTGRES_PASSWORD=pega_aqui_el_hex_de_24
POSTGRES_DB=carvajal
```

Notas:

- No definas `OLLAMA_BASE_URL`: el compose usa `http://ollama:11434`.
- No definas `FORWARDED_ALLOW_IPS`: el compose usa `127.0.0.1` porque no hay
  proxy delante.
- No cambies `POSTGRES_USER`, `POSTGRES_PASSWORD` ni `POSTGRES_DB` después del
  primer arranque; el volumen ya quedó inicializado.
- `SESSION_SECRET` debe mantenerse estable; si lo cambias, se invalidan las
  cookies de los visitantes.
- **Monitoreo del agente (opcional)**: queda apagado si no defines nada. Para
  encenderlo, añade al `.env` del VPS:

  ```dotenv
  MONITORING_ENABLED=true
  LANGSMITH_API_KEY=lsv2_pt_…
  LANGSMITH_PROJECT=carvajal
  MONITORING_SAMPLE_RATE=0.2
  MONITORING_INCLUDE_CONTENT=false
  ```

  En producción el contenido **no** se envía (`MONITORING_INCLUDE_CONTENT=false`):
  solo metadatos, tokens, latencias y tipos de error. `compose.prod.yml` inyecta
  las variables explícitas y fuerza `LANGSMITH_TRACING`/`LANGCHAIN_TRACING_V2` a
  `false`. Presupuesto: 5.000 trazas al mes del plan gratuito; con 0,2 se cubren
  hasta 25.000 turnos. Para apagarlo, `MONITORING_ENABLED=false` y reiniciar; el
  chat no cambia. Detalle en
  [`docs/monitoreo-langsmith.md`](monitoreo-langsmith.md).

Comprueba permisos y variables sin imprimir secretos:

```bash
ls -l /opt/carvajal/.env
# -rw------- deploy deploy
grep -E '^(APP_PORT|LLM_PROVIDER|POSTGRES_USER|POSTGRES_DB)=' /opt/carvajal/.env
```

## 3. Acceso a GHCR

Si el paquete `ghcr.io/almeneses/carvajal-api` es privado, crea un token clásico
con `read:packages` y autentica una vez en el VPS:

```bash
echo "ghp_TU_TOKEN" | docker login ghcr.io -u almeneses --password-stdin
```

Si haces público el paquete en GitHub → Packages, puedes omitir este paso.

## 4. Secretos de GitHub Actions

En **Settings → Secrets and variables → Actions** del repositorio:

| Secreto | Valor |
|---|---|
| `VPS_HOST` | `144.217.80.136` |
| `VPS_USER` | `deploy` |
| `VPS_SSH_KEY` | contenido de la clave privada dedicada |
| `VPS_HOST_KEY` | línea de `ssh-keyscan` del VPS |
| `VPS_PORT` | `2422` |

La host key de este VPS es:

```text
[144.217.80.136]:2422 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILWS/i9cBQ6m+KiYaIGBbaqu6RE4E5Zly62cVVyoxWPR
```

Puedes generarla de nuevo con:

```bash
ssh-keyscan -p 2422 -t ed25519 144.217.80.136
```

Genera una clave dedicada para Actions y autorízala:

```bash
# LOCAL
ssh-keygen -t ed25519 -C "github-actions@carvajal" -f deploy_key -N ""
ssh-copy-id -i deploy_key.pub -p 2422 deploy@144.217.80.136

gh secret set VPS_HOST --body "144.217.80.136"
gh secret set VPS_USER --body "deploy"
gh secret set VPS_PORT --body "2422"
gh secret set VPS_SSH_KEY < deploy_key
ssh-keyscan -p 2422 -t ed25519 144.217.80.136 | gh secret set VPS_HOST_KEY

rm -f deploy_key deploy_key.pub
gh secret list
```

## 5. Primer despliegue

El workflow vive en `.github/workflows/deploy.yml` y se dispara cuando CI
termina en `main`, o a mano desde **Actions → Despliegue → Run workflow**
(selecciona `main`).

Antes de lanzarlo, verifica que `/opt/carvajal/.env` existe con
`SESSION_SECRET`. El workflow comprueba ese archivo antes de desplegar.

```bash
# LOCAL
gh workflow run deploy.yml --ref main
gh run list --workflow deploy.yml --limit 3
gh run watch <RUN_ID>
```

Mientras corre, revisa en el VPS:

```bash
cd /opt/carvajal
ls -la
docker compose -f compose.prod.yml ps
docker compose -f compose.prod.yml logs --tail=100 app
```

Verifica desde tu máquina:

```bash
curl -fsS http://144.217.80.136:8080/live
# {"status":"ok"}

curl -i http://144.217.80.136:8080/health
```

El primer `/health` puede devolver `503` con `"status":"missing"`: la app está
viva, pero Qdrant aún no tiene el índice. Es esperado; se resuelve en el paso
siguiente.

Si el puerto 8080 no responde desde fuera, revisa el firewall de OVH y que no
haya otro proceso usándolo:

```bash
sudo ss -tulpn | grep ':8080'
```

## 6. Corpus e índice

El corpus no viaja en la imagen. Cópialo y construye el índice una vez (o cuando
cambie `data/gold`):

```bash
# LOCAL
ssh -p 2422 deploy@144.217.80.136 "mkdir -p /opt/carvajal/data/gold"
rsync -av data/gold/ deploy@144.217.80.136:/opt/carvajal/data/gold/
```

```bash
# VPS
cd /opt/carvajal

docker compose -f compose.prod.yml exec ollama ollama pull nomic-embed-text

# Copia el corpus Gold al volumen persistente de la app, cárgalo en Postgres
# y reindexa en Qdrant.
docker compose -f compose.prod.yml up -d app
docker compose -f compose.prod.yml cp /opt/carvajal/data/. app:/app/data/
docker compose -f compose.prod.yml exec app carvajal migrate-gold
docker compose -f compose.prod.yml exec app carvajal index-qdrant --recreate

curl -fsS http://144.217.80.136:8080/health
```

> El volumen `appdata` es la fuente de verdad del corpus una vez que hay cargas
> o recapturas: la copia de `/opt/carvajal/data` puede quedarse atrás. Antes de
> volver a copiar, sincroniza el volumen hacia el host o vuelve a capturar; un
> `docker compose cp` a ciegas sobrescribe los documentos más nuevos.

El índice queda en el volumen `qdrantdata` y sobrevive a reinicios y despliegues.
Las cargas de la pestaña Configuración escriben en el volumen `appdata`
montado en `/app/data`; también sobreviven a los reinicios y a los despliegues.
La captura web usa el mismo volumen y el mismo cierre Gold → PostgreSQL →
Qdrant; es pública en esta fase y se puede restringir con
`CAPTURE_ALLOWED_DOMAINS` (lista separada por coma) en el `.env` del servidor.
`deploy.sh` escribe `APP_IMAGE=ghcr.io/...:<tag>` en `.env`, así que los comandos
manuales de `docker compose` usan la misma imagen que quedó desplegada.

### Carga masiva de una carpeta (PDF, XML, JSON, CSV…)

Para cientos de archivos, la consola es el camino; la web limita a 100 archivos
de 5 MB. Sube la carpeta al host y procésala dentro del contenedor:

```bash
# LOCAL
rsync -av ./mi-corpus/ deploy@144.217.80.136:/opt/carvajal/inbox/
```

```bash
# VPS
cd /opt/carvajal
docker compose -f compose.prod.yml run --rm --no-deps \
  -v /opt/carvajal/inbox:/inbox:ro \
  app python -m apps.api.ingest_cli /inbox --source-type oficial
```

- Recorre subcarpetas, ignora extensiones no admitidas y procesa en lotes de 25
  archivos (100 MB por archivo, `--lote` y `--max-file-mb`; 256 MB por lote con
  `--max-batch-mb`). Cada lote se lee al procesarlo, así que la memoria se acota
  al lote y no al tamaño del corpus.
- Al terminar cada lote actualiza PostgreSQL y Qdrant. `--solo-extraer` se
  detiene en Gold para revisar la extracción antes de tocar la base y el índice.
- `--source-type` fija la procedencia del *front matter*: `oficial` para el sitio
  y documentos de Carvajal, `tercero` para prensa o reguladores, `colaborador`
  para material de un tercero. No cambia la capa: todo entra al núcleo
  (`data/gold`) para que el chat y la FAQ lo usen.
- Los originales quedan en Bronze dentro de `appdata`; la carpeta del host puede
  borrarse después.

## 7. Backups

```bash
# VPS
cat > /opt/carvajal/backup.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd /opt/carvajal
fecha="$(date +%F-%H%M)"
docker compose -f compose.prod.yml exec -T db \
  sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  | gzip > "/opt/carvajal/backups/carvajal-${fecha}.sql.gz"
find /opt/carvajal/backups -name 'carvajal-*.sql.gz' -mtime +14 -delete
EOF

chmod +x /opt/carvajal/backup.sh
/opt/carvajal/backup.sh
ls -lh /opt/carvajal/backups
gunzip -t /opt/carvajal/backups/*.sql.gz
```

Cron diario:

```bash
crontab -e
# 0 3 * * * /opt/carvajal/backup.sh
```

Copia los backups fuera del VPS (restic, rclone o `scp` a otra máquina).

## 8. Operación y rollback

```bash
cd /opt/carvajal
docker compose -f compose.prod.yml ps
docker compose -f compose.prod.yml logs -f --tail=100 app
docker compose -f compose.prod.yml restart app
cat .deployed_tag
df -h
docker system df
```

Rollback manual:

```bash
cd /opt/carvajal
docker images ghcr.io/almeneses/carvajal-api
IMAGE_REPO=ghcr.io/almeneses/carvajal-api bash deploy.sh sha-<tag-anterior>
```

O directamente:

```bash
APP_IMAGE=ghcr.io/almeneses/carvajal-api:sha-<tag-anterior> \
  docker compose -f compose.prod.yml up -d --no-build app
```

Recuerda: el rollback cambia la imagen, **no revierte migraciones**. Mantén
migraciones compatibles hacia atrás.

No uses `docker compose down -v`: borra los volúmenes de Postgres, Qdrant y
las cargas de `appdata`. Incluye `appdata` en el respaldo si quieres conservar
los archivos Bronze/Silver/Gold originales, además del `pg_dump`.

## 9. Seguridad de la demo

- La demo va por **HTTP**, sin TLS. No la uses para datos sensibles.
- **Chat por voz (spec 040):** el dictado exige un contexto seguro del
  navegador (`localhost` o HTTPS). Por la IP en HTTP, el micrófono no se
  ofrece y el chat explica que se puede escribir. La lectura en voz alta sí
  funciona. Para ofrecer dictado en producción hay que publicar el chat por
  HTTPS (vhost con TLS delante); no se rebaja la política para la demo.
  El permiso `microphone=(self)` se declara solo en el documento HTML de
  `/chat`; el resto del sitio y el SSE lo mantienen denegado.
- Si quieres limitar el acceso, restringe el puerto 8080 por IP en el firewall
  de OVH o en un proxy delante.
- `.env` siempre en `600`.
- La carga de documentos y la captura web de `/configuracion` son públicas: no
  hay autenticación. Si no quieres que cualquiera escriba en el corpus,
  restringe `/configuracion` por firewall o proxy.
- No publiques 5432, 6333 ni 11434; el compose solo expone `APP_PORT`.
- Pertenecer al grupo `docker` equivale a root en el host.
- No detengas nginx para poner Caddy: este servidor sirve 538 sitios con nginx.

## 10. Si más adelante pones un dominio

No uses Caddy en este host mientras nginx siga en 80/443. El camino correcto es
un vhost de nginx con certbot:

```nginx
server {
    listen 80;
    server_name TU_DOMINIO;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

En ese caso cambia el puerto del compose a `127.0.0.1:${APP_PORT:-8000}:8000`,
pon `FORWARDED_ALLOW_IPS=127.0.0.1` y emite el certificado con
`sudo certbot --nginx -d TU_DOMINIO`.

## 11. Problemas frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `docker: 'compose' is not a docker command` | Falta el plugin v2 | Instala `~/.docker/cli-plugins/docker-compose` (sección 1) |
| `permission denied` al usar Docker | `deploy` no está en el grupo `docker` | `sudo usermod -aG docker deploy` y reconecta |
| `Falta /opt/carvajal/.env` | No se creó el `.env` | Sección 2 |
| `denied` al hacer `docker compose pull` | Falta login a GHCR | Sección 3 |
| `http://IP:8080` no responde | Firewall de OVH o puerto ocupado | Abre 8080 en OVH; `sudo ss -tulpn \| grep ':8080'` |
| `/health` con `"status":"missing"` | Qdrant sin colección | Sección 6 |
| `model not found` | Falta el modelo de embeddings | `docker compose exec ollama ollama pull nomic-embed-text` |
| El despliegue falla en migraciones | Migración incompatible o base caída | `logs db` y `logs app`; el tag anterior sigue activo |
| Disco lleno | Imágenes y capas antiguas | `docker system df` y `docker image prune -a` |

## 12. Pendientes del host (fuera de Carvajal)

Detectados en la auditoría y aplazados:

- PostgreSQL 12 escucha en `*:5432` y es alcanzable desde Internet.
- El servicio `snap.certbot.renew.service` lleva horas atascado al 91 % CPU y
  tiene ~995 MB de logs; falla en `opticacolor.maidensoft.co`, que apunta a otro
  servidor.
- Ubuntu 20.04 está al final de su soporte estándar.
- UFW está deshabilitado y fail2ban no está instalado.
