---
name: atender-revision
description: Atiende las rondas de revisión de una PR de Carvajal (el bot «revisar» de DeepSeek y comentarios humanos) - espera los checks, lee los hilos por GraphQL, verifica cada comentario contra el código, arregla o refuta con evidencia, responde y marca el hilo como resuelto; y, si el owner lo pidió, fusiona con la rama al día y Calidad en verde, espera el despliegue a producción y lo comprueba. Úsala cuando pidan "atiende los comments de la PR", "N rondas de revisión", "márcalos como resolve", "mergéala" o "espera el deploy".
---

# Atender la revisión de una PR (y fusionar)

Complementa [`abrir-pr`](../abrir-pr/SKILL.md). Las reglas de merge están en `AGENTS.md`
(«Regla de merge»): si esta skill discrepa, manda `AGENTS.md`.

## Reglas

- **Cuándo fusionar.** `AGENTS.md` permite fusionar con `CLEAN` y `Calidad` en verde, y
  [`abrir-pr`](../abrir-pr/SKILL.md) lo hace al terminar una entrega. Pero si el owner pidió
  rondas de revisión o dejar la PR abierta, **manda su pedido**: atiende las rondas y fusiona
  solo si lo pidió.
- Nunca `--admin`, nunca `git push --force`, nunca fusionar con `Calidad` en rojo o pendiente.
- Una «ronda» es: esperar la revisión del último push, atender **todos** sus hilos y hacer un
  push. Respeta el número de rondas que pidieron; los comentarios de rondas extra no se
  atienden, se mencionan en el cierre.
- Cada hilo se **responde y se resuelve**, también los que refutas. Nunca resuelvas un hilo
  sin responderlo.

## 1. Esperar la revisión del último push

```bash
gh pr checks <N> --watch --interval 30      # en segundo plano; «revisar» es el bot
gh pr checks <N> | cut -f1,2                # Base de datos, Calidad, Imagen, Integración, revisar
```

`gh event listen` no siempre funciona en esta cuenta: usa `gh pr checks --watch`. El bot
publica una revisión «DeepSeek encontró hallazgos P0/P1» con hilos por línea; si no hay
hallazgos, no deja hilos.

## 2. Leer los hilos sin resolver

```bash
gh api graphql -f query='query{repository(owner:"almeneses",name:"carvajal"){pullRequest(number:<N>){
  reviewThreads(first:50){nodes{id isResolved path line comments(first:5){nodes{author{login} body}}}}}}}' \
  -q '.data.repository.pullRequest.reviewThreads.nodes[]|select(.isResolved==false)'
```

A veces el bot escribe los hallazgos en el **cuerpo** de la revisión y no en hilos: léelo
también (`reviews(last:5){nodes{body}}`) y respóndelo con un comentario en la PR.

## 3. Verificar cada comentario antes de tocar código

El bot acierta a veces y se equivoca otras (repite un hallazgo ya refutado, inventa una ruta
que el código cubre). Para cada hilo:

1. Abre el archivo y la línea. Reproduce el escenario con `uv run python -c …` o una prueba.
2. **Real** → arréglalo con una prueba de regresión que falle antes del arreglo.
3. **No real** → refútalo con evidencia concreta: el `archivo:línea` que lo cubre, la salida
   del comando que lo prueba o la prueba existente.

Si dos hilos son el mismo problema, un solo arreglo y la misma respuesta en los dos.

## 4. Push, responder y resolver

```bash
make check && git add -A && git commit -m "fix(<ámbito>): … Atiende la revisión <n> de la PR #<N>." && git push
reply() {  # $1 = id del hilo, $2 = respuesta
  gh api graphql -f query='mutation($t:ID!,$b:String!){addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$t,body:$b}){comment{id}}}' -f t="$1" -f b="$2" >/dev/null &&
  gh api graphql -f query='mutation($t:ID!){resolveReviewThread(input:{threadId:$t}){thread{isResolved}}}' -f t="$1" -q .data.resolveReviewThread.thread.isResolved
}
reply PRRT_… "Atendido en <sha>: <qué cambió>. Prueba: \`<nombre de la prueba>\`."
reply PRRT_… "No cambia nada: <evidencia con archivo:línea o salida del comando>."
```

Si una mutación falla por un error transitorio de GraphQL, comprueba si la respuesta ya quedó
antes de repetirla (no dupliques respuestas). En las respuestas no escribas `@usuario` salvo
que quieras notificar a alguien.

## 5. Fusionar

```bash
gh pr view <N> --json mergeStateStatus -q .mergeStateStatus   # debe ser CLEAN
# BEHIND → gh pr update-branch <N>, esperar Calidad de nuevo; cualquier otro estado → no fusionar
gh pr merge <N> --squash --delete-branch
```

## 6. Esperar el despliegue y comprobarlo

Fusionar en `main` despliega a producción: `deploy.yml` arranca **cuando termina la CI de
`main`** (`workflow_run`), así que justo después del merge su run todavía no existe. Espéralo
en bucle (en segundo plano) y luego comprueba lo desplegado; `.env.ensayo` está en la raíz del
checkout principal (no se versiona):

```bash
M=$(gh pr view <N> --json mergeCommit -q .mergeCommit.oid)
for i in $(seq 1 60); do
  id=$(gh run list --workflow deploy.yml --limit 5 --json databaseId,headSha \
       -q ".[]|select(.headSha==\"$M\")|.databaseId" | head -1)
  [ -n "$id" ] && break; sleep 30
done
gh run watch "$id" --interval 30 >/dev/null; gh run view "$id" --json conclusion -q .conclusion
set -a && . ./.env.ensayo && set +a && \
  ssh -o BatchMode=yes -p "$ENSAYO_APP_SSH_PORT" "$ENSAYO_APP_SSH" 'cat /opt/carvajal/.deployed_tag' && \
  curl -s "$ENSAYO_BASE_URL/health"   # .deployed_tag = sha-$M
```

El despliegue reinicia `app` y `mcp`: no fusiones mientras corre una prueba contra
producción (por ejemplo `make ensayo-whatsapp-real`). Para comprobar el cambio en vivo, repite
solo los guiones afectados (skill [`ensayo-produccion`](../ensayo-produccion/SKILL.md), §5).

## 7. Cierre

Reporta por ronda: hilos atendidos (arreglados / refutados) con el commit, estado de los checks,
si se fusionó (sha) y si el despliegue quedó en producción.
