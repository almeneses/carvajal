# Quickstart: spec 051

## Local

```bash
# .env: TYPESAFE_API_KEY=... (FOOD_PHOTO_ENABLED=true por defecto)
make api
# Abre http://localhost:8000, pulsa «Foto», elige una foto de comida y envía.
```

Sin `TYPESAFE_API_KEY` el botón no aparece. Para gastar créditos solo a propósito, deja
`FOOD_PHOTO_ENABLED=false` en el `.env` local.

## Evaluación (gasta créditos de Jev: ~1 llamada por foto)

```bash
uv run python scripts/evaluar_fotos.py --limit 20
```

Descarga las fotos de `specs/051-fotos-alimentos/evaluacion/fotos.json` (Wikimedia Commons,
licencias libres), las prepara igual que el servidor y compara tipo, temperatura y
recomendación con lo esperado. Nada se guarda salvo el informe.

## Producción (VPS)

1. `.env`: `TYPESAFE_API_KEY` (ya está si se configuró la 046) y, para WhatsApp,
   `FOOD_PHOTO_MEDIA_DIR=/openclaw-media`.
2. `compose.prod.yml` monta `${OPENCLAW_MEDIA_DIR:-/dev/null}` en el servicio `mcp` como
   `/openclaw-media:ro` (ver `docs/whatsapp-openclaw.md`).
3. OpenClaw: `tools.media.image.enabled=false` y la instrucción del agente que pasa
   `media://inbound/...` en `foto` (ver la guía).
4. `curl -s localhost:8000/health | jq .food_photo` → `{"status": "ok"}`.
