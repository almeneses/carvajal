"""Evaluación reproducible del retrieval en Qdrant.

Calcula Recall@10 y nDCG@10 con etiquetas manuales por documento y guarda el
top-k de cada consulta. No llama al LLM: separa retrieval de calidad de
respuesta, como exige la rúbrica del M2.

Uso:
    uv run python scripts/eval_retrieval.py
    uv run python scripts/eval_retrieval.py --k 10 --output docs/eval/retrieval-report.md
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from carvajal.adapters.embeddings import OllamaEmbedder
from carvajal.adapters.vector.qdrant_store import QdrantVectorStore
from carvajal.application.evidence import proper_entities_supported
from carvajal.infrastructure.settings import Settings

DEFAULT_QUERIES = Path("data") / "eval" / "retrieval_questions.json"
DEFAULT_OUTPUT = Path("docs") / "eval" / "retrieval-report.md"
DEFAULT_JSON = Path("docs") / "eval" / "retrieval-results.json"


def dcg(relevances: list[int]) -> float:
    """DCG con ganancia binaria y descuento logarítmico estándar."""

    return sum(rel / math.log2(position + 1) for position, rel in enumerate(relevances, start=1))


def ndcg_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """nDCG@k con relevancia binaria a nivel de documento."""

    if not relevant:
        return 0.0
    gains = [1 if document in relevant else 0 for document in retrieved[:k]]
    ideal = [1] * min(len(relevant), k)
    ideal_dcg = dcg(ideal)
    return dcg(gains) / ideal_dcg if ideal_dcg else 0.0


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """Fracción de documentos relevantes recuperados en el top-k."""

    if not relevant:
        return 0.0
    return len(set(retrieved[:k]) & relevant) / len(relevant)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evalúa Recall@k y nDCG@k del retrieval.")
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--k", type=int, default=10)
    args = parser.parse_args(argv)

    settings = Settings()
    store = QdrantVectorStore(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        collection=settings.qdrant_collection,
        dimensions=settings.qdrant_dimensions,
        distance=settings.qdrant_distance,
    )
    health = store.health()
    if health.get("status") != "ok":
        print(f"Qdrant no está listo: {health}", file=sys.stderr)
        return 1
    embedder = OllamaEmbedder()
    payload = json.loads(args.queries.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    recalls: list[float] = []
    ndcgs: list[float] = []
    abstentions: list[dict[str, Any]] = []

    for item in payload["queries"]:
        vector = embedder.embed(item["query"])
        results = store.search(vector, limit=args.k)
        retrieved = [result.chunk.document_id for result in results]
        # Para nDCG se cuenta cada documento una sola vez, aunque recupere
        # varios fragmentos suyos en el top-k.
        unique_retrieved = list(dict.fromkeys(retrieved))
        scores = [round(result.score, 4) for result in results]
        relevant = set(item.get("relevant", []))
        row = {
            "id": item["id"],
            "query": item["query"],
            "relevant": sorted(relevant),
            "retrieved": retrieved,
            "scores": scores,
            "top_score": scores[0] if scores else 0.0,
            "excerpts": [
                {
                    "document": result.chunk.document_id,
                    "chunk_id": result.chunk.id,
                    "score": round(result.score, 4),
                    "url": result.metadata.get("url", ""),
                    "excerpt": " ".join(result.chunk.text.split())[:220],
                }
                for result in results
            ],
        }
        if item.get("expect_abstain"):
            excerpts = [str(fragment["excerpt"]) for fragment in row["excerpts"]]
            row["abstained"] = row["top_score"] < settings.rag_min_score or not (
                proper_entities_supported(item["query"], excerpts)
            )
            abstentions.append(row)
        else:
            row["recall_at_k"] = recall_at_k(retrieved, relevant, args.k)
            row["ndcg_at_k"] = ndcg_at_k(unique_retrieved, relevant, args.k)
            recalls.append(row["recall_at_k"])
            ndcgs.append(row["ndcg_at_k"])
        rows.append(row)
        print(
            f"{item['id']}: recall@{args.k}={row.get('recall_at_k', 'n/a')} "
            f"ndcg@{args.k}={row.get('ndcg_at_k', 'n/a')} top={retrieved[:3]}"
        )

    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "collection": settings.qdrant_collection,
        "index": health,
        "k": args.k,
        "queries": len(rows),
        "labeled_queries": len(recalls),
        "recall_at_k": round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
        "ndcg_at_k": round(sum(ndcgs) / len(ndcgs), 4) if ndcgs else 0.0,
        "abstention_queries": abstentions,
        "rows": rows,
    }
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_markdown(summary), encoding="utf-8")
    print(
        f"Recall@{args.k}={summary['recall_at_k']} nDCG@{args.k}={summary['ndcg_at_k']} "
        f"-> {args.output}"
    )
    store.close()
    return 0


def render_markdown(summary: dict[str, Any]) -> str:
    """Reporte legible con el top-k de cada consulta y su relevancia."""

    lines = [
        "# Evaluación de retrieval — Qdrant",
        "",
        f"> Generado: {summary['generated_at']} · colección `{summary['collection']}` · "
        f"k={summary['k']} · puntos={summary['index'].get('points')}",
        "",
        "Etiquetas manuales a nivel de documento. La relevancia se marca sobre "
        "`data/gold`; no depende del LLM.",
        "",
        "## Resumen",
        "",
        "| Métrica | Valor |",
        "|---|---:|",
        f"| Consultas etiquetadas | {summary['labeled_queries']} |",
        f"| Recall@{summary['k']} | {summary['recall_at_k']:.4f} |",
        f"| nDCG@{summary['k']} | {summary['ndcg_at_k']:.4f} |",
        f"| Consultas sin evidencia | {len(summary['abstention_queries'])} |",
        "",
        "## Detalle por consulta",
        "",
        "| ID | Consulta | Recall@k | nDCG@k | Top-3 recuperado |",
        "|---|---|---:|---:|---|",
    ]
    for row in summary["rows"]:
        top = ", ".join(row["retrieved"][:3]) or "—"
        if "recall_at_k" in row:
            lines.append(
                f"| {row['id']} | {row['query']} | {row['recall_at_k']:.3f} | "
                f"{row['ndcg_at_k']:.3f} | {top} |"
            )
        else:
            lines.append(
                f"| {row['id']} | {row['query']} | abstención: "
                f"{'sí' if row['abstained'] else 'no'} | — | {top} |"
            )
    lines.extend(["", "## Top-3 por consulta", ""])
    for row in summary["rows"]:
        top = ", ".join(row["retrieved"][:3]) or "—"
        scores = ", ".join(str(score) for score in row["scores"][:3]) or "—"
        lines.append(f"- **{row['id']}** · {row['query']}")
        lines.append(f"  - top-3: `{top}`")
        lines.append(f"  - scores: `{scores}`")
    lines.append("")
    lines.append(
        "El JSON completo con extractos y los 10 resultados por consulta se "
        "regenera con el mismo comando y no se versiona para no inflar el PR."
    )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
