#!/usr/bin/env python3
"""Revisión automática de pull requests con DeepSeek.

Toma el diff de un PR, pide una revisión a la API de DeepSeek (compatible con la
de OpenAI) y publica el resultado como comentario en el PR. Si ya existe un
comentario previo de este script, lo actualiza en lugar de duplicarlo.

Uso:
    python .github/scripts/ai_review.py --diff pr.diff
    python .github/scripts/ai_review.py --diff pr.diff --dry-run

Variables de entorno:
    DEEPSEEK_API_KEY   Clave de la API (obligatoria salvo en --dry-run).
    GITHUB_TOKEN       Token con permiso pull-requests: write.
    GITHUB_REPOSITORY  owner/repo (lo inyecta Actions).
    PR_NUMBER          Número del PR (lo inyecta el workflow).
    PR_TITLE           Título del PR.
    PR_BODY            Descripción del PR (opcional).
    DEEPSEEK_MODEL     Modelo a usar (por defecto deepseek-flash).
    DEEPSEEK_THINKING  "enabled" activa el modo de razonamiento (por defecto
                       "disabled", que es más rápido y barato para una revisión).
    DEEPSEEK_EFFORT    Esfuerzo de razonamiento si THINKING está activo:
                       low, high o max (por defecto high).
    MAX_DIFF_CHARS     Recorte máximo del diff (por defecto 100000).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, cast

API_URL = "https://api.deepseek.com/chat/completions"
GITHUB_API = "https://api.github.com"
MARKER = "<!-- deepseek-review -->"

SYSTEM_PROMPT = """\
Eres un revisor de código senior. Revisas pull requests del proyecto Carvajal, \
un pipeline en Python 3.13 (uv, Ruff, pytest) para capturar y curar un corpus \
web destinado a RAG.

Reglas:
- Comenta solo problemas reales: correctitud, seguridad, manejo de errores, \
rendimiento, tipado y pruebas faltantes.
- No comentes estilo ni formato: Ruff ya lo resuelve automáticamente.
- Cita el archivo y la línea del diff cuando puedas.
- Si el diff está recortado, dilo explícitamente y limita tus conclusiones a \
lo que sí ves.
- No inventes código que no aparezca en el diff.
- Sé conciso: como máximo 12 hallazgos, ordenados por severidad.

Responde en español y en Markdown con esta estructura:
## Resumen
(2 o 3 frases sobre qué hace el PR y su riesgo general)
## Hallazgos
(lista agrupada por severidad: Bloqueante, Importante, Menor; si no hay \
hallazgos, escribe "Sin hallazgos bloqueantes")
## Sugerencias
(ideas opcionales, sin bloquear el merge)"""


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


def ask_deepseek(prompt: str, model: str, api_key: str) -> str:
    """Envía el prompt a DeepSeek y devuelve el texto de la revisión."""
    thinking = os.environ.get("DEEPSEEK_THINKING", "disabled").strip().lower() == "enabled"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 8000 if thinking else 4000,
        "stream": False,
    }
    if thinking:
        # Los tokens de razonamiento cuentan como salida, por eso el tope más alto.
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = os.environ.get("DEEPSEEK_EFFORT", "high")
    else:
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
    return data["choices"][0]["message"]["content"].strip()


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


def upsert_comment(body: str, token: str, repo: str, number: str) -> None:
    """Crea el comentario de revisión o actualiza el que ya exista."""
    comments = cast(
        "list[dict[str, Any]]",
        github_request("GET", f"/repos/{repo}/issues/{number}/comments?per_page=100", token),
    )
    existing = next((c for c in comments if c["body"].startswith(MARKER)), None)
    if existing:
        path = f"/repos/{repo}/issues/comments/{existing['id']}"
        github_request("PATCH", path, token, {"body": body})
        return
    github_request("POST", f"/repos/{repo}/issues/{number}/comments", token, {"body": body})


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
        return 0

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("DEEPSEEK_API_KEY no está definida.", file=sys.stderr)
        return 1

    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
    try:
        review = ask_deepseek(prompt, model, api_key)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        print(f"DeepSeek respondió {exc.code}: {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"No se pudo contactar a DeepSeek: {exc.reason}", file=sys.stderr)
        return 1

    comment = (
        f"{MARKER}\n"
        "### Revisión automática (DeepSeek)\n\n"
        f"{review}\n\n"
        "<sub>Comentario generado por IA: es una ayuda, no reemplaza la revisión humana.</sub>\n"
    )

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    number = os.environ.get("PR_NUMBER", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not repo or not number or not token:
        print("Faltan GITHUB_REPOSITORY, PR_NUMBER o GITHUB_TOKEN.", file=sys.stderr)
        return 1

    upsert_comment(comment, token, repo, number)
    print("Revisión publicada en el PR.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
