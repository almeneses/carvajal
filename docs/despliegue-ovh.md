# Despliegue demo en OVH (por IP, sin dominio)

Esta guía despliega Carvajal en un VPS como **demo por IP**. `203.0.113.10` es una IP
de ejemplo (RFC 5737): sustitúyela por la del servidor en cada orden.

Esquema:

```text
Internet
   │  http://203.0.113.10:8080
   ▼
contenedor app ── red Docker interna ── db, qdrant, ollama
```

No hay dominio ni TLS. La app se publica directamente en el puerto `8080`
(configurable con `APP_PORT`). El servidor ya tiene nginx ocupando 80/443 con
otros sitios: **no se toca nginx ni se apaga**.

Flujo automático desde `main`:

```text
merge a main (con Calidad en verde)
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
ssh -p 2422 deploy@203.0.113.10
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
scp .env.example deploy@203.0.113.10:/opt/carvajal/.env
```

Genera los secretos en el VPS:

```bash
# VPS
cd /opt/carvajal
chmod 600 .env

openssl rand -hex 32   # SESSION_SECRET
openssl rand -hex 32   # OPERATOR_TOKEN
openssl rand -hex 24   # POSTGRES_PASSWORD
nano .env
```

Valores mínimos:

```dotenv
APP_PORT=8080
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=tu_clave_de_deepseek
DEEPSEEK_MODEL=deepseek-flash
AGENT_MODE=tools

SESSION_SECRET=pega_aqui_el_hex_de_32
OPERATOR_TOKEN=pega_aqui_el_hex_de_32
POSTGRES_USER=carvajal
POSTGRES_PASSWORD=pega_aqui_el_hex_de_24
POSTGRES_DB=carvajal
```

Notas:

- No definas `OLLAMA_BASE_URL`: el compose usa `http://ollama:11434`.
- `AGENT_MODE=tools` (agente con herramientas) exige `LLM_PROVIDER=deepseek`; con otro
  proveedor el chat sigue en `routes`. `deepseek-flash` es el modelo con el que se evaluó y
  decidió el modo `tools` (ver *Modo del agente* en §8).
- No definas `FORWARDED_ALLOW_IPS` mientras no haya proxy delante: el compose usa
  `127.0.0.1`. Con nginx y TLS delante, sigue la §10 (la IP correcta es la puerta de
  enlace de la red de Docker, no `127.0.0.1`).
- No cambies `POSTGRES_USER`, `POSTGRES_PASSWORD` ni `POSTGRES_DB` después del
  primer arranque; el volumen ya quedó inicializado.
- `SESSION_SECRET` debe mantenerse estable; si lo cambias, se invalidan las
  cookies de los visitantes.
- `OPERATOR_TOKEN` habilita la carga y la captura web para el operador. Si lo
  dejas vacío, esas rutas rechazan toda escritura y solo queda la CLI interna.
  `CONFIG_TOKEN` se acepta como alias. El procedimiento completo está en
  [`seguridad-operacion-corpus.md`](seguridad-operacion-corpus.md).
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
| `VPS_HOST` | `203.0.113.10` |
| `VPS_USER` | `deploy` |
| `VPS_SSH_KEY` | contenido de la clave privada dedicada |
| `VPS_HOST_KEY` | línea de `ssh-keyscan` del VPS |
| `VPS_PORT` | `2422` |

La línea de la host key tiene esta forma:

```text
[203.0.113.10]:2422 ssh-ed25519 <clave pública del VPS>
```

Puedes generarla de nuevo con:

```bash
ssh-keyscan -p 2422 -t ed25519 203.0.113.10
```

Genera una clave dedicada para Actions y autorízala:

```bash
# LOCAL
ssh-keygen -t ed25519 -C "github-actions@carvajal" -f deploy_key -N ""
ssh-copy-id -i deploy_key.pub -p 2422 deploy@203.0.113.10

gh secret set VPS_HOST --body "203.0.113.10"
gh secret set VPS_USER --body "deploy"
gh secret set VPS_PORT --body "2422"
gh secret set VPS_SSH_KEY < deploy_key
ssh-keyscan -p 2422 -t ed25519 203.0.113.10 | gh secret set VPS_HOST_KEY

rm -f deploy_key deploy_key.pub
gh secret list
```

## 5. Primer despliegue

El workflow vive en `.github/workflows/deploy.yml` y se dispara cuando CI
termina en `main`, o a mano desde **Actions → Despliegue → Run workflow**
(selecciona `main`).

