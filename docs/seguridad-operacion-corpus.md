# Seguridad y operación de la escritura del corpus

> Estado: vigente desde 2026-11-22. Sustituye la decisión de 2026-11-14 que
> dejaba la carga y la captura web abiertas a cualquier visitante. La decisión
> está registrada en [ADR-0003](decisions/0003-escritura-corpus-operadores.md).

## Modelo de acceso

El corpus se puede **leer** sin cuenta: chat, FAQ, Cotizador, explorador del
corpus, fichas de documento y citas. La **escritura** del corpus queda en dos
fronteras explícitas:

| Frontera | Quién | Cómo se autoriza |
|----------|-------|------------------|
| Web (`/configuracion`) | Operador del despliegue | Token compartido `OPERATOR_TOKEN` + CSRF |
| Consola | Operador con acceso al servidor o al contenedor | Acceso al shell; no hay token de aplicación |

No se introdujo un sistema de cuentas. El token web es un único secreto de
despliegue, comparado en tiempo constante, y la sesión del navegador es una
cookie firmada de ocho horas (`carvajal_operator`). Cambiar `OPERATOR_TOKEN`
invalida todas las sesiones emitidas. Para automatización se acepta
`X-Operator-Token`, `Authorization: Bearer <token>` y `Authorization: Basic`
solo con el usuario fijo `operador` y el token como contraseña.

`CONFIG_TOKEN` se acepta como alias del token para no romper despliegues que
usaban el mecanismo anterior. Si no hay token configurado, la escritura web
responde `403` y el operador debe usar la consola.

CSRF sigue siendo un control **separado**: todo `POST` exige el token de
formulario aunque el operador ya esté autenticado. La autorización se evalúa
antes de leer archivos, validar URLs o crear un job de captura.

## Rutas y comandos cubiertos

### Web

| Método y ruta | Control | Efecto |
|---------------|---------|--------|
| `GET /configuracion` | Página no enlazada; `403` si no hay `OPERATOR_TOKEN` | Con token y sin sesión muestra solo el acceso de operador; con sesión, la consola |
| `POST /configuracion/acceso` | CSRF + token | Valida el token, emite la cookie y redirige a la consola |
| `POST /configuracion/salir` | CSRF | Borra la cookie de operador |
| `POST /configuracion` | CSRF + operador, antes de leer archivos | Bronze → Silver → Gold; **no** publica a PostgreSQL/Qdrant |
| `POST /configuracion/captura` | CSRF + operador, antes de validar o crear el job | Captura a Gold; **no** publica a PostgreSQL/Qdrant |
| `GET /configuracion/captura/{id}` | Operador | Estado del job |
| `GET /configuracion/captura/{id}/eventos` | Operador | Progreso SSE del job |

La pestaña **Configuración** ya no aparece en la navegación pública. El enlace
directo sigue existiendo para el operador; sin sesión solo se muestra el acceso
de operador y sin token no se procesa nada.

### Lectura pública (sin cambios)

`/chat`, `/chat/stream`, `/faq`, `/cotizador`, `/corpus`,
`/documents/{id}`, `/api/corpus`, `/api/documents/*`, `/api/chat/*` y las
citas del chat siguen disponibles para visitantes anónimos. El chat es el
único camino de respuesta y Qdrant el único índice vectorial.

### Consola

Estos comandos escriben o publican y requieren acceso al servidor:

| Comando | Efecto |
|---------|--------|
| `carvajal capture [--solo-gold]` | Captura; con `--solo-gold` se detiene en Gold |
| `python -m apps.api.ingest_cli <ruta> [--solo-extraer]` | Carga una carpeta; con `--solo-extraer` se detiene en Gold |
| `carvajal sync --layer core [--verify] [--prune]` | Publica Gold en PostgreSQL y Qdrant |
| `carvajal index-qdrant` | Reconstruye el índice vectorial |
| `carvajal import` / `carvajal migrate-gold` | Importan capas o el Gold histórico |
| `carvajal catalog-build` / `catalog-prices` | Regeneran el catálogo curado |

## Procedimiento del operador

### 1. Preparar el token (una vez)

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Guarda el valor en .env como OPERATOR_TOKEN=...
# Reinicia la app (o recrea el contenedor) para que Settings lo lea.
```

Sin `OPERATOR_TOKEN`, la web no permite escribir. La CLI sigue funcionando.

### 2. Cargar o capturar sin publicar

Desde la web, entra a `/configuracion`, pega el token y envía el lote. La
respuesta confirma cuántos documentos quedaron en Gold y recuerda que están
pendientes de publicación. Desde la consola, el camino equivalente es:

```bash
# Archivos locales
python -m apps.api.ingest_cli /ruta/de/la/carpeta --solo-extraer
# o el atajo:
make ingest ARGS="/ruta/de/la/carpeta --solo-extraer"

# Páginas web
carvajal capture --solo-gold --scope oficial
```

### 3. Revisar el Gold

Revisa los archivos nuevos o modificados en `data/gold/`, su *front matter*
(`url`, `source_type`, `captured_at`, `hash`) y el `index.jsonl`. Para detectar
diferencias sin escribir:

```bash
make sync-verify
git diff -- data/gold
```

Las fuentes nuevas no aparecen en el chat ni en el explorador hasta el paso 4.

### 4. Publicar

```bash
make sync          # equivale a carvajal sync --layer core
```

`sync` es idempotente: solo importa e indexa lo nuevo o cambiado. Usa
`carvajal sync --layer core --prune` solo cuando hayas confirmado que los
documentos ausentes deben salir del corpus consultable. Para reconstruir el
índice completo:

```bash
carvajal index-qdrant --layer core
```

### 5. Verificar la lectura pública

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/api/corpus?page_size=1
curl -fsS http://127.0.0.1:8000/api/documents/by-slug/<slug>
```

Abre una cita del chat y comprueba que el panel lateral resuelve el documento
y que «Abrir fuente original» conserva la URL publicada.

## Rotación y respuesta a incidentes

1. Cambia `OPERATOR_TOKEN` en `.env` y reinicia la app: las cookies de operador
   anteriores dejan de validar.
2. Revisa los logs `config.unauthorized` y `config.capture_unauthorized`.
3. Si hubo una carga no revisada, inspecciona `data/gold/` y el `index.jsonl`;
   retira el documento y ejecuta `carvajal sync --layer core --prune` solo tras
   confirmar el alcance.
4. Mantén `SESSION_SECRET` y `OPERATOR_TOKEN` en `.env` con permisos `600`.

## Pruebas de referencia

- `tests/test_api_config.py`: carga anónima rechazada, operador aceptado,
  carga web solo en Gold, sin token configurado.
- `tests/test_api_capture.py`: captura anónima rechazada, operador aceptado,
  estado/SSE protegidos.
- `tests/test_security.py`: firma, caducidad y rotación de la cookie de
  operador; credenciales por formulario, Bearer, Basic y cookie.
- `tests/test_web_capture.py`: `publicar=False` deja Gold y no sincroniza.
- `tests/test_api.py`: la navegación no enlaza Configuración y la lectura
  pública (`/chat`, `/faq`, `/api/csrf`, `/live`) sigue abierta con
  `OPERATOR_TOKEN` configurado; tampoco existen endpoints web de publicación.
- `tests/test_api_read.py`, `tests/test_api_documents.py`: lectura de corpus y
  documentos por sus rutas públicas.
