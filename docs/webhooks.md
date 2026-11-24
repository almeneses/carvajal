# Webhook firmado de mensajería

`POST /api/webhooks/whatsapp` recibe un mensaje de WhatsApp (u otro canal) desde un relé
propio y responde en el mismo cuerpo HTTP. Ejecuta **el mismo turno** que el servidor MCP
de [WhatsApp con OpenClaw](whatsapp-openclaw.md) (`apps.mcp.bridge.WhatsAppBridge`):
el `owner_key` es un HMAC del número, el texto sale en formato de WhatsApp, las cotizaciones
se redactan como texto con su folio y cualquier fallo devuelve un mensaje legible.

Cubre el requisito «Backend/API (FastAPI o equivalente) con webhooks y persistencia»: el
turno se guarda en Postgres (conversaciones, mensajes, cotizaciones) y la memoria del agente
en `PostgresSaver`/`PostgresStore`, igual que el chat web.

```text
WhatsApp ──► relé (Business API, Twilio, OpenClaw…) ──POST firmado──► /api/webhooks/whatsapp
                                                                          │
                                         WhatsAppBridge ── ChatService ── Postgres · Qdrant
```

## Activarlo

En el `.env` (local o `/opt/carvajal/.env` del servidor):

```bash
# Sin secreto el endpoint responde 404.
WEBHOOK_SECRET=<python -c "import secrets; print(secrets.token_urlsafe(32))">
# E.164 separados por comas. Vacía = rechaza a todos (403).
WEBHOOK_ALLOWED_NUMBERS=+573100000000,+573110000000
```

`SESSION_SECRET` también debe estar definido: firma el `owner_key` de cada número (en
producción ya es obligatorio). El mismo número cae en el mismo hilo por el webhook y por
el servidor MCP. Al arrancar, la app registra `webhook.enabled` con el número de entradas
autorizadas, o `webhook.allowlist_empty` / `webhook.allowlist_invalid_entries`, sin
mostrar ningún número.

> ⚠️ La demo se publica por HTTP sin TLS. La firma impide falsificar o repetir peticiones,
> pero **no cifra** el mensaje: mientras no haya un proxy TLS delante, el relé debe llegar
> por un túnel SSH o una red privada.

## Contrato

**Cabeceras**

| Cabecera | Valor |
|---|---|
| `Content-Type` | `application/json` |
| `X-Carvajal-Timestamp` | Segundos Unix (entero). Se acepta ±5 minutos respecto al reloj del servidor. |
| `X-Carvajal-Signature` | `sha256=<hex>`: HMAC-SHA256 con `WEBHOOK_SECRET` de `<timestamp>.<cuerpo crudo>`. |

El timestamp **va dentro de lo firmado**: así una petición capturada no se puede repetir
cambiándole la hora. Se firma el cuerpo exactamente como viaja (los mismos bytes); volver a
serializar el JSON en el emisor después de firmar invalida la firma.

**Cuerpo** (máx. 16 KiB)

```json
{"message_id": "wamid.HBgM...", "from": "+573100000000", "text": "¿Tienen vasos de 12 oz?", "nueva": false}
```

| Campo | Regla |
|---|---|
| `message_id` | Obligatorio, 1–200 caracteres. Identificador del mensaje en el canal. |
| `from` | Obligatorio. E.164; se aceptan espacios, guiones, paréntesis y el prefijo `whatsapp:`. |
| `text` | Obligatorio (puede ir vacío con `nueva`). Más de 500 caracteres recibe un aviso para resumir. |
| `nueva` | Opcional. `true` abre una conversación nueva antes del turno. |

**Respuesta 200**

```json
{"reply": "Sí, tenemos vasos de papel de 12 oz…\n\n*Fuentes:*\n[1] …", "quote_ids": ["Q-3F9A1C"]}
```

Envía `reply` al cliente tal cual. `quote_ids` son los folios de las cotizaciones emitidas.