Antes de lanzarlo, verifica que `/opt/carvajal/.env` existe con
`SESSION_SECRET` y, si se usará la carga o la captura web, con
`OPERATOR_TOKEN`. El workflow comprueba ese archivo antes de desplegar.

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
curl -fsS http://203.0.113.10:8080/live
# {"status":"ok"}

curl -i http://203.0.113.10:8080/health
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

El corpus Gold no viaja en la imagen: se copia al volumen desde el host. El
catálogo de productos y el aprovisionamiento comercial sí vienen en la imagen
para que el bootstrap pueda sembrar un volumen vacío o completar uno de la 042.
Copia el corpus y construye el índice con el perfil `bootstrap`, que descarga
el modelo de embeddings, copia el corpus al volumen solo si aún está vacío,
aplica migraciones, importa Gold a Postgres y reindexa en Qdrant. Es
idempotente.

```bash
# LOCAL: solo las capas que la demo necesita (núcleo, catálogo e indicadores).
ssh -p 2422 deploy@203.0.113.10 "mkdir -p /opt/carvajal/data"
rsync -av data/gold data/catalog data/indicators.json \
  deploy@203.0.113.10:/opt/carvajal/data/
```

```bash
# VPS
cd /opt/carvajal
docker compose -f compose.prod.yml --profile bootstrap run --rm bootstrap
curl -fsS http://203.0.113.10:8080/health
```

Desde un clon del repositorio (no solo en el VPS) el mismo paso es:

```bash
make prod-bootstrap
```

> El volumen `appdata` es la fuente de verdad del corpus una vez que hay cargas
> o recapturas: la copia de `/opt/carvajal/data` puede quedarse atrás. El
> servicio `bootstrap` solo copia `./data` cuando el volumen no tiene Gold, y
> añade desde la imagen los archivos de catálogo y comercial que falten sin
> pisar los existentes; si quieres refrescar el corpus, sincroniza el volumen
> hacia el host o vuelve a capturar antes de reindexar.

### Arranque nuevo y actualización (spec 043)

El bootstrap compara la versión del aprovisionamiento con
`EXPECTED_COMMERCIAL_VERSION` (por defecto `2026-11-25`); si no coincide,
falla antes de tocar la base. En una actualización desde la 042 siembra
`data/commercial/proveedores.csv` aunque el volumen ya exista, aplica la
migración `0009 → 0010` y conserva Gold, conversaciones, mensajes y
cotizaciones.

`deploy.sh` sincroniza el aprovisionamiento comercial en cada despliegue, antes de
migrar:

- **`catalog/`** es dato curado en el repositorio y manda el de la imagen. Siembra lo
  que falta y **reemplaza lo que difiere**. Antes de reemplazar, respalda el archivo
  del volumen en `data/respaldos/catalogo-<fecha>-<tag>/`. El log lista cada archivo
  como `sembrado:` o `actualizado:`.
- **`commercial/` e `institucional/`** solo siembran lo que falta. La evidencia de
  disponibilidad se registra en el servidor y nunca se pisa.

Luego compara la versión con `EXPECTED_COMMERCIAL_VERSION` y ejecuta
`python -m carvajal.commercial validar`. Si algo falla, restaura el catálogo
desde el respaldo y se detiene sin tocar la versión en marcha. Si el despliegue
falla más tarde (migraciones, `/live` o `/health`), `rollback` también restaura el
catálogo antes de volver a la imagen anterior. No reindexa: el bootstrap completo
solo hace falta en un servidor limpio o si cambia el corpus.

> Hasta el 2026-11-25 la siembra **no pisaba** archivos existentes. Como las PRs #256,
> #257 y #258 comparten la versión `2026-11-25`, producción siguió con el `precios.csv`
> de la #256 bajo el código de la #258 y no cotizaba los precios de referencia en ninguna
> ciudad. El primer despliegue con esta corrección reemplaza esos archivos y deja el
> respaldo en `data/respaldos/`. Los respaldos no se borran solos; límpialos a mano cuando
> ya no hagan falta.

`/health` publica la versión y los conteos que debe ver el despliegue:

```bash
curl -fsS http://127.0.0.1:8080/health | python -m json.tool
```

```json
{
  "status": "ok",
  "catalog": {
    "status": "ok",
    "version": "2026-11-25",
    "products": 298,
    "offers_co": 29,
    "offers_mx": 98,
    "sites": 29
  }
}
```

