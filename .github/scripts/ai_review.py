#!/usr/bin/env python3
"""Revisión automática de pull requests con DeepSeek.

Pide al modelo hallazgos estructurados, los ancla a las líneas del diff (cuando
la línea existe en el lado nuevo del parche) y publica:

* un comentario resumen "pegajoso" en el PR, que se actualiza en cada push;
* comentarios en línea sobre el código, con bloque ```suggestion``` cuando hay
  un reemplazo exacto, que GitHub ofrece aplicar con un clic.

Los comentarios en línea de la ejecución anterior se borran antes de publicar
los nuevos, para no acumular ruido en cada push.

El revisor es deliberadamente conservador: los hallazgos "menor" no se
comentan en línea; como máximo dos aparecen agregados en el resumen. El resto
se descarta, para que el bot no se comporte como un linter ni llene el PR de
comentarios de estilo.

El tono y las convenciones de revisión se editan en `.github/review/style.md`
(si ese archivo existe, se añade al prompt del sistema).

Uso:
    python .github/scripts/ai_review.py --diff pr.diff
    python .github/scripts/ai_review.py --diff pr.diff --dry-run

Variables de entorno:
    DEEPSEEK_API_KEY   Clave de la API (obligatoria salvo en --dry-run).
    GITHUB_TOKEN       Token con permiso pull-requests: write.
    GITHUB_REPOSITORY  owner/repo (lo inyecta Actions).
    PR_NUMBER          Número del PR (lo inyecta el workflow).
    PR_HEAD_SHA        SHA de la cabeza del PR (ancla los comentarios).
    PR_TITLE           Título del PR.
    PR_BODY            Descripción del PR (opcional).
    DEEPSEEK_MODEL     Modelo a usar (por defecto deepseek-flash).
    DEEPSEEK_THINKING  "enabled" activa el modo de razonamiento (por defecto
                       "disabled", que es más rápido y barato para una revisión).
    DEEPSEEK_EFFORT    Esfuerzo de razonamiento si THINKING está activo:
                       low, high o max (por defecto high).
    MAX_DIFF_CHARS     Recorte máximo del diff (por defecto 100000).
    MAX_INLINE         Máximo de comentarios en línea (por defecto 4).
    MAX_MINOR          Máximo de hallazgos "menor" listados en el resumen
                       (por defecto 2).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, cast

API_URL = "https://api.deepseek.com/chat/completions"
GITHUB_API = "https://api.github.com"
MARKER = "<!-- deepseek-review -->"
SEVERITIES = ("bloqueante", "importante", "menor")
# Solo estas severidades llegan como comentario en línea; los "menor" se
# agregan al resumen con un tope propio (MAX_MINOR).
INLINE_SEVERITIES = ("bloqueante", "importante")
EFFORTS = ("low", "high", "max")

ROOT = Path(__file__).resolve().parents[2]
STYLE_PATH = ROOT / ".github" / "review" / "style.md"
MAX_STYLE_CHARS = 2000

SYSTEM_PROMPT = """\
Eres un revisor de código senior. Revisas pull requests del proyecto Carvajal, \
un pipeline en Python 3.13 (uv, Ruff, pytest) para capturar y curar un corpus \
web destinado a RAG.

Criterio antes de escribir un hallazgo: ¿esta línea hace que algo falle, pierda \
datos, quede inseguro o rinda peor? Si la respuesta no es un "sí" claro y \
demostrable leyendo el diff, no lo reportes. Un PR sin comentarios es un \
resultado correcto y frecuente.

No reportes:
- estilo, formato, nombres, orden de imports ni longitud de líneas: Ruff lo resuelve.
- redacción de documentación, comentarios o mensajes de error.
- refactors, abstracciones faltantes o "quedaría más limpio".
- mejoras opcionales o hipotéticas ("si mañana se llama con..."), salvo que el
  PR ya rompa algo hoy.
- el mismo problema dos veces: un hallazgo por causa, no por línea afectada.

