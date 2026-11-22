"""Los casos dorados del Cotizador deben coincidir cifra por cifra (spec 042, SC-002)."""

from carvajal.quotation.evaluation import evaluate, load_cases


def test_los_casos_dorados_cotizan_igual() -> None:
    failures = evaluate(load_cases())

    assert failures == []