Rutas y accesos que el despliegue debe conservar:

| Ruta | Estado en la 043 |
| --- | --- |
| `/` | Redirige `303` a `/chat`. |
| `/faq` | Redirige `303` a `/chat`; `POST /api/faq` sigue disponible para sus consumidores. |
| `/cotizador` | Sin identidad redirige `303` a `/chat`; con `?thread=` abre el histórico autorizado. |
| `/cotizador/stream` y `/cotizador/nuevo` | Redirigen `303` a `/chat`. |
| `/cotizador/ficha/{slug}` | Responde `404`: las fichas de la demo se retiraron. |
| Historial `cot:<owner_key>` | Solo lo lee su dueño; otro hilo responde `404` en `/chat` y `/cotizador`. |

El arnés de navegador verifica ese contrato después de un arranque limpio o de
una actualización:

```bash
mkdir -p /tmp/cv043 && cd /tmp/cv043
npm init -y && npm install playwright-core
cd -  # raíz del repo
BASE_URL=http://127.0.0.1:8080 \
EXPECTED_CATALOG_VERSION=2026-11-18 \
NODE_PATH=/tmp/cv043/node_modules \
  node tests/browser/arranque_043.cjs
```

Salta con código distinto de cero si falta la versión esperada, el índice, una
redirección o el aislamiento del histórico. No se ejecuta en `make check` ni
en CI porque requiere Chrome.

Para reindexar sin volver a importar (por ejemplo, tras cambiar el modelo de
embeddings), usa `--recreate` en la app desplegada:

```bash
docker compose -f compose.prod.yml exec app carvajal index-qdrant --recreate
```

El índice queda en el volumen `qdrantdata` y sobrevive a reinicios y despliegues.
Las cargas de la pestaña Configuración escriben en el volumen `appdata`
montado en `/app/data`; también sobreviven a los reinicios y a los despliegues.
La carga y la captura web exigen `OPERATOR_TOKEN` y solo dejan documentos en
Gold: la publicación a PostgreSQL y Qdrant es un paso posterior con
`carvajal sync --layer core`, después de revisar el lote. Se puede restringir
además el alcance de red con `CAPTURE_ALLOWED_DOMAINS` (lista separada por
coma) en el `.env` del servidor.
`deploy.sh` escribe `APP_IMAGE=ghcr.io/...:<tag>` en `.env`, así que los comandos
manuales de `docker compose` usan la misma imagen que quedó desplegada.

### Carga masiva de una carpeta (PDF, XML, JSON, CSV…)

Para cientos de archivos, la consola es el camino; la web limita a 100 archivos
de 5 MB. Sube la carpeta al host y procésala dentro del contenedor:

```bash
# LOCAL
rsync -av ./mi-corpus/ deploy@203.0.113.10:/opt/carvajal/inbox/
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
- Al terminar cada lote actualiza PostgreSQL y Qdrant. Para fuentes nuevas,
  usa `--solo-extraer`: se detiene en Gold para revisar la extracción antes de
  tocar la base y el índice; después publica con
  `carvajal sync --layer core`.
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

### Modo del agente (spec 044)

`AGENT_MODE` elige el agente del chat: `tools` (agente con herramientas, **por defecto**
desde la decisión del owner del 2026-11-23) o `routes` (enrutador por reglas, interruptor
de emergencia). No hay migraciones ni cambios de datos: cambiar de modo es editar el
`.env` del servidor y recrear la app.

El modo `tools` **solo arranca con `LLM_PROVIDER=deepseek`** y DeepSeek completo
(`DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` y `DEEPSEEK_BASE_URL`). Con `LLM_PROVIDER=ollama` o
`claude` el chat sigue en `routes` aunque haya una clave de DeepSeek, para no enviar los
turnos a DeepSeek sin decidirlo. La decisión se tomó con `deepseek-flash`, el modelo
evaluado (`evidencia.md` §10).

**Activación (una vez, tras desplegar la versión con `tools` por defecto).** Un `.env`
copiado de `.env.example` antes de esa versión trae `AGENT_MODE=routes` y
`DEEPSEEK_MODEL=deepseek-chat` escritos, y el valor por defecto del compose no los pisa.
Ajústalos de forma explícita:

```bash
cd /opt/carvajal
grep -E '^(AGENT_MODE|LLM_PROVIDER|DEEPSEEK_MODEL)=' .env      # sin imprimir la clave
sed -i 's/^AGENT_MODE=.*/AGENT_MODE=tools/' .env
grep -q '^AGENT_MODE=' .env || echo 'AGENT_MODE=tools' >> .env
sed -i 's/^DEEPSEEK_MODEL=.*/DEEPSEEK_MODEL=deepseek-flash/' .env
grep -q '^DEEPSEEK_MODEL=' .env || echo 'DEEPSEEK_MODEL=deepseek-flash' >> .env
grep -q '^LLM_PROVIDER=deepseek$' .env || echo 'LLM_PROVIDER no es deepseek: tools no arrancará'
docker compose -f compose.prod.yml up -d app
curl -fsS http://127.0.0.1:8080/health | python3 -m json.tool | grep -A3 '"agent"'
```

`/health` publica el bloque `agent` sin construir el runtime: `mode_expected` debe ser
`tools`. Si dice `routes` con `mode_configured=tools`, `tools_unavailable_reason` da el
motivo (`llm_provider`, `missing_deepseek_api_key`, `missing_deepseek_model` o
`missing_deepseek_base_url`). El runtime se construye en la primera pregunta: haz una
pregunta en el chat y confirma el modo efectivo y el modelo en el log:

```bash
docker compose -f compose.prod.yml logs --no-log-prefix app \
  | grep -E '"event": "agent\.(runtime_ready|tools_mode_unavailable)"'