Reglas:
- "line" debe ser una línea del archivo NUEVO (lado derecho del diff), la que \
aparece en el hunk con '+' o como contexto. Si el hallazgo abarca varias \
líneas, usa la primera.
- No inventes rutas ni líneas que no estén en el diff.
- "bloqueante" solo si el merge debe detenerse: el código está roto o es \
inseguro. "importante" si debe arreglarse pronto pero no impide el merge. \
"menor" solo para un bug real de bajo impacto: se listan como máximo dos, así \
que no lo uses para nimiedades ni preferencias.
- Usa "suggestion" solo cuando el reemplazo exacto de esa línea resuelva el \
problema; si el arreglo necesita contexto, deja null y explica en "comment".
- Máximo 3 hallazgos entre "bloqueante" e "importante", más los "menor" que \
de verdad importen. Si hay más candidatos, conserva solo los que rompen algo.
- Si no hay hallazgos, devuelve "findings": [] y dilo en el resumen.

Responde ÚNICAMENTE con un objeto JSON válido, sin texto adicional, con esta forma:
{
  "summary": "2 o 3 frases sobre qué hace el PR y su riesgo general",
  "findings": [
    {
      "path": "ruta/relativa/al/archivo.py",
      "line": 42,
      "severity": "bloqueante",
      "comment": "qué está mal y por qué, en 1 a 2 frases",
      "suggestion": "código de reemplazo para esa línea, o null"
    }
  ]
}
"severity" debe ser una de: "bloqueante", "importante", "menor"."""


def load_style() -> str:
    """Lee las reglas de estilo editables de .github/review/style.md."""
    try:
        text = STYLE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return text[:MAX_STYLE_CHARS]


def build_system_prompt() -> str:
    """Combina el prompt base con el estilo definido en el repositorio."""
    style = load_style()
    if not style:
        return SYSTEM_PROMPT
    return f"{SYSTEM_PROMPT}\n\n{style}"


def read_diff(path: str, limit: int) -> tuple[str, bool]:
    """Lee el diff y lo recorta al límite indicado."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def build_prompt(diff: str, truncated: bool) -> str:
    """Construye el mensaje de usuario con el contexto y el diff del PR."""
    parts = [f"Título del PR: {os.environ.get('PR_TITLE', '(sin título)')}"]
    body = (os.environ.get("PR_BODY") or "").strip()
    if body:
        parts.append(f"Descripción del PR:\n{body[:2000]}")
    if truncated:
        parts.append("Aviso: el diff fue recortado por tamaño; lo que sigue está incompleto.")
    parts.append(f"Diff:\n```diff\n{diff}\n```")
    return "\n\n".join(parts)


def parse_anchor_lines(diff_text: str) -> dict[str, set[int]]:
    """Devuelve, por archivo, las líneas del lado nuevo que admiten comentario."""
    anchors: dict[str, set[int]] = {}
    path: str | None = None
    new_line = 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ b/"):
            path = raw[6:].strip()
            anchors.setdefault(path, set())
            continue
        if raw.startswith("+++ ") or raw.startswith("--- ") or raw.startswith("diff --git"):
            if raw.startswith("+++ /dev/null"):
                path = None
            continue
        if raw.startswith("@@"):
            match = re.search(r"\+(\d+)", raw)
            if match and path:
                new_line = int(match.group(1))
            continue
        if path is None:
            continue
        if raw.startswith("+"):
            anchors[path].add(new_line)
            new_line += 1
        elif raw.startswith("-") or raw.startswith("\\"):
            continue
        elif raw.startswith(" ") or raw == "":
            anchors[path].add(new_line)
            new_line += 1
    return anchors


