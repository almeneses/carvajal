"""Evalúa las fotos de alimentos con Jev real (spec 051, FR-027). **Gasta créditos de Jev.**

Descarga las fotos de ``specs/051-fotos-alimentos/evaluacion/fotos.json`` (Wikimedia
Commons, licencias libres), las prepara igual que el servidor, las clasifica (una llamada
por foto) y aplica la recomendación del catálogo. Mide:

- SC-001: tipo correcto en las fotos de comida (meta ≥ 85 %) y rechazo o pregunta en el
  100 % de las que no son comida.
- SC-002: ninguna sugerencia de un material que la guía declara no apto (meta: 0).
- Latencia de Jev (p50 y p95) y tokens de entrada por foto.

Las fotos solo viven en memoria. Escribe un informe Markdown y, con ``--json``, el detalle
sin imágenes. Las llamadas se anotan en ``evaluacion/jev-calls.jsonl`` (sin texto).

    uv run python scripts/evaluar_fotos.py [--limit N] [--output informe.md] [--json det.json]
"""

import argparse
import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from carvajal.adapters.judge import build_food_classifier
from carvajal.application.ports import JudgeUnavailable
from carvajal.catalog.loader import load_catalog
from carvajal.food.photo import PhotoRejected, prepare_photo
from carvajal.food.recommend import application_level, recommend
from carvajal.infrastructure.settings import Settings

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "specs" / "051-fotos-alimentos" / "evaluacion"
USER_AGENT = "carvajal-eval-fotos/0.1 (evaluacion spec 051)"


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=0, help="Máximo de fotos (0 = todas)")
    parser.add_argument("--output", type=Path, default=EVAL_DIR / "informe.md")
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    settings = Settings()
    classifier = build_food_classifier(settings, ledger=EVAL_DIR / "jev-calls.jsonl")
    if classifier is None:
        print("Falta TYPESAFE_API_KEY o FOOD_PHOTO_ENABLED=false.", file=sys.stderr)
        return 2
    classifier.client.run = "eval-051"
    catalog = load_catalog()
    photos = json.loads((EVAL_DIR / "fotos.json").read_text(encoding="utf-8"))["fotos"]
    if args.limit:
        photos = photos[: args.limit]

    rows: list[dict[str, Any]] = []
    for item in photos:
        classifier.client.item = item["id"]
        row: dict[str, Any] = {"id": item["id"], "esperado": item["esperado"]}
        try:
            prepared = prepare_photo(_download(item["url"]))
            profile, call = classifier.classify(prepared)
        except (PhotoRejected, JudgeUnavailable, OSError) as error:
            row["error"] = type(error).__name__ + (
                f":{error.status}" if isinstance(error, JudgeUnavailable) else ""
            )
            rows.append(row)
            print(f"{item['id']}: {row['error']}", flush=True)
            continue
        rec = recommend(profile, catalog=catalog, min_confidence=settings.food_photo_min_confidence)
        unfit = [
            reference.sku
            for reference in rec.references
            if rec.requirements is not None
            and (
                application_level(catalog.materials.get(reference.material), rec.requirements.use)
                == "no_apto"
                or (
                    rec.requirements.temperature == "caliente"
                    and (rule := catalog.materials.get(reference.material)) is not None
                    and not rule.hot_content
                )
            )
        ]
        expected = item["esperado"]
        row.update(
            perfil=profile.metadata(),
            estado=rec.status,
            sugerencias=[reference.sku for reference in rec.references],
            no_aptas=unfit,
            latencia_ms=call.latency_ms,
            tokens=call.input_tokens,
            tipo_ok=profile.kind in expected["tipos"]
            or (not expected["comida"] and rec.status == "not_food"),
            temperatura_ok=not expected["temperaturas"]
            or profile.temperature in expected["temperaturas"],
        )
        rows.append(row)
        print(
            f"{item['id']}: {profile.kind} ({profile.kind_confidence:.2f}) "
            f"{profile.temperature} → {rec.status} {row['sugerencias']}",
            flush=True,
        )
        time.sleep(1)  # cortesía con Wikimedia

    food = [row for row in rows if row["esperado"]["comida"] and "error" not in row]
    controls = [row for row in rows if not row["esperado"]["comida"] and "error" not in row]
    latencies = [row["latencia_ms"] for row in rows if "latencia_ms" in row]
    tokens = [row["tokens"] for row in rows if "tokens" in row]
    kind_hits = sum(1 for row in food if row["tipo_ok"])
    temp_hits = sum(1 for row in food if row["temperatura_ok"])
    rejected = sum(1 for row in controls if row["estado"] in {"not_food", "ask_kind"})
    unfit_total = sum(len(row["no_aptas"]) for row in rows if "no_aptas" in row)
    errors = [row for row in rows if "error" in row]

    lines = [
        "# Evaluación de fotos de alimentos (spec 051)",
        "",
        f"Fotos: {len(rows)} ({len(food)} de comida, {len(controls)} de control, "
        f"{len(errors)} con error). Modelo: `{settings.jev_model}`.",
        "",
        "| Métrica | Resultado | Meta |",
        "|---|---|---|",
        f"| Tipo correcto (comida) | {kind_hits}/{len(food)} | ≥ 85 % |",
        f"| Temperatura aceptada (comida) | {temp_hits}/{len(food)} | informativa |",
        f"| Controles rechazados o preguntados | {rejected}/{len(controls)} | 100 % |",
        f"| Sugerencias no aptas | {unfit_total} | 0 |",
        f"| Latencia p50 / p95 | {int(statistics.median(latencies)) if latencies else 0} / "
        f"{_p95(latencies)} ms | < 5 s |",
        f"| Tokens de entrada por foto (media) | {int(statistics.mean(tokens)) if tokens else 0} "
        "| informativa |",
        "",
        "| Foto | Tipo (conf.) | Temperatura (conf.) | Estado | Sugerencias | OK |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        if "error" in row:
            lines.append(f"| {row['id']} | error: {row['error']} | | | | ✗ |")
            continue
        perfil = row["perfil"]
        ok = "✓" if row["tipo_ok"] and row["temperatura_ok"] and not row["no_aptas"] else "✗"
        lines.append(
            f"| {row['id']} | {perfil['tipo']} ({perfil['conf_tipo']}) | "
            f"{perfil['temperatura']} ({perfil['conf_temperatura']}) | {row['estado']} | "
            f"{', '.join(row['sugerencias']) or '—'} | {ok} |"
        )
    args.output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if args.json is not None:
        args.json.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Informe: {args.output}")
    passed = (
        food
        and kind_hits / len(food) >= 0.85
        and rejected == len(controls)
        and unfit_total == 0
        and not errors
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
