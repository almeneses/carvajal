---
name: abrir-pr
description: Abre una Pull Request en el repositorio de Carvajal cumpliendo AGENTS.md - rama desde main, make check, Conventional Commits en español, CONSTITUTION.md al día, etiquetas del catálogo, assignee y merge con squash en cuanto la rama esté al día y el check Calidad en verde. Úsala cuando pidan "abre la PR", "sube los cambios", "commit y push", "crea un pull request" o al terminar una tarea que deba entregarse.
---

# Abrir una PR en Carvajal

Las reglas vienen de `AGENTS.md` (sección «Flujo Diario» y siguientes). Si esta
skill y `AGENTS.md` discrepan, manda `AGENTS.md` y hay que corregir esta skill.

## Reglas que no se negocian

- La PR apunta a `main`. `dev` no se usa desde el 2026-11-22.
- El agente **fusiona su PR en cuanto la rama esté al día con `main` y
  `Calidad` esté en verde**, sin esperar aprobación ni otros checks. La rama al
  día es **obligatoria** aunque GitHub no la imponga a la cuenta `almeneses`
  (bypass): solo se fusiona con `mergeStateStatus` = `CLEAN`. Nunca con `Calidad` en rojo o pendiente, nunca
  con `--admin`, nunca `git push --force` a `main` ni borrar `main`. No aprueba
  PRs ajenas.
- Fusionar en `main` despliega a producción: solo trabajo terminado y verificado.
- Toda dependencia pasa por `uv`; `pip` y `requirements.txt` están prohibidos.
- No se commitean secretos (`.env`, credenciales, `.claude/settings.local.json`).

## Pasos

### 1. Rama

Si estás en `main`, crea una rama antes de commitear:

```bash
git switch -c <tipo>/<descripcion-corta>   # p. ej. feat/agente-tool-calling
```

### 2. Verificación

```bash
make check   # Ruff (lint + formato) + pytest
```

Si falla, corrige y vuelve a correr. No abras la PR en rojo; si una falla no es
tuya y no puedes arreglarla, dilo en la PR con la salida del error.

Si el cambio toca el agente, el retrieval, los prompts o el cotizador, corre
también la skill `evaluar-agente` y pega su tabla en la PR.

### 3. CONSTITUTION.md

Si el cambio es arquitectónico, tecnológico o de proceso (módulos nuevos,
dependencias, rutas, flujos de trabajo), actualiza `CONSTITUTION.md` **en el
mismo commit**. Si no aplica, dilo explícitamente en la PR.

### 4. Commit

Conventional Commits, mensaje en español e identificadores en inglés:

```bash
git add <archivos>          # revisa git status; no subas artefactos ni secretos
git commit -m "tipo(ámbito): mensaje descriptivo en imperativo"
```

Tipos: `feat`, `fix`, `refactor`, `style`, `test`, `chore`, `docs`. Los hooks de
pre-commit corren solos; si fallan, corrige y crea un commit nuevo (no uses
`--no-verify` ni `--amend` sobre commits ya publicados).

### 5. Push y PR

```bash
git push -u origin HEAD
gh pr create --base main --title "tipo(ámbito): ..." --body-file <archivo>
```

El cuerpo sigue `.github/pull_request_template.md`: tipo de cambio, descripción,
qué se verificó (salida de `make check`, tabla de `evaluar-agente` si aplica) y
si se actualizó `CONSTITUTION.md`. Menciona a personas con `@login` solo si de
verdad hay que notificarlas.

### 6. Etiquetas (obligatorio)

```bash
gh label list
gh pr edit <número> --add-label "<etiqueta>,<etiqueta>"
```

Elige las más específicas del catálogo vigente; combina si el cambio cruza
áreas. Guía rápida (verifica siempre contra `gh label list`):

| Cambio | Etiquetas |
|---|---|
| API o lógica de servidor | `enhancement` + `backend` |
| Agente, retrieval, evaluación o métricas | `enhancement` + `ml` |
| Interfaz | `enhancement` + `ui` |
| Datos o curaduría | `bug` o `enhancement` + `data` |
| CI, entorno o despliegue | `chore` + `infra` |
| Solo pruebas | `test` |
| Solo documentación | `documentation` |
| Refactor sin cambio de comportamiento | `refactor` |

Cuando la PR esté lista para el owner, añade `ready-for-owner`. No inventes
etiquetas; si `gh label list` falla, no etiquetes a ciegas: déjalo escrito en la
PR y avisa al owner.

### 7. Assignee (obligatorio)

```bash
gh api user -q .login            # comprueba con qué cuenta corre gh
gh pr edit <número> --add-assignee @me
```

Si `gh` corre con una cuenta distinta del autor real, asigna al autor con
`--add-assignee <login>` o pide al owner que lo haga. Si ya hay assignee, no lo
reemplaces: solo añade al autor si falta.

### 8. Merge

La rama debe estar al día con `main` y el check `Calidad` en verde. Es
obligatorio aunque GitHub deje pasar la fusión: con la cuenta `almeneses` (bypass
del ruleset) un `gh pr merge` sobre una rama atrasada entra igual. Por eso la
comprobación va justo antes del merge y no se salta:

```bash
N=<número>
estado() { gh pr view "$N" --json mergeStateStatus -q .mergeStateStatus; }
if [ "$(estado)" = BEHIND ]; then
  gh pr update-branch "$N"                 # trae main a la rama; vuelve a correr Calidad
  sleep 5
fi
gh pr checks "$N" --watch --required       # espera los checks requeridos
s="$(estado)"
if [ "$s" = CLEAN ]; then
  gh pr merge "$N" --squash --delete-branch
else
  echo "No se fusiona: mergeStateStatus=$s (se exige CLEAN)" >&2
fi
```

Si `main` avanzó mientras corría `Calidad`, el estado vuelve a `BEHIND`: repite
el bloque.

Si la actualización da conflictos, resuélvelos en la rama (`git merge origin/main`),
corre `make check`, sube y vuelve a esperar.

Si `Calidad` falla, corrige en la misma rama, sube y vuelve a esperar. No uses
`--admin` ni fusiones con el check en rojo o pendiente.

### 9. Cierre

Verifica y reporta:

```bash
gh pr view <número> --json url,state,labels,assignees,statusCheckRollup
```

Entrega la URL, las etiquetas, el assignee, el estado de `Calidad` y si quedó
fusionada (el merge dispara el despliegue a producción).
