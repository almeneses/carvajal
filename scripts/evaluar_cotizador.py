"""Ejecuta los casos dorados del Cotizador y devuelve código 1 si alguno falla."""

from carvajal.quotation.evaluation import evaluate, load_cases


def main() -> int:
    cases = load_cases()
    failures = evaluate(cases)
    for failure in failures:
        print(f"FALLO: {failure}")
    print(f"{len(cases) - len({item.split(':')[0] for item in failures})}/{len(cases)} casos OK")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
