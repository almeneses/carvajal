---
name: abrir-pr
description: Abre una Pull Request en el repositorio de Carvajal cumpliendo AGENTS.md - rama desde main, make check, Conventional Commits en español, CONSTITUTION.md al día, etiquetas del catálogo, assignee y merge con squash cuando Calidad pasa. Úsala cuando pidan "abre la PR", "sube los cambios", "commit y push", "crea un pull request" o al terminar una tarea que deba entregarse.
---

# Abrir una PR en Carvajal

Las reglas vienen de `AGENTS.md` (sección «Flujo Diario» y siguientes). Si esta
skill y `AGENTS.md` discrepan, manda `AGENTS.md` y hay que corregir esta skill.

## Reglas que no se negocian

- La PR apunta a `main`. `dev` no se usa desde el 2026-11-22.
- El agente puede mergear la PR (squash) cuando `Calidad` está en verde, la
  rama está al día y no quedan hilos abiertos; no aprueba PRs, ni propias ni
  ajenas.
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

### 8. Cierre

Verifica y reporta:

```bash
gh pr view <número> --json url,labels,assignees,statusCheckRollup
```

Entrega la URL, las etiquetas, el assignee y el estado del check `Calidad`.

### 9. Merge

Cuando `Calidad` está en verde, la rama está al día y no quedan hilos de revisión
abiertos, el agente puede fusionar:

```bash
gh pr merge <número> --squash --admin   # el ruleset pide 1 aprobación; almeneses tiene bypass
```