def extract_json(text: str) -> dict[str, Any] | None:
    """Extrae el objeto JSON de la respuesta, tolerando cercas de Markdown."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    candidates = [cleaned]
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        candidates.append(cleaned[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def ask_deepseek(prompt: str, model: str, api_key: str) -> tuple[str, str]:
    """Envía el prompt a DeepSeek y devuelve (contenido, finish_reason)."""
    thinking = os.environ.get("DEEPSEEK_THINKING", "disabled").strip().lower() == "enabled"
    effort = os.environ.get("DEEPSEEK_EFFORT", "high").strip().lower()
    if effort not in EFFORTS:
        print(f"DEEPSEEK_EFFORT={effort!r} no es válido; se usa 'high'.", file=sys.stderr)
        effort = "high"

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
        # Los tokens de razonamiento cuentan como salida: si se activa, tope mayor.
        "max_tokens": 8000 if thinking else 4000,
        "stream": False,
    }
    if thinking:
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = effort
    else:
        # En thinking mode la API ignora temperature; solo se envía si está apagado.
        payload["thinking"] = {"type": "disabled"}
        payload["temperature"] = 0.2

    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        data = json.loads(response.read())
    choice = data["choices"][0]
    return choice["message"]["content"].strip(), choice.get("finish_reason", "")


def github_request(method: str, path: str, token: str, payload: dict | None = None) -> object:
    """Ejecuta una petición contra la API de GitHub y devuelve el JSON."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        f"{GITHUB_API}{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def list_pull_comments(repo: str, number: str, token: str) -> list[dict[str, Any]]:
    """Lista los comentarios en línea de un PR."""
    return cast(
        "list[dict[str, Any]]",
        github_request("GET", f"/repos/{repo}/pulls/{number}/comments?per_page=100", token),
    )


def is_bot_comment(comment: dict[str, Any]) -> bool:
    """Indica si el comentario lo escribió este mismo bot."""
    user = comment.get("user") or {}
    return user.get("type") == "Bot" and str(comment.get("body", "")).startswith(MARKER)


def clear_previous_inline(repo: str, number: str, token: str) -> int:
    """Borra los comentarios en línea de ejecuciones anteriores."""
    deleted = 0
    for comment in list_pull_comments(repo, number, token):
        if not is_bot_comment(comment):
            continue
        github_request("DELETE", f"/repos/{repo}/pulls/comments/{comment['id']}", token)
        deleted += 1
    return deleted


def render_finding(finding: dict[str, Any]) -> str:
    """Da formato al cuerpo de un comentario en línea."""
    severity = str(finding.get("severity", "menor")).lower()
    comment = str(finding.get("comment", "")).strip()
    body = f"{MARKER}\n**{severity}**: {comment}"
    suggestion = finding.get("suggestion")
    if isinstance(suggestion, str) and suggestion.strip():
        body += f"\n\n```suggestion\n{suggestion.rstrip()}\n```"
    return body


def post_inline(
    repo: str,
    number: str,
    head_sha: str,
    token: str,
    finding: dict[str, Any],
) -> bool:
    """Publica un comentario anclado a una línea del diff."""
    payload = {
        "body": render_finding(finding),
        "commit_id": head_sha,
        "path": finding["path"],
        "line": int(finding["line"]),
        "side": "RIGHT",
    }
    try:
        github_request("POST", f"/repos/{repo}/pulls/{number}/comments", token, payload)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        print(f"No se pudo anclar {finding['path']}:{finding['line']} ({exc.code}): {detail}")
        return False
    return True


def render_summary(
    summary: str,
    anchored: list[dict[str, Any]],
    unanchored: list[dict[str, Any]],
    minors: list[dict[str, Any]],
    truncated_output: bool,
) -> str:
    """Compone el comentario resumen del PR."""
    lines = [
        MARKER,
        "### Revisión automática (DeepSeek)",
        "",
        summary.strip() or "Sin resumen.",
        "",
    ]

    if anchored:
        lines.append(f"**{len(anchored)} hallazgo(s) comentados en línea** sobre el código.")
    if unanchored:
        lines.append("")
        lines.append("#### No anclados a una línea del diff")
        for finding in unanchored:
            line_no = finding.get("line")
            where = f"{finding['path']}:{line_no}" if line_no else str(finding["path"])
            severity = finding.get("severity", "menor")
            lines.append(f"- **{severity}** — `{where}`: {finding['comment']}")

    if minors:
        lines.append("")
        lines.append("#### Menores (opcionales)")
        for finding in minors:
            line_no = finding.get("line")
            where = f"{finding['path']}:{line_no}" if line_no else str(finding["path"])
            lines.append(f"- `{where}`: {finding['comment']}")

    if truncated_output:
        lines.append("")
        lines.append(
            "> La respuesta del modelo se cortó por límite de tokens; puede faltar código revisado."
        )

    lines.append("")
    lines.append(
        "<sub>Comentario generado por IA: es una ayuda, no reemplaza la revisión humana.</sub>"
    )
    return "\n".join(lines)


def upsert_summary(body: str, token: str, repo: str, number: str) -> None:
    """Crea el comentario resumen o actualiza el de la ejecución anterior."""
    comments = cast(
        "list[dict[str, Any]]",
        github_request("GET", f"/repos/{repo}/issues/{number}/comments?per_page=100", token),
    )
    previous = next((c for c in comments if is_bot_comment(c)), None)
    if previous:
        path = f"/repos/{repo}/issues/comments/{previous['id']}"
        github_request("PATCH", path, token, {"body": body})
        return
    github_request("POST", f"/repos/{repo}/issues/{number}/comments", token, {"body": body})


def split_findings(
    data: dict[str, Any], anchors: dict[str, set[int]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Separa los hallazgos en anclables, no anclables y "menor".

    Solo "bloqueante" e "importante" se comentan en línea. Los "menor" se
    agrupan en el resumen, con un tope de MAX_MINOR, para que la revisión no se
    convierta en una lista de nimiedades.
    """
    raw_findings = data.get("findings")
    if not isinstance(raw_findings, list):
        return [], [], []

    known = {"bloqueante": 0, "importante": 1, "menor": 2}
    findings = [f for f in raw_findings if isinstance(f, dict) and f.get("path")]
    findings.sort(key=lambda f: known.get(str(f.get("severity", "menor")).lower(), 3))

    limit = int(os.environ.get("MAX_INLINE", "4"))
    max_minor = int(os.environ.get("MAX_MINOR", "2"))
    anchored: list[dict[str, Any]] = []
    unanchored: list[dict[str, Any]] = []
    minors: list[dict[str, Any]] = []
    for finding in findings:
        path = str(finding["path"])
        try:
            line = int(finding.get("line") or 0)
        except (TypeError, ValueError):
            line = 0
        severity = str(finding.get("severity", "menor")).lower()
        if severity not in INLINE_SEVERITIES:
            if len(minors) < max_minor:
                minors.append(finding)
            continue
        if len(anchored) < limit and line in anchors.get(path, set()):
            anchored.append(finding)
        else:
            unanchored.append(finding)
    return anchored, unanchored, minors


def main() -> int:
    """Punto de entrada del script."""
    parser = argparse.ArgumentParser(description="Revisión de PRs con DeepSeek.")
    parser.add_argument("--diff", required=True, help="Ruta al archivo con el diff del PR.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Imprime el prompt y no llama a la API ni publica nada.",
    )
    args = parser.parse_args()

    limit = int(os.environ.get("MAX_DIFF_CHARS", "100000"))
    diff, truncated = read_diff(args.diff, limit)
    if not diff.strip():
        print("El diff está vacío: no hay nada que revisar.")
        return 0

    prompt = build_prompt(diff, truncated)

    if args.dry_run:
        print(prompt[:1500])
        print(f"\n[prompt completo: {len(prompt)} caracteres, recortado: {truncated}]")
        print(f"[system prompt: {len(build_system_prompt())} caracteres]")
        anchors = parse_anchor_lines(diff)
        print(f"[anclas detectadas: {sum(len(v) for v in anchors.values())} líneas]")
        return 0

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("DEEPSEEK_API_KEY no está definida.", file=sys.stderr)
        return 1

    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
    try:
        answer, finish_reason = ask_deepseek(prompt, model, api_key)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        print(f"DeepSeek respondió {exc.code}: {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"No se pudo contactar a DeepSeek: {exc.reason}", file=sys.stderr)
        return 1

    data = extract_json(answer)
    if data is None:
        print("La respuesta no era JSON válido; se publica como texto plano.")
        data = {"summary": answer, "findings": []}

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    number = os.environ.get("PR_NUMBER", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    head_sha = os.environ.get("PR_HEAD_SHA", "")
    if not repo or not number or not token:
        print("Faltan GITHUB_REPOSITORY, PR_NUMBER o GITHUB_TOKEN.", file=sys.stderr)
        return 1

    anchors = parse_anchor_lines(diff)
    anchored, unanchored, minors = split_findings(data, anchors)

    if head_sha:
        deleted = clear_previous_inline(repo, number, token)
        if deleted:
            print(f"Se borraron {deleted} comentario(s) en línea previos.")
        for finding in anchored:
            if not post_inline(repo, number, head_sha, token, finding):
                unanchored.append(finding)
    else:
        unanchored = anchored + unanchored
        anchored = []

    body = render_summary(
        str(data.get("summary", "")),
        anchored,
        unanchored,
        minors,
        finish_reason == "length",
    )
    upsert_summary(body, token, repo, number)
    print(
        f"Revisión publicada: {len(anchored)} en línea, "
        f"{len(unanchored)} en el resumen, {len(minors)} menor(es)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