**Errores** (siempre `{"detail": "…"}` en JSON, sin eco del cuerpo ni del número)

| Código | Causa |
|---|---|
| 404 | `WEBHOOK_SECRET` no está configurado. |
| 413 | El cuerpo supera 16 KiB. |
| 401 | Falta el timestamp, está fuera de la ventana o la firma no coincide. |
| 422 | El cuerpo no es JSON o no cumple el esquema (por ejemplo, `from` sin `+`). |
| 403 | El remitente no está en `WEBHOOK_ALLOWED_NUMBERS`. No se procesa nada. |
| 503 | No se pudo preparar el turno (por ejemplo, falta `SESSION_SECRET`). |

## Reintentos e idempotencia

`message_id` se convierte en el `client_turn_id` del turno (`wh_` + SHA-256 truncado). Si
el relé reintenta el mismo mensaje, el servidor encuentra el turno guardado del remitente y
**reemite la misma respuesta y los mismos folios**: no llama al agente, no cobra cupo, no
abre otra conversación aunque llegue con `nueva: true` y no emite folios nuevos. Si dos
entregas del mismo mensaje llegan a la vez, la base sigue sin duplicar el turno (índice
único por `client_turn_id`), aunque ambas puedan ejecutar el agente.

## Privacidad

El número nunca se guarda ni se registra en claro: el turno se asocia al `owner_key`
(`wa_` + HMAC con `SESSION_SECRET`) y los rechazos solo registran el motivo
(`webhook.rejected` con `reason=bad_signature`, `stale_timestamp`, `sender_not_allowed`…).
Un cuerpo inválido registra solo qué campos fallaron, nunca sus valores.

## Firmar una petición

### Python

```python
import hashlib
import hmac
import json
import time

import httpx

SECRET = "el-valor-de-WEBHOOK_SECRET"
URL = "http://127.0.0.1:8000/api/webhooks/whatsapp"

body = json.dumps(
    {"message_id": "wamid.123", "from": "+573100000000", "text": "Hola"},
    ensure_ascii=False,
).encode("utf-8")
timestamp = str(int(time.time()))
digest = hmac.new(SECRET.encode(), timestamp.encode() + b"." + body, hashlib.sha256)

response = httpx.post(
    URL,
    content=body,  # los mismos bytes que se firmaron
    headers={
        "Content-Type": "application/json",
        "X-Carvajal-Timestamp": timestamp,
        "X-Carvajal-Signature": f"sha256={digest.hexdigest()}",
    },
    timeout=60,
)
print(response.status_code, response.json())
```

### curl

```bash
WEBHOOK_SECRET='el-valor-de-WEBHOOK_SECRET'
TS=$(date +%s)
BODY='{"message_id":"wamid.123","from":"+573100000000","text":"Hola"}'
SIG=$(printf '%s.%s' "$TS" "$BODY" \
  | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" -hex | sed 's/^.* //')

curl -sS -X POST http://127.0.0.1:8000/api/webhooks/whatsapp \
  -H 'Content-Type: application/json' \
  -H "X-Carvajal-Timestamp: $TS" \
  -H "X-Carvajal-Signature: sha256=$SIG" \
  --data-binary "$BODY"
```

Usa `--data-binary` (no `-d`) para que curl envíe el cuerpo sin tocarlo. El turno puede
tardar hasta `AGENT_TURN_TIMEOUT_S` (45 s por defecto): configura el relé con un plazo
mayor o reintenta con el mismo `message_id`, que no duplica nada.

## Pruebas

`tests/test_webhooks.py` cubre, sin red ni LLM: endpoint deshabilitado, firma inválida o
malformada, cuerpo alterado, timestamp viejo o futuro, número fuera de la allowlist o
allowlist vacía, cuerpo inválido o demasiado grande, éxito con el puente falso y la
idempotencia extremo a extremo con `ChatService` sobre SQLite.
