"""Pruebas del calendario colombiano del Cotizador (spec 042, FR-013)."""

from datetime import date

from carvajal.quotation.calendar import (
    business_days_after,
    colombian_holidays,
    easter_sunday,
    is_business_day,
    next_business_day,
    window,
)


def test_pascua_2026_cae_el_cinco_de_abril() -> None:
    assert easter_sunday(2026) == date(2026, 4, 5)


def test_colombia_tiene_dieciocho_festivos_y_respeta_la_ley_emiliani() -> None:
    festivos = colombian_holidays(2026)

    assert len(festivos) == 18
    assert date(2026, 1, 1) in festivos
    assert date(2026, 7, 20) in festivos
    # La Epifanía (6 de enero, martes) se traslada al lunes 12.
    assert date(2026, 1, 12) in festivos
    # Semana Santa no se traslada: jueves 2 y viernes 3 de abril.
    assert date(2026, 4, 2) in festivos
    assert date(2026, 4, 3) in festivos


def test_fin_de_semana_y_festivo_no_son_habiles() -> None:
    assert not is_business_day(date(2026, 11, 28))  # sábado
    assert not is_business_day(date(2026, 12, 8))  # Inmaculada Concepción
    assert is_business_day(date(2026, 11, 27))  # viernes


def test_ventana_salta_fin_de_semana_y_festivo() -> None:
    # Viernes 27 de noviembre de 2026: un día hábil cae el lunes 30.
    assert next_business_day(date(2026, 11, 27)) == date(2026, 11, 30)
    # Del viernes 4 de diciembre, el día hábil siguiente es el lunes 7 (el 8 es festivo).
    assert business_days_after(date(2026, 12, 4), 1) == date(2026, 12, 7)
    assert window(date(2026, 11, 27), 1, 2) == (date(2026, 11, 30), date(2026, 12, 1))
