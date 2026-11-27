# Workspace de OpenClaw

Archivos del agente «ventas», el que responde WhatsApp. Son la fuente de verdad del
workspace que corre en la VPS de OpenClaw, en `~/.openclaw/workspace/ventas/` del
usuario que ejecuta el gateway. La configuración y la seguridad del gateway están en
[`docs/whatsapp-openclaw.md`](../docs/whatsapp-openclaw.md).

| Archivo | Qué es | Cuándo se carga |
|---|---|---|
| [`AGENTS.md`](workspace/ventas/AGENTS.md) | Reglas de operación: relé a `consultar_carvajal`, PDF adjunto, notas de voz, fotos, pedidos y memoria | Cada sesión |
| [`SOUL.md`](workspace/ventas/SOUL.md) | Persona, tono y límites, con la defensa ante instrucciones dentro de los mensajes | Cada sesión |
| [`IDENTITY.md`](workspace/ventas/IDENTITY.md) | Nombre (Carva), tipo, tono y emoji | Cada sesión |
| [`USER.md`](workspace/ventas/USER.md) | A quién atiende: directivas fechadas, válidas para todos los clientes | Cada sesión (máx. 4.000 caracteres) |
| [`MEMORY.md`](workspace/ventas/MEMORY.md) | Memoria de largo plazo: arquitectura y decisiones con fecha | En la sesión principal |
| [`memory/AAAA-MM-DD.md`](workspace/ventas/memory/) | Bitácora diaria de operación | Hoy y ayer, con `/new` o `/reset`; el resto con `memory_search` |

## Qué no se versiona

- Los resúmenes de sesión que escribe el hook `session-memory`
  (`memory/AAAA-MM-DD-HHMM.md`): llevan números y mensajes de clientes.
- `memory/.dreams/`, `media/` y el repositorio git interno del workspace.
- `openclaw.json` y `.env`: tienen la configuración del gateway y las claves.

## Quién puede escribirle al bot en el grupo

Desde el 2026-11-27 el grupo está en `groupPolicy: allowlist`: el bot solo contesta a los
números de `channels.whatsapp.groupAllowFrom` (E.164). Con eso `openclaw security audit`
queda sin críticos. Los invitados de una demo se agregan en vivo con
[`invitados.sh`](invitados.sh), que corre en la VM del gateway (`~/invitados.sh`).
OpenClaw aplica el cambio sin reiniciar.

```bash
~/invitados.sh fijar-base                     # antes del evento: el equipo queda como base
~/invitados.sh agregar 3001234567 3157654321  # cada invitado, al llegar
~/invitados.sh listar                         # cuántos y los 4 últimos dígitos
~/invitados.sh restablecer                    # al terminar: solo el equipo
```

Agregar a alguien a la lista no lo mete al grupo de WhatsApp: eso lo hace un administrador
del grupo. Los números no se versionan.

## Desplegar

En la VPS de OpenClaw, como el usuario del gateway:

```bash
W=~/.openclaw/workspace/ventas
STAMP=$(date +%Y%m%d%H%M%S)
# Bitácoras con fecha incluidas: la copia las sobrescribe. Los resúmenes de sesión
# (`AAAA-MM-DD-HHMM.md`) no se tocan y no se duplican.
cd "$W"
for f in AGENTS.md SOUL.md IDENTITY.md USER.md MEMORY.md memory/????-??-??.md; do
  [ -f "$f" ] && cp -p "$f" "$f.bak-$STAMP"
done
# Copiar los archivos de openclaw/workspace/ventas/ (por scp o git) a $W y luego:
chmod 600 "$W"/*.md "$W"/memory/*.md
```

No hace falta reiniciar el gateway: el workspace se lee al iniciar cada sesión. Si un
chat que ya estaba abierto no toma los cambios, basta con `/new`.

Cada día con cambios de operación se agrega una nota en `memory/AAAA-MM-DD.md`, sin
datos de clientes. Cuando una decisión se vuelve estable, se resume en `MEMORY.md`.
