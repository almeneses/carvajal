# AGENTS — Carvajal

> Contexto del proyecto para sesiones con asistentes LLM (OpenCode, Antigravity,
> GitHub Copilot CLI, Claude Code, etc.). Trackeado al repositorio.

## Proyecto

Pipeline de captura y curaduría del corpus web de Carvajal para RAG.

> 📘 La verdad técnica central —propósito, stack, estructura, convenciones, decisiones y
> flujo de trabajo— vive en [`CONSTITUTION.md`](./CONSTITUTION.md). **Léela antes de
> operar.** Ante cualquier cambio arquitectónico, tecnológico o de proceso, actualízala
> en el mismo commit.

---

## Flujo Diario (commit + push)

```bash
# 1. Ver cambios
git status

# 2. Verificación completa (lint + formato + pruebas)
make check

# 3. Agregar y commitear (los hooks de pre-commit se ejecutan solos)
git add -A
git commit -m "tipo: mensaje descriptivo"

# 4. Subir a GitHub
git push
```

**Convención de commits:** [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` — nueva funcionalidad
- `fix:` — corrección de bug
- `refactor:` — refactorización sin cambio de comportamiento
- `style:` — formato, linting (sin cambio de lógica)
- `test:` — pruebas
- `chore:` — mantenimiento o configuración

### Regla de merge (obligatoria para agentes)

Toda Pull Request apunta a `main`; `dev` dejó de usarse el 2026-11-22. `main`
está protegida con un ruleset: exige el check **`Calidad` en verde** y la
**rama al día** con `main`, prohíbe **borrar** la rama y prohíbe el **force
push**. Desde el 2026-11-25 (decisión del owner) no exige aprobaciones.

- Un agente **puede fusionar su PR en cuanto la rama esté al día y `Calidad`
  esté en verde**, sin esperar aprobación ni ningún otro check. Si la rama va
  detrás de `main`, la actualiza (`gh pr update-branch <número>`), espera de
  nuevo `Calidad` y fusiona con `gh pr merge <número> --squash --delete-branch`.
- **La rama al día con `main` es obligatoria** para fusionar. GitHub no lo
  impide cuando el agente corre con la cuenta `almeneses` (está en el bypass y
  la fusión entra igual), así que el agente lo comprueba justo antes de
  fusionar: solo fusiona si `gh pr view <número> --json mergeStateStatus` da
  `CLEAN`. Con `BEHIND` actualiza la rama y vuelve a esperar `Calidad`; con
  cualquier otro estado no fusiona.
- Fusionar en `main` **despliega a producción** (`deploy.yml`): fusiona solo
  trabajo terminado y verificado con `make check`.
- Nunca con `Calidad` en rojo o pendiente, y **nunca con `--admin`** para
  saltarse el check. Nunca `git push --force` a `main` ni borrar `main`.
- Un agente no aprueba PRs ajenas: la aprobación sigue siendo una decisión
  humana.

El bypass del ruleset está reservado a la cuenta `almeneses`. Que el agente
corra con ese token no lo autoriza a usarlo: la restricción es de proceso, no
solo de permisos.

### Etiquetas de PR (obligatorio para agentes)

Toda Pull Request debe llevar al menos una etiqueta adecuada antes de
considerarse entregada. En particular, todo agente debe aplicarlas sin esperar
a que el owner las pida. El mínimo es una; si el cambio abarca varias áreas,
combina las etiquetas que correspondan.

1. Consulta el catálogo vigente con `gh label list` y elige la etiqueta más
   específica. La fuente de verdad son las etiquetas existentes; los siguientes
   son solo ejemplos, no una lista cerrada. Combina varias si el cambio cubre
   áreas distintas. Si `gh label list` falla (sin red o permisos), no inventes
   etiquetas: deja constancia en la PR y avisa al owner.
   - API o lógica de servidor → `enhancement` + `backend`
   - corrección de datos o curaduría → `bug` + `data`
   - CI, entorno o despliegue → `chore` + `infra`
   - sólo pruebas → `test`
   - sólo documentación → `documentation`
2. Aplica las etiquetas justo después de crear o actualizar la PR:
   `gh pr edit <número> --add-label "enhancement,backend"`
3. **No inventes etiquetas** si existe una razonablemente equivalente. Crear una
   nueva sólo si ninguna encaja y el hueco es estructural; justifícalo en la PR.
4. Revisa las etiquetas antes de dar la PR por terminada: una PR sin etiquetas,
   o con etiquetas obsoletas, está incompleta.

En OpenCode esta regla también se aplica automáticamente mediante
[`.opencode/rules/pr-labels.md`](./.opencode/rules/pr-labels.md).

### Assignee de PR (obligatorio para agentes)

Toda PR abierta por un agente debe incluir como assignee a quien la abre. Por
defecto, el autor se asigna a sí mismo:

```bash
gh pr edit <número> --add-assignee @me
```

- No asignes a otras personas sin pedirlo; el assignee por defecto es el autor.
- Si la PR ya tiene assignee, no lo reemplaces: añade al autor solo si falta.
- Si el agente corre con un token de otra cuenta, `@me` asignaría a esa cuenta:
  usa el login del autor real (`--add-assignee <login>`) o pide al owner que lo
  asigne.
- Revisa el assignee junto con las etiquetas antes de dar la PR por terminada.

---

## Comandos del proyecto (`make`)

```bash
make help      # lista los objetivos disponibles
make dev       # entornos + hooks de pre-commit
make check     # lint + formato + pruebas
make lint      # Ruff (lint)
make format    # Ruff (formato)
make test      # pytest
make import    # importa el corpus externo a la capa extendida
make qdrant    # levanta Qdrant con volumen persistente
make index     # indexa data/gold en Qdrant (M2)
make api       # interfaz única + API en :8000
make image     # construye la imagen Docker de la app web
make up        # stack completo en Docker (app, base, Qdrant, Ollama y corpus)
make down      # detiene el stack completo (conserva los volúmenes)
make prod-up   # levanta app, Postgres, Qdrant y Ollama en APP_PORT (SESSION_SECRET)
make prod-down # detiene los servicios (conserva los volúmenes)
make prod-bootstrap # prepara un servidor limpio (modelos, migraciones y corpus)
make eval-agent # compara los modos routes y tools (spec 044; gasta tokens de DeepSeek)
```

> 🤖 `AGENT_MODE=tools` (por defecto desde el 2026-11-23, decisión del owner) activa el
> agente con herramientas de la spec 044; `AGENT_MODE=routes` vuelve al enrutador por
> reglas. El modo `tools` usa DeepSeek y solo arranca con `LLM_PROVIDER=deepseek` y
> `DEEPSEEK_API_KEY`; si no, el chat sigue en `routes` con el proveedor configurado (aviso
> `agent.tools_mode_unavailable` y bloque `agent` de `/health`). Decisión, modelo evaluado
> (`deepseek-flash`) y período de observación: `specs/044-agente-tool-calling/evidencia.md` §10.

> 📦 `uv sync --all-extras` instala también los extras `capture` (CLI de
> captura) y `legacy` (FAISS para la CLI histórica). El CLI completo requiere ambos:
> `uv tool install "carvajal[capture,legacy]"`. La imagen Docker instala solo
> el runtime web.
>
> 🐳 `compose.prod.yml` exige `SESSION_SECRET` y falla si falta. `/live` es la
> vitalidad del proceso (sin base); `/health` es el readiness con conteos.

> ⚠️ **Regla dura:** toda gestión de dependencias y entornos pasa por `uv`. Está
> **prohibido** `pip` y `requirements.txt`.

---

## Búsqueda de código: `tgrep`

Si [`tgrep`](https://github.com/microsoft/tgrep) está instalado, úsalo para búsquedas de
código en lugar de `grep` o `find` (índice de trigramas en `.tgrep/`, ignorado por Git):

```bash
tgrep -- "patrón" .
tgrep -F -- "simbolo" .
tgrep -t py -- "def " .
tgrep -g "src/**" -- "keyword" .
tgrep -l -- "import torch" .
tgrep index .
tgrep serve . &
```

En OpenCode la preferencia se aplica automáticamente mediante la regla
[`.opencode/rules/tgrep.md`](./.opencode/rules/tgrep.md).

---

## Skills del proyecto

Viven en `.claude/skills/` (formato Agent Skills; las leen Claude Code y
OpenCode). Convierten las reglas de este archivo en pasos ejecutables:

- [`abrir-pr`](./.claude/skills/abrir-pr/SKILL.md) — rama, `make check`,
  commit, CONSTITUTION, etiquetas, assignee y merge con la rama al día y `Calidad` en verde.
- [`evaluar-agente`](./.claude/skills/evaluar-agente/SKILL.md) — enrutador,
  retrieval y cotizador contra la línea base, con criterios de regresión.

Si una skill contradice este archivo, manda `AGENTS.md` y la skill se corrige en
el mismo commit.

---

## Estructura y convenciones

- `packages/core/src/carvajal/` — código del paquete (src-layout, módulo `uv_build`).
- `tests/` — pruebas con `pytest` (patrón AAA).
- `docs/` — documentación, ADRs y notas de investigación.
- `CONSTITUTION.md` — fuente única de verdad.
- Estilo impuesto por **Ruff** (`line-length = 100`, comillas dobles).
- Idioma: UI, comentarios y commits en español; identificadores en inglés.