# esperado: "mode": "tools", "tools_model": "deepseek-flash"
```

Si en lugar de `runtime_ready` con `"mode": "tools"` aparece
`agent.tools_mode_unavailable`, su campo `reason` dice qué falta. Con un motivo de
configuración es un aviso (`warning`) y el chat sigue en `routes`, sin errores ni 503;
`reason=build_failed` es un error real (`error`, con traza).

**Volver a `routes`** (ante una violación de material o de datos sin fuente, una tasa de
respaldo alta o un aumento de «No útil»). Exporta antes los logs del día (ver abajo):
recrear la app borra los del contenedor anterior.

```bash
cd /opt/carvajal
sed -i 's/^AGENT_MODE=.*/AGENT_MODE=routes/' .env
grep -q '^AGENT_MODE=' .env || echo 'AGENT_MODE=routes' >> .env
docker compose -f compose.prod.yml up -d app
docker compose -f compose.prod.yml logs --no-log-prefix app \
  | grep -F '"event": "agent.runtime_ready"'                 # "mode": "routes"
```

Para regresar a `tools`, repite la activación. Los hilos se conservan: ambos modos
comparten checkpointer y Store.

#### Período de observación

Dura **dos semanas desde el día en que `agent.runtime_ready` muestra `"mode": "tools"` en
producción** (previsto del 2026-11-23 al 2026-12-07). Anota esa fecha real y la de cierre
en `specs/044-agente-tool-calling/evidencia.md` §10; cada día que el servidor siga en
`routes` no cuenta. Quien revisa es el owner y lo apunta en el registro de §10.

1. **Export diario de logs** (los de Docker no sobreviven a recrear el contenedor):

   ```bash
   mkdir -p /opt/carvajal/obs-044
   docker compose -f compose.prod.yml logs --no-log-prefix --since 24h app \
     > "/opt/carvajal/obs-044/$(date +%F).jsonl"
   ```

2. **Tasa de respaldo** (< **2 %** de los turnos del modo `tools`, acumulada desde el
   inicio):

   ```bash
   cd /opt/carvajal/obs-044
   tools=$(cat ./*.jsonl | grep -F '"event": "chat.turn_completed"' | grep -F '"mode": "tools"')
   total=$(printf '%s\n' "$tools" | grep -c .)
   respaldo=$(printf '%s\n' "$tools" | grep -cF '"path": "fallback"')
   echo "respaldo: $respaldo / $total"
   ```

3. **Revisión de turnos** (**cero** violaciones de material y **cero** datos sin fuente).
   El monitoreo de producción solo lleva metadatos y `chat.turn_completed` solo conteos,
   así que el texto se lee de los mensajes guardados en Postgres. Cada día se toman hasta
   10 hilos al azar con turnos `tools` que no sean atajo (todos si hay menos):

   ```bash
   grep -hF '"event": "chat.turn_completed"' "/opt/carvajal/obs-044/$(date +%F).jsonl" \
     | grep -F '"mode": "tools"' | grep -vF '"path": "shortcut"' \
     | grep -o '"thread_id": "[^"]*"' | cut -d'"' -f4 | sort -u | shuf -n 10
   ```

   Por cada hilo de la muestra:

   ```bash
   hilo=<thread_id>
   docker compose -f compose.prod.yml exec -T db sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"' <<SQL
   SELECT created_at, role, content, sources FROM messages
   WHERE conversation_id = '$hilo' ORDER BY created_at, id;
   SQL
   ```

   - **Violación de material**: el texto o una tarjeta recomienda una referencia no apta
     para el uso pedido según `check_material` (p. ej. PET para caliente, algo distinto de
     PP para microondas).
   - **Dato sin fuente**: precio, total, cantidad de cajas o paquetes, existencias, plazo,
     sede o cifra financiera que no está en `sources` ni en la cotización con folio del
     turno. El tipo de g-003 (cajas calculadas por el modelo) **cuenta** como dato sin
     fuente, con la misma definición que SC-001 en el informe; si el owner decide
     reclasificarlo, lo deja escrito en §10.
   - Cada hilo revisado se anota en el registro de §10 (fecha, `thread_id`, resultado).

4. **Marcas «No útil»** (sin aumento de la tasa frente a las dos semanas previas en
   `routes`). Los botones de marca solo aparecen con `MONITORING_ENABLED=true`; si el
   monitoreo está apagado, este criterio no se puede medir y se deja constancia en §10.
   La base se congela el día de la activación con la misma consulta y se anota en §10.
   Sustituye `<INICIO>` por la fecha y hora (UTC) de la activación:

   ```bash
   docker compose -f compose.prod.yml exec -T db sh -c 'psql -U "$POSTGRES_USER" "$POSTGRES_DB"' <<'SQL'
   SELECT CASE WHEN created_at < TIMESTAMPTZ '<INICIO>' THEN '1 routes (base)' ELSE '2 tools' END
            AS ventana,
          count(*) AS marcas,
          count(*) FILTER (WHERE NOT useful) AS no_util,
          round(100.0 * count(*) FILTER (WHERE NOT useful) / nullif(count(*), 0), 1) AS pct_no_util
   FROM chat_feedback
   WHERE created_at >= TIMESTAMPTZ '<INICIO>' - INTERVAL '14 days'
     AND created_at <  TIMESTAMPTZ '<INICIO>' + INTERVAL '14 days'
   GROUP BY 1 ORDER BY 1;
   SQL
   ```

   Se compara `pct_no_util`; con menos de 20 marcas en una ventana el resultado se anota
   como no concluyente.

Si se cumplen los criterios al cierre, se abre T055 (retiro del modo `routes` como camino
principal); si alguno falla, vuelve a `routes` y registra el caso en
`specs/044-agente-tool-calling/evidencia.md` §10.

- Límites: `AGENT_MAX_TOOL_CALLS` (4), `AGENT_TURN_TIMEOUT_S` (45) y `AGENT_MAX_QUOTES` (3).
- Decisión, riesgos aceptados y criterios: `specs/044-agente-tool-calling/evidencia.md` §10.


```bash
cd /opt/carvajal
docker compose -f compose.prod.yml ps
docker compose -f compose.prod.yml logs -f --tail=100 app
docker compose -f compose.prod.yml restart app
cat .deployed_tag
df -h
docker system df
```

Actualización desde la 042:

```bash
cd /opt/carvajal
# 1. Respalda base y cargas antes de migrar.
./backup.sh
# 2. Despliega: deploy.sh siembra el catálogo que falte, lo valida y migra.
IMAGE_REPO=ghcr.io/almeneses/carvajal-api bash deploy.sh sha-<tag-043>
# 3. Verifica versión, conteos y rutas.
curl -fsS http://127.0.0.1:8080/health | python -m json.tool
```

El despliegue conserva los volúmenes; no uses `-v`. Después del arranque,
ejecuta `tests/browser/arranque_043.cjs` como se describe en la sección 6 y
confirma que las conversaciones y cotizaciones anteriores siguen abriendo con
su identidad.

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

- El puerto `8080` va por **HTTP**, sin TLS; el chat público se sirve por HTTPS
  a través del nginx del host (`PUBLIC_BASE_URL`). No la uses para datos sensibles.
- **Chat por voz (spec 040):** el dictado exige un contexto seguro del
  navegador (`localhost` o HTTPS). Por la IP en HTTP, el micrófono no se
  ofrece y el chat explica que se puede escribir. La lectura en voz alta sí
  funciona. Para ofrecer dictado en producción hay que publicar el chat por
  HTTPS (vhost con TLS delante); no se rebaja la política para la demo.
  El permiso `microphone=(self)` se declara solo en el documento HTML de
  `/chat`; el resto del sitio y el SSE lo mantienen denegado.
- **Redirección a HTTPS (`HTTPS_REDIRECT`, activa por defecto):** cuando
  `PUBLIC_BASE_URL` es `https://…` (hoy `https://144-217-80-136.sslip.io`,
  servido por el nginx del host), la app redirige con **307** a
  `PUBLIC_BASE_URL` + ruta + query a quien abre `http://144.217.80.136:8080/…`
  (o el dominio por `:8080`), para que el dictado por voz funcione. No toca
  nginx: lo hace la app (`apps/api/https_redirect.py`). Solo redirige
  navegaciones de navegador: `GET`/`HEAD` con `Accept` que incluye `text/html`,
  sin `HX-Request` y con `Sec-Fetch-Mode` ausente o `navigate`. **Nunca**
  redirige:
  - `/live`, `/health`, `/api/*` (webhook de WhatsApp incluido), `/mcp`,
    `/static/*`, el PDF firmado (`/cotizaciones/*`, que OpenClaw descarga por
    `MEDIA_BASE_URL` en http) ni las fotos del catálogo;
  - peticiones con `Host` de loopback (`127.0.0.1`, `localhost`, `::1`): los
    healthchecks del contenedor, `scripts/deploy.sh` y el ensayo por túnel SSH
    (`ENSAYO_BASE_URL=http://127.0.0.1:8080`) siguen igual;
  - peticiones cuyo `Host` ya es el público (sin puerto o con el puerto de
    `PUBLIC_BASE_URL`), así que no hay bucle aunque `X-Forwarded-Proto` falle;
  - `POST` ni clientes como `curl`/`httpx` (mandan `Accept: */*`).

  **Hilos y cotizaciones de quien ya usaba la IP.** La identidad anónima del
  chat (cookies `carvajal_visitante` y `carvajal_sesion`, más el
  `localStorage` de `identidad.js`) es de cada host: sin más, quien entraba por
  `http://144.217.80.136:8080` llegaría al dominio con una identidad nueva y
  dejaría de ver sus hilos y cotizaciones. Por eso el 307 añade
  `carvajal_traslado`: un código opaco y aleatorio, de un solo uso, que caduca
  en un minuto. La identidad **no viaja en la URL**: se guarda en memoria del
  proceso bajo ese código, así que el log de acceso del nginx del host solo ve
  un código que ya se canjeó o caducó. El host público lo canjea, fija esas
  cookies **solo si el navegador aún no tiene identidad allí** (nunca pisa una
  existente) y vuelve a redirigir a la misma URL sin el parámetro;
  `identidad.js` adopta luego la cookie en su `localStorage`. Riesgos aceptados:
  - si la persona ya había abierto el dominio con otra identidad, conserva la
    del dominio y lo hecho por la IP no se fusiona (sigue visible entrando por
    la IP con `HTTPS_REDIRECT=false` o por el túnel `127.0.0.1`);
  - quien obtenga un código antes de que la persona lo canjee (un minuto, un
    solo uso) adoptaría su identidad anónima, que no es autenticación (ver
    `apps/api/chat/identity.py`);
  - con varios procesos de uvicorn un código emitido en otro no se encuentra y
    no hay traslado; la imagen corre con un solo proceso. Tras un reinicio los
    códigos pendientes se pierden.

  Sin `PUBLIC_BASE_URL`, con `http://…` o con `HTTPS_REDIRECT=false` no hace
  nada. Para desactivarla: `echo 'HTTPS_REDIRECT=false' >> /opt/carvajal/.env`
  y `docker compose -f compose.prod.yml up -d --no-build app`. Comprobación:

  ```bash
  curl -sI -H 'Accept: text/html' http://144.217.80.136:8080/chat | grep -i '^location'
  # location: https://144-217-80-136.sslip.io/chat
  curl -s -o /dev/null -w '%{http_code}\n' http://144.217.80.136:8080/health   # 200
  ```
- **Voz del servidor (spec 047, opcional):** con `VOICE_PROVIDER=openai` y
  `OPENAI_API_KEY` en el `.env`, el dictado y la lectura usan OpenAI a través de
  la app (ver [`voz-web.md`](voz-web.md)); el dictado sigue exigiendo HTTPS. El
  bloque `voice` de `/health` dice si está disponible; cuántas llamadas lleva el
  día y el último error solo se ven con el token de operador
  (`X-Operator-Token`). El límite por IP necesita `FORWARDED_ALLOW_IPS` bien
  puesto: si no, todos comparten la IP de nginx. Sin clave o con el tope
  alcanzado, el chat usa la voz del navegador.
- Si quieres limitar el acceso, restringe el puerto 8080 por IP en el firewall
  de OVH o en un proxy delante.
- `.env` siempre en `600`.
- La carga y la captura web de `/configuracion` exigen `OPERATOR_TOKEN`. La
  página no está en la navegación pública y la escritura se rechaza si el
  token falta. La publicación posterior se hace con la CLI, que requiere
  acceso al servidor o al contenedor. Ver
  [`seguridad-operacion-corpus.md`](seguridad-operacion-corpus.md).
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

En ese caso cambia el puerto del compose a `127.0.0.1:${APP_PORT:-8000}:8000`
(y el `proxy_pass` al mismo puerto) y emite el certificado con
`sudo certbot --nginx -d TU_DOMINIO`.

**`FORWARDED_ALLOW_IPS` detrás de nginx.** Uvicorn corre con `--proxy-headers`
pero solo cree en `X-Forwarded-Proto`/`X-Forwarded-For` si la conexión llega
desde una IP de `FORWARDED_ALLOW_IPS` (por defecto `127.0.0.1`). nginx corre en
el host y se conecta al puerto publicado; Docker reenvía esa conexión al
contenedor desde la **puerta de enlace de la red del compose** (por ejemplo
`172.18.0.1`), no desde `127.0.0.1`. Con el valor por defecto la app ve `http`:
las cookies salen sin `Secure`, no hay HSTS y las redirecciones absolutas
apuntan a `http://`. Obtén la IP y fíjala en el `.env`:

```bash
docker network inspect carvajal-prod_default \
  -f '{{range .IPAM.Config}}{{.Gateway}}{{end}}'      # p. ej. 172.18.0.1
echo 'FORWARDED_ALLOW_IPS=172.18.0.1' >> /opt/carvajal/.env
docker compose -f compose.prod.yml up -d --no-build app
```

Comprueba que funcionó: `curl -sI https://TU_DOMINIO/chat | grep -i
strict-transport-security` debe mostrar la cabecera HSTS y la cookie del chat
debe llevar `Secure`. No pongas `*` ni una subred amplia: cualquiera que llegue
directo al puerto podría falsificar el esquema y la IP del cliente. Con el
puerto publicado solo en `127.0.0.1`, únicamente nginx llega a la app.

## 11. Problemas frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `docker: 'compose' is not a docker command` | Falta el plugin v2 | Instala `~/.docker/cli-plugins/docker-compose` (sección 1) |
| `permission denied` al usar Docker | `deploy` no está en el grupo `docker` | `sudo usermod -aG docker deploy` y reconecta |
| `Falta /opt/carvajal/.env` | No se creó el `.env` | Sección 2 |
| `denied` al hacer `docker compose pull` | Falta login a GHCR | Sección 3 |
| `http://IP:8080` no responde | Firewall de OVH o puerto ocupado | Abre 8080 en OVH; `sudo ss -tulpn \| grep ':8080'` |
| El despliegue hace rollback con `"catalog":{"status":"invalid"}` en `/health` | Al volumen `appdata` le falta el aprovisionamiento comercial (versiones de `deploy.sh` anteriores al 2026-11-24 no lo sembraban) | Despliega con el `deploy.sh` actual; a mano: `APP_IMAGE=<imagen nueva> docker compose -f compose.prod.yml run --rm --no-deps app sh -c 'cp -Rn /app/seed-data/catalog /app/seed-data/commercial /app/data/'` y relanza el workflow |
| `El aprovisionamiento comercial no es válido; no se despliega.` | La versión del catálogo no coincide con `EXPECTED_COMMERCIAL_VERSION` o un archivo del volumen está desactualizado | Revisa la salida de la siembra; la siembra no pisa archivos, así que un archivo viejo del volumen hay que actualizarlo a mano |
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
