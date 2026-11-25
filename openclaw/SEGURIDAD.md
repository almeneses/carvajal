# Seguridad de OpenClaw

Estado del gateway de OpenClaw (2026.11.6) en la VPS de Google, revisado y endurecido el
2026-11-25. Los cambios de configuración están en
[`config/seguridad.json5`](config/seguridad.json5). Se aplicaron con
`openclaw config patch`, con respaldo previo y recarga en caliente, sin reiniciar el
gateway. Dos revisiones independientes contrastaron este documento con la configuración
viva y con la documentación instalada de OpenClaw.

## Resultado de `openclaw security audit`

| | Crítico | Advertencia | Info |
|---|---|---|---|
| Antes (ejecución del 2026-11-25, previa al parche) | 5 | 7 | 1 |
| Después | **2** | **6** | 1 |

Los 2 críticos que quedan existen porque el grupo de WhatsApp de Carva sigue con
`groupPolicy: "open"`. El owner decidió dejarlo así por ahora (ver
[Pendiente](#pendiente)). En una simulación sobre una copia de la configuración, el mismo
parche con `groupPolicy: "allowlist"` dejó **0 críticos** y 4 advertencias, entre ellas
`security.trust_model.multi_user_heuristic`, que sigue mientras `main` tenga `exec` sin
sandbox.

## Controles

| Control | Configuración | Qué protege |
|---|---|---|
| Allowlist E.164 en mensajes directos | `channels.whatsapp.dmPolicy: "allowlist"` + `allowFrom` (un número) | Por DM solo llegan al agente personal `main` el número de `allowFrom`, el propio número vinculado (self-chat) y los emparejamientos que ya estén guardados. |
| Allowlist de grupos | `channels.whatsapp.groups` con un solo JID | OpenClaw descarta los mensajes de cualquier otro grupo antes de enrutarlos. |
| `tools.deny` en `ventas` | Sobre el perfil `messaging`, se niegan `group:runtime`, `group:fs`, `group:ui`, `group:automation`, `group:nodes`, `group:web`, `sessions_send`, `sessions_spawn`, `conversations_send`, `conversations_turn`, `subagents` y `gateway` | Carva no puede ejecutar comandos, tocar archivos, navegar, programar tareas, crear subagentes, escribir en otras conversaciones ni cambiar el gateway. Le quedan `message` (para responder y adjuntar el PDF), `consultar_carvajal` (MCP), `ask_user` y las consultas de sesión de solo lectura, limitadas por `visibility: "tree"`. |
| `exec` cerrado en `ventas` | `agents.entries.ventas.tools.exec.mode: "deny"` | Bloquea la ejecución en el host aunque alguien quite después un `deny`. |
| `fs.workspaceOnly` | `true` en `ventas` y en `main` | En `main`, `read`, `write`, `edit` y `apply_patch` solo ven su workspace, salvo que un operador ponga la sesión en modo `full`. En `ventas`, que no tiene herramientas de archivos, limita los adjuntos por ruta local al workspace, los temporales y la caché de medios. |
| Sin `elevated` | `tools.elevated.enabled: false` (y en `ventas`) | Bloquea `/elevated` y los comandos de shell `!cmd`. |
| Un chat por conversación | `agents.entries.ventas.tools.message.crossContext` con `allowWithinProvider: false` y `allowAcrossProviders: false` | Los envíos, respuestas, ediciones y borrados de `message` quedan confinados al chat donde le hablaron a Carva, aunque una inyección pida otra cosa. Las otras vías de envío (`conversations_send`/`turn`, `sessions_send`) están negadas. |
| Agentes aislados | `tools.sessions.visibility: "tree"`, `tools.agentToAgent.enabled: false` | Cada sesión ve solo la suya y las que generó. Carva no puede leer las conversaciones de `main` y los agentes no se hablan entre sí. |
| HITL en `main` | `agents.entries.main.tools.exec.mode: "ask"` con `strictInlineEval`, y `approvals.exec` y `approvals.plugin` con `agentFilter: ["main"]` | Todo comando de `main` que no esté en su allowlist pide aprobación humana. `python -c`, `node -e` y similares la piden siempre. Si el pedido nace en WhatsApp, la aprobación se da con una reacción desde el número de `allowFrom`; si nadie responde, se deniega. Los avisos de aprobación no van al grupo de clientes. |
| HITL en cambios de configuración | `tools.openclaw.autoApproveChanges: false` | Un cambio de configuración que un agente intente con la herramienta `openclaw` exige aprobación explícita, incluso en acceso completo. Como no hay `commands.ownerAllowFrom`, hoy solo se aprueba desde la Control UI; si caduca, no se aplica. |
| Plugins con allowlist | `plugins.allow` con 9 ids (`whatsapp`, `deepseek`, `openai`, `memory-core`, `browser`, `document-extract`, `web-readability`, `device-pair`, `talk-voice`) | Restringe la carga a esos plugins. OpenClaw tiene excepciones documentadas: el plugin de memoria seleccionado, el motor de contexto seleccionado, los canales incluidos habilitados explícitamente y un bloque raíz `browser`. |
| Credenciales | `chmod 700 ~/.openclaw/credentials` (y `credentials/whatsapp`) | Otro usuario del sistema no puede leer ni dejar archivos ahí. |
| Defensa ante inyección | [`SOUL.md`](workspace/ventas/SOUL.md) y [`AGENTS.md`](workspace/ventas/AGENTS.md) de `ventas`, más los controles de arriba | Los mensajes de clientes son datos, no órdenes. La regla del relé manda todo a `consultar_carvajal`, que tiene sus propias compuertas de evidencia y privacidad. Aunque el modelo cayera en una inyección, la política de herramientas no le deja nada peligroso que hacer. |

El TTS automático y la transcripción de notas de voz (`tools.media`) no son herramientas
del agente, así que estos controles no los afectan. La herramienta MCP
`consultar_carvajal` tampoco cambia.

## Hallazgos aceptados

| Hallazgo | Severidad | Por qué sigue | Cómo se cierra |
|---|---|---|---|
| `security.exposure.open_groups_with_runtime_or_fs` | Crítico | Grupo abierto, más `agents.defaults` y `main` con `exec` y archivos sin sandbox (los defaults siguen con `fs.workspaceOnly: false`). El grupo va solo a `ventas` (binding por JID), que no tiene esas herramientas, pero el audit no considera el binding. | `groupPolicy: "allowlist"` con los números del grupo. |
| `security.exposure.open_groups_with_control_plane_tools` | Crítico | Grupo abierto, más el perfil global `tools.profile: "full"`, que heredan los defaults y `main`. | Igual que el anterior. |
| `security.exposure.open_channels_with_exec` | Advertencia | `main` tiene `exec`, ahora en modo `ask` (el audit lo reporta como `security=allowlist`). | Igual que el anterior. |
| `channels.whatsapp.groups.open` | Advertencia | Decisión del owner. | Igual que el anterior. |
| `security.trust_model.multi_user_heuristic` | Advertencia | `main` tiene `exec` sin sandbox y hay un grupo configurado. Se mantiene también con la allowlist. | Sandbox real para `main` (ver [Sandbox](#sandbox)). |
| `plugins.tools_reachable_permissive_policy` | Advertencia | Perfil global `tools.profile: "full"` (contexto `default` y `main`, que lo hereda). `deepseek` solo es proveedor de modelo. | Restringir el perfil global o el de `main`, lo que le quita su uso personal. |
| `plugins.installs_unpinned_npm_specs` | Advertencia | Fijar la versión exige detener el gateway. | Con el gateway detenido: `openclaw plugins update @openclaw/whatsapp@2026.11.6`. Al ser un plugin oficial, OpenClaw retoma las actualizaciones automáticas cuando avanza el core, así que el pin dura hasta la siguiente actualización. |
| `gateway.trusted_proxies_missing` | Advertencia | No hay proxy: el gateway escucha solo en loopback. | `gateway.controlUi.enabled: false` si nadie usa el panel web (según el código del audit, el check solo aplica con la Control UI activa), o `gateway.trustedProxies` si se pone un proxy delante. |

## Sandbox

No hay sandbox activo. Esta versión solo aísla con `docker`, `podman`, `ssh`,
`openshell` o `crabbox`. La VPS no tiene docker, podman, openshell ni crabbox, y no hay
otra máquina configurada como destino del backend `ssh`.

Para `ventas` aportaría poco. El sandbox aísla `exec`, las herramientas de archivos y el
navegador, y Carva ya los tiene negados. Las herramientas MCP siguen corriendo en el
gateway: en los turnos con sandbox solo aparecen si se añaden a `tools.sandbox.tools`, y
`message` tampoco está en la lista por defecto del sandbox. El TTS automático no es una
herramienta y no le afecta.

Si se instala podman, los paquetes `podman` y `catatonit` van como root. La imagen
`openclaw-sandbox:trixie-slim` se construye como `marko` desde un checkout del código
fuente de OpenClaw (`podman build -t openclaw-sandbox:trixie-slim -f
scripts/docker/sandbox/Dockerfile .`), para que quede en el almacén rootless que usa el
gateway. El bloque para `ventas` es:

```json5
agents: { entries: { ventas: {
  sandbox: { mode: "all", backend: "podman", scope: "session", workspaceAccess: "none",
    docker: { image: "openclaw-sandbox:trixie-slim", network: "none", readOnlyRoot: true,
      capDrop: ["ALL"], memory: "256m", cpus: 0.5, pidsLimit: 128 } },
  // Sin esto, consultar_carvajal y message desaparecen en los turnos con sandbox:
  tools: { sandbox: { tools: { alsoAllow: ["carvajal__*", "message"] } } },
} } }
```

No se debe activar `mode: "all"` sin el runtime instalado. La ejecución en sandbox no
puede volver al host, y lo esperable es que los turnos de Carva fallen (no lo
comprobamos).

## Pendiente

- **Allowlist del grupo.** El owner lo decidirá después. Para activarla, como `marko`
  en la VPS (el arreglo se reemplaza completo, así que hay que incluir todos los
  números):

  ```bash
  openclaw config set channels.whatsapp.groupAllowFrom '["+57XXXXXXXXXX","+57YYYYYYYYYY"]' --strict-json --dry-run
  openclaw config set channels.whatsapp.groupAllowFrom '["+57XXXXXXXXXX","+57YYYYYYYYYY"]' --strict-json
  openclaw config set channels.whatsapp.groupPolicy allowlist
  openclaw channels status --probe
  ```

  Hoy `groupAllowFrom` tiene un número real y dos marcadores (`+57NUMERO2` y
  `+57NUMERO3`) que hay que reemplazar. Con la allowlist activa, Carva ignora en
  silencio a quien no esté en la lista.

## Revertir

Antes de cada cambio se respaldó la configuración en `~/.openclaw/`:
`openclaw.json.bak-seguridad-20261125153808` (antes del parche) y `openclaw.json.bak-deny-*`
(antes de ampliar el `deny` de `ventas`). Para volver atrás, se copia el respaldo sobre
`openclaw.json`: el gateway detecta el cambio, lo valida y lo aplica en caliente.
